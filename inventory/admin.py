from django.contrib import admin

from .models import Category, Product, ProductImage, ProductReview, ProductStockAdjustmentLog, Supplier


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
	list_display = ('name', 'created_at')
	search_fields = ('name',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
	list_display = (
		'name',
		'sku',
		'category',
		'supplier',
		'price',
		'stock',
		'min_stock',
		'unit_type',
		'is_public_listing',
		'is_active',
	)
	list_filter = ('is_active', 'is_public_listing', 'unit_type', 'category', 'supplier')
	search_fields = ('name', 'sku')


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
	list_display = ('name', 'created_at')
	search_fields = ('name',)


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
	list_display = ('id', 'product', 'created_at')
	search_fields = ('product__name', 'product__sku')


@admin.register(ProductReview)
class ProductReviewAdmin(admin.ModelAdmin):
	list_display = ('product', 'user', 'rating', 'is_approved', 'created_at')
	list_filter = ('is_approved', 'rating')
	search_fields = ('product__name', 'user__username', 'message')


@admin.register(ProductStockAdjustmentLog)
class ProductStockAdjustmentLogAdmin(admin.ModelAdmin):
	list_display = ('product', 'adjusted_by', 'previous_stock', 'adjustment', 'new_stock', 'created_at')
	list_filter = ('created_at',)
	search_fields = ('product__name', 'product__sku', 'adjusted_by__username')
