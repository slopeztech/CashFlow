from decimal import Decimal, InvalidOperation

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


def _compute_total(items_data):
    total = Decimal('0.00')
    for item in items_data:
        unit_price = Decimal(str(item['product'].price))
        total += _parse_quantity(item['quantity']) * unit_price
    return total


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
            )
        )

    SaleItem.objects.bulk_create(sale_items)

    sale.total_amount = _compute_total(items_data)
    sale.save(update_fields=['total_amount'])
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

    sale.items.all().delete()

    sale_items = []
    for item in items_data:
        sale_items.append(
            SaleItem(
                sale=sale,
                product=item['product'],
                quantity=item['quantity'],
                unit_price=item['product'].price,
            )
        )

    SaleItem.objects.bulk_create(sale_items)

    sale.customer = customer
    sale.customer_name = customer_name or (customer.username if customer else '')
    sale.total_amount = _compute_total(items_data)
    sale.save(update_fields=['customer', 'customer_name', 'total_amount'])
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
def approve_order(*, order, approved_by):
    if order.status != Order.Status.PENDING:
        raise ValidationError('Only pending orders can be approved.')

    order_items = list(order.items.select_related('product'))
    if not order_items:
        raise ValidationError('Order has no items to approve.')

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

    profile, _created = StoreUserProfile.objects.select_for_update().get_or_create(user=order.created_by)
    balance_before = profile.current_balance
    profile.current_balance -= order.total_amount
    profile.save(update_fields=['current_balance', 'updated_at'])
    BalanceLog.objects.create(
        user=order.created_by,
        changed_by=approved_by,
        source=BalanceLog.Source.ORDER_APPROVAL,
        amount_delta=-order.total_amount,
        balance_before=balance_before,
        balance_after=profile.current_balance,
        note=f'Order #{order.id} approved',
    )

    order.status = Order.Status.APPROVED
    order.approved_by = approved_by
    order.approved_at = timezone.now()
    order.rejection_reason = ''
    order.save(update_fields=['status', 'approved_by', 'approved_at', 'rejection_reason', 'updated_at'])
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
            )
        )
    OrderItem.objects.bulk_create(order_items)

    old_total = order.total_amount
    new_total = _compute_total(items_data)
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
