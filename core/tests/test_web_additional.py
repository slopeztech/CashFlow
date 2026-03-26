from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.test import tag
from django.urls import reverse
from django.utils import timezone

from core.testing_env import get_bounded_int_env
from core.tests.summary import register_section
from core.controllers.dashboard_controller import build_dashboard_context
from customers.models import BalanceLog, BalanceRequest, StoreUserProfile
from inventory.models import Category, Product, ProductReview
from sales.models import Order
from sales.services import approve_order, create_order, create_sale


TEST_BATCH_MIN = get_bounded_int_env('TEST_BATCH_MIN', 100, minimum=1)
TEST_BATCH_MAX = get_bounded_int_env('TEST_BATCH_MAX', 5000, minimum=TEST_BATCH_MIN)
TEST_VOLUME_MIN = get_bounded_int_env('TEST_VOLUME_MIN', 1, minimum=1)
TEST_VOLUME_MAX = get_bounded_int_env('TEST_VOLUME_MAX', 50000, minimum=TEST_VOLUME_MIN)

TEST_BULK_BATCH_SIZE = get_bounded_int_env(
    'TEST_BULK_BATCH_SIZE',
    1000,
    minimum=TEST_BATCH_MIN,
    maximum=TEST_BATCH_MAX,
)
TEST_SCALE_USERS_COUNT = get_bounded_int_env(
    'TEST_SCALE_USERS_COUNT',
    5000,
    minimum=TEST_VOLUME_MIN,
    maximum=TEST_VOLUME_MAX,
)
TEST_SCALE_MONTHLY_USERS_COUNT = get_bounded_int_env(
    'TEST_SCALE_MONTHLY_USERS_COUNT',
    5000,
    minimum=TEST_VOLUME_MIN,
    maximum=TEST_VOLUME_MAX,
)
TEST_SCALE_MONTHLY_ENABLED_DAYS_AGO = get_bounded_int_env(
    'TEST_SCALE_MONTHLY_ENABLED_DAYS_AGO',
    60,
    minimum=0,
    maximum=3650,
)
TEST_SCALE_PENDING_REQUESTS_COUNT = get_bounded_int_env(
    'TEST_SCALE_PENDING_REQUESTS_COUNT',
    3000,
    minimum=TEST_VOLUME_MIN,
    maximum=TEST_VOLUME_MAX,
)
TEST_SCALE_CATALOG_PRODUCTS_COUNT = get_bounded_int_env(
    'TEST_SCALE_CATALOG_PRODUCTS_COUNT',
    3000,
    minimum=TEST_VOLUME_MIN,
    maximum=TEST_VOLUME_MAX,
)
TEST_SCALE_REVIEWS_COUNT = get_bounded_int_env(
    'TEST_SCALE_REVIEWS_COUNT',
    2000,
    minimum=TEST_VOLUME_MIN,
    maximum=TEST_VOLUME_MAX,
)

SECURITY_TEST_COUNT = 4
STABILITY_TEST_COUNT = 6
SCALABILITY_TEST_COUNT = 5


def tearDownModule():
    register_section('security', tests_count=SECURITY_TEST_COUNT)
    register_section('stability', tests_count=STABILITY_TEST_COUNT)
    register_section(
        'scalability',
        tests_count=SCALABILITY_TEST_COUNT,
        details={
            'users count': TEST_SCALE_USERS_COUNT,
            'monthly users count': TEST_SCALE_MONTHLY_USERS_COUNT,
            'pending requests count': TEST_SCALE_PENDING_REQUESTS_COUNT,
            'products count': TEST_SCALE_CATALOG_PRODUCTS_COUNT,
            'reviews count': TEST_SCALE_REVIEWS_COUNT,
        },
    )


class BalanceRequestWorkflowWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='balance-admin-extra', password='testpass123', is_staff=True)
        self.user = User.objects.create_user(username='balance-user-extra', password='testpass123', is_staff=False)
        self.profile = StoreUserProfile.objects.create(user=self.user, current_balance='10.00')

    def test_admin_can_approve_balance_request_and_create_log(self):
        balance_request = BalanceRequest.objects.create(user=self.user, amount='7.50')
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('admin_balance_request_approve', kwargs={'request_id': balance_request.id}),
            {'next': 'admin_balance_requests'},
        )

        self.assertEqual(response.status_code, 302)
        balance_request.refresh_from_db()
        self.profile.refresh_from_db()

        self.assertEqual(balance_request.status, BalanceRequest.Status.APPROVED)
        self.assertEqual(self.profile.current_balance, Decimal('17.50'))

        log = BalanceLog.objects.get(user=self.user, source=BalanceLog.Source.BALANCE_REQUEST_APPROVAL)
        self.assertEqual(log.amount_delta, Decimal('7.50'))
        self.assertEqual(log.changed_by, self.admin)

    def test_admin_can_reject_balance_request_without_changing_balance(self):
        balance_request = BalanceRequest.objects.create(user=self.user, amount='9.00')
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('admin_balance_request_reject', kwargs={'request_id': balance_request.id}),
            {'rejection_reason': 'Invalid transfer proof', 'next': 'admin_balance_requests'},
        )

        self.assertEqual(response.status_code, 302)
        balance_request.refresh_from_db()
        self.profile.refresh_from_db()

        self.assertEqual(balance_request.status, BalanceRequest.Status.REJECTED)
        self.assertEqual(balance_request.rejection_reason, 'Invalid transfer proof')
        self.assertEqual(self.profile.current_balance, Decimal('10.00'))
        self.assertEqual(BalanceLog.objects.count(), 0)


class MoneyMetricsConsistencyTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='money-admin', password='testpass123', is_staff=True)
        self.user = User.objects.create_user(username='money-user', password='testpass123', is_staff=False)
        self.category = Category.objects.create(name='Money category')
        self.product = Product.objects.create(
            name='Money product',
            sku='MNY-1',
            category=self.category,
            price='10.00',
            stock='50.00',
            is_active=True,
            is_public_listing=True,
        )

    def test_dashboard_counts_approved_orders_by_approval_date(self):
        order = create_order(
            created_by=self.user,
            customer_name=self.user.username,
            items_data=[{'product': self.product, 'quantity': Decimal('2.00')}],
        )

        previous_month = timezone.localtime().replace(day=1) - timedelta(days=1)
        legacy_created_at = previous_month.replace(day=10, hour=11, minute=0, second=0, microsecond=0)
        Order.objects.filter(pk=order.pk).update(created_at=legacy_created_at, updated_at=legacy_created_at)
        order.refresh_from_db()

        approve_order(order=order, approved_by=self.admin)

        context = build_dashboard_context(self.admin)
        self.assertEqual(context['current_month_sales_orders_total'], Decimal('20.00'))
        self.assertEqual(context['cash_today_total'], Decimal('20.00'))

    def test_dashboard_and_sales_page_use_same_monthly_money_basis(self):
        create_sale(
            seller=self.admin,
            customer=self.user,
            items_data=[{'product': self.product, 'quantity': Decimal('1.00')}],
        )

        order = create_order(
            created_by=self.user,
            customer_name=self.user.username,
            items_data=[{'product': self.product, 'quantity': Decimal('2.00')}],
        )
        previous_month = timezone.localtime().replace(day=1) - timedelta(days=1)
        legacy_created_at = previous_month.replace(day=8, hour=9, minute=0, second=0, microsecond=0)
        Order.objects.filter(pk=order.pk).update(created_at=legacy_created_at, updated_at=legacy_created_at)
        order.refresh_from_db()
        approve_order(order=order, approved_by=self.admin)

        dashboard_context = build_dashboard_context(self.admin)

        self.client.force_login(self.admin)
        sales_response = self.client.get(reverse('sale_list'))
        self.assertEqual(sales_response.status_code, 200)

        self.assertEqual(
            dashboard_context['current_month_sales_orders_total'],
            sales_response.context['sales_month_revenue'],
        )


class ProductReviewWorkflowWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='review-admin-extra', password='testpass123', is_staff=True)
        self.user = User.objects.create_user(username='review-user-extra', password='testpass123', is_staff=False)
        self.product = Product.objects.create(
            name='Reviewable product',
            sku='REV-1',
            price='4.00',
            stock='20.00',
            is_active=True,
            is_public_listing=True,
        )

    def test_user_can_submit_review_after_consumption_and_admin_can_moderate(self):
        StoreUserProfile.objects.create(user=self.user, current_balance='100.00')
        order = create_order(
            created_by=self.user,
            customer_name=self.user.username,
            items_data=[{'product': self.product, 'quantity': Decimal('1.00')}],
        )
        approve_order(order=order, approved_by=self.admin)

        self.client.force_login(self.user)
        create_response = self.client.post(
            reverse('user_product_review', kwargs={'product_id': self.product.id}),
            {'rating': 5, 'message': 'Great quality'},
        )

        self.assertEqual(create_response.status_code, 302)
        review = ProductReview.objects.get(product=self.product, user=self.user)
        self.assertFalse(review.is_approved)

        self.client.force_login(self.admin)
        approve_response = self.client.post(
            reverse('admin_review_approve', kwargs={'review_id': review.id}),
            {'next': 'admin_reviews'},
        )
        self.assertEqual(approve_response.status_code, 302)
        review.refresh_from_db()
        self.assertTrue(review.is_approved)

        reject_response = self.client.post(
            reverse('admin_review_reject', kwargs={'review_id': review.id}),
            {'next': 'admin_reviews'},
        )
        self.assertEqual(reject_response.status_code, 302)
        review.refresh_from_db()
        self.assertFalse(review.is_approved)

    def test_user_cannot_submit_review_without_consumption(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('user_product_review', kwargs={'product_id': self.product.id}),
            {'rating': 3, 'message': 'I should not be able to publish this'},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProductReview.objects.filter(product=self.product, user=self.user).exists())


class AdminUserSafeDeleteWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='safe-delete-admin', password='testpass123', is_staff=True)

    def test_admin_cannot_delete_user_with_negative_balance(self):
        target_user = User.objects.create_user(username='negative-balance-user', password='testpass123', is_staff=False)
        StoreUserProfile.objects.create(user=target_user, current_balance=Decimal('-3.25'))

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('admin_user_delete', kwargs={'user_id': target_user.id}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        target_user.refresh_from_db()
        self.assertTrue(target_user.is_active)

    def test_admin_safe_delete_deactivates_user_without_removing_records(self):
        target_user = User.objects.create_user(username='safe-delete-user', password='testpass123', is_staff=False)
        StoreUserProfile.objects.create(user=target_user, current_balance=Decimal('0.00'))

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('admin_user_delete', kwargs={'user_id': target_user.id}),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        target_user.refresh_from_db()
        self.assertFalse(target_user.is_active)


@tag('scalability')
class ScalabilityWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='scale-admin', password='testpass123', is_staff=True)

    def test_admin_user_list_handles_5000_users(self):
        users = [
            User(username=f'scale-user-{index}', email=f'scale-{index}@example.com', is_staff=False)
            for index in range(TEST_SCALE_USERS_COUNT)
        ]
        User.objects.bulk_create(users, batch_size=TEST_BULK_BATCH_SIZE)

        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin_user_list'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['users'].count(), TEST_SCALE_USERS_COUNT + 1)

    def test_admin_monthly_fee_view_handles_5000_profiles(self):
        users = [
            User(username=f'monthly-user-{index}', email=f'monthly-{index}@example.com', is_staff=False)
            for index in range(TEST_SCALE_MONTHLY_USERS_COUNT)
        ]
        User.objects.bulk_create(users, batch_size=TEST_BULK_BATCH_SIZE)

        created_users = list(User.objects.filter(username__startswith='monthly-user-').only('id'))
        enabled_at = timezone.localdate() - timedelta(days=TEST_SCALE_MONTHLY_ENABLED_DAYS_AGO)
        profiles = [
            StoreUserProfile(
                user_id=user.id,
                monthly_fee_enabled=True,
                monthly_fee_enabled_at=enabled_at,
                current_balance=Decimal('0.00'),
            )
            for user in created_users
        ]
        StoreUserProfile.objects.bulk_create(profiles, batch_size=TEST_BULK_BATCH_SIZE)

        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin_monthly_fee'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['monthly_enabled_users'], TEST_SCALE_MONTHLY_USERS_COUNT)
        self.assertEqual(response.context['monthly_late_users_count'], TEST_SCALE_MONTHLY_USERS_COUNT)


@tag('security')
class SecurityAccessControlWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='security-admin', password='testpass123', is_staff=True)
        self.user = User.objects.create_user(username='security-user', password='testpass123', is_staff=False)
        self.other_user = User.objects.create_user(
            username='security-other-user',
            password='testpass123',
            is_staff=False,
        )
        self.product = Product.objects.create(
            name='Security product',
            sku='SEC-1',
            price='3.00',
            stock='15.00',
            is_active=True,
            is_public_listing=True,
        )

    def test_anonymous_user_is_redirected_from_admin_user_list(self):
        response = self.client.get(reverse('admin_user_list'))
        self.assertEqual(response.status_code, 302)

    def test_non_staff_cannot_approve_balance_requests(self):
        balance_request = BalanceRequest.objects.create(user=self.other_user, amount='8.00')
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('admin_balance_request_approve', kwargs={'request_id': balance_request.id}),
            {'next': 'admin_balance_requests'},
        )

        self.assertIn(response.status_code, {302, 403})
        balance_request.refresh_from_db()
        self.assertEqual(balance_request.status, BalanceRequest.Status.PENDING)
        self.assertEqual(BalanceLog.objects.count(), 0)

    def test_staff_cannot_use_non_staff_cart_endpoint(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse('user_cart_add'),
            {'product_id': str(self.product.id), 'purchase_mode': 'units', 'quantity': '1.00'},
        )

        self.assertIn(response.status_code, {302, 403})
        self.assertEqual(self.client.session.get('user_cart', {}), {})

    def test_user_cannot_open_other_user_order_detail(self):
        order = create_order(
            created_by=self.other_user,
            customer_name=self.other_user.username,
            items_data=[{'product': self.product, 'quantity': Decimal('1.00')}],
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('user_order_detail', kwargs={'pk': order.id}))
        self.assertEqual(response.status_code, 404)


@tag('stability')
class StabilityWorkflowWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='stability-admin', password='testpass123', is_staff=True)
        self.user = User.objects.create_user(username='stability-user', password='testpass123', is_staff=False)
        self.product = Product.objects.create(
            name='Stability product',
            sku='STB-1',
            price='2.50',
            stock='30.00',
            is_active=True,
            is_public_listing=True,
        )
        self.profile = StoreUserProfile.objects.create(user=self.user, current_balance='100.00')

    def test_approving_balance_request_twice_does_not_double_apply(self):
        balance_request = BalanceRequest.objects.create(user=self.user, amount='6.00')
        self.client.force_login(self.admin)

        self.client.post(
            reverse('admin_balance_request_approve', kwargs={'request_id': balance_request.id}),
            {'next': 'admin_balance_requests'},
        )
        self.client.post(
            reverse('admin_balance_request_approve', kwargs={'request_id': balance_request.id}),
            {'next': 'admin_balance_requests'},
        )

        self.profile.refresh_from_db()
        balance_request.refresh_from_db()
        self.assertEqual(balance_request.status, BalanceRequest.Status.APPROVED)
        self.assertEqual(self.profile.current_balance, Decimal('106.00'))
        self.assertEqual(BalanceLog.objects.filter(source=BalanceLog.Source.BALANCE_REQUEST_APPROVAL).count(), 1)

    def test_reject_after_approval_keeps_request_approved(self):
        balance_request = BalanceRequest.objects.create(user=self.user, amount='4.00')
        self.client.force_login(self.admin)

        self.client.post(
            reverse('admin_balance_request_approve', kwargs={'request_id': balance_request.id}),
            {'next': 'admin_balance_requests'},
        )
        self.client.post(
            reverse('admin_balance_request_reject', kwargs={'request_id': balance_request.id}),
            {'rejection_reason': 'Late proof', 'next': 'admin_balance_requests'},
        )

        balance_request.refresh_from_db()
        self.assertEqual(balance_request.status, BalanceRequest.Status.APPROVED)
        self.assertEqual(balance_request.rejection_reason, '')

    def test_review_repost_updates_single_record(self):
        order = create_order(
            created_by=self.user,
            customer_name=self.user.username,
            items_data=[{'product': self.product, 'quantity': Decimal('1.00')}],
        )
        approve_order(order=order, approved_by=self.admin)

        self.client.force_login(self.user)
        self.client.post(
            reverse('user_product_review', kwargs={'product_id': self.product.id}),
            {'rating': 5, 'message': 'First review'},
        )
        self.client.post(
            reverse('user_product_review', kwargs={'product_id': self.product.id}),
            {'rating': 3, 'message': 'Edited review'},
        )

        self.assertEqual(ProductReview.objects.filter(product=self.product, user=self.user).count(), 1)
        review = ProductReview.objects.get(product=self.product, user=self.user)
        self.assertEqual(review.rating, 3)
        self.assertEqual(review.message, 'Edited review')
        self.assertFalse(review.is_approved)

    def test_invalid_cart_update_removes_item_consistently(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse('user_cart_add'),
            {'product_id': str(self.product.id), 'purchase_mode': 'units', 'quantity': '2.00'},
        )
        self.assertIn(str(self.product.id), self.client.session.get('user_cart', {}))

        self.client.post(
            reverse('user_cart_update'),
            {'product_id': str(self.product.id), 'quantity': '0'},
        )

        self.assertNotIn(str(self.product.id), self.client.session.get('user_cart', {}))


class UserProductCatalogHighlightTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='catalog-admin-extra', password='testpass123', is_staff=True)
        self.user = User.objects.create_user(username='catalog-user-extra', password='testpass123', is_staff=False)
        self.other_user = User.objects.create_user(username='catalog-peer-extra', password='testpass123', is_staff=False)
        StoreUserProfile.objects.create(user=self.user, current_balance='150.00')

        self.included_category = Category.objects.create(name='Catalog included', include_in_untried=True)
        self.excluded_category = Category.objects.create(name='Catalog excluded', include_in_untried=False)

    def _make_product(self, *, name, sku, category, is_featured=False, is_new=False):
        return Product.objects.create(
            name=name,
            sku=sku,
            category=category,
            price='5.00',
            stock='40.00',
            is_active=True,
            is_public_listing=True,
            is_featured=is_featured,
            is_new=is_new,
        )

    def test_catalog_context_includes_featured_new_and_untried_category_rules(self):
        featured_product = self._make_product(
            name='Featured juice',
            sku='CAT-FEAT-1',
            category=self.included_category,
            is_featured=True,
        )
        new_product = self._make_product(
            name='New cereal',
            sku='CAT-NEW-1',
            category=self.included_category,
            is_new=True,
        )
        tried_product = self._make_product(
            name='Already tried snack',
            sku='CAT-TRIED-1',
            category=self.included_category,
        )
        excluded_product = self._make_product(
            name='Excluded category item',
            sku='CAT-EXCL-1',
            category=self.excluded_category,
        )

        order = create_order(
            created_by=self.user,
            customer_name=self.user.username,
            items_data=[{'product': tried_product, 'quantity': Decimal('1.00')}],
        )
        approve_order(order=order, approved_by=self.admin)

        self.client.force_login(self.user)
        response = self.client.get(reverse('user_products_catalog'))

        self.assertEqual(response.status_code, 200)
        featured_ids = {product.id for product in response.context['featured_products']}
        new_ids = {product.id for product in response.context['new_products']}
        untried_ids = {product.id for product in response.context['untried_products']}

        self.assertIn(featured_product.id, featured_ids)
        self.assertIn(new_product.id, new_ids)
        self.assertNotIn(tried_product.id, untried_ids)
        self.assertNotIn(excluded_product.id, untried_ids)
        self.assertTrue(response.context['has_untried_products'])

    def test_untried_products_order_uses_other_users_approved_ratings(self):
        top_rated = self._make_product(
            name='Top rated by others',
            sku='CAT-RANK-1',
            category=self.included_category,
        )
        low_rated = self._make_product(
            name='Low rated by others',
            sku='CAT-RANK-2',
            category=self.included_category,
        )

        ProductReview.objects.create(
            product=top_rated,
            user=self.other_user,
            rating=5,
            message='Excellent',
            is_approved=True,
        )
        ProductReview.objects.create(
            product=low_rated,
            user=self.other_user,
            rating=2,
            message='Poor',
            is_approved=True,
        )
        ProductReview.objects.create(
            product=low_rated,
            user=self.user,
            rating=5,
            message='Own review should not impact ordering',
            is_approved=True,
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('user_products_catalog'))

        self.assertEqual(response.status_code, 200)
        ordered_ids = [product.id for product in response.context['untried_products']]
        self.assertLess(ordered_ids.index(top_rated.id), ordered_ids.index(low_rated.id))


@tag('scalability')
class ScalabilityAdvancedWebTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='scale-admin-advanced', password='testpass123', is_staff=True)
        self.user = User.objects.create_user(username='scale-user-advanced', password='testpass123', is_staff=False)

    def test_balance_request_list_handles_3000_pending_requests(self):
        request_user = User.objects.create_user(username='requests-owner', password='testpass123', is_staff=False)
        requests = [
            BalanceRequest(user=request_user, amount=Decimal('1.00'))
            for _ in range(TEST_SCALE_PENDING_REQUESTS_COUNT)
        ]
        BalanceRequest.objects.bulk_create(requests, batch_size=TEST_BULK_BATCH_SIZE)

        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin_balance_requests'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['requests'].count(), TEST_SCALE_PENDING_REQUESTS_COUNT)
        self.assertEqual(response.context['pending_count'], TEST_SCALE_PENDING_REQUESTS_COUNT)

    def test_user_catalog_search_scales_with_3000_products(self):
        products = [
            Product(
                name=f'Catalog product {index}',
                sku=f'CAT-{index}',
                price=Decimal('1.50'),
                stock=Decimal('50.00'),
                is_active=True,
                is_public_listing=True,
            )
            for index in range(TEST_SCALE_CATALOG_PRODUCTS_COUNT)
        ]
        products.append(
            Product(
                name='Catalog special needle product',
                sku='CAT-SPECIAL',
                price=Decimal('2.00'),
                stock=Decimal('20.00'),
                is_active=True,
                is_public_listing=True,
            )
        )
        Product.objects.bulk_create(products, batch_size=TEST_BULK_BATCH_SIZE)

        self.client.force_login(self.user)
        response = self.client.get(reverse('user_products_catalog'), {'q': 'needle'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['products'].count(), 1)

    def test_admin_review_filter_scales_with_2000_reviews(self):
        product = Product.objects.create(
            name='Scalable reviewed product',
            sku='REV-SCALE',
            price='3.00',
            stock='100.00',
            is_active=True,
            is_public_listing=True,
        )

        review_users = [
            User(username=f'review-scale-user-{index}', is_staff=False)
            for index in range(TEST_SCALE_REVIEWS_COUNT)
        ]
        User.objects.bulk_create(review_users, batch_size=TEST_BULK_BATCH_SIZE)
        created_review_users = list(User.objects.filter(username__startswith='review-scale-user-').only('id'))

        reviews = [
            ProductReview(
                product=product,
                user=user,
                rating=(index % 5) + 1,
                message=f'Review message {index}',
                is_approved=False,
            )
            for index, user in enumerate(created_review_users)
        ]
        ProductReview.objects.bulk_create(reviews, batch_size=TEST_BULK_BATCH_SIZE)

        self.client.force_login(self.admin)
        response = self.client.get(reverse('admin_reviews'), {'status': 'pending', 'rating': '5'})

        self.assertEqual(response.status_code, 200)
        expected_five_star = sum(1 for index in range(TEST_SCALE_REVIEWS_COUNT) if (index % 5) + 1 == 5)
        self.assertEqual(response.context['reviews'].count(), expected_five_star)
