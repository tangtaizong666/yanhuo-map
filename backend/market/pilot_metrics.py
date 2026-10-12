"""Bounded pilot evidence from server records; browser events are kept separate."""
import math
from statistics import median

from django.db.models import F
from django.db.models.functions import TruncDate
from .models import BusinessSession, Feedback


def evidence(orders, stalls, start, now, mode):
    selected = stalls.filter(is_demo=mode == 'simulation')
    sessions = BusinessSession.objects.filter(stall__in=selected, opened_at__gte=start, opened_at__lte=now)
    active_days = sessions.annotate(day=TruncDate('opened_at')).values('stall__merchant_id', 'day').distinct().count()
    accepted = orders.filter(accepted_at__gte=start, accepted_at__lte=now).filter(accepted_at__gte=F('created_at'))
    # Keep the request bounded. State the sample size instead of implying a census.
    samples = sorted((row['accepted_at']-row['created_at']).total_seconds() for row in
        accepted.order_by('-accepted_at', '-pk').values('accepted_at', 'created_at')[:5000])
    reports = Feedback.objects.filter(stall__in=selected, created_at__gte=start, created_at__lte=now,
                                     kind__in=('not_found', 'wrong_location'))
    return {
        'source': 'server_records', 'mode': mode,
        'active_merchant_days': active_days,
        'completed_orders': orders.filter(status='completed', completed_at__gte=start, completed_at__lte=now).count(),
        'paying_customers': orders.filter(paid_at__gte=start, paid_at__lte=now,
            payment_status__in=('paid', 'refunding', 'refunded')).values('user_id').distinct().count(),
        'accept_sample_count': len(samples), 'accept_sample_limit': 5000,
        'accept_p50_seconds': round(median(samples), 1) if samples else None,
        'accept_p95_seconds': round(samples[max(0, math.ceil(len(samples)*.95)-1)], 1) if samples else None,
        'location_reports_received': reports.count(),
        'location_reports_confirmed': reports.filter(verification='confirmed').count(),
        'definitions': '开摊天数按服务端经营场次及商户去重；接单耗时取期间最近最多5000笔。位置反馈仅运营核实属实计入确认数，处理完成不等于属实。收款人数不扣除后续退款，不代表净成交人数。',
    }
