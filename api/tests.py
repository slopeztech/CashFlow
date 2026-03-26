from django.test import TestCase
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from inventory.models import Product
from sales.services import create_sale


class SalesApiTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='apiuser', password='testpass123', is_staff=True)
		self.customer = User.objects.create_user(username='apicustomer', password='testpass123', is_staff=False)
		self.client = APIClient()
		self.client.force_authenticate(user=self.user)
		self.product_a = Product.objects.create(
			name='Milk',
			sku='MLK-1',
			price='2.00',
			stock=10,
			is_active=True,
		)
		self.product_b = Product.objects.create(
			name='Bread',
			sku='BRD-1',
			price='1.20',
			stock=6,
			is_active=True,
		)

	def test_create_sale_updates_stock(self):
		payload = {
			'customer': self.customer.id,
			'items': [
				{'product': self.product_a.id, 'quantity': 3, 'unit_price': '2.00'},
			],
		}

		response = self.client.post('/api/sales/', payload, format='json')
		self.assertEqual(response.status_code, 201)

		self.product_a.refresh_from_db()
		self.assertEqual(self.product_a.stock, 7)

	def test_update_sale_rebalances_stock(self):
		sale = create_sale(
			seller=self.user,
			customer=self.customer,
			customer_name='Client B',
			items_data=[
				{'product': self.product_a, 'quantity': 2, 'unit_price': self.product_a.price},
			],
		)

		payload = {
			'customer': self.customer.id,
			'items': [
				{'product': self.product_b.id, 'quantity': 4, 'unit_price': '1.20'},
			],
		}

		response = self.client.put(f'/api/sales/{sale.id}/', payload, format='json')
		self.assertEqual(response.status_code, 200)

		self.product_a.refresh_from_db()
		self.product_b.refresh_from_db()

		self.assertEqual(self.product_a.stock, 10)
		self.assertEqual(self.product_b.stock, 2)

	def test_delete_sale_restores_stock(self):
		sale = create_sale(
			seller=self.user,
			customer=self.customer,
			customer_name='Client D',
			items_data=[
				{'product': self.product_a, 'quantity': 5, 'unit_price': self.product_a.price},
			],
		)

		response = self.client.delete(f'/api/sales/{sale.id}/')
		self.assertEqual(response.status_code, 204)

		self.product_a.refresh_from_db()
		self.assertEqual(self.product_a.stock, 10)

	def test_create_sale_rejects_insufficient_stock(self):
		payload = {
			'customer': self.customer.id,
			'items': [
				{'product': self.product_b.id, 'quantity': 999, 'unit_price': '1.20'},
			],
		}

		response = self.client.post('/api/sales/', payload, format='json')
		self.assertEqual(response.status_code, 400)

		self.product_b.refresh_from_db()
		self.assertEqual(self.product_b.stock, 6)
