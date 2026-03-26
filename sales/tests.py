from django.test import TestCase
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from decimal import Decimal

from customers.models import BalanceLog, StoreUserProfile
from inventory.models import Product
from sales.models import SaleItem
from sales.services import approve_order, create_order, create_sale, delete_sale, update_sale


class SaleServiceTests(TestCase):
	def setUp(self):
		self.seller = User.objects.create_user(username='seller', password='testpass123')
		self.product_a = Product.objects.create(
			name='Coffee',
			sku='COF-1',
			price='2.50',
			stock=10,
			is_active=True,
		)
		self.product_b = Product.objects.create(
			name='Tea',
			sku='TEA-1',
			price='1.50',
			stock=8,
			is_active=True,
		)

	def test_create_sale_reduces_stock_and_sets_total(self):
		sale = create_sale(
			seller=self.seller,
			customer_name='John',
			items_data=[
				{'product': self.product_a, 'quantity': 2, 'unit_price': self.product_a.price},
				{'product': self.product_b, 'quantity': 3, 'unit_price': self.product_b.price},
			],
		)

		self.product_a.refresh_from_db()
		self.product_b.refresh_from_db()
		sale.refresh_from_db()

		self.assertEqual(self.product_a.stock, 8)
		self.assertEqual(self.product_b.stock, 5)
		self.assertEqual(str(sale.total_amount), '9.50')
		self.assertEqual(SaleItem.objects.filter(sale=sale).count(), 2)

	def test_create_sale_rejects_insufficient_stock(self):
		with self.assertRaises(ValidationError):
			create_sale(
				seller=self.seller,
				customer_name='John',
				items_data=[
					{'product': self.product_a, 'quantity': 999, 'unit_price': self.product_a.price},
				],
			)

		self.product_a.refresh_from_db()
		self.assertEqual(self.product_a.stock, 10)

	def test_create_sale_accepts_decimal_quantity(self):
		sale = create_sale(
			seller=self.seller,
			customer_name='John',
			items_data=[
				{'product': self.product_a, 'quantity': Decimal('0.50'), 'unit_price': self.product_a.price},
			],
		)

		self.product_a.refresh_from_db()
		sale.refresh_from_db()

		self.assertEqual(self.product_a.stock, Decimal('9.50'))
		self.assertEqual(sale.total_amount, Decimal('1.25'))

	def test_update_sale_rebalances_stock(self):
		sale = create_sale(
			seller=self.seller,
			customer_name='John',
			items_data=[
				{'product': self.product_a, 'quantity': 2, 'unit_price': self.product_a.price},
			],
		)

		update_sale(
			sale=sale,
			customer_name='Jane',
			items_data=[
				{'product': self.product_a, 'quantity': 1, 'unit_price': self.product_a.price},
				{'product': self.product_b, 'quantity': 2, 'unit_price': self.product_b.price},
			],
		)

		self.product_a.refresh_from_db()
		self.product_b.refresh_from_db()
		sale.refresh_from_db()

		self.assertEqual(self.product_a.stock, 9)
		self.assertEqual(self.product_b.stock, 6)
		self.assertEqual(sale.customer_name, 'Jane')
		self.assertEqual(str(sale.total_amount), '5.50')

	def test_delete_sale_restores_stock(self):
		sale = create_sale(
			seller=self.seller,
			customer_name='John',
			items_data=[
				{'product': self.product_a, 'quantity': 4, 'unit_price': self.product_a.price},
			],
		)

		delete_sale(sale=sale)

		self.product_a.refresh_from_db()
		self.assertEqual(self.product_a.stock, 10)


class OrderServiceTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='buyer', password='testpass123')
		self.admin = User.objects.create_user(username='admin', password='testpass123', is_staff=True)
		self.profile = StoreUserProfile.objects.create(user=self.user, current_balance='5.00')
		self.product = Product.objects.create(
			name='Rice',
			sku='RC-1',
			price='3.00',
			stock=10,
			is_active=True,
		)

	def test_approve_order_reduces_stock_and_balance(self):
		order = create_order(
			created_by=self.user,
			customer_name=self.user.username,
			items_data=[{'product': self.product, 'quantity': 2}],
		)

		approve_order(order=order, approved_by=self.admin)

		self.product.refresh_from_db()
		self.profile.refresh_from_db()
		order.refresh_from_db()

		self.assertEqual(self.product.stock, 8)
		self.assertEqual(str(order.total_amount), '6.00')
		self.assertEqual(str(self.profile.current_balance), '-1.00')
		self.assertEqual(order.status, order.Status.APPROVED)
		log = BalanceLog.objects.get(user=self.user, source=BalanceLog.Source.ORDER_APPROVAL)
		self.assertEqual(str(log.amount_delta), '-6.00')
		self.assertEqual(str(log.balance_before), '5.00')
		self.assertEqual(str(log.balance_after), '-1.00')
