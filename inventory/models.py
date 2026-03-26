from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _


class Category(models.Model):
	name = models.CharField(max_length=100, unique=True)
	description = models.TextField(blank=True)
	include_in_untried = models.BooleanField(default=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['name']
		verbose_name_plural = 'Categories'

	def __str__(self):
		return self.name


class Supplier(models.Model):
	name = models.CharField(max_length=120, unique=True)
	description = models.TextField(blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['name']
		verbose_name_plural = 'Suppliers'

	def __str__(self):
		return self.name


class Product(models.Model):
	class UnitType(models.TextChoices):
		UNITS = 'units', 'Units'
		MEASURE = 'measure', 'Measure'

	class PurchaseOptions(models.TextChoices):
		BOTH = 'both', _('Both')
		UNITS_ONLY = 'units_only', _('Units only')
		AMOUNT_ONLY = 'amount_only', _('Amount only')

	name = models.CharField(max_length=150)
	sku = models.CharField(max_length=50, unique=True)
	category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='products', null=True, blank=True)
	supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='products', null=True, blank=True)
	description = models.TextField(blank=True)
	price = models.DecimalField(max_digits=10, decimal_places=2)
	stock = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	min_stock = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	unit_type = models.CharField(max_length=20, choices=UnitType.choices, default=UnitType.UNITS)
	measure_label = models.CharField(max_length=50, blank=True)
	purchase_options = models.CharField(max_length=20, choices=PurchaseOptions.choices, default=PurchaseOptions.BOTH)
	is_active = models.BooleanField(default=True)
	is_public_listing = models.BooleanField(default=True)
	is_featured = models.BooleanField(default=False)
	is_new = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['name']

	def __str__(self):
		return f"{self.name} ({self.sku})"

	@property
	def unit_display_name(self):
		if self.unit_type == self.UnitType.MEASURE:
			return self.measure_label or 'grams'
		return 'units'

	@property
	def is_below_min_stock(self):
		return self.stock <= self.min_stock


class ProductImage(models.Model):
	product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
	image = models.ImageField(upload_to='products/')
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['id']

	def __str__(self):
		return f'{self.product.name} image #{self.id}'


class ProductReview(models.Model):
	product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
	user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='product_reviews')
	rating = models.PositiveSmallIntegerField()
	message = models.TextField()
	is_approved = models.BooleanField(default=False)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-created_at']
		constraints = [
			models.UniqueConstraint(fields=['product', 'user'], name='unique_user_review_per_product'),
			models.CheckConstraint(condition=models.Q(rating__gte=1, rating__lte=5), name='review_rating_range_1_5'),
		]

	def __str__(self):
		return f"{self.product.name} review by {self.user.username}"


class ProductSheetField(models.Model):
	product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='sheet_fields')
	field_key = models.CharField(max_length=120)
	field_value = models.CharField(max_length=255)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['id']
		constraints = [
			models.UniqueConstraint(fields=['product', 'field_key'], name='unique_product_sheet_key'),
		]

	def __str__(self):
		return f"{self.product.name} | {self.field_key}"


class ProductSheetUrl(models.Model):
	product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='sheet_urls')
	url = models.URLField(max_length=500)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['id']

	def __str__(self):
		return f"{self.product.name} | {self.url}"


class ProductStockAdjustmentLog(models.Model):
	product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='stock_adjustment_logs')
	adjusted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='product_stock_adjustments')
	previous_stock = models.DecimalField(max_digits=12, decimal_places=2)
	adjustment = models.DecimalField(max_digits=12, decimal_places=2)
	new_stock = models.DecimalField(max_digits=12, decimal_places=2)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f"{self.product.name} | {self.adjustment} ({self.previous_stock} -> {self.new_stock})"
