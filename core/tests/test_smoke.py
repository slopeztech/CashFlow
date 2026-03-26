from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.test import tag
from django.urls import reverse

from core.tests.summary import register_section


SMOKE_TEST_COUNT = 3


def tearDownModule():
    register_section('smoke', tests_count=SMOKE_TEST_COUNT)


@tag('smoke')
class SmokeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='smoke-user', password='testpass123', is_staff=False)
        self.admin = User.objects.create_user(username='smoke-admin', password='testpass123', is_staff=True)

    def test_login_page_is_reachable(self):
        response = self.client.get(reverse('login_page'))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_requires_authentication(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 302)

    def test_dashboard_redirects_by_role(self):
        user_client = Client()
        user_client.force_login(self.user)
        user_response = user_client.get(reverse('dashboard'))
        self.assertRedirects(user_response, reverse('user_dashboard'))

        admin_client = Client()
        admin_client.force_login(self.admin)
        admin_response = admin_client.get(reverse('dashboard'))
        self.assertRedirects(admin_response, reverse('admin_dashboard'))
