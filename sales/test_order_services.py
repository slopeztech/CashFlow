from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from customers.models import BalanceLog, StoreUserProfile
from inventory.models import Product
from sales.models import Order
from sales.services import approve_order, create_order, delete_order, reject_order, update_order


class OrderServiceAdditionalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='order-user-extra', password='testpass123')
        self.admin = User.objects.create_user(username='order-admin-extra', password='testpass123', is_staff=True)
        self.product = Product.objects.create(
            name='Service product',
            sku='SRV-1',
            price='3.50',
            stock='20.00',
            is_active=True,
        )

    def test_reject_order_sets_status_and_reason(self):
        order = create_order(
            created_by=self.user,
            customer_name=self.user.username,
            items_data=[{'product': self.product, 'quantity': Decimal('2.00')}],
        )

        reject_order(order=order, approved_by=self.admin, reason='Out of schedule')

        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.REJECTED)
        self.assertEqual(order.rejection_reason, 'Out of schedule')
        self.assertEqual(order.approved_by, self.admin)

    def test_update_order_pending_recomputes_total(self):
        order = create_order(
            created_by=self.user,
            customer_name=self.user.username,
            items_data=[{'product': self.product, 'quantity': Decimal('1.00')}],
        )

        update_order(
            order=order,
            customer_name='renamed-customer',
            items_data=[{'product': self.product, 'quantity': Decimal('3.00')}],
        )

        order.refresh_from_db()
        self.assertEqual(order.customer_name, 'renamed-customer')
        self.assertEqual(order.total_amount, Decimal('10.50'))

    def test_update_order_rejects_non_pending_orders(self):
        order = create_order(
            created_by=self.user,
            customer_name=self.user.username,
            items_data=[{'product': self.product, 'quantity': Decimal('2.00')}],
        )
        StoreUserProfile.objects.create(user=self.user, current_balance='100.00')
        approve_order(order=order, approved_by=self.admin)

        with self.assertRaises(ValidationError):
            update_order(
                order=order,
                customer_name=self.user.username,
                items_data=[{'product': self.product, 'quantity': Decimal('1.00')}],
            )

    def test_delete_pending_order_does_not_create_balance_logs(self):
        order = create_order(
            created_by=self.user,
            customer_name=self.user.username,
            items_data=[{'product': self.product, 'quantity': Decimal('2.00')}],
        )

        delete_order(order=order, modified_by=self.admin)

        self.assertFalse(Order.objects.filter(id=order.id).exists())
        self.assertEqual(BalanceLog.objects.count(), 0)
