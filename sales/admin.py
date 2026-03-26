from django.contrib import admin

from .models import Order, OrderItem, Sale, SaleItem


class SaleItemInline(admin.TabularInline):
	model = SaleItem
	extra = 0


class OrderItemInline(admin.TabularInline):
	model = OrderItem
	extra = 0


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
	list_display = ('id', 'seller', 'customer', 'customer_name', 'total_amount', 'created_at')
	search_fields = ('id', 'seller__username', 'customer__username', 'customer_name')
	inlines = [SaleItemInline]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
	list_display = ('id', 'created_by', 'customer_name', 'total_amount', 'status', 'approved_by', 'created_at')
	list_filter = ('status',)
	search_fields = ('id', 'created_by__username', 'customer_name')
	inlines = [OrderItemInline]
