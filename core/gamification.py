from django.db.models import Q
from django.utils import timezone

from inventory.models import ProductReview
from sales.models import Order, Sale


def user_metric_value(user, gamification_type):
    if gamification_type == 'approved_reviews':
        return ProductReview.objects.filter(user=user, is_approved=True).count()

    if gamification_type == 'approved_orders':
        return Order.objects.filter(created_by=user, status=Order.Status.APPROVED).count()

    if gamification_type == 'distinct_products_tried':
        order_product_ids = set(
            Order.objects.filter(
                created_by=user,
                status=Order.Status.APPROVED,
            ).values_list('items__product_id', flat=True)
        )
        sale_product_ids = set(
            Sale.objects.filter(
                Q(customer=user) | Q(customer__isnull=True, customer_name__iexact=user.username)
            ).values_list('items__product_id', flat=True)
        )
        product_ids = {
            product_id
            for product_id in order_product_ids.union(sale_product_ids)
            if product_id is not None
        }
        return len(product_ids)

    return 0


def evaluate_gamification_for_user(gamification, user):
    current_value = user_metric_value(user, gamification.gamification_type)
    target_value = max(int(gamification.target_value or 0), 1)
    achieved = current_value >= target_value
    progress_percentage = min(100, int((current_value / target_value) * 100))

    return {
        'current_value': current_value,
        'target_value': target_value,
        'achieved': achieved,
        'progress_percentage': progress_percentage,
    }


def active_gamifications_queryset():
    now = timezone.localtime()
    from core.models import Gamification

    return Gamification.objects.filter(start_at__lte=now, end_at__gte=now).select_related('created_by')
