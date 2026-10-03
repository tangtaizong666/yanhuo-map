"""Object ownership and explicit operations permissions shared by API/services."""
from django.shortcuts import get_object_or_404
from .errors import BusinessError


def can_operate_all(user, permission):
    return bool(user.is_authenticated and user.is_active and
                (user.is_superuser or (user.is_staff and user.has_perm(permission))))


def owned_or_permitted(query, user, permission, owner_lookup='stall__merchant__user'):
    if can_operate_all(user, permission):
        return query
    if not user.is_authenticated or not user.is_active:
        return query.none()
    return query.filter(**{owner_lookup: user})


def require_stall_permission(stall, user, permission):
    if (user.is_authenticated and user.is_active and stall.merchant.user_id == user.pk) or can_operate_all(user, permission):
        return
    raise BusinessError('没有执行此操作的权限。', 'forbidden', status=403)


def get_merchant_stall(stall_id, user, permission='market.view_stall', locked=False):
    from .models import Stall
    query = Stall.objects.select_for_update(no_key=True) if locked else Stall.objects.all()
    return get_object_or_404(owned_or_permitted(query, user, permission, 'merchant__user'), pk=stall_id)
