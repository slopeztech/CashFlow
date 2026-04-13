import secrets
import string
import re
import logging
from decimal import Decimal
from zoneinfo import available_timezones

from PIL import Image, UnidentifiedImageError
from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, Value, When
from django.forms import inlineformset_factory
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import (
    Asset,
    Event,
    EventComment,
    EventRegistrationField,
    Gamification,
    Notice,
    Strike,
    Survey,
    SurveyOption,
    SystemSettings,
)
from customers.models import BalanceRequest, MonthlyFeeSettings, StoreUserProfile
from inventory.models import Category, Product, ProductReview, ProductSheetField, ProductSheetUrl, Supplier, Tag
from sales.models import Order, OrderItem, Sale, SaleItem
from core.image_processing import optimize_uploaded_image


backendlog = logging.getLogger('backendlog')

_all_time_zones = sorted(available_timezones())
PREFERRED_TIME_ZONES = [
    'UTC',
    'Europe/Madrid',
    'Atlantic/Canary',
    'America/Mexico_City',
    'America/Bogota',
    'America/Lima',
    'America/Santiago',
    'America/Argentina/Buenos_Aires',
    'America/Caracas',
]
_preferred_time_zones = [tz for tz in PREFERRED_TIME_ZONES if tz in _all_time_zones]
_remaining_time_zones = [tz for tz in _all_time_zones if tz not in _preferred_time_zones]
SYSTEM_TIME_ZONE_CHOICES = [(tz, tz) for tz in _preferred_time_zones + _remaining_time_zones]


def _ordered_products_queryset(queryset):
    return queryset.annotate(
        featured_first=Case(
            When(is_featured=True, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
        has_manual_order=Case(
            When(display_order__gt=0, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        ),
    ).order_by(
        'category__display_order',
        'category__name',
        'featured_first',
        'has_manual_order',
        'display_order',
        'name',
    )


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        single_file_clean = super().clean

        if data in (None, '', []):
            return []

        if isinstance(data, (list, tuple)):
            return [single_file_clean(uploaded_file, initial) for uploaded_file in data]

        return [single_file_clean(data, initial)]


def _validate_uploaded_image(uploaded_file, *, max_size_bytes, invalid_size_message, invalid_type_message):
    if uploaded_file.size > max_size_bytes:
        raise ValidationError(invalid_size_message)

    allowed_types = {'image/jpeg', 'image/png', 'image/webp'}
    content_type = getattr(uploaded_file, 'content_type', '')
    if content_type and content_type not in allowed_types:
        raise ValidationError(invalid_type_message)

    try:
        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError(invalid_type_message)
    finally:
        uploaded_file.seek(0)


class CashFlowAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(
            attrs={'class': 'form-control', 'placeholder': _('Username')}
        )
    )
    password = forms.CharField(
        widget=forms.PasswordInput(
            attrs={'class': 'form-control', 'placeholder': _('Password')}
        )
    )


class ProductForm(forms.ModelForm):
    new_images = MultipleFileField(
        required=False,
        widget=MultipleFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        label=_('Product images'),
    )

    class Meta:
        model = Product
        fields = [
            'name',
            'sku',
            'category',
            'supplier',
            'tags',
            'description',
            'price',
            'stock',
            'min_stock',
            'unit_type',
            'measure_label',
            'purchase_options',
            'display_order',
            'is_public_listing',
            'is_active',
            'is_featured',
            'is_new',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'sku': forms.TextInput(attrs={'class': 'form-control'}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'supplier': forms.Select(attrs={'class': 'form-select'}),
            'tags': forms.SelectMultiple(attrs={'class': 'form-select', 'size': 6}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'stock': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0}),
            'min_stock': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0}),
            'unit_type': forms.Select(attrs={'class': 'form-select'}),
            'measure_label': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g. grams'}),

            'purchase_options': forms.Select(attrs={'class': 'form-select'}),
            'display_order': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'step': 1}),
            'is_public_listing': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_featured': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_new': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = Category.objects.order_by('name')
        self.fields['supplier'].queryset = Supplier.objects.order_by('name')
        self.fields['tags'].queryset = Tag.objects.order_by('name')
        self.fields['sku'].required = False
        self.fields['category'].required = True
        self.fields['supplier'].required = False
        self.fields['tags'].required = False
        self.fields['min_stock'].required = False
        self.fields['purchase_options'].required = False
        self.fields['display_order'].required = False
        self.fields['purchase_options'].initial = Product.PurchaseOptions.BOTH

    def clean_sku(self):
        sku = (self.cleaned_data.get('sku') or '').strip()
        if sku:
            return sku

        # Keep existing SKU on updates if the field is submitted empty.
        if self.instance and self.instance.pk and self.instance.sku:
            return self.instance.sku

        name = (self.cleaned_data.get('name') or '').strip()
        base = re.sub(r'[^A-Z0-9]+', '-', name.upper()).strip('-')[:24] or 'SKU'
        candidate = f'{base}-{secrets.token_hex(3).upper()}'
        while Product.objects.filter(sku=candidate).exists():
            candidate = f'{base}-{secrets.token_hex(3).upper()}'
        return candidate

    def clean_min_stock(self):
        min_stock = self.cleaned_data.get('min_stock')

        if min_stock is None:
            return Decimal('0.00')
        return min_stock

    def clean_measure_label(self):
        unit_type = self.cleaned_data.get('unit_type')
        measure_label = (self.cleaned_data.get('measure_label') or '').strip()
        if unit_type == Product.UnitType.MEASURE and not measure_label:
            raise forms.ValidationError(_('Measure label is required when unit type is measure.'))
        if unit_type == Product.UnitType.UNITS:
            return ''
        return measure_label

    def clean_new_images(self):
        files = self.files.getlist('new_images')
        if not files:
            return None

        for image_file in files:
            _validate_uploaded_image(
                image_file,
                max_size_bytes=8 * 1024 * 1024,
                invalid_size_message=_('Each image must be 8MB or smaller.'),
                invalid_type_message=_('Images must be JPG, PNG, or WEBP.'),
            )
        return None

    def clean_purchase_options(self):
        return self.cleaned_data.get('purchase_options') or Product.PurchaseOptions.BOTH

    def clean_display_order(self):
        display_order = self.cleaned_data.get('display_order')
        if display_order is None:
            return 0
        return display_order


class ProductPurchaseOptionsForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['purchase_options']
        widgets = {
            'purchase_options': forms.Select(attrs={'class': 'form-select'}),
        }


class ProductSheetFieldForm(forms.ModelForm):
    class Meta:
        model = ProductSheetField
        fields = ['field_key', 'field_value']
        widgets = {
            'field_key': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Key')}),
            'field_value': forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Value')}),
        }

    def clean_field_key(self):
        value = (self.cleaned_data.get('field_key') or '').strip()
        if not value:
            raise forms.ValidationError(_('Key is required.'))
        return value

    def clean_field_value(self):
        value = (self.cleaned_data.get('field_value') or '').strip()
        if not value:
            raise forms.ValidationError(_('Value is required.'))
        return value


class ProductSheetUrlForm(forms.ModelForm):
    class Meta:
        model = ProductSheetUrl
        fields = ['url']
        widgets = {
            'url': forms.URLInput(attrs={'class': 'form-control', 'placeholder': 'https://example.com'}),
        }

    def clean_url(self):
        value = (self.cleaned_data.get('url') or '').strip()
        if not value:
            raise forms.ValidationError(_('URL is required.'))
        return value


class SaleForm(forms.ModelForm):
    customer = forms.ModelChoiceField(
        queryset=User.objects.filter(is_staff=False).order_by('username'),
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    class Meta:
        model = Sale
        fields = ['customer']


class SaleItemForm(forms.ModelForm):
    display_unit_price = forms.DecimalField(
        required=False,
        decimal_places=2,
        max_digits=10,
        label=_('Unit price'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'}),
    )

    class Meta:
        model = SaleItem
        fields = ['product', 'quantity', 'is_gift']
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '0.01', 'step': '0.01'}),
            'is_gift': forms.CheckboxInput(attrs={'class': 'form-check-input d-none'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].required = False
        self.fields['is_gift'].required = False
        self.fields['product'].queryset = _ordered_products_queryset(
            Product.objects.filter(is_active=True)
        )
        self.fields['product'].label_from_instance = (
            lambda obj: f'{obj.category.name if obj.category else "Uncategorized"} | {obj.name} - € {obj.price}'
        )
        if self.instance and self.instance.pk:
            self.fields['display_unit_price'].initial = self.instance.unit_price
        else:
            product = self.initial.get('product')
            if product:
                self.fields['display_unit_price'].initial = product.price
        if 'DELETE' in self.fields:
            self.fields['DELETE'].widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product')
        quantity = cleaned_data.get('quantity')

        # Allow extra empty rows created by the dynamic composer UI.
        if not product:
            cleaned_data['quantity'] = None
            return cleaned_data

        if quantity is None or quantity <= 0:
            self.add_error('quantity', _('Quantity must be greater than zero.'))

        return cleaned_data


SaleItemFormSet = inlineformset_factory(
    Sale,
    SaleItem,
    form=SaleItemForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class StoreUserProfileForm(forms.ModelForm):
    class Meta:
        model = StoreUserProfile
        fields = ['member_number', 'display_name', 'profile_image', 'language']
        widgets = {
            'member_number': forms.TextInput(attrs={'class': 'form-control'}),
            'display_name': forms.TextInput(attrs={'class': 'form-control'}),
            'profile_image': forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'language': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)
        self.fields['member_number'].disabled = True
        self.fields['member_number'].widget.attrs['readonly'] = 'readonly'

    def save(self, commit=True):
        previous_image_name = ''
        if self.instance.pk:
            previous_profile = StoreUserProfile.objects.filter(pk=self.instance.pk).only('profile_image').first()
            if previous_profile and previous_profile.profile_image:
                previous_image_name = previous_profile.profile_image.name

        profile = super().save(commit=False)
        if commit:
            profile.save()

            new_image = self.cleaned_data.get('profile_image')
            if new_image and previous_image_name and previous_image_name != profile.profile_image.name:
                try:
                    profile.profile_image.storage.delete(previous_image_name)
                except (PermissionError, OSError) as exc:
                    # On Windows the old file can remain locked briefly by another process.
                    backendlog.warning('Could not delete previous profile image %s: %s', previous_image_name, exc)

        return profile

    def clean_member_number(self):
        # Member number is immutable for users from the profile page.
        return self.instance.member_number

    def clean_display_name(self):
        return (self.cleaned_data.get('display_name') or '').strip()

    def clean_profile_image(self):
        profile_image = self.cleaned_data.get('profile_image')
        if not profile_image:
            return profile_image

        _validate_uploaded_image(
            profile_image,
            max_size_bytes=6 * 1024 * 1024,
            invalid_size_message=_('Profile image must be 6MB or smaller.'),
            invalid_type_message=_('Profile image must be JPG, PNG, or WEBP.'),
        )
        return optimize_uploaded_image(profile_image, crop_size=(600, 600), max_bytes=512 * 1024)


class StoreUserPasswordChangeForm(PasswordChangeForm):
    old_password = forms.CharField(
        label=_('Current password'),
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'current-password'}),
    )
    new_password1 = forms.CharField(
        label=_('New password'),
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
    )
    new_password2 = forms.CharField(
        label=_('Confirm new password'),
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
    )

    def __init__(self, *args, **kwargs):
        require_old_password = kwargs.pop('require_old_password', True)
        super().__init__(*args, **kwargs)
        if not require_old_password:
            self.fields.pop('old_password', None)


class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = []


class OrderItemForm(forms.ModelForm):
    display_unit_price = forms.DecimalField(
        required=False,
        decimal_places=2,
        max_digits=10,
        label=_('Unit price'),
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'readonly': 'readonly'}),
    )

    class Meta:
        model = OrderItem
        fields = ['product', 'quantity', 'is_gift']
        widgets = {
            'product': forms.Select(attrs={'class': 'form-select'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': '0.01', 'step': '0.01'}),
            'is_gift': forms.CheckboxInput(attrs={'class': 'form-check-input d-none'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['is_gift'].required = False
        self.fields['product'].queryset = _ordered_products_queryset(
            Product.objects.filter(
                is_active=True,
                is_public_listing=True,
            )
        )
        self.fields['product'].label_from_instance = (
            lambda obj: f'{obj.category.name if obj.category else "Uncategorized"} | {obj.name} - € {obj.price}'
        )
        if self.instance and self.instance.pk:
            self.fields['display_unit_price'].initial = self.instance.unit_price
        else:
            product = self.initial.get('product')
            if product:
                self.fields['display_unit_price'].initial = product.price
        if 'DELETE' in self.fields:
            self.fields['DELETE'].widget = forms.HiddenInput()


OrderItemFormSet = inlineformset_factory(
    Order,
    OrderItem,
    form=OrderItemForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


def generate_temporary_access_code(length=8):
    alphabet = string.ascii_lowercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


class StaffUserCreateForm(forms.ModelForm):
    member_number = forms.CharField(
        required=False,
        max_length=50,
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
    )
    display_name = forms.CharField(
        required=False,
        max_length=80,
        widget=forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'off'}),
    )
    is_staff = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    temporary_access_code = forms.CharField(
        label=_('Temporary access code'),
        min_length=8,
        max_length=8,
        widget=forms.TextInput(
            attrs={
                'class': 'form-control font-monospace',
                'readonly': 'readonly',
                'autocomplete': 'off',
                'spellcheck': 'false',
            }
        ),
    )
    language = forms.ChoiceField(
        choices=StoreUserProfile.Language.choices,
        initial=StoreUserProfile.Language.ENGLISH,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    monthly_fee_enabled = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    recent_movements_limit = forms.IntegerField(
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
    )

    class Meta:
        model = User
        fields = [
            'username',
            'member_number',
            'display_name',
            'is_staff',
            'temporary_access_code',
            'language',
            'monthly_fee_enabled',
            'recent_movements_limit',
        ]
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control', 'autocomplete': 'username'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound:
            posted_code = (self.data.get('temporary_access_code') or '').strip()
            self.fields['temporary_access_code'].initial = posted_code or generate_temporary_access_code()
        else:
            self.fields['temporary_access_code'].initial = generate_temporary_access_code()

    def clean_temporary_access_code(self):
        value = (self.cleaned_data.get('temporary_access_code') or '').strip().lower()
        if not re.fullmatch(r'[a-z0-9]{8}', value):
            raise forms.ValidationError(
                _('Temporary access code must have 8 characters using lowercase letters and numbers.')
            )
        return value

    def clean_member_number(self):
        value = (self.cleaned_data.get('member_number') or '').strip()
        if not value:
            return None
        exists = StoreUserProfile.objects.filter(member_number=value).exists()
        if exists:
            raise forms.ValidationError(_('A user with that member number already exists.'))
        return value

    def clean(self):
        cleaned_data = super().clean()
        limit = cleaned_data.get('recent_movements_limit')
        if not limit or limit <= 0:
            cleaned_data['recent_movements_limit'] = None
        return cleaned_data


class AdminUserUpdateForm(forms.Form):
    username = forms.CharField(widget=forms.TextInput(attrs={'class': 'form-control'}))
    member_number = forms.CharField(
        required=False,
        max_length=50,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    display_name = forms.CharField(
        required=False,
        max_length=80,
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    is_staff = forms.BooleanField(required=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}))
    phone = forms.CharField(required=False, max_length=20, widget=forms.TextInput(attrs={'class': 'form-control'}))
    address = forms.CharField(required=False, max_length=255, widget=forms.TextInput(attrs={'class': 'form-control'}))
    language = forms.ChoiceField(
        choices=StoreUserProfile.Language.choices,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    monthly_fee_enabled = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    recent_movements_limit = forms.IntegerField(
        required=False,
        min_value=0,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
    )

    def __init__(self, *args, **kwargs):
        self.user_instance = kwargs.pop('user_instance')
        super().__init__(*args, **kwargs)

        profile, _ = StoreUserProfile.objects.get_or_create(user=self.user_instance)
        self.fields['username'].initial = self.user_instance.username
        self.fields['member_number'].initial = profile.member_number or ''
        self.fields['display_name'].initial = profile.display_name
        self.fields['is_staff'].initial = self.user_instance.is_staff
        self.fields['phone'].initial = profile.phone
        self.fields['address'].initial = profile.address
        self.fields['language'].initial = profile.language
        self.fields['monthly_fee_enabled'].initial = profile.monthly_fee_enabled
        self.fields['recent_movements_limit'].initial = profile.recent_movements_limit

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        exists = User.objects.filter(username=username).exclude(id=self.user_instance.id).exists()
        if exists:
            raise forms.ValidationError(_('A user with that username already exists.'))
        return username

    def clean(self):
        cleaned_data = super().clean()
        limit = cleaned_data.get('recent_movements_limit')
        if not limit or limit <= 0:
            cleaned_data['recent_movements_limit'] = None
        return cleaned_data

    def clean_member_number(self):
        value = (self.cleaned_data.get('member_number') or '').strip()
        if not value:
            return None
        exists = StoreUserProfile.objects.filter(member_number=value).exclude(user=self.user_instance).exists()
        if exists:
            raise forms.ValidationError(_('A user with that member number already exists.'))
        return value

    def save(self):
        profile, _ = StoreUserProfile.objects.get_or_create(user=self.user_instance)
        self.user_instance.username = self.cleaned_data['username']
        self.user_instance.is_staff = self.cleaned_data.get('is_staff', False)
        self.user_instance.save(update_fields=['username', 'is_staff'])

        profile.member_number = self.cleaned_data.get('member_number')
        profile.display_name = (self.cleaned_data.get('display_name') or '').strip()
        profile.phone = self.cleaned_data.get('phone', '')
        profile.address = self.cleaned_data.get('address', '')
        profile.language = self.cleaned_data.get('language', StoreUserProfile.Language.ENGLISH)
        previous_enabled = profile.monthly_fee_enabled
        profile.monthly_fee_enabled = self.cleaned_data.get('monthly_fee_enabled', False)
        profile.recent_movements_limit = self.cleaned_data.get('recent_movements_limit')
        profile.show_all_recent_movements = profile.recent_movements_limit is None
        if profile.monthly_fee_enabled and not previous_enabled:
            profile.monthly_fee_enabled_at = timezone.localdate()
            profile.monthly_fee_last_charged_month = None
        profile.save(
            update_fields=[
                'phone',
                'address',
                'member_number',
                'display_name',
                'language',
                'monthly_fee_enabled',
                'monthly_fee_enabled_at',
                'monthly_fee_last_charged_month',
                'show_all_recent_movements',
                'recent_movements_limit',
                'updated_at',
            ]
        )
        return self.user_instance


class OrderRejectForm(forms.Form):
    rejection_reason = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': _('Optional reason')}),
    )


class ProductReviewForm(forms.ModelForm):
    class Meta:
        model = ProductReview
        fields = ['rating', 'message']
        widgets = {
            'rating': forms.Select(
                choices=[(1, '1 ★'), (2, '2 ★★'), (3, '3 ★★★'), (4, '4 ★★★★'), (5, '5 ★★★★★')],
                attrs={'class': 'form-select'},
            ),
            'message': forms.Textarea(
                attrs={
                    'class': 'form-control',
                    'rows': 6,
                    'placeholder': _('Share your experience'),
                }
            ),
        }


class BalanceRequestForm(forms.ModelForm):
    class Meta:
        model = BalanceRequest
        fields = ['amount']
        widgets = {
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0.01'}),
        }

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if amount <= 0:
            raise forms.ValidationError(_('Amount must be greater than zero.'))
        return amount


class AdminBalanceAdjustmentForm(forms.Form):
    user_id = forms.IntegerField(widget=forms.HiddenInput())
    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text=_('Use positive amount to add balance and negative amount to withdraw.'),
    )


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = [
            'name',
            'description',
            'display_order',
            'include_in_untried',
            'allow_user_ratings',
            'default_expanded',
            'image',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'display_order': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'step': 1}),
            'include_in_untried': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'allow_user_ratings': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'default_expanded': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'image': forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        }


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ['name', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class NoticeForm(forms.ModelForm):
    class Meta:
        model = Notice
        fields = ['title', 'description', 'notice_type', 'start_at', 'end_at']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'notice_type': forms.Select(attrs={'class': 'form-select'}),
            'start_at': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'end_at': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['start_at'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['end_at'].input_formats = ['%Y-%m-%dT%H:%M']

    def clean(self):
        cleaned_data = super().clean()
        start_at = cleaned_data.get('start_at')
        end_at = cleaned_data.get('end_at')
        if start_at and end_at and end_at < start_at:
            self.add_error('end_at', _('End date must be greater than or equal to start date.'))
        return cleaned_data


class EventForm(forms.ModelForm):
    new_images = MultipleFileField(
        required=False,
        widget=MultipleFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        label=_('Event photos'),
    )

    class Meta:
        model = Event
        fields = [
            'name',
            'start_at',
            'end_at',
            'description',
            'links',
            'requires_registration',
            'capacity',
            'allow_companions',
            'max_companions',
            'is_paid_event',
            'registration_fee',
            'allow_negative_balance',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'start_at': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'end_at': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'links': forms.Textarea(
                attrs={
                    'class': 'form-control',
                    'rows': 3,
                    'placeholder': _('One URL per line'),
                }
            ),
            'requires_registration': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'capacity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'allow_companions': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'max_companions': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'is_paid_event': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'registration_fee': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0}),
            'allow_negative_balance': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['start_at'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['end_at'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['capacity'].required = False
        self.fields['max_companions'].required = False
        self.fields['registration_fee'].required = False

    def clean_capacity(self):
        capacity = self.cleaned_data.get('capacity')
        requires_registration = self.cleaned_data.get('requires_registration')
        if not requires_registration:
            return None
        if capacity is not None and capacity <= 0:
            raise forms.ValidationError(_('Capacity must be greater than zero.'))
        return capacity

    def clean(self):
        cleaned_data = super().clean()
        start_at = cleaned_data.get('start_at')
        end_at = cleaned_data.get('end_at')
        is_paid_event = cleaned_data.get('is_paid_event')
        requires_registration = cleaned_data.get('requires_registration')
        registration_fee = cleaned_data.get('registration_fee')
        allow_companions = cleaned_data.get('allow_companions')
        max_companions = cleaned_data.get('max_companions')
        allow_negative_balance = cleaned_data.get('allow_negative_balance')
        if start_at and end_at and end_at < start_at:
            self.add_error('end_at', _('End date must be greater than or equal to start date.'))
        if is_paid_event:
            if not requires_registration:
                self.add_error('requires_registration', _('Paid events require registration.'))
            if registration_fee is None or registration_fee <= 0:
                self.add_error('registration_fee', _('Fee amount must be greater than zero.'))
        elif not registration_fee:
            cleaned_data['registration_fee'] = 0

        if allow_companions:
            if not requires_registration:
                self.add_error('allow_companions', _('Companions are only available for events with registration.'))
            if max_companions is None or max_companions <= 0:
                self.add_error('max_companions', _('Set a maximum companions value greater than zero.'))
        else:
            cleaned_data['max_companions'] = None

        if allow_negative_balance and not is_paid_event:
            cleaned_data['allow_negative_balance'] = False
        return cleaned_data

    def clean_new_images(self):
        files = self.files.getlist('new_images')
        if not files:
            return None

        for image_file in files:
            _validate_uploaded_image(
                image_file,
                max_size_bytes=8 * 1024 * 1024,
                invalid_size_message=_('Each event image must be 8MB or smaller.'),
                invalid_type_message=_('Event images must be JPG, PNG, or WEBP.'),
            )
        return None


class EventRegistrationFieldForm(forms.ModelForm):
    class Meta:
        model = EventRegistrationField
        fields = [
            'label',
            'help_text',
            'field_type',
            'options_text',
            'is_required',
            'sort_order',
            'is_active',
        ]
        labels = {
            'label': _('Label'),
            'help_text': _('Help text'),
            'field_type': _('Type'),
            'options_text': _('Options'),
            'is_required': _('Required'),
            'sort_order': _('Order'),
            'is_active': _('Active'),
        }
        widgets = {
            'label': forms.TextInput(attrs={'class': 'form-control'}),
            'help_text': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
            'field_type': forms.Select(attrs={'class': 'form-select'}),
            'options_text': forms.Textarea(
                attrs={
                    'class': 'form-control',
                    'rows': 2,
                    'placeholder': _('One option per line'),
                }
            ),
            'is_required': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'sort_order': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        field_type = cleaned_data.get('field_type')
        options_text = cleaned_data.get('options_text') or ''
        is_required = cleaned_data.get('is_required')

        if field_type == EventRegistrationField.FieldType.NOTICE and is_required:
            self.add_error('is_required', _('Notice fields cannot be required.'))

        requires_options = field_type in {
            EventRegistrationField.FieldType.RADIO,
            EventRegistrationField.FieldType.SELECT,
        }
        has_options = any(line.strip() for line in options_text.splitlines())
        if requires_options and not has_options:
            self.add_error('options_text', _('This field type requires at least one option.'))
        if not requires_options:
            cleaned_data['options_text'] = ''

        return cleaned_data


class AssetForm(forms.ModelForm):
    new_images = MultipleFileField(
        required=False,
        widget=MultipleFileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
        label=_('Fotos del activo'),
    )

    class Meta:
        model = Asset
        fields = [
            'name',
            'description',
            'pricing_mode',
            'price_total',
            'price_per_hour',
            'allow_negative_balance',
            'refund_hours_threshold',
            'quantity',
            'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'pricing_mode': forms.Select(attrs={'class': 'form-select'}),
            'price_total': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0}),
            'price_per_hour': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0}),
            'allow_negative_balance': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'refund_hours_threshold': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_new_images(self):
        files = self.files.getlist('new_images')
        if not files:
            return None

        for image_file in files:
            _validate_uploaded_image(
                image_file,
                max_size_bytes=8 * 1024 * 1024,
                invalid_size_message=_('Cada imagen del activo debe pesar 8MB o menos.'),
                invalid_type_message=_('Las imágenes del activo deben ser JPG, PNG o WEBP.'),
            )
        return None

    def clean(self):
        cleaned_data = super().clean()
        pricing_mode = cleaned_data.get('pricing_mode')
        price_total = cleaned_data.get('price_total')
        price_per_hour = cleaned_data.get('price_per_hour')
        allow_negative_balance = cleaned_data.get('allow_negative_balance')

        if pricing_mode == Asset.PricingMode.FREE:
            cleaned_data['price_total'] = Decimal('0.00')
            cleaned_data['price_per_hour'] = Decimal('0.00')
            cleaned_data['allow_negative_balance'] = False
        elif pricing_mode == Asset.PricingMode.HOURLY:
            if price_per_hour is None or price_per_hour < 0:
                self.add_error('price_per_hour', _('El precio por hora no puede ser negativo.'))
            cleaned_data['price_total'] = Decimal('0.00')
        else:
            if price_total is None or price_total < 0:
                self.add_error('price_total', _('El precio total no puede ser negativo.'))
            cleaned_data['price_per_hour'] = Decimal('0.00')

        if pricing_mode == Asset.PricingMode.FREE and allow_negative_balance:
            cleaned_data['allow_negative_balance'] = False

        return cleaned_data


class AssetReservationForm(forms.Form):
    start_at = forms.DateTimeField(
        input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(
            attrs={'class': 'form-control', 'type': 'datetime-local'},
            format='%Y-%m-%dT%H:%M',
        ),
        label=_('Fecha y hora de inicio'),
    )
    end_at = forms.DateTimeField(
        input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(
            attrs={'class': 'form-control', 'type': 'datetime-local'},
            format='%Y-%m-%dT%H:%M',
        ),
        label=_('Fecha y hora de fin'),
    )

    def clean(self):
        cleaned_data = super().clean()
        start_at = cleaned_data.get('start_at')
        end_at = cleaned_data.get('end_at')
        now = timezone.localtime()

        if start_at and start_at < now:
            self.add_error('start_at', _('La fecha de inicio debe ser futura.'))
        if start_at and end_at and end_at <= start_at:
            self.add_error('end_at', _('La fecha de fin debe ser mayor que la de inicio.'))
        return cleaned_data


class AdminAssetReservationRejectForm(forms.Form):
    rejection_reason = forms.CharField(
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-control form-control-sm', 'placeholder': _('Motivo:')}),
        label=_('Motivo'),
    )


EventRegistrationFieldFormSet = inlineformset_factory(
    Event,
    EventRegistrationField,
    form=EventRegistrationFieldForm,
    extra=1,
    can_delete=True,
)


class SurveyForm(forms.ModelForm):
    class Meta:
        model = Survey
        fields = ['title', 'description', 'selection_type', 'is_active']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'selection_type': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class SurveyOptionForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and 'is_active' not in self.data:
            self.initial.setdefault('is_active', True)

    class Meta:
        model = SurveyOption
        fields = ['label', 'sort_order', 'is_active']
        widgets = {
            'label': forms.TextInput(attrs={'class': 'form-control'}),
            'sort_order': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_label(self):
        label = (self.cleaned_data.get('label') or '').strip()
        if not label:
            raise forms.ValidationError(_('Option label is required.'))
        return label


SurveyOptionFormSet = inlineformset_factory(
    Survey,
    SurveyOption,
    form=SurveyOptionForm,
    extra=1,
    can_delete=True,
)


class SurveyResponseForm(forms.Form):
    selected_option = forms.ChoiceField(required=False)
    selected_options = forms.MultipleChoiceField(required=False)

    def __init__(self, *args, survey=None, **kwargs):
        if survey is None:
            raise ValueError('survey is required')
        self.survey = survey
        super().__init__(*args, **kwargs)

        options = list(survey.options.filter(is_active=True).order_by('sort_order', 'id'))
        self.using_all_options_fallback = False
        if not options:
            options = list(survey.options.order_by('sort_order', 'id'))
            self.using_all_options_fallback = True
        choices = [(str(option.id), option.label) for option in options]

        if survey.selection_type == Survey.SelectionType.CHECKBOX:
            self.fields['selected_options'].choices = choices
            self.fields['selected_options'].widget = forms.CheckboxSelectMultiple()
            self.fields.pop('selected_option')
        else:
            self.fields['selected_option'].choices = choices
            self.fields['selected_option'].widget = forms.RadioSelect()
            self.fields.pop('selected_options')

    def clean(self):
        cleaned_data = super().clean()
        if self.survey.selection_type == Survey.SelectionType.CHECKBOX:
            values = cleaned_data.get('selected_options') or []
            if not values:
                self.add_error('selected_options', _('Select at least one option.'))
        else:
            value = cleaned_data.get('selected_option')
            if not value:
                self.add_error('selected_option', _('Select one option.'))
        return cleaned_data


class EventCommentForm(forms.ModelForm):
    class Meta:
        model = EventComment
        fields = ['content']
        widgets = {
            'content': forms.Textarea(
                attrs={
                    'class': 'form-control',
                    'rows': 3,
                    'placeholder': _('Write your comment...'),
                }
            ),
        }

    def clean_content(self):
        content = (self.cleaned_data.get('content') or '').strip()
        if not content:
            raise forms.ValidationError(_('Comment cannot be empty.'))
        return content


class AdminEventCommentReplyForm(forms.ModelForm):
    class Meta:
        model = EventComment
        fields = ['content']
        widgets = {
            'content': forms.Textarea(
                attrs={
                    'class': 'form-control',
                    'rows': 2,
                    'placeholder': _('Write an admin reply...'),
                }
            ),
        }

    def clean_content(self):
        content = (self.cleaned_data.get('content') or '').strip()
        if not content:
            raise forms.ValidationError(_('Reply cannot be empty.'))
        return content


class GamificationForm(forms.ModelForm):
    class Meta:
        model = Gamification
        fields = [
            'title',
            'description',
            'reward',
            'gamification_type',
            'target_value',
            'start_at',
            'end_at',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'reward': forms.TextInput(
                attrs={
                    'class': 'form-control',
                    'placeholder': _('Example: 1 free coffee or 10% off next purchase'),
                }
            ),
            'gamification_type': forms.Select(attrs={'class': 'form-select'}),
            'target_value': forms.NumberInput(attrs={'class': 'form-control', 'min': 1}),
            'start_at': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'end_at': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['start_at'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['end_at'].input_formats = ['%Y-%m-%dT%H:%M']

    def clean_target_value(self):
        target_value = self.cleaned_data.get('target_value')
        if target_value is None or target_value <= 0:
            raise forms.ValidationError(_('Target value must be greater than zero.'))
        return target_value

    def clean(self):
        cleaned_data = super().clean()
        start_at = cleaned_data.get('start_at')
        end_at = cleaned_data.get('end_at')
        if start_at and end_at and end_at < start_at:
            self.add_error('end_at', _('End date must be greater than or equal to start date.'))
        return cleaned_data


class MonthlyFeeSettingsForm(forms.ModelForm):
    class Meta:
        model = MonthlyFeeSettings
        fields = ['monthly_amount', 'is_active']
        widgets = {
            'monthly_amount': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def clean_monthly_amount(self):
        amount = self.cleaned_data.get('monthly_amount')
        if amount is None or amount < 0:
            raise forms.ValidationError(_('Monthly amount must be zero or greater.'))
        return amount


class SystemSettingsForm(forms.ModelForm):
    class Meta:
        model = SystemSettings
        fields = [
            'store_name',
            'brand_color_primary',
            'brand_color_secondary',
            'footer_signature',
            'app_time_zone',
            'live_mode_enabled',
        ]
        widgets = {
            'store_name': forms.TextInput(attrs={'class': 'form-control'}),
            'brand_color_primary': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '#111827'}),
            'brand_color_secondary': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '#5E8DF5'}),
            'footer_signature': forms.TextInput(attrs={'class': 'form-control'}),
            'app_time_zone': forms.Select(attrs={'class': 'form-select'}),
            'live_mode_enabled': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['app_time_zone'].choices = SYSTEM_TIME_ZONE_CHOICES
        self.fields['app_time_zone'].widget.choices = SYSTEM_TIME_ZONE_CHOICES
        self.fields['app_time_zone'].label = _('Application time zone')
        self.fields['live_mode_enabled'].label = _('Enable live mode (no page reload)')
        current_value = self.initial.get('app_time_zone') or getattr(self.instance, 'app_time_zone', None)
        valid_time_zones = {choice[0] for choice in SYSTEM_TIME_ZONE_CHOICES}
        if current_value not in valid_time_zones:
            self.initial['app_time_zone'] = 'UTC'

    def _clean_hex_color(self, field_name):
        value = (self.cleaned_data.get(field_name) or '').strip()
        if not re.match(r'^#(?:[0-9A-Fa-f]{6})$', value):
            raise forms.ValidationError(_('Use a valid HEX color (example: #5E8DF5).'))
        return value.upper()

    def clean_brand_color_primary(self):
        return self._clean_hex_color('brand_color_primary')

    def clean_brand_color_secondary(self):
        return self._clean_hex_color('brand_color_secondary')

    def clean_app_time_zone(self):
        value = (self.cleaned_data.get('app_time_zone') or 'UTC').strip()
        valid_time_zones = {choice[0] for choice in SYSTEM_TIME_ZONE_CHOICES}
        if value not in valid_time_zones:
            raise forms.ValidationError(_('Invalid time zone selection.'))
        return value


class StrikeForm(forms.ModelForm):
    class Meta:
        model = Strike
        fields = ['strike_date', 'reason']
        widgets = {
            'strike_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'reason': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def clean_reason(self):
        reason = (self.cleaned_data.get('reason') or '').strip()
        if not reason:
            raise forms.ValidationError(_('Reason is required.'))
        return reason
