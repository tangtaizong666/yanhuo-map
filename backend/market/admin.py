import uuid
from django.contrib import admin, messages
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from .models import (Area, AuditLog, BusinessSession, DeliveryPoint, Event, Feedback, Follow, MerchantProfile,
    Order, OrderItem, PaymentAttempt, PaymentRefund, Product, Review, SiteConfiguration, Stall, StallLocation,
    MerchantApplication, RestockBatch, StockCorrection, FinancialEvidence, WorkerHeartbeat)
from .services import audit


class AuditedAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        audit(request.user, 'admin_update' if change else 'admin_create', f'{obj._meta.label}:{obj.pk}', fields=form.changed_data)
    def delete_model(self, request, obj):
        audit(request.user, 'admin_delete', f'{obj._meta.label}:{obj.pk}')
        super().delete_model(request, obj)
    def delete_queryset(self, request, queryset):
        for obj in queryset:
            audit(request.user, 'admin_delete', f'{obj._meta.label}:{obj.pk}')
        super().delete_queryset(request, queryset)


@admin.register(MerchantProfile)
class MerchantAdmin(AuditedAdmin):
    list_display = ['business_name', 'user', 'is_verified', 'license_number']
    list_filter = ['is_verified']
    search_fields = ['business_name', 'user__username']


class LocationInline(admin.StackedInline):
    model = StallLocation
    extra = 1


class ProductInline(admin.TabularInline):
    model = Product
    extra = 0
    can_delete = False
    show_change_link = True
    fields = ['name', 'price_cents', 'stock', 'stock_version', 'sale_paused', 'is_active']
    readonly_fields = fields
    def has_add_permission(self, request, obj=None): return False
    def has_change_permission(self, request, obj=None): return False


@admin.register(Stall)
class StallAdmin(AuditedAdmin):
    list_display = ['name', 'area', 'category', 'transaction_enabled', 'delivery_approved', 'delivery_enabled', 'is_visible', 'is_demo']
    list_filter = ['area', 'transaction_enabled', 'delivery_approved', 'is_demo']
    filter_horizontal = ['delivery_points']
    search_fields = ['name']
    inlines = [LocationInline, ProductInline]
    readonly_fields = ['current_session', 'receiving_seen_at']
    def save_model(self, request, obj, form, change):
        if not change: return super().save_model(request, obj, form, change)
        with transaction.atomic():
            current = Stall.objects.select_for_update(no_key=True).get(pk=obj.pk)
            concrete = {field.name for field in Stall._meta.concrete_fields}
            changed = [name for name in form.changed_data if name in concrete and name not in self.readonly_fields]
            for name in changed: setattr(current, name, getattr(obj, name))
            if changed: current.save(update_fields=changed)
            obj.refresh_from_db()
            audit(request.user, 'admin_update', f'{obj._meta.label}:{obj.pk}', fields=changed)
    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        for formset in formsets:
            for inline_form in formset.forms:
                if inline_form.changed_data:
                    audit(request.user, 'admin_inline_update', f'{inline_form.instance._meta.label}:{inline_form.instance.pk}', fields=inline_form.changed_data)


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    def has_add_permission(self, request, obj=None): return False
    def has_change_permission(self, request, obj=None): return False


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


@admin.register(Order)
class OrderAdmin(ReadOnlyAdmin):
    list_display = ['number', 'mode', 'stall_name', 'status', 'payment_status', 'total_cents', 'created_at']
    list_filter = ['mode', 'status', 'payment_status', 'stall']
    search_fields = ['number', 'user__username']
    inlines = [OrderItemInline]
    change_form_template = 'admin/market/order/change_form.html'
    def get_urls(self):
        return [path('<path:object_id>/financial-resolution/', self.admin_site.admin_view(self.financial_view),
            name='market_order_financial_resolution')] + super().get_urls()
    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = {**(extra_context or {}), 'can_resolve_finance': request.user.has_perm('market.resolve_payments')}
        return super().change_view(request, object_id, form_url, extra_context)
    def financial_view(self, request, object_id):
        from .errors import BusinessError
        from .reconciliation import require_operator, verify_and_resolve, verify_external_refund, request_operator_refund
        from django.core.exceptions import PermissionDenied
        from django.http import Http404
        try: require_operator(request.user)
        except BusinessError: raise PermissionDenied from None
        order = self.get_object(request, object_id)
        if order is None: raise Http404
        form = FinancialResolutionForm(request.POST if request.method == 'POST' else None, order=order,
            initial={'operation_key': uuid.uuid4().hex})
        if request.method == 'POST' and form.is_valid():
            values = form.cleaned_data
            try:
                if values['operation'] == 'verify':
                    verify_and_resolve(order.pk, request.user, values['reason'])
                elif values['operation'] == 'external':
                    verify_external_refund(order.pk, values['payment'].pk, values['out_refund_no'], request.user, values['reason'])
                else:
                    request_operator_refund(order.pk, values['payment'].pk, request.user, values['reason'],
                        values['operation_key'], values['expected_amount_cents'],
                        replaces_id=values['refund'].pk if values['operation'] == 'retry' else None)
                self.message_user(request, '已保存核验结果。申请退款仍以支付服务方确认成功为准；有待处理资金时继续保留限制。')
                return HttpResponseRedirect(reverse('admin:market_order_financial_resolution', args=[order.pk]))
            except BusinessError as exc:
                form.add_error(None, exc.detail.get('detail', '资金结果尚未核验。'))
                order.refresh_from_db()
        return TemplateResponse(request, 'admin/market/order/financial_resolution.html', {
            **self.admin_site.each_context(request), 'title': f'资金核验：{order.number}', 'opts': self.model._meta,
            'order': order, 'payments': order.payments.all(), 'refunds': order.refunds.all(),
            'evidence': order.financial_evidence.select_related('actor')[:50], 'form': form})


@admin.register(AuditLog)
class AuditAdmin(ReadOnlyAdmin):
    list_display = ['created_at', 'actor', 'action', 'target']
    list_filter = ['action']
    search_fields = ['target', 'actor__username']


class FeedbackVerificationForm(forms.ModelForm):
    class Meta:
        model = Feedback
        fields = '__all__'

    def clean(self):
        values = super().clean()
        if values.get('verification') in ('confirmed', 'dismissed') and not values.get('verification_note', '').strip():
            self.add_error('verification_note', '请记录核实方式与结论依据，处理完成本身不代表已核实。')
        return values


@admin.register(Feedback)
class FeedbackAdmin(AuditedAdmin):
    form = FeedbackVerificationForm
    list_display = ['created_at', 'kind', 'stall', 'order', 'user', 'content', 'verification', 'resolved']
    list_filter = ['resolved', 'kind', 'verification']
    readonly_fields = ['user', 'kind', 'stall', 'order', 'location_snapshot', 'content', 'contact', 'created_at', 'dedupe_key', 'request_hash']
    def has_add_permission(self, request): return False


@admin.register(SiteConfiguration)
class ConfigurationAdmin(AuditedAdmin):
    list_display = ['stale_minutes', 'pending_minutes']
    def has_add_permission(self, request): return not SiteConfiguration.objects.exists()
    def has_delete_permission(self, request, obj=None): return False


admin.site.register(Area, AuditedAdmin)
@admin.register(DeliveryPoint)
class DeliveryPointAdmin(AuditedAdmin):
    list_display = ['name', 'area', 'address', 'is_active', 'is_simulation']
    list_filter = ['area', 'is_active', 'is_simulation']
    readonly_fields = ['is_simulation']
    search_fields = ['name', 'address']

class StockCorrectionForm(forms.Form):
    stock = forms.IntegerField(label='更正后的线上可售余量', min_value=0, max_value=100000,
        help_text='只填写尚未被线上订单预留的余量；不要把已接单份数或线下备用份数计入。')
    reason = forms.CharField(label='更正原因', max_length=200, widget=forms.Textarea(attrs={'rows': 3}))
    expected_stock_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)
    idempotency_key = forms.CharField(min_length=8, max_length=128, widget=forms.HiddenInput)


@admin.register(Product)
class ProductAdmin(AuditedAdmin):
    list_display = ['name', 'stall', 'stock', 'stock_version', 'sale_paused', 'is_active']
    list_filter = ['sale_paused', 'is_active', 'stall']
    search_fields = ['name', 'stall__name']
    change_form_template = 'admin/market/product/change_form.html'
    def get_readonly_fields(self, request, obj=None):
        return ['stock_version', 'stock', 'stall'] if obj else ['stock_version']
    def save_model(self, request, obj, form, change):
        if not change: return super().save_model(request, obj, form, change)
        with transaction.atomic():
            from .views import merchant_stall
            merchant_stall(obj.stall_id, request.user, locked=True, permission='market.change_product')
            desired = {name: getattr(obj, name) for name in form.changed_data if name not in ('stock', 'stock_version')}
            current = Product.objects.select_for_update().get(pk=obj.pk)
            for name, value in desired.items(): setattr(current, name, value)
            if desired: current.save(update_fields=list(desired))
            obj.refresh_from_db()
            audit(request.user, 'admin_update', f'{obj._meta.label}:{obj.pk}', fields=list(desired))
    def get_urls(self):
        return [path('<path:object_id>/correct-stock/', self.admin_site.admin_view(self.correct_stock_view),
            name='market_product_correct_stock')] + super().get_urls()
    def correct_stock_view(self, request, object_id):
        from .counter import correct_stock
        from .errors import BusinessError
        from rest_framework.exceptions import ValidationError as APIValidationError
        from django.core.exceptions import PermissionDenied
        from django.http import Http404
        product = self.get_object(request, object_id)
        if product is None: raise Http404
        if not self.has_change_permission(request, product): raise PermissionDenied
        form = StockCorrectionForm(request.POST if request.method == 'POST' else None,
            initial={'stock': product.stock, 'expected_stock_version': product.stock_version, 'idempotency_key': uuid.uuid4().hex})
        if request.method == 'POST' and form.is_valid():
            try:
                _, replayed = correct_stock(product.pk, request.user, form.cleaned_data)
                self.message_user(request, '此更正已处理，未重复修改库存。' if replayed else '线上可售余量已更正并记录。')
                return HttpResponseRedirect(reverse('admin:market_product_change', args=[product.pk]))
            except (BusinessError, APIValidationError) as exc:
                form.add_error(None, exc.detail.get('detail', str(exc.detail)))
                product.refresh_from_db()
        return TemplateResponse(request, 'admin/market/product/correct_stock.html', {
            **self.admin_site.each_context(request), 'title': f'更正线上可售余量：{product.name}',
            'opts': self.model._meta, 'product': product, 'form': form})


admin.site.register(Review, ReadOnlyAdmin)
admin.site.register(Event, ReadOnlyAdmin)
admin.site.register(BusinessSession, ReadOnlyAdmin)
admin.site.register(RestockBatch, ReadOnlyAdmin)
admin.site.register(StockCorrection, ReadOnlyAdmin)


class ApplicationAdminForm(forms.ModelForm):
    class Meta:
        model = MerchantApplication
        fields = '__all__'
    def clean_user(self):
        user = self.cleaned_data['user']
        if not self.instance.pk and (not user.is_active or user.is_staff or MerchantProfile.objects.filter(user=user).exists()):
            raise ValidationError('请选择已注册、启用且尚无商户档案的普通账号。')
        return user


@admin.register(MerchantApplication)
class ApplicationAdmin(AuditedAdmin):
    form = ApplicationAdminForm
    list_display = ['stall_name', 'user', 'source', 'status', 'confirmed_at', 'updated_at']
    list_filter = ['status', 'source']
    search_fields = ['stall_name', 'business_name', 'user__username']
    actions = ['approve', 'needs_changes', 'reject']
    readonly_fields = ['source', 'status', 'approved_stall', 'reviewed_by', 'confirmed_at', 'submitted_at', 'created_at', 'updated_at']
    application_fields = ['business_name', 'stall_name', 'contact_phone', 'area', 'category', 'address_note', 'description']
    def get_readonly_fields(self, request, obj=None):
        fields = list(self.readonly_fields)
        if obj:
            fields.append('user')
            if obj.status in ('submitted', 'approved'): fields += self.application_fields
        return fields
    def has_delete_permission(self, request, obj=None): return False
    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            # Use the same account -> application lock order as owner submit and
            # approval. An old admin form cannot overwrite a newer submission.
            get_user_model().objects.select_for_update(no_key=True).get(pk=obj.user_id)
            if not change:
                obj.source = 'assisted'
            else:
                current = MerchantApplication.objects.select_for_update().get(pk=obj.pk)
                desired = {name: getattr(obj, name) for name in form.changed_data}
                if current.status in ('submitted', 'approved'):
                    desired = {name: value for name, value in desired.items() if name == 'review_note'}
                # Preserve server-owned status, reviewer and approval linkage.
                obj.refresh_from_db()
                for name, value in desired.items(): setattr(obj, name, value)
                if any(name in desired for name in self.application_fields): obj.confirmed_at = None
            super().save_model(request, obj, form, change)
    def review(self, request, queryset, decision):
        from .operations import review_application
        from .errors import BusinessError
        from rest_framework.exceptions import ValidationError as APIValidationError
        for application in queryset:
            try:
                value = review_application(application.pk, request.user, decision)
                self.message_user(request, f'{value.stall_name}：{value.get_status_display()}。建档后仍须独立核验位置、资质与接单权限。')
            except (BusinessError, APIValidationError) as exc:
                self.message_user(request, f'{application.stall_name}：{exc.detail}', level=messages.ERROR)
    @admin.action(description='批准建档（不会开放展示或交易）', permissions=['change'])
    def approve(self, request, queryset): self.review(request, queryset, 'approved')
    @admin.action(description='要求补充资料（先填写审核意见）', permissions=['change'])
    def needs_changes(self, request, queryset): self.review(request, queryset, 'needs_changes')
    @admin.action(description='不予通过（先填写审核意见）', permissions=['change'])
    def reject(self, request, queryset): self.review(request, queryset, 'rejected')


@admin.register(PaymentAttempt)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ['out_trade_no', 'mode', 'merchant', 'order', 'status', 'amount_cents', 'created_at', 'paid_at']
    list_filter = ['mode', 'status', 'channel', 'merchant']
    search_fields = ['out_trade_no', 'transaction_id', 'order__number']
    exclude = ['code_url', 'h5_url']
    change_list_template = 'admin/market/paymentattempt/change_list.html'
    def get_urls(self):
        return [path('reconciliation-export/', self.admin_site.admin_view(self.export_view),
            name='market_payment_reconciliation_export')] + super().get_urls()
    def export_view(self, request):
        from .errors import BusinessError
        from .reconciliation import require_operator
        from .financial_export import reconciliation_export
        from django.core.exceptions import PermissionDenied
        try: require_operator(request.user, 'market.export_financial_reconciliation')
        except BusinessError: raise PermissionDenied from None
        form = FinancialExportForm(request.POST if request.method == 'POST' else None)
        if request.method == 'POST' and form.is_valid():
            try:
                return reconciliation_export(request.user, form.cleaned_data['merchant'].pk,
                    form.cleaned_data['starts_on'], form.cleaned_data['ends_on'])
            except BusinessError as exc:
                form.add_error(None, exc.detail.get('detail', '无法导出。'))
        return TemplateResponse(request, 'admin/market/paymentattempt/reconciliation_export.html', {
            **self.admin_site.each_context(request), 'title': '商户资金对账导出', 'opts': self.model._meta, 'form': form})


@admin.register(PaymentRefund)
class RefundAdmin(ReadOnlyAdmin):
    list_display = ['out_refund_no', 'mode', 'order', 'status', 'source', 'amount_cents', 'created_at', 'completed_at', 'resolved_at']
    list_filter = ['mode', 'status']
    search_fields = ['out_refund_no', 'refund_id', 'order__number']


class PaymentChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.out_trade_no} · {obj.amount_cents}分 · {obj.get_status_display()}'


class RefundChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.out_refund_no} · {obj.amount_cents}分 · {obj.get_status_display()}'


class FinancialResolutionForm(forms.Form):
    operation = forms.ChoiceField(label='操作', choices=[('verify', '查询全部流水并尝试结案'),
        ('external', '核验外部全额退款'), ('retry', '核验关闭退款并申请新尝试'), ('compensation', '对异常付款申请全额补偿')])
    payment = PaymentChoiceField(label='原付款', queryset=PaymentAttempt.objects.none(), required=False)
    refund = RefundChoiceField(label='原关闭退款', queryset=PaymentRefund.objects.none(), required=False)
    out_refund_no = forms.CharField(label='外部商户退款单号', max_length=64, required=False)
    expected_amount_cents = forms.IntegerField(label='确认退款金额（分）', min_value=1, required=False)
    reason = forms.CharField(label='原因及处理依据', max_length=200, widget=forms.Textarea(attrs={'rows': 3}))
    confirm_refund = forms.BooleanField(label='我已核对原付款与全额金额，授权向原支付渠道申请此退款', required=False)
    operation_key = forms.CharField(min_length=8, max_length=128, widget=forms.HiddenInput)
    def __init__(self, *args, order, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['payment'].queryset = order.payments.filter(mode='live')
        self.fields['refund'].queryset = order.refunds.filter(mode='live', status='closed', resolved_at__isnull=True)
    def clean(self):
        values = super().clean()
        operation, payment, refund = values.get('operation'), values.get('payment'), values.get('refund')
        if operation != 'verify' and payment is None: self.add_error('payment', '请选择要核验的原付款。')
        if operation == 'external' and not values.get('out_refund_no'): self.add_error('out_refund_no', '请填写外部退款号。')
        if operation == 'retry':
            if refund is None: self.add_error('refund', '请选择原关闭退款。')
            elif payment and refund.payment_id != payment.pk: self.add_error('refund', '此退款不属于所选付款。')
        if operation in ('retry', 'compensation'):
            if not values.get('confirm_refund'): self.add_error('confirm_refund', '申请退款须确认原付款与金额。')
            if payment and values.get('expected_amount_cents') != payment.amount_cents:
                self.add_error('expected_amount_cents', '必须与所选原付款的全额金额一致。')
        return values


class FinancialExportForm(forms.Form):
    merchant = forms.ModelChoiceField(label='收款商户', queryset=MerchantProfile.objects.all())
    starts_on = forms.DateField(label='开始日期', widget=forms.DateInput(attrs={'type': 'date'}))
    ends_on = forms.DateField(label='结束日期（含）', widget=forms.DateInput(attrs={'type': 'date'}))


@admin.register(FinancialEvidence)
class FinancialEvidenceAdmin(ReadOnlyAdmin):
    list_display = ['created_at', 'order', 'operation', 'outcome', 'actor']
    list_filter = ['operation', 'outcome', 'created_at']
    search_fields = ['order__number', 'payment__out_trade_no', 'refund__out_refund_no']


@admin.register(WorkerHeartbeat)
class WorkerHeartbeatAdmin(ReadOnlyAdmin):
    list_display = ['name', 'last_success_at', 'last_failure_at', 'last_error_code', 'failure_count']
