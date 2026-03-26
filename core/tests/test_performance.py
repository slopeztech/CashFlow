from datetime import timedelta
from decimal import Decimal
from time import perf_counter
from unittest import skipIf

from django.contrib.auth.models import User
from django.test import TestCase
from django.test import tag
from django.urls import reverse
from django.utils import timezone

from customers.models import StoreUserProfile
from inventory.models import Product
from core.testing_env import get_bounded_int_env, get_env, get_float_env
from core.tests.summary import register_section


def _performance_disabled() -> bool:
    return get_env('RUN_PERFORMANCE_TESTS', '1') == '0'


def _max_seconds() -> float:
    return get_float_env('PERFORMANCE_MAX_SECONDS', 8.0)


@skipIf(_performance_disabled(), 'Set RUN_PERFORMANCE_TESTS=1 or unset it to run performance tests.')
@tag('performance')
class PerformanceSmokeTests(TestCase):
    TEST_COUNT = 3
    metrics = []

    @classmethod
    def setUpTestData(cls):
        batch_min = get_bounded_int_env('TEST_BATCH_MIN', 100, minimum=1)
        batch_max = get_bounded_int_env('TEST_BATCH_MAX', 5000, minimum=batch_min)
        volume_min = get_bounded_int_env('TEST_VOLUME_MIN', 1, minimum=1)
        volume_max = get_bounded_int_env('TEST_VOLUME_MAX', 50000, minimum=volume_min)

        cls.max_seconds = _max_seconds()
        cls.bulk_batch_size = get_bounded_int_env(
            'TEST_BULK_BATCH_SIZE',
            1000,
            minimum=batch_min,
            maximum=batch_max,
        )
        cls.perf_users_count = get_bounded_int_env(
            'TEST_PERF_USERS_COUNT',
            5000,
            minimum=volume_min,
            maximum=volume_max,
        )
        cls.perf_monthly_users_count = get_bounded_int_env(
            'TEST_PERF_MONTHLY_USERS_COUNT',
            5000,
            minimum=volume_min,
            maximum=volume_max,
        )
        cls.perf_products_count = get_bounded_int_env(
            'TEST_PERF_PRODUCTS_COUNT',
            5000,
            minimum=volume_min,
            maximum=volume_max,
        )
        cls.perf_low_stock_ratio = get_bounded_int_env(
            'TEST_PERF_LOW_STOCK_RATIO',
            10,
            minimum=1,
            maximum=max(1, volume_max),
        )
        cls.perf_monthly_enabled_days_ago = get_bounded_int_env(
            'TEST_PERF_MONTHLY_ENABLED_DAYS_AGO',
            60,
            minimum=0,
            maximum=3650,
        )
        cls.admin = User.objects.create_user(username='perf-admin', password='testpass123', is_staff=True)

    @classmethod
    def tearDownClass(cls):
        details = {}
        if cls.metrics:
            for metric in cls.metrics:
                details[metric['name']] = (
                    f"{metric['elapsed']:.3f}s "
                    f"(limit={metric['limit']:.3f}s, status={metric['status']})"
                )
        register_section('performance', tests_count=cls.TEST_COUNT, details=details)
        super().tearDownClass()

    def _assert_response_time(self, *, name, seconds_limit, url_name=None, path=None):
        self.client.force_login(self.admin)

        if url_name:
            target = reverse(url_name)
        elif path:
            target = path
        else:
            raise ValueError('Either url_name or path must be provided.')

        start = perf_counter()
        response = self.client.get(target)
        elapsed = perf_counter() - start

        status = 'ok' if elapsed <= seconds_limit else 'failed'
        self.metrics.append(
            {
                'name': name,
                'elapsed': elapsed,
                'limit': seconds_limit,
                'status': status,
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            elapsed,
            seconds_limit,
            msg=f'Endpoint {name} exceeded time limit: {elapsed:.3f}s > {seconds_limit:.3f}s',
        )
        return response

    def test_admin_user_list_under_time_budget_with_5000_users(self):
        users = [
            User(username=f'perf-user-{index}', email=f'perf-{index}@example.com', is_staff=False)
            for index in range(self.perf_users_count)
        ]
        User.objects.bulk_create(users, batch_size=self.bulk_batch_size)

        self._assert_response_time(
            name='admin_user_list',
            url_name='admin_user_list',
            seconds_limit=self.max_seconds,
        )

    def test_admin_monthly_fee_under_time_budget_with_5000_profiles(self):
        users = [
            User(username=f'perf-monthly-{index}', email=f'perf-monthly-{index}@example.com', is_staff=False)
            for index in range(self.perf_monthly_users_count)
        ]
        User.objects.bulk_create(users, batch_size=self.bulk_batch_size)

        enabled_at = timezone.localdate() - timedelta(days=self.perf_monthly_enabled_days_ago)
        created_users = list(User.objects.filter(username__startswith='perf-monthly-').only('id'))
        profiles = [
            StoreUserProfile(
                user_id=user.id,
                monthly_fee_enabled=True,
                monthly_fee_enabled_at=enabled_at,
                current_balance=Decimal('0.00'),
            )
            for user in created_users
        ]
        StoreUserProfile.objects.bulk_create(profiles, batch_size=self.bulk_batch_size)

        self._assert_response_time(
            name='admin_monthly_fee',
            url_name='admin_monthly_fee',
            seconds_limit=self.max_seconds,
        )

    def test_api_low_stock_under_time_budget_with_5000_products(self):
        self.client.force_login(self.admin)

        products = []
        for index in range(self.perf_products_count):
            stock = Decimal('3.00') if index % self.perf_low_stock_ratio == 0 else Decimal('15.00')
            products.append(
                Product(
                    name=f'perf-product-{index}',
                    sku=f'PERF-{index}',
                    price=Decimal('1.25'),
                    stock=stock,
                    is_active=True,
                )
            )
        Product.objects.bulk_create(products, batch_size=self.bulk_batch_size)

        response = self._assert_response_time(
            name='api_products_low_stock',
            path='/api/products/low_stock/',
            seconds_limit=self.max_seconds,
        )
        expected_low_stock = sum(
            1 for index in range(self.perf_products_count) if index % self.perf_low_stock_ratio == 0
        )
        self.assertEqual(len(response.data), expected_low_stock)
