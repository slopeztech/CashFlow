from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _

from inventory.models import Product


class Sale(models.Model):
	seller = models.ForeignKey(User, on_delete=models.PROTECT, related_name='sales')
	customer = models.ForeignKey(User, on_delete=models.PROTECT, related_name='purchases', null=True, blank=True)
	customer_name = models.CharField(max_length=150, blank=True)
	total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	is_voided = models.BooleanField(default=False)
	voided_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='voided_sales',
	)
	voided_at = models.DateTimeField(null=True, blank=True)
	void_reason = models.CharField(max_length=255, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f"Sale #{self.id} - {self.created_at:%Y-%m-%d %H:%M}"


class SaleItem(models.Model):
	sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name='items')
	product = models.ForeignKey(Product, on_delete=models.PROTECT)
	quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
	unit_price = models.DecimalField(max_digits=10, decimal_places=2)

	class Meta:
		verbose_name = 'Sale item'
		verbose_name_plural = 'Sale items'

	@property
	def subtotal(self):
		return self.quantity * self.unit_price

	def __str__(self):
		return f"{self.product.name} x {self.quantity}"


class Order(models.Model):
	class Status(models.TextChoices):
		PENDING = 'pending', _('Pending')
		APPROVED = 'approved', _('Approved')
		REJECTED = 'rejected', _('Rejected')
		CANCELED = 'canceled', _('Canceled')

	created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
	customer_name = models.CharField(max_length=150, blank=True)
	total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
	status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
	approved_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='approved_orders',
	)
	approved_at = models.DateTimeField(null=True, blank=True)
	rejection_reason = models.CharField(max_length=255, blank=True)
	canceled_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='canceled_orders',
	)
	canceled_at = models.DateTimeField(null=True, blank=True)
	cancellation_reason = models.CharField(max_length=255, blank=True)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ['-created_at']

	def __str__(self):
		return f"Order #{self.id} - {self.status}"


class OrderItem(models.Model):
	order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
	product = models.ForeignKey(Product, on_delete=models.PROTECT)
	quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
	unit_price = models.DecimalField(max_digits=10, decimal_places=2)

	class Meta:
		verbose_name = 'Order item'
		verbose_name_plural = 'Order items'

	@property
	def subtotal(self):
		return self.quantity * self.unit_price

	def __str__(self):
		return f"{self.product.name} x {self.quantity}"
