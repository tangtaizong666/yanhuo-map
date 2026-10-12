"""Displayed supply is independent of inventory reserved for online orders."""
from .admission import pickup_eligibility_reason


def display_only(stall):
    return bool(pickup_eligibility_reason(stall))


def product_availability(product):
    if not product.is_active: return 'unavailable'
    if display_only(product.stall) and product.display_availability:
        return product.display_availability
    if product.sale_paused: return 'paused'
    return 'available' if product.stock > 0 else 'sold_out'


def available_products():
    """Match the serializer in SQL so pagination excludes unavailable dishes."""
    from django.db.models import Q, Subquery
    from .admission import pickup_eligible_q
    from .models import Stall
    eligible_stalls = Stall.objects.filter(pickup_eligible_q()).values('pk')
    trades = Q(stall_id__in=Subquery(eligible_stalls))
    stock = Q(sale_paused=False, stock__gt=0)
    return Q(is_active=True) & ((trades & stock) | (~trades & (
        Q(display_availability='available') | (Q(display_availability='') & stock))))
