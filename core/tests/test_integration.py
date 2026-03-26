from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.test import tag
from django.urls import reverse
from rest_framework.test import APIClient

from core.tests.summary import register_section

from customers.models import BalanceLog, BalanceRequest, StoreUserProfile
from inventory.models import Product
from sales.models import Order


INTEGRATION_TEST_COUNT = 3


def tearDownModule():
    register_section('integration', tests_count=INTEGRATION_TEST_COUNT)


@tag('integration')
class OrderApprovalIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='integration-user', password='testpass123', is_staff=False)
        self.admin = User.objects.create_user(username='integration-admin', password='testpass123', is_staff=True)
        self.profile = StoreUserProfile.objects.create(user=self.user, current_balance='50.00')
        self.product = Product.objects.create(
            name='Integration product',
            sku='INT-1',
            price='2.00',
            stock='10.00',
            is_active=True,
            is_public_listing=True,
        )

    def test_cart_submit_and_admin_approval_updates_stock_and_balance(self):
        user_client = Client()
        user_client.force_login(self.user)

        add_response = user_client.post(
            reverse('user_cart_add'),
            {'product_id': str(self.product.id), 'purchase_mode': 'units', 'quantity': '2.00'},
        )
        self.assertEqual(add_response.status_code, 302)

        submit_response = user_client.post(reverse('user_cart_submit'))
        self.assertEqual(submit_response.status_code, 302)

        order = Order.objects.get(created_by=self.user)
        self.assertEqual(order.status, Order.Status.PENDING)

        admin_client = Client()
        admin_client.force_login(self.admin)
        approve_response = admin_client.post(
            reverse('admin_order_approval', kwargs={'pk': order.id}),
            {'action': 'approve', 'next': 'admin_actions'},
        )
        self.assertEqual(approve_response.status_code, 302)

        order.refresh_from_db()
        self.profile.refresh_from_db()
        self.product.refresh_from_db()

        self.assertEqual(order.status, Order.Status.APPROVED)
        self.assertEqual(self.product.stock, Decimal('8.00'))
        self.assertEqual(self.profile.current_balance, Decimal('46.00'))
        self.assertTrue(
            BalanceLog.objects.filter(
                user=self.user,
                source=BalanceLog.Source.ORDER_APPROVAL,
                amount_delta=Decimal('-4.00'),
            ).exists()
        )


@tag('integration')
class ApiAndWebIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='integration-customer', password='testpass123', is_staff=False)
        self.admin = User.objects.create_user(username='integration-seller', password='testpass123', is_staff=True)
        self.product = Product.objects.create(
            name='API integration product',
            sku='API-INT-1',
            price='3.00',
            stock='20.00',
            is_active=True,
            is_public_listing=True,
        )

    def test_api_sale_is_visible_in_user_orders_history_view(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.admin)

        response = api_client.post(
            '/api/sales/',
            {
                'customer': self.user.id,
                'items': [
                    {'product': self.product.id, 'quantity': '2.00', 'unit_price': '3.00'},
                ],
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201)

        web_client = Client()
        web_client.force_login(self.user)
        orders_response = web_client.get(reverse('user_orders'))

        self.assertEqual(orders_response.status_code, 200)
        direct_sales = orders_response.context['direct_sales']
        self.assertEqual(direct_sales.count(), 1)
        self.assertEqual(str(direct_sales.first().total_amount), '6.00')


@tag('integration')
class BalanceRequestIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='integration-balance-user',
            password='testpass123',
            is_staff=False,
        )
        self.admin = User.objects.create_user(
            username='integration-balance-admin',
            password='testpass123',
            is_staff=True,
        )
        self.profile = StoreUserProfile.objects.create(user=self.user, current_balance='5.00')

    def test_user_request_then_admin_approve_updates_user_balance_screen(self):
        user_client = Client()
        user_client.force_login(self.user)
        request_response = user_client.post(reverse('user_balance_requests'), {'amount': '12.00'})
        self.assertEqual(request_response.status_code, 302)

        balance_request = BalanceRequest.objects.get(user=self.user)

        admin_client = Client()
        admin_client.force_login(self.admin)
        approve_response = admin_client.post(
            reverse('admin_balance_request_approve', kwargs={'request_id': balance_request.id}),
            {'next': 'admin_balance_requests'},
        )
        self.assertEqual(approve_response.status_code, 302)

        balance_page = user_client.get(reverse('user_balance_requests'))
        self.assertEqual(balance_page.status_code, 200)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.current_balance, Decimal('17.00'))
        self.assertContains(balance_page, '17.00')
