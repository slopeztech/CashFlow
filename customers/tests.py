from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from customers.models import BalanceLog, MonthlyFeeSettings, StoreUserProfile
from customers.services import months_due_for_profile, process_monthly_fee_for_user


def _next_month(value: date) -> date:
	if value.month == 12:
		return date(value.year + 1, 1, 1)
	return date(value.year, value.month + 1, 1)


class MonthlyFeeBillingRulesTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username='monthly_user', password='testpass123')
		self.profile, _created = StoreUserProfile.objects.get_or_create(user=self.user)
		self.profile.monthly_fee_enabled = True
		self.profile.current_balance = Decimal('0.00')

		MonthlyFeeSettings.objects.create(monthly_amount=Decimal('10.00'), is_active=True)

	def test_newly_enabled_monthly_fee_is_not_due_in_same_month(self):
		as_of = date(2026, 3, 16)
		self.profile.monthly_fee_enabled_at = as_of
		self.profile.monthly_fee_last_charged_month = None
		self.profile.save(
			update_fields=[
				'monthly_fee_enabled',
				'monthly_fee_enabled_at',
				'monthly_fee_last_charged_month',
				'current_balance',
				'updated_at',
			]
		)

		self.assertEqual(months_due_for_profile(self.profile, as_of=as_of), 0)

	def test_monthly_fee_charges_starting_next_month(self):
		enabled_at = date(2026, 3, 16)
		same_month = date(2026, 3, 20)
		next_month = _next_month(date(enabled_at.year, enabled_at.month, 1))

		self.profile.monthly_fee_enabled_at = enabled_at
		self.profile.monthly_fee_last_charged_month = None
		self.profile.save(
			update_fields=[
				'monthly_fee_enabled',
				'monthly_fee_enabled_at',
				'monthly_fee_last_charged_month',
				'current_balance',
				'updated_at',
			]
		)

		charged_now = process_monthly_fee_for_user(self.user, as_of=same_month)
		self.assertEqual(charged_now, 0)

		self.profile.refresh_from_db()
		self.assertEqual(self.profile.current_balance, Decimal('0.00'))

		charged_next_month = process_monthly_fee_for_user(self.user, as_of=next_month)
		self.assertEqual(charged_next_month, 1)

		self.profile.refresh_from_db()
		self.assertEqual(self.profile.current_balance, Decimal('-10.00'))
		self.assertEqual(self.profile.monthly_fee_last_charged_month, next_month)
		self.assertTrue(
			BalanceLog.objects.filter(
				user=self.user,
				source=BalanceLog.Source.MONTHLY_FEE,
				amount_delta=Decimal('-10.00'),
			).exists()
		)
