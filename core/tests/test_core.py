from datetime import timedelta
from decimal import Decimal

from django.contrib.sessions.models import Session
from django.test import RequestFactory, TestCase
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from core.models import (
	Event,
	EventComment,
	EventRegistration,
	EventRegistrationField,
	Notice,
	Survey,
	SurveyOption,
	SurveyResponse,
	UserSession,
)
from customers.models import BalanceLog, StoreUserProfile
from inventory.models import Category, Product, ProductSheetField, ProductSheetUrl, Tag
from inventory.models import ProductReview
from sales.models import Order, Sale
from sales.services import approve_order, create_order, create_sale
from core.webviews.sales_views import SaleCreateView


class ProductWebCrudTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='webuser', password='testpass123', is_staff=True)
		self.customer_user = User.objects.create_user(username='sheetviewer', password='testpass123', is_staff=False)
		self.category = Category.objects.create(name='Beverages')
		self.client.force_login(self.user)

	def test_create_update_delete_product(self):
		create_response = self.client.post(
			reverse('product_create'),
			{
				'name': 'Orange Juice',
				'sku': 'OJ-1',
				'category': str(self.category.id),
				'description': 'Fresh',
				'price': '3.20',
				'stock': 12,
				'unit_type': 'units',
				'is_public_listing': 'on',
				'is_active': 'on',
			},
			follow=True,
		)
		self.assertEqual(create_response.status_code, 200)

		product = Product.objects.get(sku='OJ-1')
		self.assertEqual(product.stock, 12)

		update_response = self.client.post(
			reverse('product_update', kwargs={'pk': product.pk}),
			{
				'name': 'Orange Juice Premium',
				'sku': 'OJ-1',
				'category': str(self.category.id),
				'description': 'Fresh premium',
				'price': '3.50',
				'stock': 9,
				'unit_type': 'units',
				'is_public_listing': 'on',
				'is_active': 'on',
			},
			follow=True,
		)
		self.assertEqual(update_response.status_code, 200)

		product.refresh_from_db()
		self.assertEqual(product.name, 'Orange Juice Premium')
		self.assertEqual(str(product.price), '3.50')
		self.assertEqual(product.stock, 9)

		delete_response = self.client.post(
			reverse('product_delete', kwargs={'pk': product.pk}),
			follow=True,
		)
		self.assertEqual(delete_response.status_code, 200)
		product.refresh_from_db()
		self.assertFalse(product.is_active)
		self.assertFalse(product.is_public_listing)

	def test_admin_can_crud_product_sheet_fields(self):
		product = Product.objects.create(
			name='Sparkling Water',
			sku='SW-1',
			category=self.category,
			price='1.20',
			stock=15,
			is_active=True,
		)

		create_response = self.client.post(
			reverse('product_sheet', kwargs={'pk': product.pk}),
			{'field_key': 'Origin', 'field_value': 'Spain'},
			follow=True,
		)
		self.assertEqual(create_response.status_code, 200)
		sheet_field = ProductSheetField.objects.get(product=product, field_key='Origin')
		self.assertEqual(sheet_field.field_value, 'Spain')

		update_response = self.client.post(
			reverse('product_sheet', kwargs={'pk': product.pk}),
			{'field_id': sheet_field.id, 'field_key': 'Origin', 'field_value': 'Portugal'},
			follow=True,
		)
		self.assertEqual(update_response.status_code, 200)
		sheet_field.refresh_from_db()
		self.assertEqual(sheet_field.field_value, 'Portugal')

		delete_response = self.client.post(
			reverse('product_sheet_delete', kwargs={'pk': product.pk, 'field_id': sheet_field.id}),
			follow=True,
		)
		self.assertEqual(delete_response.status_code, 200)
		self.assertFalse(ProductSheetField.objects.filter(pk=sheet_field.id).exists())

	def test_admin_can_add_and_delete_product_sheet_url(self):
		product = Product.objects.create(
			name='Rice',
			sku='RC-1',
			category=self.category,
			price='2.00',
			stock=30,
			is_active=True,
		)

		create_response = self.client.post(
			reverse('product_sheet_url_create', kwargs={'pk': product.pk}),
			{'url': 'https://example.com/rice-spec'},
			follow=True,
		)
		self.assertEqual(create_response.status_code, 200)
		sheet_url = ProductSheetUrl.objects.get(product=product)
		self.assertEqual(sheet_url.url, 'https://example.com/rice-spec')

		delete_response = self.client.post(
			reverse('product_sheet_url_delete', kwargs={'pk': product.pk, 'url_id': sheet_url.id}),
			follow=True,
		)
		self.assertEqual(delete_response.status_code, 200)
		self.assertFalse(ProductSheetUrl.objects.filter(pk=sheet_url.id).exists())

	def test_user_product_detail_shows_product_sheet_table(self):
		product = Product.objects.create(
			name='Olive Oil',
			sku='OO-1',
			category=self.category,
			price='4.50',
			stock=20,
			is_active=True,
			is_public_listing=True,
		)
		ProductSheetField.objects.create(product=product, field_key='Volume', field_value='500 ml')
		ProductSheetUrl.objects.create(product=product, url='https://example.com/olive-oil')

		self.client.force_login(self.customer_user)
		response = self.client.get(reverse('user_product_detail', kwargs={'product_id': product.id}))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Product sheet')
		self.assertContains(response, 'Volume')
		self.assertContains(response, '500 ml')
		self.assertContains(response, 'https://example.com/olive-oil')

	def test_admin_can_update_purchase_options_from_product_edit(self):
		product = Product.objects.create(
			name='Tea',
			sku='TE-1',
			category=self.category,
			price='1.10',
			stock=20,
			is_active=True,
		)

		response = self.client.post(
			reverse('product_update', kwargs={'pk': product.pk}),
			{
				'name': product.name,
				'sku': product.sku,
				'category': str(self.category.id),
				'description': '',
				'price': '1.10',
				'stock': 20,
				'min_stock': 0,
				'unit_type': Product.UnitType.UNITS,
				'measure_label': '',
				'purchase_options': Product.PurchaseOptions.AMOUNT_ONLY,
				'is_public_listing': 'on',
				'is_active': 'on',
			},
		)
		self.assertEqual(response.status_code, 302)

		product.refresh_from_db()
		self.assertEqual(product.purchase_options, Product.PurchaseOptions.AMOUNT_ONLY)

	def test_admin_can_adjust_product_stock_with_positive_and_negative_values(self):
		product = Product.objects.create(
			name='Pasta',
			sku='PA-1',
			category=self.category,
			price='1.30',
			stock=10,
			is_active=True,
		)

		response_add = self.client.post(
			reverse('product_stock_adjust', kwargs={'pk': product.pk}),
			{'stock_delta': '+20'},
		)
		self.assertEqual(response_add.status_code, 302)
		product.refresh_from_db()
		self.assertEqual(product.stock, 30)

		response_sub = self.client.post(
			reverse('product_stock_adjust', kwargs={'pk': product.pk}),
			{'stock_delta': '-5'},
		)
		self.assertEqual(response_sub.status_code, 302)
		product.refresh_from_db()
		self.assertEqual(product.stock, 25)

	def test_admin_cannot_adjust_product_stock_below_zero(self):
		product = Product.objects.create(
			name='Flour',
			sku='FL-1',
			category=self.category,
			price='0.90',
			stock=4,
			is_active=True,
		)

		response = self.client.post(
			reverse('product_stock_adjust', kwargs={'pk': product.pk}),
			{'stock_delta': '-30'},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)
		product.refresh_from_db()
		self.assertEqual(product.stock, 4)

	def test_admin_can_crud_tags(self):
		create_response = self.client.post(
			reverse('tag_list'),
			{'name': 'Vegano', 'description': 'Sin ingredientes animales'},
		)
		self.assertEqual(create_response.status_code, 302)

		tag = Tag.objects.get(name='Vegano')

		update_response = self.client.post(
			reverse('tag_update', kwargs={'pk': tag.pk}),
			{'name': 'Vegetariano', 'description': 'Apto para vegetarianos'},
		)
		self.assertEqual(update_response.status_code, 302)

		tag.refresh_from_db()
		self.assertEqual(tag.name, 'Vegetariano')

		delete_response = self.client.post(
			reverse('tag_delete', kwargs={'pk': tag.pk}),
		)
		self.assertEqual(delete_response.status_code, 302)
		self.assertFalse(Tag.objects.filter(pk=tag.pk).exists())

	def test_admin_can_assign_tags_to_product_and_cannot_delete_used_tag(self):
		tag = Tag.objects.create(name='Orgánico')
		product = Product.objects.create(
			name='Miel',
			sku='MI-1',
			category=self.category,
			price='5.00',
			stock=8,
			is_active=True,
		)

		update_response = self.client.post(
			reverse('product_update', kwargs={'pk': product.pk}),
			{
				'name': product.name,
				'sku': product.sku,
				'category': str(self.category.id),
				'description': '',
				'price': '5.00',
				'stock': 8,
				'min_stock': 0,
				'unit_type': Product.UnitType.UNITS,
				'measure_label': '',
				'purchase_options': Product.PurchaseOptions.BOTH,
				'tags': [str(tag.id)],
				'is_public_listing': 'on',
				'is_active': 'on',
			},
		)
		self.assertEqual(update_response.status_code, 302)

		product.refresh_from_db()
		self.assertEqual(list(product.tags.values_list('id', flat=True)), [tag.id])

		delete_response = self.client.post(
			reverse('tag_delete', kwargs={'pk': tag.pk}),
		)
		self.assertEqual(delete_response.status_code, 302)
		self.assertTrue(Tag.objects.filter(pk=tag.pk).exists())

	def test_admin_can_update_product_tags_with_dedicated_endpoint(self):
		tag_a = Tag.objects.create(name='Sin gluten')
		tag_b = Tag.objects.create(name='Premium')
		product = Product.objects.create(
			name='Pan de arroz',
			sku='PA-AR-1',
			category=self.category,
			price='3.90',
			stock=6,
			is_active=True,
		)

		response = self.client.post(
			reverse('product_tags_update', kwargs={'pk': product.pk}),
			{'tags': [str(tag_a.id), str(tag_b.id)]},
		)
		self.assertEqual(response.status_code, 302)

		product.refresh_from_db()
		self.assertEqual(
			set(product.tags.values_list('id', flat=True)),
			{tag_a.id, tag_b.id},
		)


class SaleWebCrudTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='sellerweb', password='testpass123', is_staff=True)
		self.customer = User.objects.create_user(username='customerweb', password='testpass123', is_staff=False)
		self.category = Category.objects.create(name='Snacks')
		self.client.force_login(self.user)
		self.product_a = Product.objects.create(
			name='Cookies',
			sku='CK-1',
			category=self.category,
			price='2.30',
			stock=10,
			is_active=True,
		)
		self.product_b = Product.objects.create(
			name='Soda',
			sku='SD-1',
			category=self.category,
			price='1.80',
			stock=7,
			is_active=True,
		)

	def test_create_sale_from_web_reduces_stock(self):
		StoreUserProfile.objects.create(user=self.customer, current_balance='20.00')

		response = self.client.post(
			reverse('sale_create'),
			{
				'customer': str(self.customer.id),
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '0',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-product': str(self.product_a.id),
				'items-0-quantity': '4',
			},
		)
		self.assertEqual(response.status_code, 302)

		self.product_a.refresh_from_db()
		profile = StoreUserProfile.objects.get(user=self.customer)
		self.assertEqual(self.product_a.stock, 6)
		self.assertEqual(str(profile.current_balance), '10.80')

	def test_update_sale_from_web_rebalances_stock(self):
		sale = create_sale(
			seller=self.user,
			customer_name='Customer Two',
			items_data=[
				{'product': self.product_a, 'quantity': 2, 'unit_price': self.product_a.price},
			],
		)
		item = sale.items.first()

		response = self.client.post(
			reverse('sale_update', kwargs={'pk': sale.pk}),
			{
				'customer': str(self.customer.id),
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '1',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-id': str(item.id),
				'items-0-product': str(self.product_b.id),
				'items-0-quantity': '3',
			},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)

		self.product_a.refresh_from_db()
		self.product_b.refresh_from_db()
		sale.refresh_from_db()

		self.assertEqual(self.product_a.stock, 10)
		self.assertEqual(self.product_b.stock, 4)
		self.assertEqual(sale.customer_id, self.customer.id)

	def test_delete_sale_from_web_restores_stock(self):
		sale = create_sale(
			seller=self.user,
			customer=self.customer,
			customer_name='Customer Three',
			items_data=[
				{'product': self.product_a, 'quantity': 5, 'unit_price': self.product_a.price},
			],
		)

		response = self.client.post(
			reverse('sale_delete', kwargs={'pk': sale.pk}),
			follow=True,
		)
		self.assertEqual(response.status_code, 200)

		self.product_a.refresh_from_db()
		sale.refresh_from_db()
		self.assertEqual(self.product_a.stock, 10)
		self.assertTrue(sale.is_voided)

	def test_create_sale_from_web_rejects_insufficient_stock(self):
		response = self.client.post(
			reverse('sale_create'),
			{
				'customer': str(self.customer.id),
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '0',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-product': str(self.product_b.id),
				'items-0-quantity': '100',
			},
		)
		self.assertEqual(response.status_code, 200)

		self.product_b.refresh_from_db()
		self.assertEqual(self.product_b.stock, 7)

	def test_create_sale_from_web_accepts_decimal_quantity(self):
		response = self.client.post(
			reverse('sale_create'),
			{
				'customer': str(self.customer.id),
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '0',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-product': str(self.product_a.id),
				'items-0-quantity': '0.50',
			},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)

		sale = Sale.objects.latest('id')
		self.assertEqual(sale.total_amount, Decimal('1.15'))
		self.product_a.refresh_from_db()
		self.assertEqual(self.product_a.stock, Decimal('9.50'))

	def test_create_sale_from_web_ignores_extra_blank_row(self):
		StoreUserProfile.objects.create(user=self.customer, current_balance='20.00')

		response = self.client.post(
			reverse('sale_create'),
			{
				'customer': str(self.customer.id),
				'items-TOTAL_FORMS': '2',
				'items-INITIAL_FORMS': '0',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-product': str(self.product_a.id),
				'items-0-quantity': '2',
				'items-1-product': '',
				'items-1-quantity': '',
			},
		)
		self.assertEqual(response.status_code, 302)

		sale = Sale.objects.latest('id')
		self.assertEqual(sale.items.count(), 1)
		self.assertEqual(str(sale.total_amount), '4.60')

	def test_sale_create_get_prefills_customer_from_query(self):
		request = RequestFactory().get(reverse('sale_create'), {'customer': str(self.customer.id)})
		form = SaleCreateView()._build_sale_form(request)
		self.assertEqual(form.initial.get('customer'), self.customer.id)


class UserOrderFlowTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='normaluser', password='testpass123', is_staff=False)
		self.client.force_login(self.user)
		self.category = Category.objects.create(name='Bakery')
		self.product = Product.objects.create(
			name='Bread',
			sku='BR-100',
			category=self.category,
			price='1.50',
			stock=50,
			is_active=True,
		)

	def test_user_can_create_pending_order(self):
		response = self.client.post(
			reverse('user_order_create'),
			{
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '0',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-product': str(self.product.id),
				'items-0-quantity': '3',
			},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)
		order = Order.objects.get(created_by=self.user)
		self.assertEqual(order.status, Order.Status.PENDING)

	def test_user_can_open_pending_order_edit_form(self):
		order_response = self.client.post(
			reverse('user_order_create'),
			{
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '0',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-product': str(self.product.id),
				'items-0-quantity': '1',
			},
		)
		self.assertEqual(order_response.status_code, 302)
		order = Order.objects.get(created_by=self.user)

		response = self.client.get(reverse('user_order_update', kwargs={'pk': order.pk}))
		self.assertEqual(response.status_code, 200)

	def test_user_can_update_pending_order_and_is_redirected(self):
		create_response = self.client.post(
			reverse('user_order_create'),
			{
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '0',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-product': str(self.product.id),
				'items-0-quantity': '1',
			},
		)
		self.assertEqual(create_response.status_code, 302)
		order = Order.objects.get(created_by=self.user)
		item = order.items.first()

		response = self.client.post(
			reverse('user_order_update', kwargs={'pk': order.pk}),
			{
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '1',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-id': str(item.id),
				'items-0-order': str(order.id),
				'items-0-product': str(self.product.id),
				'items-0-quantity': '2',
			},
		)

		self.assertEqual(response.status_code, 302)
		self.assertEqual(response.url, reverse('user_order_detail', kwargs={'pk': order.pk}))

		order.refresh_from_db()
		self.assertEqual(order.status, Order.Status.PENDING)
		self.assertEqual(order.items.count(), 1)
		self.assertEqual(order.items.first().quantity, 2)

	def test_repeat_button_not_shown_for_pending_order(self):
		create_response = self.client.post(
			reverse('user_order_create'),
			{
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '0',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-product': str(self.product.id),
				'items-0-quantity': '1',
			},
		)
		self.assertEqual(create_response.status_code, 302)
		order = Order.objects.get(created_by=self.user)

		response = self.client.get(reverse('user_order_detail', kwargs={'pk': order.pk}))
		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'Repeat order')

	def test_user_cannot_repeat_pending_order(self):
		create_response = self.client.post(
			reverse('user_order_create'),
			{
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '0',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-product': str(self.product.id),
				'items-0-quantity': '1',
			},
		)
		self.assertEqual(create_response.status_code, 302)
		order = Order.objects.get(created_by=self.user)

		response = self.client.post(reverse('user_order_repeat', kwargs={'pk': order.pk}))
		self.assertEqual(response.status_code, 302)

	def test_user_can_create_pending_order_with_decimal_quantity(self):
		response = self.client.post(
			reverse('user_order_create'),
			{
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '0',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-product': str(self.product.id),
				'items-0-quantity': '0.50',
			},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)

		order = Order.objects.get(created_by=self.user)
		self.assertEqual(order.status, Order.Status.PENDING)
		self.assertEqual(order.items.first().quantity, Decimal('0.50'))
		self.assertEqual(order.total_amount, Decimal('0.75'))

	def test_recent_movements_limit_applies_to_orders_and_balance(self):
		profile, _ = StoreUserProfile.objects.get_or_create(user=self.user)
		profile.show_all_recent_movements = False
		profile.recent_movements_limit = 1
		profile.save(update_fields=['show_all_recent_movements', 'recent_movements_limit', 'updated_at'])

		for idx in range(2):
			create_order(
				created_by=self.user,
				customer_name=self.user.username,
				items_data=[{'product': self.product, 'quantity': 1}],
			)

		orders_response = self.client.get(reverse('user_orders'))
		self.assertEqual(orders_response.status_code, 200)
		self.assertEqual(len(orders_response.context['order_events']), 1)

		BalanceLog.objects.create(
			user=self.user,
			changed_by=None,
			source=BalanceLog.Source.MANUAL_ADJUSTMENT,
			amount_delta='1.00',
			balance_before='0.00',
			balance_after='1.00',
			note='test 1',
		)
		BalanceLog.objects.create(
			user=self.user,
			changed_by=None,
			source=BalanceLog.Source.MANUAL_ADJUSTMENT,
			amount_delta='2.00',
			balance_before='1.00',
			balance_after='3.00',
			note='test 2',
		)

		balance_response = self.client.get(reverse('user_balance_requests'))
		self.assertEqual(balance_response.status_code, 200)
		self.assertEqual(len(balance_response.context['balance_entries']), 1)

	def test_user_can_add_to_cart_by_amount(self):
		response = self.client.post(
			reverse('user_cart_add'),
			{
				'product_id': str(self.product.id),
				'purchase_mode': 'amount',
				'amount': '3.10',
			},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)
		session_cart = self.client.session.get('user_cart', {})
		self.assertEqual(session_cart.get(str(self.product.id)), '2.0667')

	def test_user_can_add_to_cart_with_decimal_quantity(self):
		response = self.client.post(
			reverse('user_cart_add'),
			{
				'product_id': str(self.product.id),
				'purchase_mode': 'units',
				'quantity': '0.50',
			},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)
		session_cart = self.client.session.get('user_cart', {})
		self.assertEqual(session_cart.get(str(self.product.id)), '0.5')

		cart_response = self.client.get(reverse('user_cart_detail'))
		self.assertEqual(cart_response.status_code, 200)
		self.assertContains(cart_response, 'value="0.5"')

	def test_user_can_add_to_cart_by_low_amount_when_fractional_units_apply(self):
		response = self.client.post(
			reverse('user_cart_add'),
			{
				'product_id': str(self.product.id),
				'purchase_mode': 'amount',
				'amount': '0.01',
			},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)
		session_cart = self.client.session.get('user_cart', {})
		self.assertEqual(session_cart.get(str(self.product.id)), '0.0067')

	def test_cart_subtotal_is_truncated_to_two_decimals(self):
		response = self.client.post(
			reverse('user_cart_add'),
			{
				'product_id': str(self.product.id),
				'purchase_mode': 'amount',
				'amount': '3.10',
			},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)

		cart_response = self.client.get(reverse('user_cart_detail'))
		self.assertEqual(cart_response.status_code, 200)
		subtotal = cart_response.context['cart_items'][0]['subtotal']
		self.assertEqual(subtotal, Decimal('3.10'))

	def test_user_cannot_add_amount_when_product_is_units_only(self):
		self.product.purchase_options = Product.PurchaseOptions.UNITS_ONLY
		self.product.save(update_fields=['purchase_options', 'updated_at'])

		response = self.client.post(
			reverse('user_cart_add'),
			{
				'product_id': str(self.product.id),
				'purchase_mode': 'amount',
				'amount': '5.00',
			},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)
		session_cart = self.client.session.get('user_cart', {})
		self.assertNotIn(str(self.product.id), session_cart)

	def test_user_cannot_add_units_when_product_is_amount_only(self):
		self.product.purchase_options = Product.PurchaseOptions.AMOUNT_ONLY
		self.product.save(update_fields=['purchase_options', 'updated_at'])

		response = self.client.post(
			reverse('user_cart_add'),
			{
				'product_id': str(self.product.id),
				'purchase_mode': 'units',
				'quantity': '2',
			},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)
		session_cart = self.client.session.get('user_cart', {})
		self.assertNotIn(str(self.product.id), session_cart)


class AdminBalanceLogWebTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username='adminbalance', password='testpass123', is_staff=True)
		self.user = User.objects.create_user(username='userbalance', password='testpass123', is_staff=False)
		StoreUserProfile.objects.create(user=self.user, current_balance='10.00')
		self.client.force_login(self.admin)

	def test_manual_balance_adjustment_creates_log(self):
		response = self.client.post(
			reverse('admin_user_balance_adjust', kwargs={'user_id': self.user.id}),
			{'user_id': self.user.id, 'amount': '-3.50'},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)
		profile = StoreUserProfile.objects.get(user=self.user)
		self.assertEqual(str(profile.current_balance), '6.50')

		log = BalanceLog.objects.get(user=self.user, source=BalanceLog.Source.MANUAL_ADJUSTMENT)
		self.assertEqual(str(log.amount_delta), '-3.50')
		self.assertEqual(str(log.balance_before), '10.00')
		self.assertEqual(str(log.balance_after), '6.50')


class NoticeWebTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username='admin_notice', password='testpass123', is_staff=True)
		self.user = User.objects.create_user(username='user_notice', password='testpass123', is_staff=False)

	def test_admin_can_create_notice(self):
		self.client.force_login(self.admin)
		now = timezone.localtime()
		response = self.client.post(
			reverse('admin_notice_create'),
			{
				'title': 'Planned maintenance',
				'description': 'System maintenance window.',
				'notice_type': 'warning',
				'start_at': now.strftime('%Y-%m-%dT%H:%M'),
				'end_at': (now + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
			},
		)

		self.assertEqual(response.status_code, 302)
		notice = Notice.objects.get(title='Planned maintenance')
		self.assertEqual(notice.created_by, self.admin)
		self.assertEqual(notice.notice_type, 'warning')

	def test_user_dashboard_shows_only_active_notices(self):
		now = timezone.localtime()
		Notice.objects.create(
			title='Active notice',
			description='Active message',
			notice_type='info',
			start_at=now - timedelta(hours=1),
			end_at=now + timedelta(hours=2),
			created_by=self.admin,
		)
		Notice.objects.create(
			title='Future notice',
			description='Future message',
			notice_type='warning',
			start_at=now + timedelta(hours=4),
			end_at=now + timedelta(days=2),
			created_by=self.admin,
		)
		Notice.objects.create(
			title='Expired notice',
			description='Expired message',
			notice_type='danger',
			start_at=now - timedelta(days=2),
			end_at=now - timedelta(days=1),
			created_by=self.admin,
		)

		self.client.force_login(self.user)
		response = self.client.get(reverse('user_dashboard'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Active notice')
		self.assertContains(response, 'Future notice')
		self.assertNotContains(response, 'Expired notice')

	def test_admin_notices_list_shows_published_in_progress_and_completed_statuses(self):
		now = timezone.localtime()
		Notice.objects.create(
			title='Future schedule',
			description='Future',
			notice_type='info',
			start_at=now + timedelta(hours=2),
			end_at=now + timedelta(days=1),
			created_by=self.admin,
		)
		Notice.objects.create(
			title='Current notice',
			description='Current',
			notice_type='success',
			start_at=now - timedelta(hours=1),
			end_at=now + timedelta(hours=1),
			created_by=self.admin,
		)
		Notice.objects.create(
			title='Past notice',
			description='Past',
			notice_type='danger',
			start_at=now - timedelta(days=2),
			end_at=now - timedelta(days=1),
			created_by=self.admin,
		)

		self.client.force_login(self.admin)
		response = self.client.get(reverse('admin_notices'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Published')
		self.assertContains(response, 'In progress')
		self.assertContains(response, 'Completed')


class AdminApprovedOrderManagementTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username='admin_orders', password='testpass123', is_staff=True)
		self.user = User.objects.create_user(username='buyer_orders', password='testpass123', is_staff=False)
		StoreUserProfile.objects.create(user=self.user, current_balance='100.00')
		self.category = Category.objects.create(name='Fruits')
		self.product_a = Product.objects.create(
			name='Apple',
			sku='APL-1',
			category=self.category,
			price='2.00',
			stock=20,
			is_active=True,
			is_public_listing=True,
		)
		self.product_b = Product.objects.create(
			name='Banana',
			sku='BAN-1',
			category=self.category,
			price='1.50',
			stock=30,
			is_active=True,
			is_public_listing=True,
		)
		self.client.force_login(self.admin)

	def _create_approved_order(self):
		order = create_order(
			created_by=self.user,
			customer_name=self.user.username,
			items_data=[{'product': self.product_a, 'quantity': 4}],
		)
		approve_order(order=order, approved_by=self.admin)
		return order

	def test_admin_can_update_approved_order_and_rebalance_stock_and_balance(self):
		order = self._create_approved_order()
		item = order.items.first()

		response = self.client.post(
			reverse('admin_order_update', kwargs={'pk': order.pk}),
			{
				'items-TOTAL_FORMS': '1',
				'items-INITIAL_FORMS': '1',
				'items-MIN_NUM_FORMS': '1',
				'items-MAX_NUM_FORMS': '1000',
				'items-0-id': str(item.id),
				'items-0-order': str(order.id),
				'items-0-product': str(self.product_b.id),
				'items-0-quantity': '2',
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		order.refresh_from_db()
		self.product_a.refresh_from_db()
		self.product_b.refresh_from_db()
		profile = StoreUserProfile.objects.get(user=self.user)

		self.assertEqual(self.product_a.stock, 20)
		self.assertEqual(self.product_b.stock, 28)
		self.assertEqual(str(order.total_amount), '3.00')
		self.assertEqual(str(profile.current_balance), '97.00')

	def test_admin_can_delete_approved_order_and_restore_stock_and_balance(self):
		order = self._create_approved_order()

		response = self.client.post(
			reverse('admin_order_delete', kwargs={'pk': order.pk}),
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		order.refresh_from_db()
		self.assertEqual(order.status, Order.Status.CANCELED)
		self.product_a.refresh_from_db()
		profile = StoreUserProfile.objects.get(user=self.user)

		self.assertEqual(self.product_a.stock, 20)
		self.assertEqual(str(profile.current_balance), '100.00')


class ProductInfoViewTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username='admin_product_info', password='testpass123', is_staff=True)
		self.user = User.objects.create_user(username='buyer_product_info', password='testpass123', is_staff=False)
		self.category = Category.objects.create(name='Dairy')
		self.product = Product.objects.create(
			name='Yogurt',
			sku='YG-1',
			category=self.category,
			price='2.50',
			stock=40,
			is_active=True,
			is_public_listing=True,
		)
		self.client.force_login(self.admin)

	def test_product_info_view_displays_metrics_and_reviews(self):
		create_sale(
			seller=self.admin,
			customer=self.user,
			items_data=[{'product': self.product, 'quantity': 3}],
		)

		order = create_order(
			created_by=self.user,
			customer_name=self.user.username,
			items_data=[{'product': self.product, 'quantity': 2}],
		)
		approve_order(order=order, approved_by=self.admin)

		ProductReview.objects.create(
			product=self.product,
			user=self.user,
			rating=4,
			message='Very good',
			is_approved=True,
		)

		response = self.client.get(reverse('product_info', kwargs={'pk': self.product.pk}))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Yogurt')
		self.assertContains(response, '5')
		self.assertContains(response, 'Very good')


class UserEventFlowTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username='admin_event', password='testpass123', is_staff=True)
		self.user = User.objects.create_user(username='event_user', password='testpass123', is_staff=False)
		self.other_user = User.objects.create_user(username='event_user_2', password='testpass123', is_staff=False)
		StoreUserProfile.objects.create(user=self.user, current_balance='20.00')

	def test_event_is_visible_on_user_dashboard_timeline(self):
		event = Event.objects.create(
			name='Product tasting',
			description='Try new products.',
			start_at=timezone.localtime() - timedelta(hours=1),
			end_at=timezone.localtime() + timedelta(days=1),
			requires_registration=False,
			created_by=self.admin,
		)
		self.client.force_login(self.user)
		response = self.client.get(reverse('user_dashboard'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Product tasting')
		self.assertContains(response, reverse('user_event_detail', kwargs={'pk': event.pk}))

	def test_event_registration_respects_capacity(self):
		event = Event.objects.create(
			name='Limited workshop',
			description='Only one seat.',
			start_at=timezone.localtime() + timedelta(hours=3),
			end_at=timezone.localtime() + timedelta(days=1),
			requires_registration=True,
			capacity=1,
			created_by=self.admin,
		)

		self.client.force_login(self.user)
		response = self.client.post(reverse('user_event_register', kwargs={'pk': event.pk}), follow=True)
		self.assertEqual(response.status_code, 200)
		self.assertTrue(EventRegistration.objects.filter(event=event, user=self.user).exists())

		self.client.force_login(self.other_user)
		response = self.client.post(reverse('user_event_register', kwargs={'pk': event.pk}), follow=True)
		self.assertEqual(response.status_code, 200)
		self.assertFalse(EventRegistration.objects.filter(event=event, user=self.other_user).exists())

	def test_registration_with_dynamic_form_fields_saves_answers(self):
		event = Event.objects.create(
			name='BBQ form event',
			description='Custom form',
			start_at=timezone.localtime() + timedelta(hours=2),
			end_at=timezone.localtime() + timedelta(days=1),
			requires_registration=True,
			created_by=self.admin,
		)
		text_field = EventRegistrationField.objects.create(
			event=event,
			label='Alergias',
			field_type=EventRegistrationField.FieldType.SHORT_TEXT,
			is_required=True,
			sort_order=1,
		)
		radio_field = EventRegistrationField.objects.create(
			event=event,
			label='Nivel de picante',
			field_type=EventRegistrationField.FieldType.RADIO,
			options_text='Suave\nMedio\nPicante',
			is_required=True,
			sort_order=2,
		)

		self.client.force_login(self.user)
		response = self.client.post(
			reverse('user_event_register', kwargs={'pk': event.pk}),
			{
				f'event_field_{text_field.id}': 'Sin gluten',
				f'event_field_{radio_field.id}': 'Medio',
			},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		registration = EventRegistration.objects.get(event=event, user=self.user)
		self.assertEqual(registration.answers[str(text_field.id)]['value'], 'Sin gluten')
		self.assertEqual(registration.answers[str(radio_field.id)]['value'], 'Medio')

	def test_registration_with_missing_required_dynamic_field_is_rejected(self):
		event = Event.objects.create(
			name='Form required event',
			description='Missing field',
			start_at=timezone.localtime() + timedelta(hours=2),
			end_at=timezone.localtime() + timedelta(days=1),
			requires_registration=True,
			created_by=self.admin,
		)
		_required_field = EventRegistrationField.objects.create(
			event=event,
			label='Teléfono de contacto',
			field_type=EventRegistrationField.FieldType.SHORT_TEXT,
			is_required=True,
		)

		self.client.force_login(self.user)
		response = self.client.post(
			reverse('user_event_register', kwargs={'pk': event.pk}),
			{},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		self.assertFalse(EventRegistration.objects.filter(event=event, user=self.user).exists())

	def test_user_can_unregister_from_event(self):
		event = Event.objects.create(
			name='Workshop',
			description='Hands-on.',
			start_at=timezone.localtime() + timedelta(hours=1),
			end_at=timezone.localtime() + timedelta(days=1),
			requires_registration=True,
			capacity=5,
			created_by=self.admin,
		)

		EventRegistration.objects.create(event=event, user=self.user)
		self.client.force_login(self.user)

		response = self.client.post(reverse('user_event_unregister', kwargs={'pk': event.pk}), follow=True)
		self.assertEqual(response.status_code, 200)
		self.assertFalse(EventRegistration.objects.filter(event=event, user=self.user).exists())

	def test_dashboard_timeline_orders_upcoming_events_first(self):
		event_soon = Event.objects.create(
			name='Soon event',
			description='Soon',
			start_at=timezone.localtime() + timedelta(hours=2),
			end_at=timezone.localtime() + timedelta(days=1),
			requires_registration=False,
			created_by=self.admin,
		)
		event_later = Event.objects.create(
			name='Later event',
			description='Later',
			start_at=timezone.localtime() + timedelta(days=4),
			end_at=timezone.localtime() + timedelta(days=5),
			requires_registration=False,
			created_by=self.admin,
		)

		self.client.force_login(self.user)
		response = self.client.get(reverse('user_dashboard'))
		self.assertEqual(response.status_code, 200)

		event_titles = [entry['title'] for entry in response.context['timeline_events'] if entry.get('kind') == 'event']
		self.assertGreaterEqual(len(event_titles), 2)
		self.assertLess(event_titles.index(event_soon.name), event_titles.index(event_later.name))

	def test_dashboard_marks_registered_event(self):
		event = Event.objects.create(
			name='Registered marker event',
			description='Marker',
			start_at=timezone.localtime() + timedelta(hours=5),
			end_at=timezone.localtime() + timedelta(days=1),
			requires_registration=True,
			capacity=10,
			created_by=self.admin,
		)
		EventRegistration.objects.create(event=event, user=self.user)

		self.client.force_login(self.user)
		response = self.client.get(reverse('user_dashboard'))
		self.assertEqual(response.status_code, 200)

		event_entries = [
			entry
			for entry in response.context['timeline_events']
			if entry.get('kind') == 'event' and entry.get('title') == event.name
		]
		self.assertEqual(len(event_entries), 1)
		self.assertTrue(event_entries[0].get('is_registered'))
		self.assertContains(response, 'timeline-event-registered')

	def test_paid_event_registration_charges_user_balance(self):
		event = Event.objects.create(
			name='Premium workshop',
			description='Paid access.',
			start_at=timezone.localtime() + timedelta(hours=2),
			end_at=timezone.localtime() + timedelta(days=1),
			requires_registration=True,
			is_paid_event=True,
			registration_fee='5.00',
			created_by=self.admin,
		)

		self.client.force_login(self.user)
		response = self.client.post(reverse('user_event_register', kwargs={'pk': event.pk}), follow=True)
		self.assertEqual(response.status_code, 200)
		self.assertTrue(EventRegistration.objects.filter(event=event, user=self.user).exists())

		profile = StoreUserProfile.objects.get(user=self.user)
		self.assertEqual(str(profile.current_balance), '15.00')
		self.assertTrue(
			BalanceLog.objects.filter(
				user=self.user,
				source=BalanceLog.Source.EVENT_REGISTRATION_CHARGE,
				amount_delta='-5.00',
			).exists()
		)

	def test_paid_event_unregister_before_start_refunds_balance(self):
		event = Event.objects.create(
			name='Premium workshop refund',
			description='Paid access.',
			start_at=timezone.localtime() + timedelta(hours=3),
			end_at=timezone.localtime() + timedelta(days=1),
			requires_registration=True,
			is_paid_event=True,
			registration_fee='7.00',
			created_by=self.admin,
		)

		EventRegistration.objects.create(event=event, user=self.user)
		profile = StoreUserProfile.objects.get(user=self.user)
		profile.current_balance = '13.00'
		profile.save(update_fields=['current_balance', 'updated_at'])

		self.client.force_login(self.user)
		response = self.client.post(reverse('user_event_unregister', kwargs={'pk': event.pk}), follow=True)
		self.assertEqual(response.status_code, 200)
		self.assertFalse(EventRegistration.objects.filter(event=event, user=self.user).exists())

		profile.refresh_from_db()
		self.assertEqual(str(profile.current_balance), '20.00')
		self.assertTrue(
			BalanceLog.objects.filter(
				user=self.user,
				source=BalanceLog.Source.EVENT_REGISTRATION_REFUND,
				amount_delta='7.00',
			).exists()
		)

	def test_paid_event_registration_fails_with_insufficient_balance(self):
		event = Event.objects.create(
			name='Very expensive event',
			description='Paid access.',
			start_at=timezone.localtime() + timedelta(hours=4),
			end_at=timezone.localtime() + timedelta(days=1),
			requires_registration=True,
			is_paid_event=True,
			registration_fee='25.00',
			created_by=self.admin,
		)

		self.client.force_login(self.user)
		response = self.client.post(reverse('user_event_register', kwargs={'pk': event.pk}), follow=True)
		self.assertEqual(response.status_code, 200)
		self.assertFalse(EventRegistration.objects.filter(event=event, user=self.user).exists())

		profile = StoreUserProfile.objects.get(user=self.user)
		self.assertEqual(str(profile.current_balance), '20.00')

	def test_user_can_post_event_comment(self):
		event = Event.objects.create(
			name='Comment event',
			description='Talk here.',
			start_at=timezone.localtime() + timedelta(hours=2),
			end_at=timezone.localtime() + timedelta(days=1),
			requires_registration=False,
			created_by=self.admin,
		)

		self.client.force_login(self.user)
		response = self.client.post(
			reverse('user_event_comment_create', kwargs={'pk': event.pk}),
			{'content': 'Nos vemos en el evento.'},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		self.assertTrue(
			EventComment.objects.filter(event=event, user=self.user, content='Nos vemos en el evento.').exists()
		)
		self.assertContains(response, 'Nos vemos en el evento.')


class SurveyFlowTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username='admin_survey', password='testpass123', is_staff=True)
		self.user = User.objects.create_user(username='survey_user', password='testpass123', is_staff=False)
		self.client.force_login(self.user)

	def test_active_survey_is_visible_on_user_dashboard_timeline(self):
		survey = Survey.objects.create(
			title='Encuesta de snacks',
			description='Elige tu snack favorito.',
			selection_type=Survey.SelectionType.RADIO,
			is_active=True,
			created_by=self.admin,
		)
		SurveyOption.objects.create(survey=survey, label='Patatas', sort_order=1)
		SurveyOption.objects.create(survey=survey, label='Frutos secos', sort_order=2)

		response = self.client.get(reverse('user_dashboard'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Encuesta de snacks')
		self.assertContains(response, reverse('user_survey_detail', kwargs={'pk': survey.pk}))

	def test_user_survey_detail_shows_available_options(self):
		survey = Survey.objects.create(
			title='Eleccion de dia',
			selection_type=Survey.SelectionType.CHECKBOX,
			is_active=True,
			created_by=self.admin,
		)
		SurveyOption.objects.create(survey=survey, label='Jueves 26', sort_order=1, is_active=True)
		SurveyOption.objects.create(survey=survey, label='Viernes 27', sort_order=2, is_active=True)

		response = self.client.get(reverse('user_survey_detail', kwargs={'pk': survey.pk}))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'Jueves 26')
		self.assertContains(response, 'Viernes 27')

	def test_user_can_submit_single_choice_survey_once(self):
		survey = Survey.objects.create(
			title='Bebida preferida',
			selection_type=Survey.SelectionType.RADIO,
			is_active=True,
			created_by=self.admin,
		)
		option_1 = SurveyOption.objects.create(survey=survey, label='Cafe', sort_order=1)
		SurveyOption.objects.create(survey=survey, label='Te', sort_order=2)

		response = self.client.post(
			reverse('user_survey_submit', kwargs={'pk': survey.pk}),
			{'selected_option': str(option_1.pk)},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)
		self.assertEqual(SurveyResponse.objects.filter(survey=survey, user=self.user).count(), 1)

		second_response = self.client.post(
			reverse('user_survey_submit', kwargs={'pk': survey.pk}),
			{'selected_option': str(option_1.pk)},
			follow=True,
		)
		self.assertEqual(second_response.status_code, 200)
		self.assertEqual(SurveyResponse.objects.filter(survey=survey, user=self.user).count(), 1)

	def test_user_can_edit_existing_survey_response(self):
		survey = Survey.objects.create(
			title='Dia preferido',
			selection_type=Survey.SelectionType.CHECKBOX,
			is_active=True,
			created_by=self.admin,
		)
		option_1 = SurveyOption.objects.create(survey=survey, label='Jueves 26', sort_order=1)
		option_2 = SurveyOption.objects.create(survey=survey, label='Viernes 27', sort_order=2)

		self.client.post(
			reverse('user_survey_submit', kwargs={'pk': survey.pk}),
			{'selected_options': [str(option_1.pk)]},
			follow=True,
		)

		response = self.client.post(
			reverse('user_survey_submit', kwargs={'pk': survey.pk}),
			{'selected_options': [str(option_2.pk)]},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)

		survey_response = SurveyResponse.objects.get(survey=survey, user=self.user)
		selected_ids = set(survey_response.selected_options.values_list('option_id', flat=True))
		self.assertEqual(selected_ids, {option_2.pk})

	def test_admin_survey_info_shows_respondent_and_selected_option(self):
		survey = Survey.objects.create(
			title='Horario de apertura',
			selection_type=Survey.SelectionType.CHECKBOX,
			is_active=True,
			created_by=self.admin,
		)
		option_1 = SurveyOption.objects.create(survey=survey, label='Mañanas', sort_order=1)
		option_2 = SurveyOption.objects.create(survey=survey, label='Tardes', sort_order=2)

		response_obj = SurveyResponse.objects.create(survey=survey, user=self.user)
		response_obj.selected_options.create(option=option_1)
		response_obj.selected_options.create(option=option_2)

		self.client.force_login(self.admin)
		response = self.client.get(reverse('admin_survey_info', kwargs={'pk': survey.pk}))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'survey_user')
		self.assertContains(response, 'Mañanas')
		self.assertContains(response, 'Tardes')


class AdminEventInfoTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username='admin_event_info', password='testpass123', is_staff=True)
		self.user = User.objects.create_user(username='event_info_user', password='testpass123', is_staff=False)
		StoreUserProfile.objects.create(user=self.user, current_balance='10.00')
		self.client.force_login(self.admin)

	def test_information_action_is_visible_only_for_capacity_or_paid_events(self):
		now = timezone.localtime()
		event_with_capacity = Event.objects.create(
			name='Cap event',
			start_at=now + timedelta(hours=2),
			end_at=now + timedelta(days=1),
			requires_registration=True,
			capacity=5,
			created_by=self.admin,
		)
		event_paid = Event.objects.create(
			name='Paid event',
			start_at=now + timedelta(hours=3),
			end_at=now + timedelta(days=1),
			requires_registration=True,
			is_paid_event=True,
			registration_fee='3.00',
			created_by=self.admin,
		)
		event_plain = Event.objects.create(
			name='Plain event',
			start_at=now + timedelta(hours=4),
			end_at=now + timedelta(days=1),
			requires_registration=False,
			created_by=self.admin,
		)

		response = self.client.get(reverse('admin_events'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, reverse('admin_event_info', kwargs={'pk': event_with_capacity.pk}))
		self.assertContains(response, reverse('admin_event_info', kwargs={'pk': event_paid.pk}))
		self.assertNotContains(response, reverse('admin_event_info', kwargs={'pk': event_plain.pk}))

	def test_admin_event_info_shows_registered_users_and_total_collected(self):
		now = timezone.localtime()
		event = Event.objects.create(
			name='Revenue event',
			start_at=now + timedelta(hours=2),
			end_at=now + timedelta(days=1),
			requires_registration=True,
			capacity=10,
			is_paid_event=True,
			registration_fee='4.00',
			created_by=self.admin,
		)
		EventRegistration.objects.create(event=event, user=self.user)

		response = self.client.get(reverse('admin_event_info', kwargs={'pk': event.pk}))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'event_info_user')
		self.assertContains(response, '€ 4.00')

	def test_admin_can_remove_registration_and_refund_paid_event_before_start(self):
		now = timezone.localtime()
		event = Event.objects.create(
			name='Manual remove event',
			start_at=now + timedelta(hours=5),
			end_at=now + timedelta(days=1),
			requires_registration=True,
			capacity=10,
			is_paid_event=True,
			registration_fee='5.00',
			created_by=self.admin,
		)
		registration = EventRegistration.objects.create(event=event, user=self.user)

		profile = StoreUserProfile.objects.get(user=self.user)
		profile.current_balance = '5.00'
		profile.save(update_fields=['current_balance', 'updated_at'])

		response = self.client.post(
			reverse('admin_event_registration_remove', kwargs={'pk': event.pk, 'registration_id': registration.pk}),
			follow=True,
		)
		self.assertEqual(response.status_code, 200)
		self.assertFalse(EventRegistration.objects.filter(pk=registration.pk).exists())

		profile.refresh_from_db()
		self.assertEqual(str(profile.current_balance), '10.00')
		self.assertTrue(
			BalanceLog.objects.filter(
				user=self.user,
				source=BalanceLog.Source.EVENT_REGISTRATION_REFUND,
				amount_delta='5.00',
			).exists()
		)

	def test_admin_can_reply_event_comment(self):
		now = timezone.localtime()
		event = Event.objects.create(
			name='Commented event',
			start_at=now + timedelta(hours=2),
			end_at=now + timedelta(days=1),
			requires_registration=False,
			created_by=self.admin,
		)
		comment = EventComment.objects.create(
			event=event,
			user=self.user,
			content='¿Hay parking?',
		)

		response = self.client.post(
			reverse('admin_event_comment_reply', kwargs={'pk': event.pk, 'comment_id': comment.pk}),
			{'content': 'Si, hay parking disponible.'},
			follow=True,
		)

		self.assertEqual(response.status_code, 200)
		self.assertTrue(
			EventComment.objects.filter(
				event=event,
				parent=comment,
				user=self.admin,
				content='Si, hay parking disponible.',
			).exists()
		)


class UserPasswordReminderAndProfilePasswordChangeTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='member_pwd', password='InitialPass123!', is_staff=False)

	def test_user_dashboard_shows_password_reminder_modal_when_required(self):
		self.client.force_login(self.user)
		response = self.client.get(reverse('user_dashboard'))
		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'passwordChangeReminderModal')

	def test_user_dashboard_hides_password_reminder_modal_when_not_required(self):
		profile, _ = StoreUserProfile.objects.get_or_create(user=self.user)
		profile.password_change_required = False
		profile.save(update_fields=['password_change_required', 'updated_at'])

		self.client.force_login(self.user)
		response = self.client.get(reverse('user_dashboard'))
		self.assertEqual(response.status_code, 200)
		self.assertNotContains(response, 'passwordChangeReminderModal')

	def test_user_can_change_password_from_profile_and_disable_reminder(self):
		self.client.force_login(self.user)
		response = self.client.post(
			reverse('profile_edit'),
			{
				'change_password': '1',
				'old_password': 'InitialPass123!',
				'new_password1': 'UpdatedPass456!',
				'new_password2': 'UpdatedPass456!',
			},
			follow=True,
		)
		self.assertEqual(response.status_code, 200)

		self.user.refresh_from_db()
		self.assertTrue(self.user.check_password('UpdatedPass456!'))

		profile = StoreUserProfile.objects.get(user=self.user)
		self.assertFalse(profile.password_change_required)


class AdminUserCreateTemporaryCodeTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username='admin_creator', password='testpass123', is_staff=True)
		self.client.force_login(self.admin)

	def test_create_form_displays_generated_temporary_access_code(self):
		response = self.client.get(reverse('admin_user_create'))
		self.assertEqual(response.status_code, 200)

		form = response.context['form']
		temporary_code = form.fields['temporary_access_code'].initial
		self.assertRegex(temporary_code, r'^[a-z0-9]{8}$')

	def test_admin_can_create_user_with_generated_temporary_access_code(self):
		response = self.client.get(reverse('admin_user_create'))
		self.assertEqual(response.status_code, 200)
		temporary_code = response.context['form'].fields['temporary_access_code'].initial

		create_response = self.client.post(
			reverse('admin_user_create'),
			{
				'username': 'temp_user_01',
				'email': 'temp_user_01@example.com',
				'is_staff': '',
				'temporary_access_code': temporary_code,
				'language': 'es',
				'monthly_fee_enabled': '',
				'recent_movements_limit': '',
			},
			follow=True,
		)
		self.assertEqual(create_response.status_code, 200)
		self.assertContains(create_response, temporary_code)

		created_user = User.objects.get(username='temp_user_01')
		self.assertTrue(created_user.check_password(temporary_code))

		profile = StoreUserProfile.objects.get(user=created_user)
		self.assertEqual(profile.language, 'es')


class AdminUserResetPasswordTests(TestCase):
	def setUp(self):
		self.admin = User.objects.create_user(username='admin_reset', password='testpass123', is_staff=True)
		self.target_user = User.objects.create_user(username='reset_target', password='oldpass123', is_staff=False)
		self.profile, _ = StoreUserProfile.objects.get_or_create(user=self.target_user)
		self.profile.password_change_required = False
		self.profile.temporary_access_code_plain = ''
		self.profile.save(update_fields=['password_change_required', 'temporary_access_code_plain', 'updated_at'])

	def test_admin_can_reset_user_password_and_force_relogin(self):
		admin_client = self.client
		admin_client.force_login(self.admin)

		target_client = self.client_class()
		target_client.force_login(self.target_user)
		target_session_key = target_client.session.session_key
		UserSession.objects.create(user=self.target_user, session_key=target_session_key)

		response = admin_client.post(
			reverse('admin_user_edit', kwargs={'user_id': self.target_user.id}),
			{'reset_user_password': '1'},
			follow=False,
		)
		self.assertEqual(response.status_code, 302)

		self.target_user.refresh_from_db()
		self.profile.refresh_from_db()

		self.assertTrue(self.profile.password_change_required)
		self.assertRegex(self.profile.temporary_access_code_plain, r'^[a-z0-9]{8}$')
		self.assertTrue(self.target_user.check_password(self.profile.temporary_access_code_plain))

		self.assertFalse(Session.objects.filter(session_key=target_session_key).exists())
		self.assertFalse(UserSession.objects.filter(user=self.target_user, session_key=target_session_key).exists())

		protected_response = target_client.get(reverse('user_dashboard'))
		self.assertEqual(protected_response.status_code, 302)
		self.assertIn(reverse('login'), protected_response.url)
