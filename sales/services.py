from decimal import Decimal, InvalidOperation, ROUND_DOWN

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from customers.models import BalanceLog, StoreUserProfile
from inventory.models import Product
from sales.models import Order, OrderItem, Sale, SaleItem


def _aggregate_quantities(items_data):
    quantities = {}
    for item in items_data:
        product = item['product']
        quantity = _parse_quantity(item['quantity'])
        if quantity <= 0:
            raise ValidationError('Quantity must be greater than zero.')
        quantities[product.id] = quantities.get(product.id, Decimal('0')) + quantity
    return quantities


def _parse_quantity(raw_quantity):
    try:
        quantity = Decimal(str(raw_quantity))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError('Quantity must be greater than zero.')
    return quantity


def _lock_products(product_ids):
    products = Product.objects.select_for_update().filter(id__in=product_ids)
    return {product.id: product for product in products}


def _compute_total(items_data, *, include_gifts=True):
    total = Decimal('0.00')
    for item in items_data:
        if not include_gifts and item.get('is_gift'):
            continue
        requested_amount = item.get('requested_amount')
        if requested_amount is not None:
            try:
                amount_value = Decimal(str(requested_amount))
            except (InvalidOperation, TypeError, ValueError):
                raise ValidationError('Invalid requested amount.')
            if amount_value < 0:
                raise ValidationError('Requested amount cannot be negative.')
            total += amount_value
            continue
        unit_price = Decimal(str(item['product'].price))
        total += _parse_quantity(item['quantity']) * unit_price
    return total


def _distribute_item_amounts(order_items, target_total):
    quant = Decimal('0.01')
    target = Decimal(target_total or 0).quantize(quant)
    if not order_items:
        return {}

    buckets = []
    base_sum = Decimal('0.00')
    for item in order_items:
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

    return {row['item_id']: row['base'] for row in buckets}


def _apply_balance_delta(*, user, changed_by, amount_delta, note):
    if user is None or amount_delta == 0:
        return

    profile, _created = StoreUserProfile.objects.select_for_update().get_or_create(user=user)
    balance_before = profile.current_balance
    profile.current_balance += amount_delta
    profile.save(update_fields=['current_balance', 'updated_at'])

    BalanceLog.objects.create(
        user=user,
        changed_by=changed_by,
        source=BalanceLog.Source.MANUAL_ADJUSTMENT,
        amount_delta=amount_delta,
        balance_before=balance_before,
        balance_after=profile.current_balance,
        note=note,
    )


@transaction.atomic
def create_sale(*, seller, customer=None, customer_name='', items_data):
    if not items_data:
        raise ValidationError('A sale must include at least one item.')

    quantities = _aggregate_quantities(items_data)
    products = _lock_products(quantities.keys())

    for product_id, quantity in quantities.items():
        product = products.get(product_id)
        if product is None:
            raise ValidationError('Product not found.')
        if product.stock < quantity:
            raise ValidationError(f'Insufficient stock for {product.name}.')

    for product_id, quantity in quantities.items():
        product = products[product_id]
        product.stock -= quantity
        product.save(update_fields=['stock', 'updated_at'])

    sale = Sale.objects.create(
        seller=seller,
        customer=customer,
        customer_name=customer_name or (customer.username if customer else ''),
    )

    sale_items = []
    for item in items_data:
        sale_items.append(
            SaleItem(
                sale=sale,
                product=item['product'],
                quantity=item['quantity'],
                unit_price=item['product'].price,
                is_gift=bool(item.get('is_gift')),
            )
        )

    SaleItem.objects.bulk_create(sale_items)

    sale.total_amount = _compute_total(items_data, include_gifts=False)
    sale.save(update_fields=['total_amount'])

    _apply_balance_delta(
        user=customer,
        changed_by=seller,
        amount_delta=-sale.total_amount,
        note=_('Sale #%(sale_id)s charged by admin') % {'sale_id': sale.id},
    )
    return sale


@transaction.atomic
def update_sale(*, sale, customer=None, customer_name='', items_data):
    if sale.is_voided:
        raise ValidationError(_('Voided sales cannot be edited.'))

    if not items_data:
        raise ValidationError('A sale must include at least one item.')

    old_items = list(sale.items.select_related('product'))
    old_quantities = {}
    for item in old_items:
        old_quantities[item.product_id] = old_quantities.get(item.product_id, 0) + item.quantity

    new_quantities = _aggregate_quantities(items_data)

    all_product_ids = set(old_quantities.keys()) | set(new_quantities.keys())
    products = _lock_products(all_product_ids)

    for product_id in all_product_ids:
        product = products.get(product_id)
        if product is None:
            raise ValidationError('Product not found.')

        restored_quantity = old_quantities.get(product_id, 0)
        required_quantity = new_quantities.get(product_id, 0)
        projected_stock = product.stock + restored_quantity - required_quantity

        if projected_stock < 0:
            raise ValidationError(f'Insufficient stock for {product.name}.')

    for product_id in all_product_ids:
        product = products[product_id]
        restored_quantity = old_quantities.get(product_id, 0)
        required_quantity = new_quantities.get(product_id, 0)
        product.stock = product.stock + restored_quantity - required_quantity
        product.save(update_fields=['stock', 'updated_at'])

    old_total = sale.total_amount
    old_customer = sale.customer

    sale.items.all().delete()

    sale_items = []
    for item in items_data:
        sale_items.append(
            SaleItem(
                sale=sale,
                product=item['product'],
                quantity=item['quantity'],
                unit_price=item['product'].price,
                is_gift=bool(item.get('is_gift')),
            )
        )

    SaleItem.objects.bulk_create(sale_items)

    sale.customer = customer
    sale.customer_name = customer_name or (customer.username if customer else '')
    new_total = _compute_total(items_data, include_gifts=False)
    sale.total_amount = new_total
    sale.save(update_fields=['customer', 'customer_name', 'total_amount'])

    if old_customer_id := getattr(old_customer, 'id', None):
        if customer and old_customer_id == customer.id:
            _apply_balance_delta(
                user=customer,
                changed_by=sale.seller,
                amount_delta=(old_total - new_total),
                note=_('Sale #%(sale_id)s edited by admin') % {'sale_id': sale.id},
            )
        else:
            _apply_balance_delta(
                user=old_customer,
                changed_by=sale.seller,
                amount_delta=old_total,
                note=_('Sale #%(sale_id)s customer changed (refund)') % {'sale_id': sale.id},
            )
            _apply_balance_delta(
                user=customer,
                changed_by=sale.seller,
                amount_delta=-new_total,
                note=_('Sale #%(sale_id)s customer changed (charge)') % {'sale_id': sale.id},
            )
    else:
        _apply_balance_delta(
            user=customer,
            changed_by=sale.seller,
            amount_delta=-new_total,
            note=_('Sale #%(sale_id)s customer assigned') % {'sale_id': sale.id},
        )

    return sale


@transaction.atomic
def delete_sale(*, sale, modified_by=None):
    if sale.is_voided:
        return sale

    sale_items = list(sale.items.select_related('product'))
    quantities = {}
    for item in sale_items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity

    products = _lock_products(quantities.keys())
    for product_id, quantity in quantities.items():
        product = products.get(product_id)
        if product is not None:
            product.stock += quantity
            product.save(update_fields=['stock', 'updated_at'])

    sale.is_voided = True
    sale.voided_by = modified_by
    sale.voided_at = timezone.now()
    sale.void_reason = _('Voided by admin')
    sale.save(update_fields=['is_voided', 'voided_by', 'voided_at', 'void_reason'])

    _apply_balance_delta(
        user=sale.customer,
        changed_by=modified_by,
        amount_delta=sale.total_amount,
        note=_('Sale #%(sale_id)s voided by admin (refund)') % {'sale_id': sale.id},
    )
    return sale


@transaction.atomic
def create_order(*, created_by, customer_name, items_data):
    if not items_data:
        raise ValidationError('An order must include at least one item.')

    for item in items_data:
        if _parse_quantity(item['quantity']) <= 0:
            raise ValidationError('Quantity must be greater than zero.')

    order = Order.objects.create(created_by=created_by, customer_name=customer_name)

    order_items = []
    for item in items_data:
        order_items.append(
            OrderItem(
                order=order,
                product=item['product'],
                quantity=item['quantity'],
                unit_price=item['product'].price,
                is_gift=bool(item.get('is_gift')),
            )
        )

    OrderItem.objects.bulk_create(order_items)
    order.total_amount = _compute_total(items_data)
    order.save(update_fields=['total_amount'])
    return order


@transaction.atomic
def update_order(*, order, customer_name, items_data):
    if order.status != Order.Status.PENDING:
        raise ValidationError('Only pending orders can be edited.')

    if not items_data:
        raise ValidationError('An order must include at least one item.')

    for item in items_data:
        if _parse_quantity(item['quantity']) <= 0:
            raise ValidationError('Quantity must be greater than zero.')

    order.items.all().delete()

    order_items = []
    for item in items_data:
        order_items.append(
            OrderItem(
                order=order,
                product=item['product'],
                quantity=item['quantity'],
                unit_price=item['product'].price,
            )
        )

    OrderItem.objects.bulk_create(order_items)
    order.customer_name = customer_name
    order.total_amount = _compute_total(items_data)
    order.save(update_fields=['customer_name', 'total_amount', 'updated_at'])
    return order


@transaction.atomic
def approve_order(*, order, approved_by, gift_item_ids=None):
    if order.status != Order.Status.PENDING:
        raise ValidationError('Only pending orders can be approved.')

    order_items = list(order.items.select_related('product'))
    if not order_items:
        raise ValidationError('Order has no items to approve.')

    if gift_item_ids is not None:
        parsed_gift_item_ids = set()
        for item_id in gift_item_ids:
            try:
                parsed_gift_item_ids.add(int(item_id))
            except (TypeError, ValueError):
                continue
        for order_item in order_items:
            should_be_gift = order_item.id in parsed_gift_item_ids
            if order_item.is_gift != should_be_gift:
                order_item.is_gift = should_be_gift
                order_item.save(update_fields=['is_gift'])

    quantities = {}
    for item in order_items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity

    products = _lock_products(quantities.keys())
    for product_id, quantity in quantities.items():
        product = products.get(product_id)
        if product is None:
            raise ValidationError('Product not found.')
        if product.stock < quantity:
            raise ValidationError(f'Insufficient stock for {product.name}.')

    for product_id, quantity in quantities.items():
        product = products[product_id]
        product.stock -= quantity
        product.save(update_fields=['stock', 'updated_at'])

    # Preserve the exact order amount saved at request time; do not recompute from rounded DB quantities.
    distributed_amounts = _distribute_item_amounts(order_items, order.total_amount)
    charge_total = sum(
        (
            distributed_amounts.get(item.id, Decimal('0.00'))
            for item in order_items
            if not item.is_gift
        ),
        Decimal('0.00'),
    )

    profile, _created = StoreUserProfile.objects.select_for_update().get_or_create(user=order.created_by)
    balance_before = profile.current_balance
    profile.current_balance -= charge_total
    profile.save(update_fields=['current_balance', 'updated_at'])
    BalanceLog.objects.create(
        user=order.created_by,
        changed_by=approved_by,
        source=BalanceLog.Source.ORDER_APPROVAL,
        amount_delta=-charge_total,
        balance_before=balance_before,
        balance_after=profile.current_balance,
        note=f'Order #{order.id} approved',
    )

    order.total_amount = charge_total
    order.status = Order.Status.APPROVED
    order.approved_by = approved_by
    order.approved_at = timezone.now()
    order.rejection_reason = ''
    order.save(update_fields=['total_amount', 'status', 'approved_by', 'approved_at', 'rejection_reason', 'updated_at'])
    return order


@transaction.atomic
def reject_order(*, order, approved_by, reason=''):
    if order.status != Order.Status.PENDING:
        raise ValidationError('Only pending orders can be rejected.')

    order.status = Order.Status.REJECTED
    order.approved_by = approved_by
    order.approved_at = timezone.now()
    order.rejection_reason = reason
    order.save(update_fields=['status', 'approved_by', 'approved_at', 'rejection_reason', 'updated_at'])
    return order


@transaction.atomic
def update_approved_order(*, order, items_data, modified_by):
    if order.status != Order.Status.APPROVED:
        raise ValidationError('Only approved orders can be edited here.')

    if not items_data:
        raise ValidationError('An order must include at least one item.')

    old_items = list(order.items.select_related('product'))
    old_quantities = {}
    for item in old_items:
        old_quantities[item.product_id] = old_quantities.get(item.product_id, 0) + item.quantity

    new_quantities = _aggregate_quantities(items_data)

    all_product_ids = set(old_quantities.keys()) | set(new_quantities.keys())
    products = _lock_products(all_product_ids)

    for product_id in all_product_ids:
        product = products.get(product_id)
        if product is None:
            raise ValidationError('Product not found.')

        restored_quantity = old_quantities.get(product_id, 0)
        required_quantity = new_quantities.get(product_id, 0)
        projected_stock = product.stock + restored_quantity - required_quantity

        if projected_stock < 0:
            raise ValidationError(f'Insufficient stock for {product.name}.')

    for product_id in all_product_ids:
        product = products[product_id]
        restored_quantity = old_quantities.get(product_id, 0)
        required_quantity = new_quantities.get(product_id, 0)
        product.stock = product.stock + restored_quantity - required_quantity
        product.save(update_fields=['stock', 'updated_at'])

    order.items.all().delete()

    order_items = []
    for item in items_data:
        order_items.append(
            OrderItem(
                order=order,
                product=item['product'],
                quantity=item['quantity'],
                unit_price=item['product'].price,
                is_gift=bool(item.get('is_gift')),
            )
        )
    OrderItem.objects.bulk_create(order_items)

    old_total = order.total_amount
    new_total = _compute_total(items_data, include_gifts=False)
    delta = new_total - old_total

    profile, _created = StoreUserProfile.objects.select_for_update().get_or_create(user=order.created_by)
    if delta != 0:
        balance_before = profile.current_balance
        profile.current_balance -= delta
        profile.save(update_fields=['current_balance', 'updated_at'])
        BalanceLog.objects.create(
            user=order.created_by,
            changed_by=modified_by,
            source=BalanceLog.Source.ORDER_APPROVAL,
            amount_delta=-delta,
            balance_before=balance_before,
            balance_after=profile.current_balance,
            note=f'Order #{order.id} edited by admin',
        )

    order.total_amount = new_total
    order.save(update_fields=['total_amount', 'updated_at'])
    return order


@transaction.atomic
def delete_order(*, order, modified_by):
    if order.status == Order.Status.CANCELED:
        return order

    if order.status != Order.Status.APPROVED:
        order.delete()
        return order

    if order.status == Order.Status.APPROVED:
        order_items = list(order.items.select_related('product'))
        quantities = {}
        for item in order_items:
            quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity

        products = _lock_products(quantities.keys())
        for product_id, quantity in quantities.items():
            product = products.get(product_id)
            if product is not None:
                product.stock += quantity
                product.save(update_fields=['stock', 'updated_at'])

        profile, _created = StoreUserProfile.objects.select_for_update().get_or_create(user=order.created_by)
        balance_before = profile.current_balance
        profile.current_balance += order.total_amount
        profile.save(update_fields=['current_balance', 'updated_at'])
        BalanceLog.objects.create(
            user=order.created_by,
            changed_by=modified_by,
            source=BalanceLog.Source.ORDER_APPROVAL,
            amount_delta=order.total_amount,
            balance_before=balance_before,
            balance_after=profile.current_balance,
            note=_('Order #%(order_id)s canceled by admin') % {'order_id': order.id},
        )

    order.status = Order.Status.CANCELED
    order.canceled_by = modified_by
    order.canceled_at = timezone.now()
    order.cancellation_reason = _('Canceled by admin')
    order.save(update_fields=['status', 'canceled_by', 'canceled_at', 'cancellation_reason', 'updated_at'])
    return order
