import json
from calendar import monthrange
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from django.contrib.auth.models import User
from django.db.models import Avg, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDay, TruncMonth
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from core.gamification import evaluate_gamification_for_user
from core.models import Event, Gamification, GamificationRewardCompletion, Notice, Survey, SurveyResponse
from customers.models import BalanceRequest
from inventory.models import Product
from inventory.models import ProductReview
from sales.models import Order, Sale


def _rating_stars(rating):
    safe_rating = max(0, min(int(rating or 0), 5))
    return '★' * safe_rating + '☆' * (5 - safe_rating)


def _truncate_money(value):
    return Decimal(value or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _distribute_totals_for_items(items, target_total):
    quant = Decimal('0.01')
    target = _truncate_money(target_total)
    chargeable_items = [item for item in items if not getattr(item, 'is_gift', False)]
    if not chargeable_items:
        return {getattr(item, 'id', None): Decimal('0.00') for item in items}

    buckets = []
    base_sum = Decimal('0.00')
    for item in chargeable_items:
        raw = (Decimal(item.quantity) * Decimal(item.unit_price)).quantize(Decimal('0.0001'))
        base = raw.quantize(quant, rounding=ROUND_DOWN)
        remainder = raw - base
        base_sum += base
        buckets.append({'item_id': item.id, 'base': base, 'remainder': remainder})

    diff = target - base_sum
    steps = int((diff / quant).to_integral_value())
    if steps > 0:
        buckets.sort(key=lambda row: (row['remainder'], row['item_id']), reverse=True)
        for idx in range(steps):
            buckets[idx % len(buckets)]['base'] += quant
    elif steps < 0:
        buckets.sort(key=lambda row: (row['remainder'], row['item_id']))
        for idx in range(abs(steps)):
            candidate = buckets[idx % len(buckets)]
            if candidate['base'] >= quant:
                candidate['base'] -= quant

    distributed = {row['item_id']: row['base'] for row in buckets}
    for item in items:
        distributed.setdefault(item.id, Decimal('0.00'))
    return distributed


def _approved_orders_with_money_date():
    # Monetary time for approved orders is approval time; use updated_at as legacy fallback.
    return Order.objects.filter(status=Order.Status.APPROVED).annotate(
        money_at=Coalesce('approved_at', 'updated_at')
    )


def _build_monthly_series(year, month):
    days_in_month = monthrange(year, month)[1]
    sales_totals_by_day = {
        entry['bucket'].day: float(entry['total'] or 0)
        for entry in (
            Sale.objects.filter(is_voided=False, created_at__year=year, created_at__month=month)
            .annotate(bucket=TruncDay('created_at'))
            .values('bucket')
            .annotate(total=Sum('total_amount'))
            .order_by('bucket')
        )
    }
    order_totals_by_day = {
        entry['bucket'].day: float(entry['total'] or 0)
        for entry in (
            _approved_orders_with_money_date()
            .filter(money_at__year=year, money_at__month=month)
            .annotate(bucket=TruncDay('money_at'))
            .values('bucket')
            .annotate(total=Sum('total_amount'))
            .order_by('bucket')
        )
    }
    labels = [str(day) for day in range(1, days_in_month + 1)]
    data = [
        sales_totals_by_day.get(day, 0) + order_totals_by_day.get(day, 0)
        for day in range(1, days_in_month + 1)
    ]
    return labels, data


def _build_yearly_series(year):
    sales_totals_by_month = {
        entry['bucket'].month: float(entry['total'] or 0)
        for entry in (
            Sale.objects.filter(is_voided=False, created_at__year=year)
            .annotate(bucket=TruncMonth('created_at'))
            .values('bucket')
            .annotate(total=Sum('total_amount'))
            .order_by('bucket')
        )
    }
    order_totals_by_month = {
        entry['bucket'].month: float(entry['total'] or 0)
        for entry in (
            _approved_orders_with_money_date()
            .filter(money_at__year=year)
            .annotate(bucket=TruncMonth('money_at'))
            .values('bucket')
            .annotate(total=Sum('total_amount'))
            .order_by('bucket')
        )
    }
    labels = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']
    data = [
        sales_totals_by_month.get(month, 0) + order_totals_by_month.get(month, 0)
        for month in range(1, 13)
    ]
    return labels, data


def _build_cash_projection_series(current_month_data, previous_month_data):
    days_count = len(current_month_data)
    previous_days = len(previous_month_data) or 1

    previous_aligned = [
        previous_month_data[index] if index < len(previous_month_data) else 0
        for index in range(days_count)
    ]

    current_cumulative = []
    current_running = 0
    for value in current_month_data:
        current_running += value
        current_cumulative.append(round(current_running, 2))

    previous_cumulative = []
    previous_running = 0
    for value in previous_aligned:
        previous_running += value
        previous_cumulative.append(round(previous_running, 2))

    previous_month_total = sum(previous_month_data or [0])
    projected_daily_avg = previous_month_total / previous_days
    forecast_cumulative = [round(projected_daily_avg * (index + 1), 2) for index in range(days_count)]

    return current_cumulative, previous_cumulative, forecast_cumulative


def build_dashboard_context(user):
    products_count = Product.objects.count()
    low_stock_count = Product.objects.filter(stock__lte=F('min_stock'), is_active=True).count()
    total_sales = Sale.objects.filter(is_voided=False).aggregate(total=Sum('total_amount'))['total'] or 0
    now = timezone.localtime()
    current_year = now.year
    current_month = now.month
    previous_month_date = (now.replace(day=1) - timedelta(days=1))
    previous_year = previous_month_date.year
    previous_month = previous_month_date.month

    current_month_labels, current_month_data = _build_monthly_series(current_year, current_month)
    previous_month_labels, previous_month_data = _build_monthly_series(previous_year, previous_month)
    yearly_labels, yearly_data = _build_yearly_series(current_year)
    current_cash_series, previous_cash_series, forecast_cash_series = _build_cash_projection_series(
        current_month_data,
        previous_month_data,
    )

    profile = getattr(user, 'store_profile', None)
    balance = profile.current_balance if profile else 0
    current_month_sales_total = Sale.objects.filter(
        is_voided=False,
        created_at__year=current_year,
        created_at__month=current_month,
    ).aggregate(total=Sum('total_amount'))['total'] or 0
    current_month_orders_total = _approved_orders_with_money_date().filter(
        money_at__year=current_year,
        money_at__month=current_month,
    ).aggregate(total=Sum('total_amount'))['total'] or 0

    pending_orders_count = Order.objects.filter(status=Order.Status.PENDING).count()
    pending_balance_requests_count = BalanceRequest.objects.filter(status=BalanceRequest.Status.PENDING).count()
    pending_reviews_count = ProductReview.objects.filter(is_approved=False).count()
    pending_actions_count = (
        pending_orders_count
        + pending_balance_requests_count
        + pending_reviews_count
    )

    active_since = timezone.now() - timedelta(hours=2)
    active_users_count = (
        User.objects.filter(tracked_sessions__last_activity__gte=active_since)
        .distinct()
        .count()
    )

    cash_today_sales_total = Sale.objects.filter(is_voided=False, created_at__date=now.date()).aggregate(total=Sum('total_amount'))['total'] or 0
    cash_today_orders_total = _approved_orders_with_money_date().filter(
        money_at__date=now.date(),
    ).aggregate(total=Sum('total_amount'))['total'] or 0
    cash_today_total = cash_today_sales_total + cash_today_orders_total

    return {
        'products_count': products_count,
        'low_stock_count': low_stock_count,
        'total_sales': total_sales,
        'current_month_sales_orders_total': current_month_sales_total + current_month_orders_total,
        'pending_orders_count': pending_orders_count,
        'pending_balance_requests_count': pending_balance_requests_count,
        'pending_reviews_count': pending_reviews_count,
        'pending_actions_count': pending_actions_count,
        'active_users_count': active_users_count,
        'cash_today_total': cash_today_total,
        'current_balance': balance,
        'sales_current_month_title': f'{now.strftime("%B").capitalize()} {current_year}',
        'sales_previous_month_title': f'{previous_month_date.strftime("%B").capitalize()} {previous_year}',
        'sales_year_title': f'{current_year}',
        'sales_current_month_labels_json': json.dumps(current_month_labels),
        'sales_current_month_data_json': json.dumps(current_month_data),
        'sales_previous_month_labels_json': json.dumps(previous_month_labels),
        'sales_previous_month_data_json': json.dumps(previous_month_data),
        'sales_year_labels_json': json.dumps(yearly_labels),
        'sales_year_data_json': json.dumps(yearly_data),
        'cash_projection_labels_json': json.dumps(current_month_labels),
        'cash_current_month_data_json': json.dumps(current_cash_series),
        'cash_previous_month_data_json': json.dumps(previous_cash_series),
        'cash_forecast_data_json': json.dumps(forecast_cash_series),
    }


def build_user_dashboard_context(user):
    profile = getattr(user, 'store_profile', None)
    now = timezone.localtime()

    timeline_events = []

    visible_notices = Notice.objects.filter(end_at__gte=now)

    approved_orders = (
        Order.objects.filter(
            created_by=user,
            status=Order.Status.APPROVED,
        )
        .prefetch_related('items__product')
        .only(
            'id',
            'total_amount',
            'approved_at',
            'created_at',
            'items__quantity',
            'items__unit_price',
            'items__is_gift',
            'items__product__name',
        )
    )
    for order in approved_orders:
        event_date = order.approved_at or order.created_at
        order_total = _truncate_money(order.total_amount)
        order_items = list(order.items.all())
        line_totals = _distribute_totals_for_items(order_items, order_total)
        products = [
            {
                'label': f'{item.quantity}x {item.product.name}',
                'total': _truncate_money(line_totals.get(item.id, Decimal('0.00'))),
                'is_gift': item.is_gift,
            }
            for item in order_items
            if item.product_id
        ]
        timeline_events.append(
            {
                'kind': 'purchase',
                'title': _('Order'),
                'description': _('Your order was approved. Total: € %(total)s') % {'total': order_total},
                'event_date': event_date,
                'event_url': reverse('user_order_detail', kwargs={'pk': order.id}),
                'products': products,
            }
        )

    direct_sales = Sale.objects.filter(
        Q(customer=user) | Q(customer__isnull=True, customer_name__iexact=user.username),
        is_voided=False,
    ).prefetch_related('items__product').only(
        'id',
        'total_amount',
        'created_at',
        'items__quantity',
        'items__unit_price',
        'items__is_gift',
        'items__product__name',
    )
    for sale in direct_sales:
        sale_total = _truncate_money(sale.total_amount)
        sale_items = list(sale.items.all())
        line_totals = _distribute_totals_for_items(sale_items, sale_total)
        products = [
            {
                'label': f'{item.quantity}x {item.product.name}',
                'total': _truncate_money(line_totals.get(item.id, Decimal('0.00'))),
                'is_gift': item.is_gift,
            }
            for item in sale_items
            if item.product_id
        ]
        timeline_events.append(
            {
                'kind': 'purchase',
                'title': _('Direct purchase #%(id)s') % {'id': sale.id},
                'description': _('Purchase registered by admin. Total: € %(total)s') % {'total': sale_total},
                'event_date': sale.created_at,
                'event_url': reverse('user_sale_detail', kwargs={'pk': sale.id}),
                'products': products,
            }
        )

    consumed_product_ids = set(
        Order.objects.filter(
            created_by=user,
            status=Order.Status.APPROVED,
        ).values_list('items__product_id', flat=True)
    )
    consumed_product_ids.update(
        Sale.objects.filter(
            Q(customer=user) | Q(customer__isnull=True, customer_name__iexact=user.username),
            is_voided=False,
        ).values_list('items__product_id', flat=True)
    )
    consumed_product_ids.discard(None)

    if consumed_product_ids:
        reviewed_product_ids = set(
            ProductReview.objects.filter(
                user=user,
                product_id__in=consumed_product_ids,
            ).values_list('product_id', flat=True)
        )

        unreviewed_products = Product.objects.filter(
            id__in=(consumed_product_ids - reviewed_product_ids),
            is_active=True,
            is_public_listing=True,
        ).select_related('category').order_by('name')

        for product in unreviewed_products:
            if product.category and not product.category.allow_user_ratings:
                continue
            timeline_events.append(
                {
                    'kind': 'pending_review',
                    'title': _('Review pending: %(product)s') % {'product': product.name},
                    'description': _('You already tried this product. Tap here to submit your review.'),
                    'event_date': now,
                    'event_url': reverse('user_product_review', kwargs={'product_id': product.id}),
                }
            )

    visible_events = Event.objects.filter(end_at__gte=now).prefetch_related('registrations', 'images')
    for event in visible_events:
        registrations_count = event.registrations.count()
        is_full = bool(event.capacity and registrations_count >= event.capacity)
        is_registered = any(registration.user_id == user.id for registration in event.registrations.all())
        first_image = event.images.first()
        if event.requires_registration:
            if is_full:
                registration_text = _('Capacity full')
            else:
                registration_text = _('Registration required')
        else:
            registration_text = _('Open event')

        timeline_events.append(
            {
                'kind': 'event',
                'title': event.name,
                'description': registration_text,
                'event_date': event.start_at,
                'event_url': reverse('user_event_detail', kwargs={'pk': event.pk}),
                'event_image_url': first_image.image.url if first_image and first_image.image else None,
                'is_registered': is_registered,
            }
        )

    active_surveys = Survey.objects.filter(is_active=True).prefetch_related('options')
    answered_survey_ids = set(
        SurveyResponse.objects.filter(user=user).values_list('survey_id', flat=True)
    )
    for survey in active_surveys:
        options_count = survey.options.filter(is_active=True).count()
        timeline_events.append(
            {
                'kind': 'survey',
                'title': survey.title,
                'description': _('%(count)s options · %(status)s')
                % {
                    'count': options_count,
                    'status': _('Answered') if survey.id in answered_survey_ids else _('Pending response'),
                },
                'event_date': survey.created_at,
                'event_url': reverse('user_survey_detail', kwargs={'pk': survey.pk}),
                'is_answered': survey.id in answered_survey_ids,
            }
        )

    highlighted_products = (
        Product.objects.filter(is_active=True, is_public_listing=True)
        .filter(Q(is_featured=True) | Q(is_new=True))
        .annotate(approved_avg_rating=Avg('reviews__rating', filter=Q(reviews__is_approved=True)))
        .order_by('-is_featured', '-is_new', '-created_at', 'name')[:8]
    )
    for product in highlighted_products:
        badges = []
        if product.is_featured:
            badges.append(_('Featured'))
        if product.is_new:
            badges.append(_('New'))

        description = _('%(labels)s · Price: € %(price)s') % {
            'labels': ' · '.join(badges),
            'price': _truncate_money(product.price),
        }
        if product.approved_avg_rating:
            description = _('%(base)s · Rating: %(rating).1f/5') % {
                'base': description,
                'rating': product.approved_avg_rating,
            }

        timeline_events.append(
            {
                'kind': 'product',
                'title': product.name,
                'description': description,
                'event_date': product.updated_at,
                'event_url': reverse('user_product_detail', kwargs={'product_id': product.pk}),
                'is_featured': product.is_featured,
                'is_new': product.is_new,
            }
        )

    active_gamifications = Gamification.objects.filter(start_at__lte=now, end_at__gte=now)
    rewarded_gamification_ids = set(
        GamificationRewardCompletion.objects.filter(user=user).values_list('gamification_id', flat=True)
    )
    for gamification in active_gamifications:
        status = evaluate_gamification_for_user(gamification, user)
        timeline_events.append(
            {
                'kind': 'gamification',
                'title': gamification.title,
                'description': _('%(current)s/%(target)s completed · Reward: %(reward)s')
                % {
                    'current': status['current_value'],
                    'target': status['target_value'],
                    'reward': gamification.reward,
                },
                'event_date': gamification.end_at,
                'event_url': reverse('user_gamification_detail', kwargs={'pk': gamification.pk}),
                'is_completed': status['achieved'],
                'is_reward_completed': gamification.pk in rewarded_gamification_ids,
            }
        )

    approved_reviews = ProductReview.objects.filter(is_approved=True).select_related('product', 'user').only(
        'rating',
        'message',
        'updated_at',
        'product_id',
        'product__name',
        'user__username',
    )
    for review in approved_reviews:
        timeline_events.append(
            {
                'kind': 'review',
                'title': _('Review for %(product)s') % {'product': review.product.name},
                'description': _('%(stars)s · %(message)s')
                % {
                    'stars': _rating_stars(review.rating),
                    'message': review.message,
                },
                'event_date': review.updated_at,
                'event_url': reverse('user_product_detail', kwargs={'product_id': review.product_id}),
            }
        )

    def _timeline_sort_key(item):
        if item.get('kind') == 'gamification':
            return (0, item['event_date'])
        if item.get('kind') == 'pending_review':
            return (1, item['event_date'])
        if item.get('kind') == 'event':
            return (2, item['event_date'])
        if item.get('kind') == 'survey':
            return (2, item['event_date'])
        return (3, -item['event_date'].timestamp())

    timeline_events.sort(key=_timeline_sort_key)

    priority_kinds = {'event', 'survey', 'gamification', 'pending_review'}
    priority_events = [item for item in timeline_events if item.get('kind') in priority_kinds]
    highlighted_product_events = [item for item in timeline_events if item.get('kind') == 'product']
    recent_activity_events = [
        item
        for item in timeline_events
        if item.get('kind') not in priority_kinds and item.get('kind') != 'product'
    ]

    return {
        'active_notices': visible_notices,
        'my_orders_total': Order.objects.filter(created_by=user).count(),
        'my_orders_pending': Order.objects.filter(created_by=user, status=Order.Status.PENDING).count(),
        'my_orders_approved': Order.objects.filter(created_by=user, status=Order.Status.APPROVED).count(),
        'current_balance': profile.current_balance if profile else 0,
        'timeline_events': timeline_events,
        'priority_events': priority_events,
        'highlighted_product_events': highlighted_product_events,
        'recent_activity_events': recent_activity_events,
    }
