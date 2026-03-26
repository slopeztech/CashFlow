from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.test import tag
from django.urls import reverse
from django.utils import timezone

from core.tests.summary import register_section

from core.models import Event, EventRegistration
from customers.models import BalanceLog, StoreUserProfile
from inventory.models import Product
from sales.models import Order


E2E_TEST_COUNT = 2


def tearDownModule():
    register_section('e2e', tests_count=E2E_TEST_COUNT)


@tag('e2e')
class OrderJourneyE2ETests(TestCase):
    def setUp(self):
        self.user_password = 'testpass123'
        self.admin_password = 'testpass123'
        self.user = User.objects.create_user(username='e2e-user', password=self.user_password, is_staff=False)
        self.admin = User.objects.create_user(username='e2e-admin', password=self.admin_password, is_staff=True)
        self.profile = StoreUserProfile.objects.create(user=self.user, current_balance='100.00')
        self.product = Product.objects.create(
            name='E2E product',
            sku='E2E-1',
            price='2.50',
            stock='15.00',
            is_active=True,
            is_public_listing=True,
        )

    def test_user_order_to_admin_approval_full_journey(self):
        user_client = Client()
        login_response = user_client.post(
            reverse('login_page'),
            {'username': self.user.username, 'password': self.user_password},
        )
        self.assertEqual(login_response.status_code, 302)

        user_client.post(
            reverse('user_cart_add'),
            {'product_id': str(self.product.id), 'purchase_mode': 'units', 'quantity': '3.00'},
        )
        submit_response = user_client.post(reverse('user_cart_submit'))
        self.assertEqual(submit_response.status_code, 302)

        order = Order.objects.get(created_by=self.user)
        self.assertEqual(order.status, Order.Status.PENDING)

        admin_client = Client()
        admin_login = admin_client.post(
            reverse('login_page'),
            {'username': self.admin.username, 'password': self.admin_password},
        )
        self.assertEqual(admin_login.status_code, 302)

        approve_response = admin_client.post(
            reverse('admin_order_approval', kwargs={'pk': order.id}),
              {'action': 'approve', 'next': 'admin_actions'},
        )
        self.assertEqual(approve_response.status_code, 302)

        order.refresh_from_db()
        self.product.refresh_from_db()
        self.profile.refresh_from_db()

        self.assertEqual(order.status, Order.Status.APPROVED)
        self.assertEqual(self.product.stock, Decimal('12.00'))
        self.assertEqual(self.profile.current_balance, Decimal('92.50'))


@tag('e2e')
class PaidEventE2ETests(TestCase):
    def setUp(self):
        self.user_password = 'testpass123'
        self.user = User.objects.create_user(username='e2e-event-user', password=self.user_password, is_staff=False)
        self.profile = StoreUserProfile.objects.create(user=self.user, current_balance='30.00')
        self.event = Event.objects.create(
            name='E2E paid event',
            description='Integration end-to-end event',
            start_at=timezone.localtime() + timedelta(days=2),
            end_at=timezone.localtime() + timedelta(days=2, hours=2),
            requires_registration=True,
            is_paid_event=True,
            registration_fee='7.00',
            capacity=20,
        )

    def test_paid_event_register_and_unregister_refunds_balance(self):
        user_client = Client()
        login_response = user_client.post(
            reverse('login_page'),
            {'username': self.user.username, 'password': self.user_password},
        )
        self.assertEqual(login_response.status_code, 302)

        register_response = user_client.post(reverse('user_event_register', kwargs={'pk': self.event.id}))
        self.assertEqual(register_response.status_code, 302)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.current_balance, Decimal('23.00'))
        self.assertTrue(EventRegistration.objects.filter(event=self.event, user=self.user).exists())

        unregister_response = user_client.post(reverse('user_event_unregister', kwargs={'pk': self.event.id}))
        self.assertEqual(unregister_response.status_code, 302)

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.current_balance, Decimal('30.00'))
        self.assertFalse(EventRegistration.objects.filter(event=self.event, user=self.user).exists())

        self.assertTrue(
            BalanceLog.objects.filter(
                user=self.user,
                source=BalanceLog.Source.EVENT_REGISTRATION_CHARGE,
                amount_delta=Decimal('-7.00'),
            ).exists()
        )
        self.assertTrue(
            BalanceLog.objects.filter(
                user=self.user,
                source=BalanceLog.Source.EVENT_REGISTRATION_REFUND,
                amount_delta=Decimal('7.00'),
            ).exists()
        )
