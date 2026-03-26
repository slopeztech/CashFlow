from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _


class MonthlyFeeSettings(models.Model):
	monthly_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	is_active = models.BooleanField(default=True)
	updated_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='updated_monthly_fee_settings',
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Monthly fee settings'
		verbose_name_plural = 'Monthly fee settings'

	def __str__(self):
		return f'Monthly fee: {self.monthly_amount}'


class StoreUserProfile(models.Model):
	class Language(models.TextChoices):
		ENGLISH = 'en', 'English'
		SPANISH = 'es', 'Español'

	user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='store_profile')
	current_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	phone = models.CharField(max_length=20, blank=True)
	address = models.CharField(max_length=255, blank=True)
	member_number = models.CharField(max_length=50, blank=True, null=True, unique=True)
	display_name = models.CharField(max_length=80, blank=True)
	profile_image = models.FileField(upload_to='profile_images/', blank=True, null=True)
	language = models.CharField(max_length=5, choices=Language.choices, default=Language.ENGLISH)
	monthly_fee_enabled = models.BooleanField(default=False)
	monthly_fee_enabled_at = models.DateField(null=True, blank=True)
	monthly_fee_last_charged_month = models.DateField(null=True, blank=True)
	show_all_recent_movements = models.BooleanField(default=True)
	recent_movements_limit = models.PositiveIntegerField(null=True, blank=True)
	password_change_required = models.BooleanField(default=True)
	temporary_access_code_plain = models.CharField(max_length=8, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'Store profile'
		verbose_name_plural = 'Store profiles'

	def __str__(self):
		return f"Profile for {self.user.username}"


class BalanceRequest(models.Model):
	class Status(models.TextChoices):
		PENDING = 'pending', 'Pending'
		APPROVED = 'approved', 'Approved'
		REJECTED = 'rejected', 'Rejected'

	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='balance_requests')
	amount = models.DecimalField(max_digits=10, decimal_places=2)
	status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
	reviewed_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='reviewed_balance_requests',
	)
	reviewed_at = models.DateTimeField(null=True, blank=True)
	rejection_reason = models.CharField(max_length=255, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f"Balance request #{self.id} by {self.user.username}"


class BalanceLog(models.Model):
	class Source(models.TextChoices):
		MANUAL_ADJUSTMENT = 'manual_adjustment', _('Manual adjustment')
		BALANCE_REQUEST_APPROVAL = 'balance_request_approval', _('Balance request approval')
		ORDER_APPROVAL = 'order_approval', _('Order approval')
		MONTHLY_FEE = 'monthly_fee', _('Monthly fee charge')
		EVENT_REGISTRATION_CHARGE = 'event_registration_charge', _('Event registration charge')
		EVENT_REGISTRATION_REFUND = 'event_registration_refund', _('Event registration refund')

	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='balance_logs')
	changed_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='performed_balance_logs',
	)
	source = models.CharField(max_length=50, choices=Source.choices)
	amount_delta = models.DecimalField(max_digits=10, decimal_places=2)
	balance_before = models.DecimalField(max_digits=10, decimal_places=2)
	balance_after = models.DecimalField(max_digits=10, decimal_places=2)
	note = models.CharField(max_length=255, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f"Balance log #{self.id} for {self.user.username}"
