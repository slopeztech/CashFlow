from django.contrib import admin

from .models import BalanceLog, BalanceRequest, StoreUserProfile


@admin.register(StoreUserProfile)
class StoreUserProfileAdmin(admin.ModelAdmin):
	list_display = ('user', 'current_balance', 'phone', 'updated_at')
	search_fields = ('user__username', 'user__email', 'phone')


@admin.register(BalanceRequest)
class BalanceRequestAdmin(admin.ModelAdmin):
	list_display = ('id', 'user', 'amount', 'status', 'reviewed_by', 'created_at')
	list_filter = ('status',)
	search_fields = ('user__username', 'user__email', 'rejection_reason')


@admin.register(BalanceLog)
class BalanceLogAdmin(admin.ModelAdmin):
	list_display = ('id', 'user', 'source', 'amount_delta', 'balance_before', 'balance_after', 'changed_by', 'created_at')
	list_filter = ('source',)
	search_fields = ('user__username', 'changed_by__username', 'note')
