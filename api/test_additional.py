from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from customers.models import StoreUserProfile
from inventory.models import Product


class ProductApiAdditionalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='api-extra-admin', password='testpass123', is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_low_stock_returns_only_active_products_below_or_equal_threshold(self):
        low_stock = Product.objects.create(
            name='Low stock product',
            sku='LOW-1',
            price='2.00',
            stock='5.00',
            is_active=True,
        )
        Product.objects.create(
            name='High stock product',
            sku='HIGH-1',
            price='2.00',
            stock='9.00',
            is_active=True,
        )
        Product.objects.create(
            name='Inactive low stock product',
            sku='INACTIVE-LOW-1',
            price='2.00',
            stock='3.00',
            is_active=False,
        )

        response = self.client.get('/api/products/low_stock/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], low_stock.id)


class StoreUserProfileApiAdditionalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='api-profile-user', password='testpass123')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_can_create_and_update_profile_through_api(self):
        payload = {
            'user': self.user.id,
            'current_balance': '12.50',
            'phone': '+34123456789',
            'address': 'Test street 12',
        }

        create_response = self.client.post('/api/profiles/', payload, format='json')

        self.assertEqual(create_response.status_code, 201)
        profile_id = create_response.data['id']
        self.assertTrue(StoreUserProfile.objects.filter(id=profile_id, user=self.user).exists())

        patch_response = self.client.patch(
            f'/api/profiles/{profile_id}/',
            {'phone': '+34999999999', 'address': 'Updated street 99'},
            format='json',
        )

        self.assertEqual(patch_response.status_code, 200)
        profile = StoreUserProfile.objects.get(id=profile_id)
        self.assertEqual(profile.phone, '+34999999999')
        self.assertEqual(profile.address, 'Updated street 99')


class ApiAuthorizationAdditionalTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='api-auth-staff', password='testpass123', is_staff=True)
        self.user = User.objects.create_user(username='api-auth-user', password='testpass123', is_staff=False)
        self.other_user = User.objects.create_user(username='api-auth-other', password='testpass123', is_staff=False)
        self.client = APIClient()

    def test_non_staff_cannot_access_product_or_sale_endpoints(self):
        self.client.force_authenticate(user=self.user)

        product_response = self.client.get('/api/products/')
        sale_response = self.client.get('/api/sales/')

        self.assertEqual(product_response.status_code, 403)
        self.assertEqual(sale_response.status_code, 403)

    def test_non_staff_only_sees_own_profile_queryset(self):
        own_profile = StoreUserProfile.objects.create(user=self.user)
        StoreUserProfile.objects.create(user=self.other_user)

        self.client.force_authenticate(user=self.user)
        list_response = self.client.get('/api/profiles/')

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data), 1)
        self.assertEqual(list_response.data[0]['id'], own_profile.id)

    def test_staff_can_access_product_and_sale_endpoints(self):
        self.client.force_authenticate(user=self.staff)

        product_response = self.client.get('/api/products/')
        sale_response = self.client.get('/api/sales/')

        self.assertEqual(product_response.status_code, 200)
        self.assertEqual(sale_response.status_code, 200)
