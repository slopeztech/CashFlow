from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class Notice(models.Model):
	class NoticeType(models.TextChoices):
		INFO = 'info', _('Info')
		SUCCESS = 'success', _('Success')
		WARNING = 'warning', _('Warning')
		DANGER = 'danger', _('Danger')

	title = models.CharField(max_length=160)
	description = models.TextField()
	notice_type = models.CharField(max_length=20, choices=NoticeType.choices, default=NoticeType.INFO)
	start_at = models.DateTimeField()
	end_at = models.DateTimeField()
	created_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='created_notices',
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-start_at', '-created_at']

	def __str__(self):
		return self.title

	@property
	def is_active(self):
		now = timezone.localtime()
		return self.start_at <= now <= self.end_at

	def clean(self):
		if self.start_at and self.end_at and self.end_at < self.start_at:
			raise ValidationError({'end_at': _('End date must be greater than or equal to start date.')})


class Event(models.Model):
	name = models.CharField(max_length=160)
	description = models.TextField(blank=True)
	links = models.TextField(blank=True)
	start_at = models.DateTimeField()
	end_at = models.DateTimeField()
	requires_registration = models.BooleanField(default=False)
	capacity = models.PositiveIntegerField(null=True, blank=True)
	is_paid_event = models.BooleanField(default=False)
	registration_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
	allow_companions = models.BooleanField(default=False)
	max_companions = models.PositiveIntegerField(null=True, blank=True)
	allow_negative_balance = models.BooleanField(default=False)
	created_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='created_events',
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['start_at', 'created_at']

	def __str__(self):
		return self.name

	@property
	def is_active(self):
		now = timezone.localtime()
		return self.start_at <= now <= self.end_at

	@property
	def links_list(self):
		if not self.links:
			return []
		return [line.strip() for line in self.links.splitlines() if line.strip()]

	@property
	def is_full(self):
		if not self.capacity:
			return False
		return self.total_registered_attendees >= self.capacity

	@property
	def total_registered_attendees(self):
		return sum(registration.total_attendees for registration in self.registrations.all())

	def clean(self):
		if self.start_at and self.end_at and self.end_at < self.start_at:
			raise ValidationError({'end_at': _('End date must be greater than or equal to start date.')})
		if self.capacity is not None and self.capacity <= 0:
			raise ValidationError({'capacity': _('Capacity must be greater than zero.')})
		if self.is_paid_event:
			if not self.requires_registration:
				raise ValidationError({'requires_registration': _('Paid events require registration.')})
			if self.registration_fee is None or self.registration_fee <= 0:
				raise ValidationError({'registration_fee': _('Fee amount must be greater than zero.')})

		if self.allow_companions:
			if not self.requires_registration:
				raise ValidationError({'allow_companions': _('Companions are only available for events with registration.')})
			if self.max_companions is None or self.max_companions <= 0:
				raise ValidationError({'max_companions': _('Set a maximum companions value greater than zero.')})
		else:
			self.max_companions = None

		if self.allow_negative_balance and not self.is_paid_event:
			self.allow_negative_balance = False


class EventImage(models.Model):
	event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='images')
	image = models.ImageField(upload_to='events/')
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['id']

	def __str__(self):
		return f'{self.event.name} image #{self.id}'


class EventRegistrationField(models.Model):
	class FieldType(models.TextChoices):
		NOTICE = 'notice', _('Notice text')
		SHORT_TEXT = 'short_text', _('Short text')
		LONG_TEXT = 'long_text', _('Long text')
		RADIO = 'radio', _('Radio options')
		SELECT = 'select', _('Select options')
		CHECKBOX = 'checkbox', _('Checkbox')

	event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='registration_fields')
	label = models.CharField(max_length=180)
	help_text = models.TextField(blank=True)
	field_type = models.CharField(max_length=20, choices=FieldType.choices, default=FieldType.SHORT_TEXT)
	options_text = models.TextField(blank=True)
	is_required = models.BooleanField(default=False)
	sort_order = models.PositiveIntegerField(default=0)
	is_active = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['sort_order', 'id']

	def __str__(self):
		return f'{self.event.name} | {self.label}'

	@property
	def options_list(self):
		if not self.options_text:
			return []
		return [line.strip() for line in self.options_text.splitlines() if line.strip()]

	def clean(self):
		if self.field_type == self.FieldType.NOTICE and self.is_required:
			raise ValidationError({'is_required': _('Notice fields cannot be required.')})

		requires_options = self.field_type in {self.FieldType.RADIO, self.FieldType.SELECT}
		has_options = bool(self.options_list)
		if requires_options and not has_options:
			raise ValidationError({'options_text': _('This field type requires at least one option.')})
		if not requires_options and self.options_text:
			self.options_text = ''


class EventRegistration(models.Model):
	event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='registrations')
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='event_registrations')
	answers = models.JSONField(default=dict, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']
		constraints = [
			models.UniqueConstraint(fields=['event', 'user'], name='unique_event_registration'),
		]

	def __str__(self):
		return f'{self.user.username} -> {self.event.name}'

	@property
	def companion_names(self):
		companions = (self.answers or {}).get('_companions')
		if not isinstance(companions, list):
			return []
		cleaned = []
		for companion_name in companions:
			name = str(companion_name).strip()
			if name:
				cleaned.append(name)
		return cleaned

	@property
	def companion_count(self):
		return len(self.companion_names)

	@property
	def total_attendees(self):
		return 1 + self.companion_count


class EventComment(models.Model):
	event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='comments')
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='event_comments')
	parent = models.ForeignKey(
		'self',
		on_delete=models.CASCADE,
		null=True,
		blank=True,
		related_name='replies',
	)
	content = models.TextField()
	is_ignored_by_admin = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['created_at', 'id']

	def __str__(self):
		return f'{self.user.username} | {self.event.name}'

	@property
	def is_admin_reply(self):
		return bool(self.parent_id and self.user.is_staff)

	def clean(self):
		if self.parent_id:
			if self.parent.event_id != self.event_id:
				raise ValidationError({'parent': _('Comment reply must belong to the same event.')})
			if self.parent.parent_id:
				raise ValidationError({'parent': _('Only one reply level is allowed.')})


class Survey(models.Model):
	class SelectionType(models.TextChoices):
		RADIO = 'radio', _('Single choice')
		CHECKBOX = 'checkbox', _('Multiple choice')

	title = models.CharField(max_length=180)
	description = models.TextField(blank=True)
	selection_type = models.CharField(max_length=20, choices=SelectionType.choices, default=SelectionType.RADIO)
	is_active = models.BooleanField(default=True)
	created_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='created_surveys',
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-created_at', '-id']

	def __str__(self):
		return self.title


class SurveyOption(models.Model):
	survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='options')
	label = models.CharField(max_length=180)
	sort_order = models.PositiveIntegerField(default=0)
	is_active = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['sort_order', 'id']

	def __str__(self):
		return f'{self.survey.title} | {self.label}'


class SurveyResponse(models.Model):
	survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='responses')
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='survey_responses')
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-created_at', '-id']
		constraints = [
			models.UniqueConstraint(fields=['survey', 'user'], name='unique_survey_response'),
		]

	def __str__(self):
		return f'{self.user.username} -> {self.survey.title}'

	def clean(self):
		if not self.survey_id:
			return
		selected_count = self.selected_options.count()
		if selected_count == 0:
			raise ValidationError(_('Please select at least one option.'))
		if self.survey.selection_type == Survey.SelectionType.RADIO and selected_count != 1:
			raise ValidationError(_('Single choice surveys require exactly one selected option.'))


class SurveyResponseOption(models.Model):
	response = models.ForeignKey(SurveyResponse, on_delete=models.CASCADE, related_name='selected_options')
	option = models.ForeignKey(SurveyOption, on_delete=models.CASCADE, related_name='response_links')
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['id']
		constraints = [
			models.UniqueConstraint(fields=['response', 'option'], name='unique_survey_response_option'),
		]

	def __str__(self):
		return f'{self.response.user.username} | {self.option.label}'

	def clean(self):
		if self.response_id and self.option_id and self.response.survey_id != self.option.survey_id:
			raise ValidationError({'option': _('Selected option must belong to the same survey.')})


class Gamification(models.Model):
	class GamificationType(models.TextChoices):
		APPROVED_REVIEWS = 'approved_reviews', _('Approved reviews count')
		DISTINCT_PRODUCTS_TRIED = 'distinct_products_tried', _('Distinct products tried')
		APPROVED_ORDERS = 'approved_orders', _('Approved orders count')

	title = models.CharField(max_length=160)
	description = models.TextField()
	reward = models.CharField(max_length=255)
	gamification_type = models.CharField(max_length=40, choices=GamificationType.choices)
	target_value = models.PositiveIntegerField()
	start_at = models.DateTimeField()
	end_at = models.DateTimeField()
	created_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='created_gamifications',
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-start_at', '-created_at']

	def __str__(self):
		return self.title

	@property
	def is_active(self):
		now = timezone.localtime()
		return self.start_at <= now <= self.end_at

	def clean(self):
		if self.start_at and self.end_at and self.end_at < self.start_at:
			raise ValidationError({'end_at': _('End date must be greater than or equal to start date.')})
		if self.target_value is not None and self.target_value <= 0:
			raise ValidationError({'target_value': _('Target value must be greater than zero.')})


class GamificationRewardCompletion(models.Model):
	gamification = models.ForeignKey(
		Gamification,
		on_delete=models.CASCADE,
		related_name='reward_completions',
	)
	user = models.ForeignKey(
		User,
		on_delete=models.CASCADE,
		related_name='gamification_reward_completions',
	)
	rewarded_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='rewarded_gamification_completions',
	)
	rewarded_at = models.DateTimeField(default=timezone.now)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-rewarded_at', '-created_at']
		constraints = [
			models.UniqueConstraint(
				fields=['gamification', 'user'],
				name='unique_gamification_reward_completion',
			),
		]

	def __str__(self):
		return f'{self.user.username} | {self.gamification.title}'


class Strike(models.Model):
	user = models.ForeignKey(
		User,
		on_delete=models.CASCADE,
		related_name='strikes',
	)
	strike_date = models.DateField(default=timezone.localdate)
	reason = models.CharField(max_length=255)
	created_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='created_strikes',
	)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-strike_date', '-created_at']

	def __str__(self):
		return f'{self.user.username} | {self.strike_date} | {self.reason}'


class UserSession(models.Model):
	user = models.ForeignKey(
		User,
		on_delete=models.CASCADE,
		related_name='tracked_sessions',
	)
	session_key = models.CharField(max_length=40)
	created_at = models.DateTimeField(auto_now_add=True)
	last_activity = models.DateTimeField(default=timezone.now)

	class Meta:
		ordering = ['-last_activity']
		constraints = [
			models.UniqueConstraint(fields=['user', 'session_key'], name='unique_user_session_key'),
		]
		indexes = [
			models.Index(fields=['last_activity']),
		]

	def __str__(self):
		return f'{self.user.username} | {self.last_activity}'


class SystemSettings(models.Model):
	store_name = models.CharField(max_length=120, default='CashFlow')
	brand_color_primary = models.CharField(max_length=7, default='#111827')
	brand_color_secondary = models.CharField(max_length=7, default='#5E8DF5')
	footer_signature = models.CharField(max_length=255, default='StarAdmin2 integrated template')
	app_time_zone = models.CharField(max_length=64, default='UTC')
	live_mode_enabled = models.BooleanField(default=True)
	updated_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='updated_system_settings',
	)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		verbose_name = 'System settings'
		verbose_name_plural = 'System settings'

	def __str__(self):
		return self.store_name


class SystemTestRun(models.Model):
	class TestType(models.TextChoices):
		IO_RW = 'io_rw', _('Read/write test')
		DB = 'db', _('Database test')
		REQUIREMENTS = 'requirements', _('System requirements test')
		DATA_QUALITY = 'data_quality', _('Data quality test')

	class Status(models.TextChoices):
		SUCCESS = 'success', _('Success')
		FAIL = 'fail', _('Fail')
		SKIPPED = 'skipped', _('Skipped')

	test_type = models.CharField(max_length=32, choices=TestType.choices)
	supported = models.BooleanField(default=True)
	status = models.CharField(max_length=16, choices=Status.choices)
	duration_ms = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
	summary = models.CharField(max_length=255, blank=True)
	details = models.JSONField(default=dict, blank=True)
	executed_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='system_test_runs',
	)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at', '-id']
		indexes = [
			models.Index(fields=['test_type', 'created_at']),
		]

	def __str__(self):
		return f'{self.test_type} | {self.status} | {self.created_at}'
