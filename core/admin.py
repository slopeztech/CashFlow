from django.contrib import admin

from core.models import Notice


@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
	list_display = ('title', 'notice_type', 'start_at', 'end_at', 'created_by')
	list_filter = ('notice_type', 'start_at', 'end_at')
	search_fields = ('title', 'description')
