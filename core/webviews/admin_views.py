import json
import os
import csv
import base64
import socket
import subprocess
import time
import shutil
from io import BytesIO
from urllib.parse import urlparse
from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Avg, Case, Count, Exists, F, IntegerField, Max, OuterRef, Prefetch, Q, Sum, Value, When
from django.db.models.functions import TruncDate, TruncMonth
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import ListView, TemplateView

from core.controllers import build_dashboard_context
from core.gamification_admin import achieved_gamification_rows
from core.forms import (
    AdminEventCommentReplyForm,
    AdminBalanceAdjustmentForm,
    EventForm,
    EventRegistrationFieldFormSet,
    GamificationForm,
    SurveyForm,
    SurveyOptionFormSet,
    AdminUserUpdateForm,
    MonthlyFeeSettingsForm,
    NoticeForm,
    OrderRejectForm,
    StrikeForm,
    StaffUserCreateForm,
    SystemSettingsForm,
    generate_temporary_access_code,
)
from core.image_processing import optimize_uploaded_image
from core.models import (
    Event,
    EventComment,
    EventImage,
    EventRegistration,
    Gamification,
    GamificationRewardCompletion,
    Notice,
    Strike,
    Survey,
    SurveyOption,
    SurveyResponse,
    SystemSettings,
    UserSession,
)
from core.security import safe_redirect_target
from core.system_tests import build_system_tests_overview, run_system_test
from core.update_runner import (
    get_git_executable,
    get_update_log_path,
    is_update_running,
    start_platform_update_background,
)
from core.webviews.mixins import ResponsiveTemplateMixin, StaffRequiredMixin
from customers.models import BalanceLog, BalanceRequest, MonthlyFeeSettings, StoreUserProfile
from customers.services import months_due_for_profile, process_monthly_fee_for_user
from inventory.models import Product, ProductReview
from sales.models import Order, OrderItem, Sale, SaleItem
from sales.services import approve_order, reject_order


class AdminDashboardView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = 'admin/dashboard/overview.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_dashboard_context(self.request.user))
        context['users_count'] = User.objects.count()
        now = timezone.localtime()
        active_gamifications = Gamification.objects.filter(start_at__lte=now, end_at__gte=now)
        achieved_rows = achieved_gamification_rows(gamifications=active_gamifications)
        pending_reward_rows = [row for row in achieved_rows if not row['reward_completed']]
        context['gamification_completed_users_count'] = len({row['user'].id for row in pending_reward_rows})
        context['pending_gamification_count'] = len(pending_reward_rows)
        context['admin_calendar_items_json'] = json.dumps(self._build_calendar_items(now))
        return context

    def _build_calendar_items(self, now):
        window_start = now - timedelta(days=60)
        window_end = now + timedelta(days=365)
        calendar_items = {}

        def add_item(dt_value, kind, title, url):
            date_key = timezone.localtime(dt_value).date().isoformat()
            calendar_items.setdefault(date_key, []).append(
                {
                    'kind': kind,
                    'title': title,
                    'url': url,
                }
            )

        def add_range(start_dt, end_dt, kind, title, url):
            if not start_dt:
                return

            start_local = timezone.localtime(start_dt)
            end_local = timezone.localtime(end_dt) if end_dt else start_local
            if end_local < start_local:
                end_local = start_local

            visible_start = max(start_local.date(), window_start.date())
            visible_end = min(end_local.date(), window_end.date())
            if visible_end < visible_start:
                return

            current_day = visible_start
            while current_day <= visible_end:
                date_key = current_day.isoformat()
                calendar_items.setdefault(date_key, []).append(
                    {
                        'kind': kind,
                        'title': title,
                        'url': url,
                    }
                )
                current_day += timedelta(days=1)

        notices = Notice.objects.filter(
            start_at__lte=window_end,
            end_at__gte=window_start,
        ).only('id', 'title', 'start_at', 'end_at')
        for notice in notices:
            add_range(notice.start_at, notice.end_at, 'notice', notice.title, reverse('admin_notices'))

        events = Event.objects.filter(
            start_at__lte=window_end,
            end_at__gte=window_start,
        ).only('id', 'name', 'start_at', 'end_at')
        for event in events:
            add_range(
                event.start_at,
                event.end_at,
                'event',
                event.name,
                reverse('admin_event_info', kwargs={'pk': event.pk}),
            )

        surveys = Survey.objects.filter(
            is_active=True,
            created_at__gte=window_start,
            created_at__lte=window_end,
        ).only('id', 'title', 'created_at')
        for survey in surveys:
            add_item(survey.created_at, 'survey', survey.title, reverse('admin_survey_info', kwargs={'pk': survey.pk}))

        gamifications = Gamification.objects.filter(
            start_at__lte=window_end,
            end_at__gte=window_start,
        ).only('id', 'title', 'start_at', 'end_at')
        for gamification in gamifications:
            add_range(
                gamification.start_at,
                gamification.end_at,
                'gamification',
                gamification.title,
                reverse('admin_gamification_update', kwargs={'pk': gamification.pk}),
            )

        return calendar_items


class AdminNoticeListView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = 'admin/notices/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['notices'] = Notice.objects.select_related('created_by').all()
        context['now'] = timezone.localtime()
        return context


class AdminNoticeCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/notices/form.html'

    def get(self, request):
        form = NoticeForm()
        return render(request, self.get_template_names()[0], {'form': form, 'mode': 'create'})

    def post(self, request):
        form = NoticeForm(request.POST)
        if form.is_valid():
            notice = form.save(commit=False)
            notice.created_by = request.user
            notice.save()
            messages.success(request, _('Notice created successfully.'))
            return redirect('admin_notices')
        return render(request, self.get_template_names()[0], {'form': form, 'mode': 'create'})


class AdminNoticeUpdateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/notices/form.html'

    def get(self, request, pk):
        notice = get_object_or_404(Notice, pk=pk)
        form = NoticeForm(instance=notice)
        return render(request, self.get_template_names()[0], {'form': form, 'mode': 'edit', 'notice': notice})

    def post(self, request, pk):
        notice = get_object_or_404(Notice, pk=pk)
        form = NoticeForm(request.POST, instance=notice)
        if form.is_valid():
            form.save()
            messages.success(request, _('Notice updated successfully.'))
            return redirect('admin_notices')
        return render(request, self.get_template_names()[0], {'form': form, 'mode': 'edit', 'notice': notice})


class AdminNoticeDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk):
        notice = get_object_or_404(Notice, pk=pk)
        notice.delete()
        messages.success(request, _('Notice deleted successfully.'))
        return redirect('admin_notices')


class AdminEventListView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = 'admin/events/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['events'] = Event.objects.select_related('created_by').prefetch_related('images', 'registrations')
        context['now'] = timezone.localtime()
        return context


class AdminEventCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/events/form.html'

    def get(self, request):
        form = EventForm()
        formset = EventRegistrationFieldFormSet(prefix='reg_fields')
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'create',
            },
        )

    def post(self, request):
        form = EventForm(request.POST, request.FILES)
        formset = EventRegistrationFieldFormSet(request.POST, prefix='reg_fields')
        if form.is_valid() and formset.is_valid():
            event = form.save(commit=False)
            event.created_by = request.user
            event.save()
            formset.instance = event
            formset.save()
            self._save_new_images(event)
            messages.success(request, _('Event created successfully.'))
            return redirect('admin_events')
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'create',
            },
        )

    def _save_new_images(self, event):
        for uploaded_file in self.request.FILES.getlist('new_images'):
            optimized_file = optimize_uploaded_image(uploaded_file, crop_size=(1200, 1200), max_bytes=512 * 1024)
            EventImage.objects.create(event=event, image=optimized_file)


class AdminEventUpdateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/events/form.html'

    def get(self, request, pk):
        event = get_object_or_404(Event, pk=pk)
        form = EventForm(instance=event)
        formset = EventRegistrationFieldFormSet(instance=event, prefix='reg_fields')
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'edit',
                'event': event,
                'event_images': event.images.all(),
            },
        )

    def post(self, request, pk):
        event = get_object_or_404(Event, pk=pk)
        form = EventForm(request.POST, request.FILES, instance=event)
        formset = EventRegistrationFieldFormSet(request.POST, instance=event, prefix='reg_fields')
        if form.is_valid() and formset.is_valid():
            updated = form.save()
            formset.save()
            self._remove_selected_images(updated)
            self._save_new_images(updated)
            messages.success(request, _('Event updated successfully.'))
            return redirect('admin_events')
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'edit',
                'event': event,
                'event_images': event.images.all(),
            },
        )

    def _save_new_images(self, event):
        for uploaded_file in self.request.FILES.getlist('new_images'):
            optimized_file = optimize_uploaded_image(uploaded_file, crop_size=(1200, 1200), max_bytes=512 * 1024)
            EventImage.objects.create(event=event, image=optimized_file)

    def _remove_selected_images(self, event):
        image_ids = [image_id for image_id in self.request.POST.getlist('remove_images') if image_id.isdigit()]
        if not image_ids:
            return
        images = EventImage.objects.filter(event=event, id__in=image_ids)
        for event_image in images:
            if event_image.image:
                event_image.image.delete(save=False)
            event_image.delete()


class AdminEventDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk):
        event = get_object_or_404(Event, pk=pk)
        event.delete()
        messages.success(request, _('Event deleted successfully.'))
        return redirect('admin_events')


class AdminSurveyListView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = 'admin/surveys/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        option_queryset = SurveyOption.objects.annotate(selected_count=Count('response_links', distinct=True))
        context['surveys'] = Survey.objects.select_related('created_by').prefetch_related(
            Prefetch('options', queryset=option_queryset)
        ).annotate(responses_count=Count('responses', distinct=True))
        return context


class AdminSurveyCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/surveys/form.html'

    def get(self, request):
        form = SurveyForm()
        formset = SurveyOptionFormSet(prefix='survey_options')
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'create',
            },
        )

    def post(self, request):
        form = SurveyForm(request.POST)
        formset = SurveyOptionFormSet(request.POST, prefix='survey_options')
        if form.is_valid() and formset.is_valid() and self._has_minimum_options(formset):
            survey = form.save(commit=False)
            survey.created_by = request.user
            survey.save()
            formset.instance = survey
            formset.save()
            messages.success(request, _('Survey created successfully.'))
            return redirect('admin_surveys')

        if formset.is_valid() and not self._has_minimum_options(formset):
            messages.error(request, _('Add at least two options for the survey.'))

        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'create',
            },
        )

    def _has_minimum_options(self, formset):
        valid_forms = 0
        for item in formset.forms:
            if not hasattr(item, 'cleaned_data'):
                continue
            cleaned_data = item.cleaned_data
            if not cleaned_data or cleaned_data.get('DELETE'):
                continue
            if cleaned_data.get('label'):
                valid_forms += 1
        return valid_forms >= 2


class AdminSurveyUpdateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/surveys/form.html'

    def get(self, request, pk):
        survey = get_object_or_404(Survey, pk=pk)
        form = SurveyForm(instance=survey)
        formset = SurveyOptionFormSet(instance=survey, prefix='survey_options')
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'edit',
                'survey': survey,
            },
        )

    def post(self, request, pk):
        survey = get_object_or_404(Survey, pk=pk)
        form = SurveyForm(request.POST, instance=survey)
        formset = SurveyOptionFormSet(request.POST, instance=survey, prefix='survey_options')
        if form.is_valid() and formset.is_valid() and self._has_minimum_options(formset):
            form.save()
            formset.save()
            messages.success(request, _('Survey updated successfully.'))
            return redirect('admin_surveys')

        if formset.is_valid() and not self._has_minimum_options(formset):
            messages.error(request, _('Add at least two options for the survey.'))

        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'edit',
                'survey': survey,
            },
        )

    def _has_minimum_options(self, formset):
        valid_forms = 0
        for item in formset.forms:
            if not hasattr(item, 'cleaned_data'):
                continue
            cleaned_data = item.cleaned_data
            if not cleaned_data or cleaned_data.get('DELETE'):
                continue
            if cleaned_data.get('label'):
                valid_forms += 1
        return valid_forms >= 2


class AdminSurveyDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk):
        survey = get_object_or_404(Survey, pk=pk)
        survey.delete()
        messages.success(request, _('Survey deleted successfully.'))
        return redirect('admin_surveys')


class AdminSurveyInfoView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = 'admin/surveys/info.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        survey = get_object_or_404(Survey.objects.select_related('created_by'), pk=self.kwargs['pk'])
        options = list(survey.options.order_by('sort_order', 'id'))
        responses = SurveyResponse.objects.filter(survey=survey).select_related('user').prefetch_related(
            'selected_options__option'
        )

        option_counts = {
            row['selected_options__option_id']: row['total']
            for row in survey.responses.values('selected_options__option_id').annotate(total=Count('id'))
            if row['selected_options__option_id'] is not None
        }
        total_responses = responses.count()

        for option in options:
            selected_total = option_counts.get(option.id, 0)
            option.selected_total = selected_total
            option.selected_percentage = (selected_total * 100 / total_responses) if total_responses else 0

        context.update(
            {
                'survey': survey,
                'survey_options': options,
                'survey_responses': responses,
                'responses_count': total_responses,
            }
        )
        return context


class AdminEventInfoView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = 'admin/events/info.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = get_object_or_404(Event.objects.select_related('created_by'), pk=self.kwargs['pk'])
        registrations = EventRegistration.objects.filter(event=event).select_related('user').order_by('-created_at')
        registrations_count = registrations.count()
        total_collected = (event.registration_fee * registrations_count) if event.is_paid_event else Decimal('0.00')
        registration_fields = event.registration_fields.filter(is_active=True).order_by('sort_order', 'id')
        comments = EventComment.objects.filter(event=event, parent__isnull=True).select_related(
            'user',
            'user__store_profile',
        ).prefetch_related(
            'replies__user',
            'replies__user__store_profile',
        )

        context.update(
            {
                'event': event,
                'registrations': registrations,
                'registrations_count': registrations_count,
                'total_collected': total_collected,
                'registration_fields': registration_fields,
                'event_comments': comments,
                'comment_reply_form': AdminEventCommentReplyForm(),
            }
        )
        return context


class AdminEventCommentReplyView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk, comment_id):
        event = get_object_or_404(Event, pk=pk)
        parent_comment = get_object_or_404(
            EventComment,
            pk=comment_id,
            event=event,
            parent__isnull=True,
        )
        form = AdminEventCommentReplyForm(request.POST)
        if form.is_valid():
            EventComment.objects.create(
                event=event,
                user=request.user,
                parent=parent_comment,
                content=form.cleaned_data['content'],
            )
            messages.success(request, _('Admin reply posted successfully.'))
        else:
            messages.error(request, _('Please write a reply before posting.'))
        return redirect('admin_event_info', pk=event.pk)


class AdminEventCommentIgnoreView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, comment_id):
        comment = get_object_or_404(
            EventComment,
            pk=comment_id,
            parent__isnull=True,
            user__is_staff=False,
        )
        if not comment.is_ignored_by_admin:
            comment.is_ignored_by_admin = True
            comment.save(update_fields=['is_ignored_by_admin', 'updated_at'])
            messages.success(request, _('Event comment ignored successfully.'))
        else:
            messages.info(request, _('This event comment was already ignored.'))
        return redirect('admin_actions')


class AdminEventRegistrationRemoveView(LoginRequiredMixin, StaffRequiredMixin, View):
    @transaction.atomic
    def post(self, request, pk, registration_id):
        event = get_object_or_404(Event.objects.select_for_update(), pk=pk)
        registration = get_object_or_404(
            EventRegistration.objects.select_for_update().select_related('user'),
            pk=registration_id,
            event=event,
        )

        if event.is_paid_event and timezone.localtime() < event.start_at:
            profile, created = StoreUserProfile.objects.select_for_update().get_or_create(user=registration.user)
            balance_before = profile.current_balance
            balance_after = balance_before + event.registration_fee
            profile.current_balance = balance_after
            profile.save(update_fields=['current_balance', 'updated_at'])
            BalanceLog.objects.create(
                user=registration.user,
                changed_by=request.user,
                source=BalanceLog.Source.EVENT_REGISTRATION_REFUND,
                amount_delta=event.registration_fee,
                balance_before=balance_before,
                balance_after=balance_after,
                note=_('Admin removed event registration: %(event)s') % {'event': event.name},
            )

        registration.delete()
        messages.success(request, _('User registration removed successfully.'))
        return redirect('admin_event_info', pk=event.pk)


class AdminGamificationListView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = 'admin/gamifications/list.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['gamifications'] = Gamification.objects.select_related('created_by').all()
        context['now'] = timezone.localtime()
        return context


class AdminGamificationCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/gamifications/form.html'

    def get(self, request):
        form = GamificationForm()
        return render(request, self.get_template_names()[0], {'form': form, 'mode': 'create'})

    def post(self, request):
        form = GamificationForm(request.POST)
        if form.is_valid():
            gamification = form.save(commit=False)
            gamification.created_by = request.user
            gamification.save()
            messages.success(request, _('Gamification created successfully.'))
            return redirect('admin_gamifications')
        return render(request, self.get_template_names()[0], {'form': form, 'mode': 'create'})


class AdminGamificationUpdateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/gamifications/form.html'

    def get(self, request, pk):
        gamification = get_object_or_404(Gamification, pk=pk)
        form = GamificationForm(instance=gamification)
        return render(
            request,
            self.get_template_names()[0],
            {'form': form, 'mode': 'edit', 'gamification': gamification},
        )

    def post(self, request, pk):
        gamification = get_object_or_404(Gamification, pk=pk)
        form = GamificationForm(request.POST, instance=gamification)
        if form.is_valid():
            form.save()
            messages.success(request, _('Gamification updated successfully.'))
            return redirect('admin_gamifications')
        return render(
            request,
            self.get_template_names()[0],
            {'form': form, 'mode': 'edit', 'gamification': gamification},
        )


class AdminGamificationDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk):
        gamification = get_object_or_404(Gamification, pk=pk)
        gamification.delete()
        messages.success(request, _('Gamification deleted successfully.'))
        return redirect('admin_gamifications')


class AdminGamificationCompletedUsersView(
    ResponsiveTemplateMixin,
    LoginRequiredMixin,
    StaffRequiredMixin,
    TemplateView,
):
    template_name = 'admin/gamifications/completions.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        rows = achieved_gamification_rows()

        status_filter = (self.request.GET.get('status') or 'pending').strip().lower()
        if status_filter not in {'pending', 'rewarded', 'all'}:
            status_filter = 'pending'

        gamification_id = (self.request.GET.get('gamification_id') or '').strip()
        if gamification_id.isdigit():
            rows = [row for row in rows if row['gamification'].id == int(gamification_id)]
        else:
            gamification_id = ''

        user_query = (self.request.GET.get('user_q') or '').strip().lower()
        if user_query:
            rows = [row for row in rows if user_query in row['user'].username.lower()]

        if status_filter == 'pending':
            rows = [row for row in rows if not row['reward_completed']]
        elif status_filter == 'rewarded':
            rows = [row for row in rows if row['reward_completed']]

        context['completion_rows'] = rows
        context['completed_users_count'] = len({row['user'].id for row in rows})
        context['status_filter'] = status_filter
        context['selected_gamification_id'] = gamification_id
        context['user_q'] = self.request.GET.get('user_q', '')
        context['gamifications'] = Gamification.objects.order_by('-start_at', '-id')
        return context


class AdminGamificationRewardCompleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, user_id, gamification_id):
        user = get_object_or_404(User, pk=user_id, is_staff=False)
        gamification = get_object_or_404(Gamification, pk=gamification_id)

        # Ensure reward completion is available only for users who actually achieved the challenge.
        rows = achieved_gamification_rows(users=User.objects.filter(pk=user.id), gamifications=[gamification])
        next_page = safe_redirect_target(
            request,
            request.POST.get('next'),
            'admin_gamification_completions',
        )

        if not rows:
            messages.error(request, _('This user has not completed the selected gamification yet.'))
            return redirect(next_page)

        GamificationRewardCompletion.objects.get_or_create(
            user=user,
            gamification=gamification,
            defaults={
                'rewarded_by': request.user,
                'rewarded_at': timezone.localtime(),
            },
        )
        messages.success(request, _('Reward marked as completed.'))
        return redirect(next_page)


class AdminChartsView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = 'admin/charts/metrics.html'

    METRIC_OPTIONS = {
        'sales_amount': _('Sales (amount)'),
        'orders_count': _('Orders (count)'),
        'sales_and_orders_count': _('Sales + Orders (count)'),
        'sales_and_orders_amount': _('Sales + Orders (€)'),
        'cash_total': _('Cash box total (€)'),
        'units_sold': _('Units sold (count)'),
        'pending_orders_count': _('Pending orders (count)'),
        'balance_requests_count': _('Balance requests (count)'),
        'new_users_count': _('New users (count)'),
        'reviews_count': _('Approved reviews (count)'),
        'gamification_rewards_completed_count': _('Gamification rewards completed (count)'),
    }

    def _get_date_range(self):
        today = timezone.localdate()
        default_start = today - timedelta(days=29)
        default_end = today

        start_raw = self.request.GET.get('start_date')
        end_raw = self.request.GET.get('end_date')

        start_date = parse_date(start_raw) if start_raw else default_start
        end_date = parse_date(end_raw) if end_raw else default_end

        if not start_date:
            start_date = default_start
        if not end_date:
            end_date = default_end
        if start_date > end_date:
            start_date, end_date = end_date, start_date

        max_days = 366
        if (end_date - start_date).days + 1 > max_days:
            start_date = end_date - timedelta(days=max_days - 1)

        return start_date, end_date

    def _series_sales_amount(self, start_date, end_date):
        queryset = (
            Sale.objects.filter(created_at__date__range=(start_date, end_date))
            .annotate(bucket=TruncDate('created_at'))
            .values('bucket')
            .annotate(value=Sum('total_amount'))
            .order_by('bucket')
        )
        return {entry['bucket']: float(entry['value'] or 0) for entry in queryset}

    def _series_sales_count(self, start_date, end_date):
        queryset = (
            Sale.objects.filter(created_at__date__range=(start_date, end_date))
            .annotate(bucket=TruncDate('created_at'))
            .values('bucket')
            .annotate(value=Count('id'))
            .order_by('bucket')
        )
        return {entry['bucket']: float(entry['value'] or 0) for entry in queryset}

    def _series_orders_count(self, start_date, end_date, status=None):
        queryset = Order.objects.filter(created_at__date__range=(start_date, end_date))
        if status:
            queryset = queryset.filter(status=status)
        queryset = (
            queryset.annotate(bucket=TruncDate('created_at'))
            .values('bucket')
            .annotate(value=Count('id'))
            .order_by('bucket')
        )
        return {entry['bucket']: float(entry['value'] or 0) for entry in queryset}

    def _series_orders_amount(self, start_date, end_date, status=None):
        queryset = Order.objects.filter(created_at__date__range=(start_date, end_date))
        if status:
            queryset = queryset.filter(status=status)
        queryset = (
            queryset.annotate(bucket=TruncDate('created_at'))
            .values('bucket')
            .annotate(value=Sum('total_amount'))
            .order_by('bucket')
        )
        return {entry['bucket']: float(entry['value'] or 0) for entry in queryset}

    def _series_reviews_count(self, start_date, end_date):
        queryset = (
            ProductReview.objects.filter(is_approved=True, updated_at__date__range=(start_date, end_date))
            .annotate(bucket=TruncDate('updated_at'))
            .values('bucket')
            .annotate(value=Count('id'))
            .order_by('bucket')
        )
        return {entry['bucket']: float(entry['value'] or 0) for entry in queryset}

    def _series_units_sold(self, start_date, end_date):
        sales_series = (
            Sale.objects.filter(created_at__date__range=(start_date, end_date))
            .annotate(bucket=TruncDate('created_at'))
            .values('bucket')
            .annotate(value=Sum('items__quantity'))
            .order_by('bucket')
        )
        orders_series = (
            Order.objects.filter(created_at__date__range=(start_date, end_date), status=Order.Status.APPROVED)
            .annotate(bucket=TruncDate('created_at'))
            .values('bucket')
            .annotate(value=Sum('items__quantity'))
            .order_by('bucket')
        )
        values = {}
        for entry in sales_series:
            values[entry['bucket']] = values.get(entry['bucket'], 0) + float(entry['value'] or 0)
        for entry in orders_series:
            values[entry['bucket']] = values.get(entry['bucket'], 0) + float(entry['value'] or 0)
        return values

    def _series_balance_requests_count(self, start_date, end_date):
        queryset = (
            BalanceRequest.objects.filter(created_at__date__range=(start_date, end_date))
            .annotate(bucket=TruncDate('created_at'))
            .values('bucket')
            .annotate(value=Count('id'))
            .order_by('bucket')
        )
        return {entry['bucket']: float(entry['value'] or 0) for entry in queryset}

    def _series_new_users_count(self, start_date, end_date):
        queryset = (
            User.objects.filter(is_staff=False, date_joined__date__range=(start_date, end_date))
            .annotate(bucket=TruncDate('date_joined'))
            .values('bucket')
            .annotate(value=Count('id'))
            .order_by('bucket')
        )
        return {entry['bucket']: float(entry['value'] or 0) for entry in queryset}

    def _series_gamification_rewards_completed_count(self, start_date, end_date):
        queryset = (
            GamificationRewardCompletion.objects.filter(rewarded_at__date__range=(start_date, end_date))
            .annotate(bucket=TruncDate('rewarded_at'))
            .values('bucket')
            .annotate(value=Count('id'))
            .order_by('bucket')
        )
        return {entry['bucket']: float(entry['value'] or 0) for entry in queryset}

    def _aggregate_metric(self, metric, start_date, end_date):
        if metric == 'sales_amount':
            return self._series_sales_amount(start_date, end_date)
        if metric == 'orders_count':
            return self._series_orders_count(start_date, end_date)
        if metric == 'reviews_count':
            return self._series_reviews_count(start_date, end_date)
        if metric == 'units_sold':
            return self._series_units_sold(start_date, end_date)
        if metric == 'pending_orders_count':
            return self._series_orders_count(start_date, end_date, status=Order.Status.PENDING)
        if metric == 'balance_requests_count':
            return self._series_balance_requests_count(start_date, end_date)
        if metric == 'new_users_count':
            return self._series_new_users_count(start_date, end_date)
        if metric == 'gamification_rewards_completed_count':
            return self._series_gamification_rewards_completed_count(start_date, end_date)
        return self._series_sales_amount(start_date, end_date)

    def _build_detail_table(self, metric, start_date, end_date):
        if metric == 'sales_and_orders_count':
            headers = [str(_('Date')), str(_('Sales count')), str(_('Orders count')), str(_('Total actions'))]
            sales_values_by_day = self._series_sales_count(start_date, end_date)
            orders_values_by_day = self._series_orders_count(start_date, end_date)
            rows = []
            current_day = end_date
            while current_day >= start_date:
                sales_count = int(sales_values_by_day.get(current_day, 0))
                orders_count = int(orders_values_by_day.get(current_day, 0))
                rows.append(
                    [
                        current_day.strftime('%Y-%m-%d'),
                        sales_count,
                        orders_count,
                        sales_count + orders_count,
                    ]
                )
                current_day -= timedelta(days=1)
        elif metric == 'sales_and_orders_amount':
            headers = [
                str(_('Date')),
                str(_('Sales total (€)')),
                str(_('Orders total (€)')),
                str(_('Combined total (€)')),
            ]
            sales_values_by_day = self._series_sales_amount(start_date, end_date)
            orders_values_by_day = self._series_orders_amount(start_date, end_date, status=Order.Status.APPROVED)
            rows = []
            current_day = end_date
            while current_day >= start_date:
                sales_amount = sales_values_by_day.get(current_day, 0)
                orders_amount = orders_values_by_day.get(current_day, 0)
                rows.append(
                    [
                        current_day.strftime('%Y-%m-%d'),
                        f'{sales_amount:.2f}',
                        f'{orders_amount:.2f}',
                        f'{(sales_amount + orders_amount):.2f}',
                    ]
                )
                current_day -= timedelta(days=1)
        elif metric == 'orders_count':
            headers = [str(_('Date')), str(_('Order')), str(_('User')), str(_('Status')), str(_('Total (€)'))]
            queryset = (
                Order.objects.filter(created_at__date__range=(start_date, end_date))
                .select_related('created_by')
                .order_by('-created_at')[:500]
            )
            rows = [
                [
                    item.created_at.strftime('%Y-%m-%d %H:%M'),
                    f'#{item.id}',
                    item.created_by.username,
                    item.get_status_display(),
                    str(item.total_amount),
                ]
                for item in queryset
            ]
        elif metric == 'cash_total':
            headers = [str(_('Date')), str(_('Daily income (€)')), str(_('Cash box total (€)'))]
            sales_values_by_day = self._series_sales_amount(start_date, end_date)
            orders_values_by_day = self._series_orders_amount(start_date, end_date, status=Order.Status.APPROVED)
            rows = []
            cumulative = 0.0
            current_day = start_date
            while current_day <= end_date:
                daily_income = sales_values_by_day.get(current_day, 0) + orders_values_by_day.get(current_day, 0)
                cumulative += daily_income
                rows.append(
                    [
                        current_day.strftime('%Y-%m-%d'),
                        f'{daily_income:.2f}',
                        f'{cumulative:.2f}',
                    ]
                )
                current_day += timedelta(days=1)
            rows.reverse()
        elif metric == 'pending_orders_count':
            headers = [str(_('Date')), str(_('Pending orders'))]
            values_by_day = self._series_orders_count(start_date, end_date, status=Order.Status.PENDING)
            rows = []
            current_day = end_date
            while current_day >= start_date:
                rows.append([current_day.strftime('%Y-%m-%d'), int(values_by_day.get(current_day, 0))])
                current_day -= timedelta(days=1)
        elif metric == 'units_sold':
            headers = [str(_('Date')), str(_('Units sold'))]
            values_by_day = self._series_units_sold(start_date, end_date)
            rows = []
            current_day = end_date
            while current_day >= start_date:
                rows.append([current_day.strftime('%Y-%m-%d'), int(values_by_day.get(current_day, 0))])
                current_day -= timedelta(days=1)
        elif metric == 'balance_requests_count':
            headers = [str(_('Date')), str(_('Balance requests'))]
            values_by_day = self._series_balance_requests_count(start_date, end_date)
            rows = []
            current_day = end_date
            while current_day >= start_date:
                rows.append([current_day.strftime('%Y-%m-%d'), int(values_by_day.get(current_day, 0))])
                current_day -= timedelta(days=1)
        elif metric == 'new_users_count':
            headers = [str(_('Date')), str(_('New users'))]
            values_by_day = self._series_new_users_count(start_date, end_date)
            rows = []
            current_day = end_date
            while current_day >= start_date:
                rows.append([current_day.strftime('%Y-%m-%d'), int(values_by_day.get(current_day, 0))])
                current_day -= timedelta(days=1)
        elif metric == 'gamification_rewards_completed_count':
            headers = [str(_('Date')), str(_('Completed rewards'))]
            values_by_day = self._series_gamification_rewards_completed_count(start_date, end_date)
            rows = []
            current_day = end_date
            while current_day >= start_date:
                rows.append([current_day.strftime('%Y-%m-%d'), int(values_by_day.get(current_day, 0))])
                current_day -= timedelta(days=1)
        elif metric == 'reviews_count':
            headers = [
                str(_('Date')),
                str(_('Review')),
                str(_('Product')),
                str(_('User')),
                str(_('Rating')),
                str(_('Message')),
            ]
            queryset = (
                ProductReview.objects.filter(is_approved=True, updated_at__date__range=(start_date, end_date))
                .select_related('product', 'user')
                .order_by('-updated_at')[:500]
            )
            rows = [
                [
                    item.updated_at.strftime('%Y-%m-%d %H:%M'),
                    f'#{item.id}',
                    item.product.name,
                    item.user.username,
                    f'{item.rating}/5',
                    (item.message[:90] + '...') if len(item.message) > 90 else item.message,
                ]
                for item in queryset
            ]
        else:
            headers = [str(_('Date')), str(_('Sale')), str(_('Customer')), str(_('Seller')), str(_('Total (€)'))]
            queryset = (
                Sale.objects.filter(created_at__date__range=(start_date, end_date))
                .select_related('customer', 'seller')
                .order_by('-created_at')[:500]
            )
            rows = [
                [
                    item.created_at.strftime('%Y-%m-%d %H:%M'),
                    f'#{item.id}',
                    item.customer.username if item.customer else (item.customer_name or '-'),
                    item.seller.username,
                    str(item.total_amount),
                ]
                for item in queryset
            ]

        return headers, rows

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        metric = (self.request.GET.get('metric') or 'sales_amount').strip()
        if metric not in self.METRIC_OPTIONS:
            metric = 'sales_amount'

        start_date, end_date = self._get_date_range()
        detail_headers, detail_rows = self._build_detail_table(metric, start_date, end_date)

        labels = []
        chart_datasets = []
        current_day = start_date

        chart_left_axis_label = ''
        chart_right_axis_label = ''

        if metric == 'sales_and_orders_count':
            sales_values_by_day = self._series_sales_count(start_date, end_date)
            orders_values_by_day = self._series_orders_count(start_date, end_date)
            sales_data = []
            orders_data = []
            while current_day <= end_date:
                labels.append(current_day.strftime('%Y-%m-%d'))
                sales_data.append(sales_values_by_day.get(current_day, 0))
                orders_data.append(orders_values_by_day.get(current_day, 0))
                current_day += timedelta(days=1)

            chart_datasets = [
                {
                    'label': str(_('Sales (count)')),
                    'data': sales_data,
                    'borderColor': '#4B49AC',
                    'backgroundColor': '#4B49AC22',
                    'yAxisID': 'y_left',
                },
                {
                    'label': str(_('Orders (count)')),
                    'data': orders_data,
                    'borderColor': '#57B657',
                    'backgroundColor': '#57B65722',
                    'yAxisID': 'y_right',
                },
            ]
            chart_total_label = _(
                'Sales total: %(sales_total)s | Orders total: %(orders_total)s'
            ) % {
                'sales_total': int(sum(sales_data)),
                'orders_total': int(sum(orders_data)),
            }
            has_secondary_axis = True
            chart_left_axis_label = str(_('Sales (count)'))
            chart_right_axis_label = str(_('Orders (count)'))
        elif metric == 'sales_and_orders_amount':
            sales_values_by_day = self._series_sales_amount(start_date, end_date)
            orders_values_by_day = self._series_orders_amount(start_date, end_date, status=Order.Status.APPROVED)
            sales_data = []
            orders_data = []
            while current_day <= end_date:
                labels.append(current_day.strftime('%Y-%m-%d'))
                sales_data.append(sales_values_by_day.get(current_day, 0))
                orders_data.append(orders_values_by_day.get(current_day, 0))
                current_day += timedelta(days=1)

            chart_datasets = [
                {
                    'label': str(_('Sales (€)')),
                    'data': sales_data,
                    'borderColor': '#4B49AC',
                    'backgroundColor': '#4B49AC22',
                    'yAxisID': 'y_left',
                },
                {
                    'label': str(_('Orders (€)')),
                    'data': orders_data,
                    'borderColor': '#57B657',
                    'backgroundColor': '#57B65722',
                    'yAxisID': 'y_right',
                },
            ]
            chart_total_label = _(
                'Sales total: € %(sales_total).2f | Orders total: € %(orders_total).2f'
            ) % {
                'sales_total': sum(sales_data),
                'orders_total': sum(orders_data),
            }
            has_secondary_axis = True
            chart_left_axis_label = str(_('Sales (€)'))
            chart_right_axis_label = str(_('Orders (€)'))
        elif metric == 'cash_total':
            sales_values_by_day = self._series_sales_amount(start_date, end_date)
            orders_values_by_day = self._series_orders_amount(start_date, end_date, status=Order.Status.APPROVED)
            data = []
            cumulative = 0.0
            while current_day <= end_date:
                labels.append(current_day.strftime('%Y-%m-%d'))
                cumulative += sales_values_by_day.get(current_day, 0) + orders_values_by_day.get(current_day, 0)
                data.append(cumulative)
                current_day += timedelta(days=1)

            chart_datasets = [
                {
                    'label': str(_('Cash box total (€)')),
                    'data': data,
                    'borderColor': '#4B49AC',
                    'backgroundColor': '#4B49AC22',
                    'yAxisID': 'y',
                }
            ]
            chart_total_label = _('Current cash box total: € %(total).2f') % {'total': (data[-1] if data else 0)}
            has_secondary_axis = False
            chart_left_axis_label = str(_('Cash box total (€)'))
        else:
            values_by_day = self._aggregate_metric(metric, start_date, end_date)
            data = []
            while current_day <= end_date:
                labels.append(current_day.strftime('%Y-%m-%d'))
                data.append(values_by_day.get(current_day, 0))
                current_day += timedelta(days=1)

            chart_datasets = [
                {
                    'label': str(self.METRIC_OPTIONS[metric]),
                    'data': data,
                    'borderColor': '#4B49AC',
                    'backgroundColor': '#4B49AC22',
                    'yAxisID': 'y',
                }
            ]
            chart_total_label = _('Range total: %(total)s') % {'total': round(sum(data), 2)}
            has_secondary_axis = False
            chart_left_axis_label = str(self.METRIC_OPTIONS[metric])

        context.update(
            {
                'metric_options': self.METRIC_OPTIONS,
                'selected_metric': metric,
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat(),
                'chart_labels_json': json.dumps(labels),
                'chart_datasets_json': json.dumps(chart_datasets),
                'chart_metric_label': str(self.METRIC_OPTIONS[metric]),
                'chart_total_label': chart_total_label,
                'has_secondary_axis': has_secondary_axis,
                'chart_left_axis_label': chart_left_axis_label,
                'chart_right_axis_label': chart_right_axis_label,
                'detail_headers': detail_headers,
                'detail_rows': detail_rows,
            }
        )
        return context


class AdminUserListCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/users/list.html'

    def get(self, request):
        users = (
            User.objects.select_related('store_profile')
            .annotate(strikes_count=Count('strikes'))
            .filter(is_active=True)
            .order_by('username')
        )
        active_since = timezone.now() - timedelta(hours=2)
        recent_activity_cutoff = timezone.now() - timedelta(minutes=20)
        active_users = (
            User.objects.filter(is_active=True, tracked_sessions__last_activity__gte=active_since)
            .annotate(last_seen_at=Max('tracked_sessions__last_activity'))
            .order_by('-last_seen_at', 'username')
            .distinct()
        )
        return render(
            request,
            self.get_template_names()[0],
            {
                'users': users,
                'active_users': active_users,
                'active_users_count': active_users.count(),
                'recent_activity_cutoff': recent_activity_cutoff,
            },
        )


class AdminUserCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/users/form.html'

    def get(self, request):
        form = StaffUserCreateForm()
        return render(request, self.get_template_names()[0], {'form': form, 'mode': 'create'})

    def post(self, request):
        form = StaffUserCreateForm(request.POST)
        if form.is_valid():
            temporary_access_code = form.cleaned_data['temporary_access_code']
            user = form.save(commit=False)
            user.email = ''
            user.is_staff = form.cleaned_data.get('is_staff', False)
            user.set_password(temporary_access_code)
            user.save()
            profile, profile_created = StoreUserProfile.objects.get_or_create(user=user)
            profile.language = form.cleaned_data.get('language', StoreUserProfile.Language.ENGLISH)
            profile.member_number = form.cleaned_data.get('member_number')
            profile.display_name = (form.cleaned_data.get('display_name') or '').strip()
            profile.monthly_fee_enabled = form.cleaned_data.get('monthly_fee_enabled', False)
            profile.recent_movements_limit = form.cleaned_data.get('recent_movements_limit')
            profile.show_all_recent_movements = profile.recent_movements_limit is None
            profile.temporary_access_code_plain = temporary_access_code
            if profile.monthly_fee_enabled and not profile.monthly_fee_enabled_at:
                profile.monthly_fee_enabled_at = timezone.localdate()
                profile.monthly_fee_last_charged_month = None
            profile.save(
                update_fields=[
                    'language',
                    'member_number',
                    'display_name',
                    'monthly_fee_enabled',
                    'monthly_fee_enabled_at',
                    'monthly_fee_last_charged_month',
                    'show_all_recent_movements',
                    'recent_movements_limit',
                    'temporary_access_code_plain',
                    'updated_at',
                ]
            )
            messages.success(
                request,
                _('User created successfully. Temporary access code: %(code)s') % {'code': temporary_access_code},
            )
            return redirect('admin_user_list')
        return render(request, self.get_template_names()[0], {'form': form, 'mode': 'create'})


class AdminMonthlyFeeSettingsView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/balance/monthly_fee.html'

    def get(self, request):
        settings_obj, settings_created = MonthlyFeeSettings.objects.get_or_create(pk=1)
        form = MonthlyFeeSettingsForm(instance=settings_obj)
        return self._render(request, form, settings_obj)

    def post(self, request):
        settings_obj, settings_created = MonthlyFeeSettings.objects.get_or_create(pk=1)
        form = MonthlyFeeSettingsForm(request.POST, instance=settings_obj)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.updated_by = request.user
            updated.save()
            messages.success(request, _('Monthly fee settings updated successfully.'))
            return redirect('admin_monthly_fee')
        return self._render(request, form, settings_obj)

    def _render(self, request, form, settings_obj):
        profiles = list(
            StoreUserProfile.objects.select_related('user').filter(user__is_staff=False, monthly_fee_enabled=True)
        )
        late_users = []
        for profile in profiles:
            due_months = months_due_for_profile(profile)
            if due_months > 0:
                late_users.append({'user': profile.user, 'due_months': due_months, 'balance': profile.current_balance})

        total_users = User.objects.filter(is_staff=False).count()
        enabled_users = len(profiles)

        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'settings_obj': settings_obj,
                'monthly_enabled_users': enabled_users,
                'monthly_disabled_users': max(total_users - enabled_users, 0),
                'monthly_late_users': late_users,
                'monthly_late_users_count': len(late_users),
            },
        )


class AdminMonthlyFeeLateUsersView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/balance/monthly_fee_late_users.html'
    SECURITY_WORD = 'COBRAR'

    def get(self, request):
        return render(request, self.get_template_names()[0], self._build_context())

    @transaction.atomic
    def post(self, request, user_id):
        security_answer = (request.POST.get('security_answer') or '').strip().upper()
        if security_answer != self.SECURITY_WORD:
            messages.error(request, _('Security confirmation failed.'))
            return redirect('admin_monthly_fee_late_users')

        settings_obj, _settings_created = MonthlyFeeSettings.objects.get_or_create(pk=1)
        if not settings_obj.is_active or settings_obj.monthly_amount <= Decimal('0'):
            messages.error(request, _('Monthly fee processing is disabled.'))
            return redirect('admin_monthly_fee_late_users')

        target_user = get_object_or_404(User, pk=user_id, is_staff=False)
        profile, _profile_created = StoreUserProfile.objects.get_or_create(user=target_user)

        due_months_before = months_due_for_profile(profile)
        if due_months_before <= 0:
            messages.info(request, _('This user has no pending months.'))
            return redirect('admin_monthly_fee_late_users')

        charged_months = process_monthly_fee_for_user(target_user)
        if charged_months > 0:
            messages.success(
                request,
                _('Monthly fees charged for %(username)s: %(months)s month(s).')
                % {'username': target_user.username, 'months': charged_months},
            )
        else:
            messages.warning(request, _('No monthly fee charges were applied.'))

        return redirect('admin_monthly_fee_late_users')

    def _build_context(self):
        settings_obj, _settings_created = MonthlyFeeSettings.objects.get_or_create(pk=1)
        profiles = list(
            StoreUserProfile.objects.select_related('user').filter(user__is_staff=False, monthly_fee_enabled=True)
        )

        late_users = []
        for profile in profiles:
            due_months = months_due_for_profile(profile)
            if due_months <= 0:
                continue
            total_due = (settings_obj.monthly_amount or Decimal('0')) * due_months
            late_users.append(
                {
                    'user': profile.user,
                    'due_months': due_months,
                    'balance': profile.current_balance,
                    'monthly_amount': settings_obj.monthly_amount,
                    'total_due': total_due,
                }
            )

        late_users.sort(key=lambda item: item['due_months'], reverse=True)
        total_due_amount = sum((item['total_due'] for item in late_users), Decimal('0'))
        total_pending_months = sum((item['due_months'] for item in late_users), 0)

        return {
            'settings_obj': settings_obj,
            'monthly_late_users': late_users,
            'monthly_late_users_count': len(late_users),
            'monthly_pending_months_total': total_pending_months,
            'monthly_late_total_due': total_due_amount,
            'monthly_security_word': self.SECURITY_WORD,
        }


class AdminUserUpdateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/users/form.html'

    @staticmethod
    def _invalidate_user_sessions(target_user):
        session_keys = list(
            UserSession.objects.filter(user=target_user).values_list('session_key', flat=True)
        )
        if session_keys:
            Session.objects.filter(session_key__in=session_keys).delete()
        UserSession.objects.filter(user=target_user).delete()

    @staticmethod
    def _build_section_form_data(target_user, request_post, section):
        profile, _profile_created = StoreUserProfile.objects.get_or_create(user=target_user)
        data = {
            'username': target_user.username,
            'member_number': profile.member_number or '',
            'display_name': profile.display_name or '',
            'is_staff': 'on' if target_user.is_staff else '',
            'phone': profile.phone or '',
            'address': profile.address or '',
            'language': profile.language,
            'monthly_fee_enabled': 'on' if profile.monthly_fee_enabled else '',
            'recent_movements_limit': '' if profile.recent_movements_limit is None else str(profile.recent_movements_limit),
        }

        if section == 'basic':
            for field_name in ['username', 'member_number', 'display_name', 'phone', 'address']:
                if field_name in request_post:
                    data[field_name] = request_post.get(field_name, '')
        elif section == 'checks':
            data['is_staff'] = 'on' if request_post.get('is_staff') else ''
            data['monthly_fee_enabled'] = 'on' if request_post.get('monthly_fee_enabled') else ''
            data['recent_movements_limit'] = request_post.get('recent_movements_limit', '')
            data['language'] = request_post.get('language', data['language'])
        elif section == 'admin_access':
            data['is_staff'] = 'on' if request_post.get('is_staff') else ''

        return data

    def get(self, request, user_id):
        target_user = get_object_or_404(User, id=user_id)
        form = AdminUserUpdateForm(user_instance=target_user)
        profile, _profile_created = StoreUserProfile.objects.get_or_create(user=target_user)
        pending_temporary_access_code = (
            profile.temporary_access_code_plain if profile.password_change_required else ''
        )
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'mode': 'edit',
                'target_user': target_user,
                'pending_temporary_access_code': pending_temporary_access_code,
            },
        )

    def post(self, request, user_id):
        target_user = get_object_or_404(User, id=user_id)

        if 'reset_user_password' in request.POST:
            temporary_access_code = generate_temporary_access_code()
            target_user.set_password(temporary_access_code)
            target_user.save(update_fields=['password'])

            profile, _profile_created = StoreUserProfile.objects.get_or_create(user=target_user)
            profile.password_change_required = True
            profile.temporary_access_code_plain = temporary_access_code
            profile.save(update_fields=['password_change_required', 'temporary_access_code_plain', 'updated_at'])

            self._invalidate_user_sessions(target_user)
            messages.success(
                request,
                _('Password reset successfully. Temporary access code: %(code)s') % {'code': temporary_access_code},
            )
            return redirect('admin_user_edit', user_id=target_user.id)

        section = None
        if 'save_basic_section' in request.POST:
            section = 'basic'
        elif 'save_checks_section' in request.POST:
            section = 'checks'
        elif 'save_admin_access_section' in request.POST:
            section = 'admin_access'

        if section:
            merged_data = self._build_section_form_data(target_user, request.POST, section)
            form = AdminUserUpdateForm(merged_data, user_instance=target_user)
            if form.is_valid():
                form.save()
                messages.success(request, _('User updated successfully.'))
                return redirect('admin_user_edit', user_id=target_user.id)

            profile, _profile_created = StoreUserProfile.objects.get_or_create(user=target_user)
            pending_temporary_access_code = (
                profile.temporary_access_code_plain if profile.password_change_required else ''
            )
            return render(
                request,
                self.get_template_names()[0],
                {
                    'form': form,
                    'mode': 'edit',
                    'target_user': target_user,
                    'pending_temporary_access_code': pending_temporary_access_code,
                },
            )

        form = AdminUserUpdateForm(request.POST, user_instance=target_user)
        if form.is_valid():
            form.save()
            messages.success(request, _('User updated successfully.'))
            return redirect('admin_user_list')
        profile, _profile_created = StoreUserProfile.objects.get_or_create(user=target_user)
        pending_temporary_access_code = (
            profile.temporary_access_code_plain if profile.password_change_required else ''
        )
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'mode': 'edit',
                'target_user': target_user,
                'pending_temporary_access_code': pending_temporary_access_code,
            },
        )


class AdminUserDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    SECURITY_WORD = 'BORRAR'

    def post(self, request, user_id):
        target_user = get_object_or_404(User.objects.select_related('store_profile'), id=user_id)

        delete_confirmation_word = (request.POST.get('delete_confirmation_word') or '').strip().upper()
        if delete_confirmation_word != self.SECURITY_WORD:
            messages.error(request, _('Delete confirmation failed. Please type BORRAR.'))
            return redirect('admin_user_list')

        if target_user.id == request.user.id:
            messages.error(request, _('You cannot delete your own account.'))
            return redirect('admin_user_list')

        if target_user.is_superuser:
            messages.error(request, _('Superuser accounts cannot be deleted from this interface.'))
            return redirect('admin_user_list')

        profile = getattr(target_user, 'store_profile', None)
        current_balance = profile.current_balance if profile else Decimal('0.00')
        if current_balance < 0:
            messages.error(
                request,
                _('User cannot be deleted because the current balance is negative (%(balance)s).')
                % {'balance': current_balance},
            )
            return redirect('admin_user_list')

        if BalanceRequest.objects.filter(user=target_user, status=BalanceRequest.Status.PENDING).exists():
            messages.error(request, _('User cannot be deleted because there are pending balance requests.'))
            return redirect('admin_user_list')

        if Order.objects.filter(created_by=target_user, status=Order.Status.PENDING).exists():
            messages.error(request, _('User cannot be deleted because there are pending orders.'))
            return redirect('admin_user_list')

        if not target_user.is_active:
            messages.info(request, _('User account is already inactive.'))
            return redirect('admin_user_list')

        target_user.is_active = False
        target_user.save(update_fields=['is_active'])
        messages.success(request, _('User account deactivated successfully. Historical records were preserved.'))
        return redirect('admin_user_list')


class AdminUserBalanceAdjustView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/users/balance_adjust.html'

    def get(self, request, user_id):
        target_user = get_object_or_404(User, id=user_id)
        form = AdminBalanceAdjustmentForm(initial={'user_id': target_user.id})
        profile = getattr(target_user, 'store_profile', None)
        current_balance = profile.current_balance if profile else 0
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'target_user': target_user,
                'current_balance': current_balance,
            },
        )

    @transaction.atomic
    def post(self, request, user_id):
        target_user = get_object_or_404(User, id=user_id)
        form = AdminBalanceAdjustmentForm(request.POST)
        if not form.is_valid():
            profile = getattr(target_user, 'store_profile', None)
            current_balance = profile.current_balance if profile else 0
            messages.error(request, _('Invalid balance amount.'))
            return render(
                request,
                self.get_template_names()[0],
                {
                    'form': form,
                    'target_user': target_user,
                    'current_balance': current_balance,
                },
            )

        amount = form.cleaned_data['amount']
        if form.cleaned_data['user_id'] != target_user.id:
            messages.error(request, _('User mismatch for balance adjustment.'))
            return redirect('admin_user_balance_adjust', user_id=target_user.id)

        profile, profile_created = StoreUserProfile.objects.select_for_update().get_or_create(user=target_user)
        balance_before = profile.current_balance
        profile.current_balance += amount
        profile.save(update_fields=['current_balance', 'updated_at'])
        BalanceLog.objects.create(
            user=target_user,
            changed_by=request.user,
            source=BalanceLog.Source.MANUAL_ADJUSTMENT,
            amount_delta=amount,
            balance_before=balance_before,
            balance_after=profile.current_balance,
            note='Admin manual adjustment',
        )
        messages.success(
            request,
            _('Balance updated for %(username)s.') % {'username': target_user.username},
        )
        return redirect('admin_user_balance_adjust', user_id=target_user.id)


class AdminOrderListView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, ListView):
    template_name = 'admin/orders/list.html'
    context_object_name = 'orders'

    def get_queryset(self):
        status = self.request.GET.get('status', 'pending')
        queryset = Order.objects.select_related('created_by', 'approved_by').prefetch_related('items__product')
        if status == 'approved':
            queryset = queryset.filter(status=Order.Status.APPROVED)
        elif status == 'pending':
            queryset = queryset.filter(status=Order.Status.PENDING)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_status'] = self.request.GET.get('status', 'pending')
        return context


class AdminActionsView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/actions/list.html'

    def _build_context(self):
        context = {}
        pending_orders = (
            Order.objects.filter(status=Order.Status.PENDING)
            .select_related('created_by')
            .prefetch_related('items__product')
            .order_by('id')
        )
        pending_reviews = (
            ProductReview.objects.filter(is_approved=False)
            .select_related('product', 'user')
            .order_by('id')
        )
        pending_balance_requests = (
            BalanceRequest.objects.filter(status=BalanceRequest.Status.PENDING)
            .select_related('user')
            .order_by('id')
        )
        pending_event_comments = (
            EventComment.objects.filter(
                parent__isnull=True,
                user__is_staff=False,
                is_ignored_by_admin=False,
            )
            .annotate(
                has_staff_reply=Exists(
                    EventComment.objects.filter(parent_id=OuterRef('pk'), user__is_staff=True)
                )
            )
            .filter(has_staff_reply=False)
            .select_related('event', 'user')
            .order_by('created_at')
        )
        monthly_profiles = list(
            StoreUserProfile.objects.select_related('user').filter(user__is_staff=False, monthly_fee_enabled=True).only(
                'id',
                'user_id',
                'user__username',
                'current_balance',
                'monthly_fee_enabled',
                'monthly_fee_enabled_at',
                'monthly_fee_last_charged_month',
            )
        )
        monthly_late_users_count = 0
        monthly_pending_months_total = 0
        monthly_late_users = []
        settings_obj, _settings_created = MonthlyFeeSettings.objects.get_or_create(pk=1)
        for profile in monthly_profiles:
            due_months = months_due_for_profile(profile)
            if due_months > 0:
                monthly_late_users_count += 1
                monthly_pending_months_total += due_months
                monthly_late_users.append(
                    {
                        'user': profile.user,
                        'due_months': due_months,
                        'balance': profile.current_balance,
                        'monthly_amount': settings_obj.monthly_amount,
                        'total_due': (settings_obj.monthly_amount or Decimal('0')) * due_months,
                    }
                )
        monthly_late_users.sort(key=lambda item: item['due_months'], reverse=True)
        context['pending_orders'] = pending_orders
        context['pending_reviews'] = pending_reviews
        context['pending_balance_requests'] = pending_balance_requests
        context['pending_event_comments'] = pending_event_comments
        context['monthly_late_users'] = monthly_late_users
        context['monthly_late_users_count'] = monthly_late_users_count
        context['monthly_pending_months_total'] = monthly_pending_months_total
        pending_gamification_rows = [row for row in achieved_gamification_rows() if not row['reward_completed']]
        context['pending_gamification_rows'] = pending_gamification_rows
        context['pending_gamification_count'] = len(pending_gamification_rows)
        pending_counts = {
            'orders': pending_orders.count(),
            'reviews': pending_reviews.count(),
            'balance': pending_balance_requests.count(),
            'event_comments': pending_event_comments.count(),
            'monthly_fee_late': monthly_late_users_count,
            'gamification_validations': len(pending_gamification_rows),
        }
        priority_within_group = {
            'balance': 0,
            'orders': 1,
            'reviews': 2,
            'gamification_validations': 3,
            'event_comments': 4,
            'monthly_fee_late': 5,
        }
        sorted_cards = sorted(
            priority_within_group.keys(),
            key=lambda key: (0 if pending_counts[key] > 0 else 1, priority_within_group[key]),
        )
        context['action_card_order'] = {card_key: idx + 1 for idx, card_key in enumerate(sorted_cards)}
        return context

    def get(self, request):
        return render(request, self.get_template_names()[0], self._build_context())


class AdminSystemView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/system/index.html'

    TAB_SUMMARY = 'summary'
    TAB_UPDATES = 'updates'
    TAB_ENV = 'environment'
    TAB_BACKUPS = 'backups'
    TAB_TESTS = 'tests'
    TAB_BACKENDLOG = 'backendlog'
    ALLOWED_TABS = {TAB_SUMMARY, TAB_UPDATES, TAB_ENV, TAB_BACKUPS, TAB_TESTS, TAB_BACKENDLOG}
    LOG_MAX_LINES = 2000
    LOG_AUTO_REFRESH_OPTIONS = {0, 5, 10, 30, 60}
    EXPORT_DAYS_DEFAULT = 30
    EXPORT_DAYS_MIN = 1
    EXPORT_DAYS_MAX = 365
    BACKUP_TYPES = {'db', 'users_balances', 'products', 'low_stock_products', 'balance_logs_30d', 'orders_recent'}
    UPDATES_HISTORY_MAX_COMMITS = 50
    UPDATE_LOG_MAX_LINES = 4000

    @staticmethod
    def _format_gigabytes(value_bytes):
        return f'{(value_bytes / (1024 ** 3)):.2f} GB'

    @staticmethod
    def _detect_primary_local_ip():
        # UDP connect discovers the outbound local interface/IP without sending traffic.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(('8.8.8.8', 80))
                ip = (sock.getsockname()[0] or '').strip()
                if ip and not ip.startswith('127.'):
                    return ip
        except OSError:
            return ''
        return ''

    def _resolved_app_domain_url(self):
        app_public_url = (os.getenv('APP_PUBLIC_URL') or '').strip()
        if app_public_url:
            parsed = urlparse(app_public_url)
            if parsed.scheme and parsed.hostname:
                # Preserve explicit ports; otherwise use current request port when non-default.
                if parsed.port:
                    return app_public_url.rstrip('/')

                request_port = (self.request.get_port() or '').strip()
                default_port = '443' if parsed.scheme == 'https' else '80'
                host_with_port = parsed.hostname
                if request_port and request_port != default_port:
                    host_with_port = f'{parsed.hostname}:{request_port}'
                rebuilt = parsed._replace(netloc=host_with_port).geturl()
                return rebuilt.rstrip('/')

            return app_public_url.rstrip('/')

        return self.request.build_absolute_uri('/').rstrip('/')

    @staticmethod
    def _build_qr_data_uri(value):
        if not value:
            return ''

        try:
            import qrcode  # type: ignore
        except Exception:
            return ''

        try:
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=12,
                border=2,
            )
            qr.add_data(value)
            qr.make(fit=True)
            image = qr.make_image(fill_color='black', back_color='white')

            buffer = BytesIO()
            image.save(buffer, format='PNG')
            encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
            return f'data:image/png;base64,{encoded}'
        except Exception:
            return ''

    @staticmethod
    def _safe_machine_ips():
        host_name = socket.gethostname()
        endpoints = {}

        # Try to resolve IP-interface mapping via psutil when available.
        try:
            import psutil  # type: ignore

            for interface_name, addresses in psutil.net_if_addrs().items():
                for address in addresses:
                    if address.family != socket.AF_INET:
                        continue
                    ip = (address.address or '').strip()
                    if not ip:
                        continue
                    endpoints[ip] = interface_name
        except Exception:
            pass

        primary_ip = AdminSystemView._detect_primary_local_ip()
        if primary_ip:
            endpoints.setdefault(primary_ip, endpoints.get(primary_ip) or '')

        if endpoints:
            ordered_ips = sorted(endpoints.keys(), key=lambda ip: (ip.startswith('127.'), ip))
            return [{'ip': ip, 'interface': endpoints.get(ip) or ''} for ip in ordered_ips]

        addresses = set()
        try:
            addresses.update(socket.gethostbyname_ex(host_name)[2])
        except OSError:
            pass

        try:
            for item in socket.getaddrinfo(host_name, None):
                ip = item[4][0]
                if ':' in ip:
                    continue
                addresses.add(ip)
        except OSError:
            pass

        if primary_ip:
            addresses.add(primary_ip)

        ordered = sorted(addresses, key=lambda ip: (ip.startswith('127.'), ip))
        if not ordered:
            return [{'ip': 'N/A', 'interface': ''}]
        return [{'ip': ip, 'interface': ''} for ip in ordered]

    def _server_runtime_info(self):
        app_domain_url = self._resolved_app_domain_url()

        cpu_count = os.cpu_count() or 1
        disk_stats = shutil.disk_usage(settings.BASE_DIR)
        db_ping_ms = 'N/A'
        try:
            start = time.perf_counter()
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                cursor.fetchone()
            elapsed_ms = (time.perf_counter() - start) * 1000
            db_ping_ms = f'{elapsed_ms:.1f} ms'
        except Exception:
            db_ping_ms = 'N/A'

        return {
            'app_domain_url': app_domain_url,
            'app_domain_qr_data_uri': self._build_qr_data_uri(app_domain_url),
            'machine_ips': self._safe_machine_ips(),
            'performance': {
                'cpu_count': cpu_count,
                'db_ping_ms': db_ping_ms,
                'disk_free': self._format_gigabytes(disk_stats.free),
                'disk_total': self._format_gigabytes(disk_stats.total),
            },
        }

    def _selected_tab(self):
        requested_tab = (self.request.GET.get('tab') or self.TAB_SUMMARY).strip().lower()
        if requested_tab not in self.ALLOWED_TABS:
            return self.TAB_SUMMARY
        return requested_tab

    def _system_settings(self):
        settings_obj, _created = SystemSettings.objects.get_or_create(pk=1)
        return settings_obj

    def _monthly_settings(self):
        settings_obj, _created = MonthlyFeeSettings.objects.get_or_create(pk=1)
        return settings_obj

    def _build_context(self, *, system_form=None, monthly_form=None):
        selected_tab = self._selected_tab()
        system_settings = self._system_settings()
        monthly_settings = self._monthly_settings()
        git_info = self._resolve_git_info()
        git_repo_url = self._resolve_git_repo_url()
        git_repo_links = self._normalize_git_repo_links(git_repo_url)
        git_history = (
            self._resolve_git_history(limit=self.UPDATES_HISTORY_MAX_COMMITS)
            if selected_tab == self.TAB_UPDATES
            else None
        )
        last_update_log = self._read_last_update_log() if selected_tab == self.TAB_UPDATES else None

        if system_form is None:
            system_form = SystemSettingsForm(instance=system_settings, prefix='system')
        if monthly_form is None:
            monthly_form = MonthlyFeeSettingsForm(instance=monthly_settings, prefix='monthly')

        backendlog_filter = (self.request.GET.get('log_filter') or '').strip()
        try:
            backendlog_auto_refresh = int(self.request.GET.get('log_auto_refresh') or 0)
        except (TypeError, ValueError):
            backendlog_auto_refresh = 0
        if backendlog_auto_refresh not in self.LOG_AUTO_REFRESH_OPTIONS:
            backendlog_auto_refresh = 0

        balance_export_days = self._parse_export_days_value(self.request.GET.get('balance_days'))
        orders_export_days = self._parse_export_days_value(self.request.GET.get('orders_days'))

        backendlog = self._read_backendlog_content() if selected_tab == self.TAB_BACKENDLOG else None
        server_runtime_info = self._server_runtime_info() if selected_tab == self.TAB_SUMMARY else None
        system_tests_overview = build_system_tests_overview() if selected_tab == self.TAB_TESTS else None

        return {
            'selected_tab': selected_tab,
            'system_form': system_form,
            'monthly_form': monthly_form,
            'system_settings': system_settings,
            'code_version': os.getenv('APP_VERSION') or 'N/A',
            'git_branch': git_info['branch'],
            'git_commit': git_info['commit'],
            'git_repo_url': git_repo_url,
            'git_repo_web_url': git_repo_links['web_url'],
            'git_repo_app_url': git_repo_links['app_url'],
            'updates_current_commit': (git_history or {}).get(
                'current',
                {'hash': 'N/A', 'subject': 'N/A', 'date': ''},
            ),
            'updates_commit_history': (git_history or {}).get('history', []),
            'updates_last_log': last_update_log,
            'updates_is_running': is_update_running(),
            'platform_label': system_settings.store_name,
            'backendlog': backendlog,
            'backendlog_filter': backendlog_filter,
            'backendlog_auto_refresh': backendlog_auto_refresh,
            'balance_export_days': balance_export_days,
            'balance_export_days_min': self.EXPORT_DAYS_MIN,
            'balance_export_days_max': self.EXPORT_DAYS_MAX,
            'orders_export_days': orders_export_days,
            'orders_export_days_min': self.EXPORT_DAYS_MIN,
            'orders_export_days_max': self.EXPORT_DAYS_MAX,
            'server_runtime_info': server_runtime_info,
            'system_tests_overview': system_tests_overview,
        }

    def _resolve_git_info(self):
        fallback = {
            'branch': 'N/A',
            'commit': 'N/A',
        }

        base_dir = str(getattr(settings, 'BASE_DIR', '')) or os.getcwd()
        git_cmd = get_git_executable()
        if not git_cmd:
            return fallback

        try:
            branch_result = subprocess.run(
                [git_cmd, 'rev-parse', '--abbrev-ref', 'HEAD'],
                cwd=base_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            commit_result = subprocess.run(
                [git_cmd, 'log', '-1', '--pretty=format:%h %s'],
                cwd=base_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return fallback

        branch = (branch_result.stdout or '').strip() if branch_result.returncode == 0 else ''
        commit = (commit_result.stdout or '').strip() if commit_result.returncode == 0 else ''

        return {
            'branch': branch or fallback['branch'],
            'commit': commit or fallback['commit'],
        }

    def _resolve_git_history(self, limit=50):
        fallback = {
            'current': {
                'hash': 'N/A',
                'subject': 'N/A',
                'date': '',
            },
            'history': [],
        }

        base_dir = str(getattr(settings, 'BASE_DIR', '')) or os.getcwd()
        git_cmd = get_git_executable()
        if not git_cmd:
            return fallback

        try:
            history_result = subprocess.run(
                [git_cmd, 'log', f'-n{limit}', '--pretty=format:%h%x09%s%x09%ad', '--date=short'],
                cwd=base_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return fallback

        if history_result.returncode != 0:
            return fallback

        lines = [(line or '').strip() for line in (history_result.stdout or '').splitlines() if (line or '').strip()]
        if not lines:
            return fallback

        formatted = []
        for line in lines:
            parts = line.split('\t', 2)
            if len(parts) == 3:
                sha, subject, date_str = parts
                formatted.append(
                    {
                        'hash': sha.strip() or 'N/A',
                        'subject': subject.strip() or 'N/A',
                        'date': date_str.strip(),
                    }
                )
            else:
                formatted.append(
                    {
                        'hash': 'N/A',
                        'subject': line,
                        'date': '',
                    }
                )

        return {
            'current': formatted[0],
            'history': formatted[1:],
        }

    def _resolve_git_repo_url(self):
        fallback = 'N/A'
        base_dir = str(getattr(settings, 'BASE_DIR', '')) or os.getcwd()
        git_cmd = get_git_executable()
        if not git_cmd:
            return fallback

        try:
            repo_result = subprocess.run(
                [git_cmd, 'config', '--get', 'remote.origin.url'],
                cwd=base_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return fallback

        repo_url = (repo_result.stdout or '').strip() if repo_result.returncode == 0 else ''
        return repo_url or fallback

    def _normalize_git_repo_links(self, repo_url):
        fallback = {
            'web_url': 'N/A',
            'app_url': '',
        }

        source_url = (repo_url or '').strip()
        if not source_url or source_url == 'N/A':
            return fallback

        web_url = source_url

        # git@github.com:owner/repo.git -> https://github.com/owner/repo
        if source_url.startswith('git@') and ':' in source_url:
            host_part, path_part = source_url.split(':', 1)
            host = host_part.split('@', 1)[-1]
            repo_path = path_part.rstrip('/')
            if repo_path.endswith('.git'):
                repo_path = repo_path[:-4]
            web_url = f'https://{host}/{repo_path}'
        else:
            parsed = urlparse(source_url)
            if parsed.scheme in {'ssh', 'git', 'http', 'https'} and parsed.netloc:
                repo_path = (parsed.path or '').rstrip('/')
                if repo_path.endswith('.git'):
                    repo_path = repo_path[:-4]
                scheme = 'https' if parsed.scheme in {'ssh', 'git'} else parsed.scheme
                web_url = f'{scheme}://{parsed.netloc}{repo_path}'

        app_url = ''
        if web_url.startswith('https://github.com/'):
            app_url = f'x-github-client://openRepo/{web_url}'

        return {
            'web_url': web_url,
            'app_url': app_url,
        }

    def _read_last_update_log(self):
        log_path = str(get_update_log_path())
        if not os.path.exists(log_path):
            return {
                'path': log_path,
                'exists': False,
                'content': '',
                'truncated': False,
                'max_lines': self.UPDATE_LOG_MAX_LINES,
            }

        with open(log_path, 'r', encoding='utf-8', errors='replace') as log_file:
            lines = log_file.readlines()

        truncated = len(lines) > self.UPDATE_LOG_MAX_LINES
        if truncated:
            lines = lines[-self.UPDATE_LOG_MAX_LINES :]

        return {
            'path': log_path,
            'exists': True,
            'content': ''.join(lines),
            'truncated': truncated,
            'max_lines': self.UPDATE_LOG_MAX_LINES,
        }

    def _parse_export_days_value(self, raw_days):
        try:
            days = int(raw_days)
        except (TypeError, ValueError):
            return self.EXPORT_DAYS_DEFAULT

        if days < self.EXPORT_DAYS_MIN:
            return self.EXPORT_DAYS_MIN
        if days > self.EXPORT_DAYS_MAX:
            return self.EXPORT_DAYS_MAX
        return days

    def _read_backendlog_content(self):
        log_path = str(getattr(settings, 'BACKEND_LOG_FILE', 'backendlog.log'))
        if not os.path.exists(log_path):
            return {
                'path': log_path,
                'exists': False,
                'is_empty': False,
                'content': '',
                'truncated': False,
                'max_lines': self.LOG_MAX_LINES,
            }

        with open(log_path, 'r', encoding='utf-8', errors='replace') as log_file:
            lines = log_file.readlines()

        truncated = len(lines) > self.LOG_MAX_LINES
        if truncated:
            lines = lines[-self.LOG_MAX_LINES :]

        return {
            'path': log_path,
            'exists': True,
            'is_empty': len(lines) == 0,
            'content': ''.join(lines),
            'truncated': truncated,
            'max_lines': self.LOG_MAX_LINES,
        }

    def _download_database_backup(self):
        from django.conf import settings as django_settings

        db_engine = django_settings.DATABASES['default']['ENGINE']
        db_name = django_settings.DATABASES['default']['NAME']

        if 'sqlite3' not in db_engine:
            messages.error(self.request, _('Automatic backup download is only available for SQLite projects.'))
            return redirect(f"{self.request.path}?tab={self.TAB_BACKUPS}")

        db_path = str(db_name)
        if not os.path.exists(db_path):
            messages.error(self.request, _('Database file was not found.'))
            return redirect(f"{self.request.path}?tab={self.TAB_BACKUPS}")

        filename = f"cashflow-backup-{timezone.now().strftime('%Y%m%d-%H%M%S')}.sqlite3"
        return FileResponse(open(db_path, 'rb'), as_attachment=True, filename=filename)

    def _csv_response(self, *, filename, headers, rows):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        writer = csv.writer(response)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
        return response

    def _download_users_balances_backup(self):
        timestamp = timezone.now().strftime('%Y%m%d-%H%M%S')
        users = User.objects.select_related('store_profile').order_by('username')

        rows = []
        for user in users:
            profile = getattr(user, 'store_profile', None)
            current_balance = profile.current_balance if profile else Decimal('0.00')
            member_number = (profile.member_number if profile else '') or ''
            rows.append([
                user.id,
                user.username,
                member_number,
                'admin' if user.is_staff else 'user',
                f"{current_balance:.2f}",
            ])

        return self._csv_response(
            filename=f'cashflow-users-balances-{timestamp}.csv',
            headers=['user_id', 'username', 'member_number', 'role', 'current_balance'],
            rows=rows,
        )

    def _download_products_backup(self):
        timestamp = timezone.now().strftime('%Y%m%d-%H%M%S')
        products = Product.objects.select_related('category', 'supplier').annotate(
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

        rows = []
        for product in products:
            rows.append([
                product.id,
                product.name,
                product.sku,
                product.category.name if product.category else '',
                product.supplier.name if product.supplier else '',
                f"{product.price:.2f}",
                f"{product.stock:.2f}",
                f"{product.min_stock:.2f}",
                'yes' if product.is_active else 'no',
                'yes' if product.is_public_listing else 'no',
            ])

        return self._csv_response(
            filename=f'cashflow-products-{timestamp}.csv',
            headers=[
                'product_id',
                'name',
                'sku',
                'category',
                'supplier',
                'price',
                'stock',
                'min_stock',
                'is_active',
                'is_public_listing',
            ],
            rows=rows,
        )

    def _download_low_stock_products_backup(self):
        timestamp = timezone.now().strftime('%Y%m%d-%H%M%S')
        products = (
            Product.objects.select_related('category')
            .filter(is_active=True, stock__lte=F('min_stock'))
            .order_by('stock', 'name')
        )

        low_stock_rows = []
        for product in products:
            low_stock_rows.append([
                product.id,
                product.name,
                product.category.name if product.category else '',
                f"{product.stock:.2f}",
                f"{product.min_stock:.2f}",
            ])

        return self._csv_response(
            filename=f'cashflow-low-stock-products-{timestamp}.csv',
            headers=['product_id', 'name', 'category', 'stock', 'min_stock'],
            rows=low_stock_rows,
        )

    def _download_balance_logs_recent_backup(self, *, days):
        timestamp = timezone.now().strftime('%Y%m%d-%H%M%S')
        from_date = timezone.now() - timedelta(days=days)
        logs = (
            BalanceLog.objects.select_related('user', 'changed_by')
            .filter(created_at__gte=from_date)
            .order_by('-created_at')
        )

        rows = []
        for log in logs:
            rows.append([
                log.id,
                log.created_at.isoformat(),
                log.user.username,
                log.changed_by.username if log.changed_by else '',
                log.source,
                f"{log.amount_delta:.2f}",
                f"{log.balance_before:.2f}",
                f"{log.balance_after:.2f}",
                log.note,
            ])

        return self._csv_response(
            filename=f'cashflow-balance-logs-last-{days}d-{timestamp}.csv',
            headers=[
                'log_id',
                'created_at',
                'username',
                'changed_by',
                'source',
                'amount_delta',
                'balance_before',
                'balance_after',
                'note',
            ],
            rows=rows,
        )

    def _download_orders_recent_backup(self, *, days):
        timestamp = timezone.now().strftime('%Y%m%d-%H%M%S')
        from_date = timezone.now() - timedelta(days=days)
        orders = (
            Order.objects.select_related('created_by', 'approved_by')
            .annotate(items_count=Count('items'))
            .filter(created_at__gte=from_date)
            .order_by('-created_at')
        )

        rows = []
        for order in orders:
            rows.append([
                order.id,
                order.created_at.isoformat(),
                order.created_by.username,
                order.customer_name,
                f"{order.total_amount:.2f}",
                order.status,
                order.items_count,
                order.approved_by.username if order.approved_by else '',
                order.approved_at.isoformat() if order.approved_at else '',
                order.rejection_reason,
            ])

        return self._csv_response(
            filename=f'cashflow-orders-last-{days}d-{timestamp}.csv',
            headers=[
                'order_id',
                'created_at',
                'created_by',
                'customer_name',
                'total_amount',
                'status',
                'items_count',
                'approved_by',
                'approved_at',
                'rejection_reason',
            ],
            rows=rows,
        )

    def _download_backup(self, backup_type):
        if backup_type == 'db':
            return self._download_database_backup()
        if backup_type == 'users_balances':
            return self._download_users_balances_backup()
        if backup_type == 'products':
            return self._download_products_backup()
        if backup_type == 'low_stock_products':
            return self._download_low_stock_products_backup()
        if backup_type == 'balance_logs_30d':
            days = self._parse_export_days_value(self.request.GET.get('balance_days'))
            return self._download_balance_logs_recent_backup(days=days)
        if backup_type == 'orders_recent':
            days = self._parse_export_days_value(self.request.GET.get('orders_days'))
            return self._download_orders_recent_backup(days=days)
        return redirect(f"{self.request.path}?tab={self.TAB_BACKUPS}")

    def get(self, request):
        if self._selected_tab() == self.TAB_BACKUPS:
            backup_type = (request.GET.get('download') or '').strip().lower()
            if backup_type in self.BACKUP_TYPES:
                return self._download_backup(backup_type)
        return render(request, self.get_template_names()[0], self._build_context())

    def post(self, request):
        action = (request.POST.get('action') or '').strip()

        if action == 'run_system_test':
            test_type = (request.POST.get('test_type') or '').strip()
            try:
                test_run = run_system_test(test_type, executed_by=request.user, save_result=True)
                if test_run.status == test_run.Status.SUCCESS:
                    messages.success(request, _('Test executed successfully.'))
                elif test_run.status == test_run.Status.SKIPPED:
                    messages.warning(request, _('Test is not supported in this environment.'))
                else:
                    messages.error(request, _('Test failed. Review logs for details.'))
            except ValueError:
                messages.error(request, _('Invalid test type.'))
            return redirect(f"{request.path}?tab={self.TAB_TESTS}")

        if action == 'save_environment':
            system_settings = self._system_settings()
            monthly_settings = self._monthly_settings()
            system_form = SystemSettingsForm(request.POST, instance=system_settings, prefix='system')
            monthly_form = MonthlyFeeSettingsForm(request.POST, instance=monthly_settings, prefix='monthly')

            if system_form.is_valid() and monthly_form.is_valid():
                updated_system = system_form.save(commit=False)
                updated_system.updated_by = request.user
                updated_system.save()

                updated_monthly = monthly_form.save(commit=False)
                updated_monthly.updated_by = request.user
                updated_monthly.save()

                messages.success(request, _('System settings updated successfully.'))
                return redirect(f"{request.path}?tab={self.TAB_ENV}")

            context = self._build_context(system_form=system_form, monthly_form=monthly_form)
            context['selected_tab'] = self.TAB_ENV
            return render(request, self.get_template_names()[0], context)

        if action == 'run_update':
            result = start_platform_update_background(initiated_by=request.user.username)
            if result['started']:
                messages.success(request, _('Platform update started in background. The page will refresh while it runs.'))
            else:
                messages.warning(request, _('An update is already running.'))
            return redirect(f"{request.path}?tab={self.TAB_UPDATES}")

        return redirect(f"{request.path}?tab={self._selected_tab()}")


class AdminOrderApprovalView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/orders/approval.html'

    @staticmethod
    def _build_context(order, form):
        profile, _created = StoreUserProfile.objects.get_or_create(user=order.created_by)
        current_balance = profile.current_balance
        charge_total = sum(
            ((item.quantity * item.unit_price) for item in order.items.all() if not item.is_gift),
            Decimal('0.00'),
        )
        return {
            'order': order,
            'form': form,
            'order_user_current_balance': current_balance,
            'order_charge_total': charge_total,
            'order_user_balance_after_approval': current_balance - charge_total,
        }

    def get(self, request, pk):
        order = get_object_or_404(Order, pk=pk)
        form = OrderRejectForm()
        return render(request, self.get_template_names()[0], self._build_context(order, form))

    def post(self, request, pk):
        order = get_object_or_404(Order, pk=pk)
        action = request.POST.get('action')
        next_page = safe_redirect_target(request, request.POST.get('next'), 'admin_actions')
        form = OrderRejectForm(request.POST)

        if action == 'approve':
            try:
                gift_item_ids = request.POST.getlist('gift_item_ids')
                approve_order(order=order, approved_by=request.user, gift_item_ids=gift_item_ids)
                messages.success(request, _('Order approved and stock updated.'))
                return redirect(next_page)
            except ValidationError as exc:
                messages.error(request, exc.message)

        if action == 'reject':
            if form.is_valid():
                try:
                    reject_order(
                        order=order,
                        approved_by=request.user,
                        reason=form.cleaned_data.get('rejection_reason', ''),
                    )
                    messages.success(request, _('Order rejected.'))
                    return redirect(next_page)
                except ValidationError as exc:
                    messages.error(request, exc.message)

        return render(request, self.get_template_names()[0], self._build_context(order, form))


class AdminUserPurchaseHistoryView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = 'admin/users/purchase_history.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target_user = get_object_or_404(User, pk=self.kwargs['user_id'])
        context['target_user'] = target_user
        context['approved_orders'] = Order.objects.filter(
            created_by=target_user,
            status=Order.Status.APPROVED,
        ).prefetch_related('items__product')
        context['direct_sales'] = Sale.objects.filter(customer=target_user).prefetch_related('items__product')
        return context


class AdminUserStrikesView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/users/strikes.html'

    def get(self, request, user_id):
        target_user = get_object_or_404(User, pk=user_id)
        strikes = Strike.objects.filter(user=target_user).select_related('created_by')
        form = StrikeForm(initial={'strike_date': timezone.localdate()})
        return render(
            request,
            self.get_template_names()[0],
            {
                'target_user': target_user,
                'strikes': strikes,
                'strikes_count': strikes.count(),
                'form': form,
            },
        )

    def post(self, request, user_id):
        target_user = get_object_or_404(User, pk=user_id)
        strikes = Strike.objects.filter(user=target_user).select_related('created_by')
        form = StrikeForm(request.POST)
        if form.is_valid():
            strike = form.save(commit=False)
            strike.user = target_user
            strike.created_by = request.user
            strike.save()
            messages.success(request, _('Strike added successfully.'))
            return redirect('admin_user_strikes', user_id=target_user.id)

        return render(
            request,
            self.get_template_names()[0],
            {
                'target_user': target_user,
                'strikes': strikes,
                'strikes_count': strikes.count(),
                'form': form,
            },
        )


class AdminUserInfoView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, TemplateView):
    template_name = 'admin/users/info.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        target_user = get_object_or_404(User, pk=self.kwargs['user_id'])
        profile, _profile_created = StoreUserProfile.objects.get_or_create(user=target_user)
        pending_temporary_access_code = profile.temporary_access_code_plain if profile.password_change_required else ''

        approved_orders = Order.objects.filter(created_by=target_user, status=Order.Status.APPROVED)
        direct_sales = Sale.objects.filter(customer=target_user)

        order_items_qs = OrderItem.objects.filter(order__in=approved_orders).select_related(
            'product',
            'product__category',
        )
        sale_items_qs = SaleItem.objects.filter(sale__in=direct_sales).select_related(
            'product',
            'product__category',
        )

        product_stats = {}
        category_stats = {}

        def register_item(item):
            product_id = item.product_id
            if product_id not in product_stats:
                product_stats[product_id] = {
                    'product': item.product,
                    'quantity': 0,
                    'spent': Decimal('0.00'),
                    'lines': 0,
                }
            product_stats[product_id]['quantity'] += item.quantity
            product_stats[product_id]['spent'] += item.quantity * item.unit_price
            product_stats[product_id]['lines'] += 1

            category_name = item.product.category.name if item.product.category else _('Uncategorized')
            if category_name not in category_stats:
                category_stats[category_name] = 0
            category_stats[category_name] += item.quantity

        for item in order_items_qs:
            register_item(item)
        for item in sale_items_qs:
            register_item(item)

        ranked_products = sorted(product_stats.values(), key=lambda value: value['quantity'], reverse=True)
        favorite_products = ranked_products[:3]

        tried_product_ids = set(product_stats.keys())
        untried_products = list(
            Product.objects.filter(is_active=True, is_public_listing=True)
            .exclude(id__in=tried_product_ids)
            .annotate(
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
            )
            .order_by(
                'category__display_order',
                'category__name',
                'featured_first',
                'has_manual_order',
                'display_order',
                'name',
            )[:12]
        )

        reviewed_product_ids = set(
            ProductReview.objects.filter(user=target_user).values_list('product_id', flat=True)
        )
        unreviewed_products = [
            entry['product'] for entry in ranked_products if entry['product'].id not in reviewed_product_ids
        ][:12]

        action_dates = list(
            approved_orders.exclude(approved_at__isnull=True).values_list('approved_at', flat=True)
        ) + list(direct_sales.values_list('created_at', flat=True))
        action_dates = sorted(action_dates)
        avg_action_days = None
        if len(action_dates) > 1:
            gaps = []
            for index in range(1, len(action_dates)):
                delta = action_dates[index] - action_dates[index - 1]
                gaps.append(delta.total_seconds() / 86400)
            avg_action_days = sum(gaps) / len(gaps)

        favorite_product_hint = None
        if favorite_products:
            top = favorite_products[0]
            avg_qty = top['quantity'] / top['lines'] if top['lines'] else 0
            favorite_product_hint = {
                'name': top['product'].name,
                'avg_qty': avg_qty,
            }

        monthly_spend_rows = (
            BalanceLog.objects.filter(
                user=target_user,
                source=BalanceLog.Source.ORDER_APPROVAL,
                amount_delta__lt=0,
            )
            .annotate(month=TruncMonth('created_at'))
            .values('month')
            .annotate(spent=Sum('amount_delta'))
            .order_by('month')
        )
        monthly_spend_values = [abs(row['spent']) for row in monthly_spend_rows if row['spent'] is not None]
        avg_monthly_spend = (
            sum(monthly_spend_values) / len(monthly_spend_values)
            if monthly_spend_values
            else Decimal('0.00')
        )

        approved_count = approved_orders.count()
        pending_count = Order.objects.filter(created_by=target_user, status=Order.Status.PENDING).count()
        rejected_count = Order.objects.filter(created_by=target_user, status=Order.Status.REJECTED).count()
        total_actions = approved_count + direct_sales.count()
        total_spent = (
            approved_orders.aggregate(value=Sum('total_amount'))['value'] or Decimal('0.00')
        ) + (
            direct_sales.aggregate(value=Sum('total_amount'))['value'] or Decimal('0.00')
        )
        avg_ticket = (total_spent / total_actions) if total_actions else Decimal('0.00')

        top_categories = sorted(
            category_stats.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:3]

        gamification_rows = achieved_gamification_rows(
            users=User.objects.filter(pk=target_user.id),
        )
        completed_gamifications = [row for row in gamification_rows if row['status']['achieved']]
        rewarded_gamifications = [row for row in completed_gamifications if row['reward_completed']]
        strikes = list(
            Strike.objects.filter(user=target_user)
            .select_related('created_by')
            .order_by('-strike_date', '-created_at')
        )

        context.update(
            {
                'target_user': target_user,
                'pending_temporary_access_code': pending_temporary_access_code,
                'favorite_products': favorite_products,
                'untried_products': untried_products,
                'unreviewed_products': unreviewed_products,
                'avg_action_days': avg_action_days,
                'favorite_product_hint': favorite_product_hint,
                'avg_monthly_spend': avg_monthly_spend,
                'approved_count': approved_count,
                'pending_count': pending_count,
                'rejected_count': rejected_count,
                'total_actions': total_actions,
                'total_spent': total_spent,
                'avg_ticket': avg_ticket,
                'top_categories': top_categories,
                'last_action_at': action_dates[-1] if action_dates else None,
                'completed_gamifications': completed_gamifications,
                'completed_gamifications_count': len(completed_gamifications),
                'rewarded_gamifications_count': len(rewarded_gamifications),
                'strikes': strikes,
                'strikes_count': len(strikes),
            }
        )
        return context


class AdminReviewModerationView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, ListView):
    template_name = 'admin/reviews/list.html'
    context_object_name = 'reviews'

    def get_queryset(self):
        status = self.request.GET.get('status', 'pending')
        queryset = ProductReview.objects.select_related('product', 'user')
        query = (self.request.GET.get('q') or '').strip()
        rating = (self.request.GET.get('rating') or '').strip()

        if status == 'approved':
            queryset = queryset.filter(is_approved=True)
        elif status != 'all':
            queryset = queryset.filter(is_approved=False)

        if query:
            queryset = queryset.filter(
                Q(product__name__icontains=query)
                | Q(user__username__icontains=query)
                | Q(message__icontains=query)
            )

        if rating in {'1', '2', '3', '4', '5'}:
            queryset = queryset.filter(rating=int(rating))

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_status'] = self.request.GET.get('status', 'pending')
        context['query'] = (self.request.GET.get('q') or '').strip()
        context['filter_rating'] = (self.request.GET.get('rating') or '').strip()
        context['pending_count'] = ProductReview.objects.filter(is_approved=False).count()
        context['approved_count'] = ProductReview.objects.filter(is_approved=True).count()
        context['avg_rating'] = ProductReview.objects.filter(is_approved=True).aggregate(
            value=Avg('rating')
        )['value']
        return context


class AdminReviewApproveView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, review_id):
        review = get_object_or_404(ProductReview, id=review_id)
        review.is_approved = True
        review.save(update_fields=['is_approved', 'updated_at'])
        messages.success(request, _('Review approved.'))
        return redirect(safe_redirect_target(request, request.POST.get('next'), 'admin_reviews'))


class AdminReviewRejectView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, review_id):
        review = get_object_or_404(ProductReview, id=review_id)
        review.is_approved = False
        review.save(update_fields=['is_approved', 'updated_at'])
        messages.success(request, _('Review hidden from public listing.'))
        return redirect(safe_redirect_target(request, request.POST.get('next'), 'admin_reviews'))


class AdminBalanceRequestListView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, ListView):
    template_name = 'admin/balance/requests.html'
    context_object_name = 'requests'

    def get_queryset(self):
        status = self.request.GET.get('status', 'pending')
        queryset = BalanceRequest.objects.select_related('user', 'reviewed_by')
        if status == 'approved':
            queryset = queryset.filter(status=BalanceRequest.Status.APPROVED)
        elif status == 'rejected':
            queryset = queryset.filter(status=BalanceRequest.Status.REJECTED)
        elif status != 'all':
            queryset = queryset.filter(status=BalanceRequest.Status.PENDING)

        query = (self.request.GET.get('q') or '').strip()
        if query:
            queryset = queryset.filter(user__username__icontains=query)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_status'] = self.request.GET.get('status', 'pending')
        context['query'] = (self.request.GET.get('q') or '').strip()
        context['pending_count'] = BalanceRequest.objects.filter(status=BalanceRequest.Status.PENDING).count()
        context['approved_count'] = BalanceRequest.objects.filter(status=BalanceRequest.Status.APPROVED).count()
        context['rejected_count'] = BalanceRequest.objects.filter(status=BalanceRequest.Status.REJECTED).count()
        return context


class AdminBalanceRequestApproveView(LoginRequiredMixin, StaffRequiredMixin, View):
    @transaction.atomic
    def post(self, request, request_id):
        balance_request = get_object_or_404(BalanceRequest, id=request_id)
        next_page = safe_redirect_target(request, request.POST.get('next'), 'admin_balance_requests')
        if balance_request.status != BalanceRequest.Status.PENDING:
            messages.error(request, _('Only pending requests can be approved.'))
            return redirect(next_page)

        profile, profile_created = StoreUserProfile.objects.select_for_update().get_or_create(user=balance_request.user)
        balance_before = profile.current_balance
        profile.current_balance += balance_request.amount
        profile.save(update_fields=['current_balance', 'updated_at'])
        BalanceLog.objects.create(
            user=balance_request.user,
            changed_by=request.user,
            source=BalanceLog.Source.BALANCE_REQUEST_APPROVAL,
            amount_delta=balance_request.amount,
            balance_before=balance_before,
            balance_after=profile.current_balance,
            note=f'Balance request #{balance_request.id} approved',
        )

        balance_request.status = BalanceRequest.Status.APPROVED
        balance_request.reviewed_by = request.user
        balance_request.reviewed_at = timezone.now()
        balance_request.rejection_reason = ''
        balance_request.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'rejection_reason', 'updated_at'])
        messages.success(request, _('Balance request approved and applied.'))
        return redirect(next_page)


class AdminBalanceRequestRejectView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, request_id):
        balance_request = get_object_or_404(BalanceRequest, id=request_id)
        next_page = safe_redirect_target(request, request.POST.get('next'), 'admin_balance_requests')
        if balance_request.status != BalanceRequest.Status.PENDING:
            messages.error(request, _('Only pending requests can be rejected.'))
            return redirect(next_page)

        balance_request.status = BalanceRequest.Status.REJECTED
        balance_request.reviewed_by = request.user
        balance_request.reviewed_at = timezone.now()
        balance_request.rejection_reason = (request.POST.get('rejection_reason') or '').strip()
        balance_request.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'rejection_reason', 'updated_at'])
        messages.success(request, _('Balance request rejected.'))
        return redirect(next_page)


class AdminBalanceLogListView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, ListView):
    template_name = 'admin/balance/logs.html'
    context_object_name = 'logs'

    def get_queryset(self):
        queryset = BalanceLog.objects.select_related('user', 'changed_by')
        query = (self.request.GET.get('q') or '').strip()
        source = (self.request.GET.get('source') or '').strip()
        user_id = (self.request.GET.get('user_id') or '').strip()

        if query:
            queryset = queryset.filter(
                Q(user__username__icontains=query)
                | Q(changed_by__username__icontains=query)
                | Q(note__icontains=query)
            )
        if source in {choice[0] for choice in BalanceLog.Source.choices}:
            queryset = queryset.filter(source=source)
        if user_id.isdigit():
            queryset = queryset.filter(user_id=int(user_id))
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['query'] = (self.request.GET.get('q') or '').strip()
        context['filter_source'] = (self.request.GET.get('source') or '').strip()
        context['filter_user_id'] = (self.request.GET.get('user_id') or '').strip()
        return context
