from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Case, Count, IntegerField, Prefetch, Q, Value, When
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from core.controllers import build_user_dashboard_context
from core.gamification import evaluate_gamification_for_user
from core.forms import (
    BalanceRequestForm,
    EventCommentForm,
    OrderForm,
    OrderItemFormSet,
    ProductReviewForm,
    SurveyResponseForm,
    StoreUserPasswordChangeForm,
    StoreUserProfileForm,
)
from core.models import (
    Event,
    EventComment,
    EventRegistration,
    Gamification,
    Survey,
    SurveyOption,
    SurveyResponse,
    SurveyResponseOption,
)
from core.webviews.mixins import NonStaffRequiredMixin, ResponsiveTemplateMixin
from customers.models import BalanceLog, BalanceRequest, MonthlyFeeSettings, StoreUserProfile
from customers.services import months_due_for_profile
from inventory.models import Category, Product, ProductReview, ProductSheetField, ProductSheetUrl
from sales.models import Order, Sale
from sales.services import create_order, update_order
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from datetime import date


CART_SESSION_KEY = 'user_cart'


def _parse_positive_quantity(raw_value):
    try:
        parsed = Decimal(str(raw_value).strip().replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError, AttributeError):
        return None
    if parsed <= 0:
        return None
    return parsed.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)


def _serialize_quantity(value):
    normalized = format(Decimal(value).normalize(), 'f')
    if '.' in normalized:
        normalized = normalized.rstrip('0').rstrip('.')
    return normalized or '0'


def _truncate_money(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _distribute_line_totals(items, target_total):
    quant = Decimal('0.01')
    target = _truncate_money(target_total or Decimal('0.00'))
    chargeable = [item for item in items if not getattr(item, 'is_gift', False)]
    if not chargeable:
        return {getattr(item, 'id', None): Decimal('0.00') for item in items}

    buckets = []
    base_sum = Decimal('0.00')
    for item in chargeable:
        raw = (Decimal(item.quantity) * Decimal(item.unit_price)).quantize(Decimal('0.0001'))
        base = raw.quantize(quant, rounding=ROUND_DOWN)
        remainder = raw - base
        base_sum += base
        buckets.append({'item_id': item.id, 'base': base, 'remainder': remainder})

    diff = target - base_sum
    steps = int((diff / quant).to_integral_value())
    if steps > 0:
        buckets.sort(key=lambda row: (row['remainder'], row['item_id']), reverse=True)
        for idx in range(steps):
            buckets[idx % len(buckets)]['base'] += quant
    elif steps < 0:
        buckets.sort(key=lambda row: (row['remainder'], row['item_id']))
        for idx in range(abs(steps)):
            candidate = buckets[idx % len(buckets)]
            if candidate['base'] >= quant:
                candidate['base'] -= quant

    distributed = {row['item_id']: row['base'] for row in buckets}
    for item in items:
        distributed.setdefault(item.id, Decimal('0.00'))
    return distributed


def _formset_items(formset):
    items = []
    for form in formset.forms:
        if not hasattr(form, 'cleaned_data'):
            continue
        data = form.cleaned_data
        if not data or data.get('DELETE'):
            continue
        items.append(
            {
                'product': data['product'],
                'quantity': data['quantity'],
            }
        )
    return items


def _can_review_product(user, product):
    approved_order_consumed = Order.objects.filter(
        created_by=user,
        status=Order.Status.APPROVED,
        items__product=product,
    ).exists()
    direct_sale_consumed = Sale.objects.filter(customer=user, items__product=product, is_voided=False).exists()
    return approved_order_consumed or direct_sale_consumed


def _read_cart(request):
    raw_cart = request.session.get(CART_SESSION_KEY, {})
    if not isinstance(raw_cart, dict):
        return {}
    cleaned = {}
    for product_id, quantity in raw_cart.items():
        if not str(product_id).isdigit():
            continue
        parsed_quantity = _parse_positive_quantity(quantity)
        if parsed_quantity is None:
            continue
        cleaned[str(product_id)] = parsed_quantity
    return cleaned


def _write_cart(request, cart):
    serialized = {}
    for product_id, quantity in cart.items():
        serialized[str(product_id)] = _serialize_quantity(quantity)
    request.session[CART_SESSION_KEY] = serialized
    request.session.modified = True


def _build_cart_summary(request):
    cart = _read_cart(request)
    if not cart:
        return {'cart_items': [], 'cart_total_quantity': Decimal('0.00'), 'cart_total_amount': Decimal('0.00')}

    product_ids = [int(product_id) for product_id in cart.keys()]
    products = {
        product.id: product
        for product in Product.objects.filter(id__in=product_ids, is_active=True, is_public_listing=True)
    }

    items = []
    total_quantity = Decimal('0.00')
    total_amount = Decimal('0.00')
    for product_id_str, quantity in cart.items():
        product = products.get(int(product_id_str))
        if not product:
            continue
        subtotal = _truncate_money(product.price * quantity)
        total_quantity += quantity
        total_amount += subtotal
        items.append(
            {
                'product': product,
                'quantity': quantity,
                'subtotal': subtotal,
            }
        )

    return {
        'cart_items': items,
        'cart_total_quantity': total_quantity,
        'cart_total_amount': total_amount,
    }


def _get_recent_movements_limit(user):
    profile, _ = StoreUserProfile.objects.get_or_create(user=user)
    if not profile.recent_movements_limit or profile.recent_movements_limit <= 0:
        return None
    return profile.recent_movements_limit


def _build_event_registration_form(event, data=None):
    active_fields = list(event.registration_fields.filter(is_active=True).order_by('sort_order', 'id'))

    class EventRegistrationDynamicForm(forms.Form):
        pass

    notice_fields = []
    input_fields = []

    for field in active_fields:
        field_name = f'event_field_{field.id}'
        if field.field_type == field.FieldType.NOTICE:
            notice_fields.append(field)
            continue

        common_kwargs = {
            'label': field.label,
            'required': field.is_required,
            'help_text': field.help_text,
        }
        if field.field_type == field.FieldType.SHORT_TEXT:
            EventRegistrationDynamicForm.base_fields[field_name] = forms.CharField(
                **common_kwargs,
                widget=forms.TextInput(attrs={'class': 'form-control'}),
            )
        elif field.field_type == field.FieldType.LONG_TEXT:
            EventRegistrationDynamicForm.base_fields[field_name] = forms.CharField(
                **common_kwargs,
                widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            )
        elif field.field_type == field.FieldType.CHECKBOX:
            EventRegistrationDynamicForm.base_fields[field_name] = forms.BooleanField(
                **common_kwargs,
                required=False,
                widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            )
        elif field.field_type == field.FieldType.RADIO:
            choices = [(option, option) for option in field.options_list]
            EventRegistrationDynamicForm.base_fields[field_name] = forms.ChoiceField(
                **common_kwargs,
                choices=choices,
                widget=forms.RadioSelect(),
            )
        elif field.field_type == field.FieldType.SELECT:
            choices = [(option, option) for option in field.options_list]
            EventRegistrationDynamicForm.base_fields[field_name] = forms.ChoiceField(
                **common_kwargs,
                choices=choices,
                widget=forms.Select(attrs={'class': 'form-select'}),
            )

        input_fields.append(field)

    form = EventRegistrationDynamicForm(data=data)
    form.notice_fields = notice_fields
    form.input_fields = input_fields
    return form


def _attach_profile_image(comments):
    for comment in comments:
        profile = getattr(comment.user, 'store_profile', None)
        comment.profile_image_url = (
            profile.profile_image.url if profile and profile.profile_image else ''
        )
        for reply in comment.replies.all():
            reply_profile = getattr(reply.user, 'store_profile', None)
            reply.profile_image_url = (
                reply_profile.profile_image.url
                if reply_profile and reply_profile.profile_image
                else ''
            )
    return comments


class UserDashboardView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, TemplateView):
    template_name = 'user/dashboard/overview.html'
    timeline_page_size = 20

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(build_user_dashboard_context(self.request.user))
        profile, _created = StoreUserProfile.objects.get_or_create(user=self.request.user)
        context['show_password_change_modal'] = profile.password_change_required
        context['password_change_url'] = (
            f"{reverse('profile_edit')}#password-settings"
        )

        all_events = context.get('timeline_events', [])
        movements_limit = _get_recent_movements_limit(self.request.user)
        priority_kinds = {'event', 'survey', 'gamification'}

        try:
            timeline_page = int(self.request.GET.get('timeline_page', 1))
        except (TypeError, ValueError):
            timeline_page = 1
        timeline_page = max(1, timeline_page)

        if movements_limit is None:
            visible_events = all_events
        else:
            visible_events = all_events[:movements_limit]

        visible_count = len(visible_events)
        cards_to_show = min(visible_count, timeline_page * self.timeline_page_size)
        paginated_events = visible_events[:cards_to_show]

        context['timeline_events'] = paginated_events
        context['priority_events'] = [item for item in paginated_events if item.get('kind') in priority_kinds]
        context['highlighted_product_events'] = [item for item in paginated_events if item.get('kind') == 'product']
        context['recent_activity_events'] = [
            item
            for item in paginated_events
            if item.get('kind') not in priority_kinds and item.get('kind') != 'product'
        ]
        context['timeline_has_more'] = cards_to_show < visible_count
        context['timeline_next_page'] = timeline_page + 1 if context['timeline_has_more'] else None

        return context


class UserGamificationDetailView(ResponsiveTemplateMixin, LoginRequiredMixin, TemplateView):
    template_name = 'user/gamifications/detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        gamification = get_object_or_404(Gamification, pk=self.kwargs['pk'])
        context['gamification'] = gamification
        context['status'] = evaluate_gamification_for_user(gamification, self.request.user)
        return context


class ProfileEditView(ResponsiveTemplateMixin, LoginRequiredMixin, View):
    template_name = 'user/profile/edit.html'

    @staticmethod
    def _build_password_form(user, profile, data=None):
        return StoreUserPasswordChangeForm(
            user=user,
            data=data,
            require_old_password=not profile.password_change_required,
        )

    def get(self, request):
        profile, _created = StoreUserProfile.objects.get_or_create(user=request.user)
        form = StoreUserProfileForm(instance=profile, user=request.user)
        password_form = self._build_password_form(request.user, profile)
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'profile': profile,
                'password_form': password_form,
                'password_requires_old_password': not profile.password_change_required,
            },
        )

    def post(self, request):
        profile, _created = StoreUserProfile.objects.get_or_create(user=request.user)

        if 'change_password' in request.POST:
            password_form = self._build_password_form(request.user, profile, data=request.POST)
            form = StoreUserProfileForm(instance=profile, user=request.user)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, request.user)
                if profile.password_change_required:
                    profile.password_change_required = False
                    profile.temporary_access_code_plain = ''
                    profile.save(
                        update_fields=[
                            'password_change_required',
                            'temporary_access_code_plain',
                            'updated_at',
                        ]
                    )
                messages.success(request, _('Password changed successfully.'))
                return redirect('profile_edit')
            return render(
                request,
                self.get_template_names()[0],
                {
                    'form': form,
                    'profile': profile,
                    'password_form': password_form,
                    'password_requires_old_password': not profile.password_change_required,
                },
            )

        form = StoreUserProfileForm(request.POST, request.FILES, instance=profile, user=request.user)
        password_form = self._build_password_form(request.user, profile)
        if form.is_valid():
            form.save()
            messages.success(request, _('Profile updated successfully.'))
            return redirect('profile_edit')
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'profile': profile,
                'password_form': password_form,
                'password_requires_old_password': not profile.password_change_required,
            },
        )


class UserOrderListView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, ListView):
    template_name = 'user/orders/list.html'
    context_object_name = 'orders'

    def get_queryset(self):
        return Order.objects.filter(created_by=self.request.user).prefetch_related('items__product')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        approved_orders = Order.objects.filter(
            created_by=user,
            status=Order.Status.APPROVED,
        ).prefetch_related('items__product')
        direct_sales = Sale.objects.filter(
            Q(customer=user) | Q(customer__isnull=True, customer_name__iexact=user.username),
            is_voided=False,
        ).prefetch_related('items__product')

        order_events = []
        for order in context['orders']:
            order_items = list(order.items.all())
            order_line_totals = _distribute_line_totals(order_items, order.total_amount)
            for item in order_items:
                item.display_total = order_line_totals.get(item.id, Decimal('0.00'))
            order_events.append(
                {
                    'kind': 'order',
                    'pk': order.pk,
                    'created_at': order.created_at,
                    'href': reverse('user_order_detail', kwargs={'pk': order.pk}),
                    'status': order.status,
                    'total_amount': order.total_amount,
                    'items': order_items,
                }
            )

        for sale in direct_sales:
            sale_items = list(sale.items.all())
            sale_line_totals = _distribute_line_totals(sale_items, sale.total_amount)
            for item in sale_items:
                item.display_total = sale_line_totals.get(item.id, Decimal('0.00'))
            order_events.append(
                {
                    'kind': 'sale',
                    'pk': sale.pk,
                    'created_at': sale.created_at,
                    'href': reverse('user_sale_detail', kwargs={'pk': sale.pk}),
                    'status': Order.Status.APPROVED,
                    'total_amount': sale.total_amount,
                    'items': sale_items,
                }
            )

        order_events.sort(key=lambda event: event['created_at'], reverse=True)
        movements_limit = _get_recent_movements_limit(user)
        if movements_limit is not None:
            order_events = order_events[:movements_limit]

        context['approved_orders'] = approved_orders
        context['direct_sales'] = direct_sales
        context['order_events'] = order_events
        return context


class UserOrderDetailView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, DetailView):
    template_name = 'user/orders/detail.html'
    model = Order

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if obj.created_by_id != self.request.user.id:
            raise Http404('Order not found')
        return obj

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        order = context['object']
        order_items = list(order.items.all())
        line_totals = _distribute_line_totals(order_items, order.total_amount)
        for item in order_items:
            item.display_total = line_totals.get(item.id, Decimal('0.00'))
        context['order_items'] = order_items
        return context


class UserOrderRepeatView(LoginRequiredMixin, NonStaffRequiredMixin, View):
    def post(self, request, pk):
        order = get_object_or_404(
            Order.objects.prefetch_related('items__product'),
            pk=pk,
            created_by=request.user,
        )

        if order.status == Order.Status.PENDING:
            messages.error(request, _('You can only repeat approved or rejected orders.'))
            return redirect('user_order_detail', pk=order.pk)

        new_cart = {}
        skipped_items = []

        for item in order.items.all():
            product = item.product
            if not product or not product.is_active or not product.is_public_listing or product.stock <= 0:
                skipped_items.append(product.name if product else _('Unknown product'))
                continue

            quantity = min(item.quantity, product.stock)
            if quantity <= 0:
                skipped_items.append(product.name)
                continue

            new_cart[str(product.id)] = quantity

        _write_cart(request, new_cart)

        if new_cart:
            messages.success(request, _('A new cart was created from this order.'))
        else:
            messages.error(request, _('No available products from this order could be added to cart.'))

        if skipped_items:
            messages.warning(request, _('Some products were skipped due to availability.'))

        return redirect('user_cart_detail')


class UserSaleDetailView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, DetailView):
    template_name = 'user/orders/sale_detail.html'
    model = Sale

    def get_queryset(self):
        return Sale.objects.filter(is_voided=False)

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        user = self.request.user
        if obj.customer_id == user.id:
            return obj
        if obj.customer_id is None and (obj.customer_name or '').strip().lower() == user.username.lower():
            return obj
        raise Http404('Sale not found')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sale = context['object']
        sale_items = list(sale.items.all())
        line_totals = _distribute_line_totals(sale_items, sale.total_amount)
        for item in sale_items:
            item.display_total = line_totals.get(item.id, Decimal('0.00'))
        context['sale_items'] = sale_items
        return context


class UserOrderCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, View):
    template_name = 'user/orders/form.html'

    def _current_balance(self, user):
        profile, created = StoreUserProfile.objects.get_or_create(user=user)
        return profile.current_balance

    def get(self, request):
        form = OrderForm()
        formset = OrderItemFormSet()
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'create',
                'current_balance': self._current_balance(request.user),
            },
        )

    def post(self, request):
        form = OrderForm(request.POST)
        formset = OrderItemFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            try:
                order = create_order(
                    created_by=request.user,
                    customer_name=request.user.username,
                    items_data=_formset_items(formset),
                )
                messages.success(request, _('Order submitted for admin approval.'))
                return HttpResponseRedirect(reverse('user_order_detail', kwargs={'pk': order.pk}))
            except ValidationError as exc:
                messages.error(request, exc.message)
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'create',
                'current_balance': self._current_balance(request.user),
            },
        )


class UserOrderUpdateView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, View):
    template_name = 'user/orders/form.html'

    def _current_balance(self, user):
        profile, created = StoreUserProfile.objects.get_or_create(user=user)
        return profile.current_balance

    def _get_order(self, user, pk):
        order = get_object_or_404(Order, pk=pk, created_by=user)
        if order.status != Order.Status.PENDING:
            raise Http404('Only pending orders can be updated')
        return order

    def get(self, request, pk):
        order = self._get_order(request.user, pk)
        form = OrderForm(instance=order)
        formset = OrderItemFormSet(instance=order)
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'update',
                'order': order,
                'current_balance': self._current_balance(request.user),
            },
        )

    def post(self, request, pk):
        order = self._get_order(request.user, pk)
        form = OrderForm(request.POST, instance=order)
        formset = OrderItemFormSet(request.POST, instance=order)
        if form.is_valid() and formset.is_valid():
            try:
                updated_order = update_order(
                    order=order,
                    customer_name=request.user.username,
                    items_data=_formset_items(formset),
                )
                messages.success(request, _('Order updated successfully.'))
                return HttpResponseRedirect(reverse('user_order_detail', kwargs={'pk': updated_order.pk}))
            except ValidationError as exc:
                messages.error(request, exc.message)
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'update',
                'order': order,
                'current_balance': self._current_balance(request.user),
            },
        )


class UserPurchaseHistoryView(LoginRequiredMixin, NonStaffRequiredMixin, TemplateView):
    def get(self, request, *args, **kwargs):
        return redirect('user_orders')


class UserEventDetailView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, DetailView):
    template_name = 'user/events/detail.html'
    model = Event
    context_object_name = 'event'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        event = self.object
        registrations_count = event.registrations.count()
        capacity = event.capacity
        is_full = bool(capacity and registrations_count >= capacity)
        is_registered = event.registrations.filter(user=self.request.user).exists()
        comments = EventComment.objects.filter(event=event, parent__isnull=True).select_related(
            'user',
            'user__store_profile',
        ).prefetch_related(
            Prefetch(
                'replies',
                queryset=(
                    EventComment.objects.select_related('user', 'user__store_profile')
                    .order_by('created_at', 'id')
                ),
            )
        )

        context.update(
            {
                'event_images': event.images.all(),
                'event_links': event.links_list,
                'registrations_count': registrations_count,
                'is_registered': is_registered,
                'is_full': is_full,
                'can_register': event.requires_registration and not is_registered and not is_full,
                'registration_form': _build_event_registration_form(event),
                'comment_form': EventCommentForm(),
                'event_comments': _attach_profile_image(comments),
            }
        )
        return context


class UserEventCommentCreateView(LoginRequiredMixin, NonStaffRequiredMixin, View):
    def post(self, request, pk):
        event = get_object_or_404(Event, pk=pk)
        form = EventCommentForm(request.POST)
        if form.is_valid():
            EventComment.objects.create(
                event=event,
                user=request.user,
                content=form.cleaned_data['content'],
            )
            messages.success(request, _('Comment posted successfully.'))
        else:
            messages.error(request, _('Please write a comment before posting.'))
        return redirect('user_event_detail', pk=event.pk)


class UserEventRegisterView(LoginRequiredMixin, NonStaffRequiredMixin, View):
    @transaction.atomic
    def post(self, request, pk):
        event = get_object_or_404(Event.objects.select_for_update(), pk=pk)

        if not event.requires_registration:
            messages.error(request, _('This event does not require registration.'))
            return redirect('user_event_detail', pk=event.pk)

        if event.end_at < timezone.localtime():
            messages.error(request, _('Registration is closed for this event.'))
            return redirect('user_event_detail', pk=event.pk)

        if EventRegistration.objects.filter(event=event, user=request.user).exists():
            messages.info(request, _('You are already registered for this event.'))
            return redirect('user_event_detail', pk=event.pk)

        current_registrations = EventRegistration.objects.filter(event=event).count()
        if event.capacity and current_registrations >= event.capacity:
            messages.error(request, _('This event is full.'))
            return redirect('user_event_detail', pk=event.pk)

        registration_form = _build_event_registration_form(event, data=request.POST)
        if not registration_form.is_valid():
            messages.error(request, _('Please complete the registration form fields.'))
            return redirect('user_event_detail', pk=event.pk)

        profile, created = StoreUserProfile.objects.select_for_update().get_or_create(user=request.user)
        if event.is_paid_event:
            if profile.current_balance < event.registration_fee:
                messages.error(request, _('Insufficient balance for this paid event.'))
                return redirect('user_event_detail', pk=event.pk)

            balance_before = profile.current_balance
            balance_after = balance_before - event.registration_fee
            profile.current_balance = balance_after
            profile.save(update_fields=['current_balance', 'updated_at'])
            BalanceLog.objects.create(
                user=request.user,
                changed_by=None,
                source=BalanceLog.Source.EVENT_REGISTRATION_CHARGE,
                amount_delta=-event.registration_fee,
                balance_before=balance_before,
                balance_after=balance_after,
                note=_('Paid registration for event: %(event)s') % {'event': event.name},
            )

        answers = {}
        for field in registration_form.input_fields:
            field_key = f'event_field_{field.id}'
            value = registration_form.cleaned_data.get(field_key)
            answers[str(field.id)] = {
                'label': field.label,
                'type': field.field_type,
                'value': value,
            }

        EventRegistration.objects.create(event=event, user=request.user, answers=answers)
        messages.success(request, _('Registration completed successfully.'))
        return redirect('user_event_detail', pk=event.pk)


class UserEventUnregisterView(LoginRequiredMixin, NonStaffRequiredMixin, View):
    @transaction.atomic
    def post(self, request, pk):
        event = get_object_or_404(Event.objects.select_for_update(), pk=pk)

        registration = EventRegistration.objects.filter(event=event, user=request.user).first()
        if not registration:
            messages.info(request, _('You are not registered for this event.'))
            return redirect('user_event_detail', pk=event.pk)

        registration.delete()

        now = timezone.localtime()
        if event.is_paid_event and now < event.start_at:
            profile, created = StoreUserProfile.objects.select_for_update().get_or_create(user=request.user)
            balance_before = profile.current_balance
            balance_after = balance_before + event.registration_fee
            profile.current_balance = balance_after
            profile.save(update_fields=['current_balance', 'updated_at'])
            BalanceLog.objects.create(
                user=request.user,
                changed_by=None,
                source=BalanceLog.Source.EVENT_REGISTRATION_REFUND,
                amount_delta=event.registration_fee,
                balance_before=balance_before,
                balance_after=balance_after,
                note=_('Refund for event cancellation: %(event)s') % {'event': event.name},
            )

        messages.success(request, _('Your event registration was cancelled.'))
        return redirect('user_event_detail', pk=event.pk)


class UserSurveyDetailView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, DetailView):
    template_name = 'user/surveys/detail.html'
    model = Survey
    context_object_name = 'survey'

    def get_queryset(self):
        return Survey.objects.filter(is_active=True).prefetch_related('options')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        survey = self.object
        response = SurveyResponse.objects.filter(survey=survey, user=self.request.user).first()
        active_options = list(survey.options.filter(is_active=True).order_by('sort_order', 'id'))
        options_for_display = active_options or list(survey.options.order_by('sort_order', 'id'))

        selected_option_ids = []
        if response:
            selected_option_ids = list(
                response.selected_options.values_list('option_id', flat=True)
            )

        context.update(
            {
                'response_form': SurveyResponseForm(survey=survey),
                'has_answered': bool(response),
                'selected_option_ids': selected_option_ids,
                'response': response,
                'has_available_options': bool(options_for_display),
                'survey_options_for_display': options_for_display,
            }
        )
        return context


class UserSurveySubmitView(LoginRequiredMixin, NonStaffRequiredMixin, View):
    @transaction.atomic
    def post(self, request, pk):
        survey = get_object_or_404(Survey.objects.select_for_update(), pk=pk, is_active=True)
        existing_response = SurveyResponse.objects.filter(survey=survey, user=request.user).first()
        is_edit = existing_response is not None

        form = SurveyResponseForm(request.POST, survey=survey)
        if not form.is_valid():
            messages.error(request, _('Please select an option to continue.'))
            return redirect('user_survey_detail', pk=survey.pk)

        if survey.selection_type == Survey.SelectionType.CHECKBOX:
            option_ids = [int(value) for value in form.cleaned_data['selected_options']]
        else:
            option_ids = [int(form.cleaned_data['selected_option'])]

        valid_options_qs = SurveyOption.objects.filter(survey=survey, id__in=option_ids)
        if survey.options.filter(is_active=True).exists():
            valid_options_qs = valid_options_qs.filter(is_active=True)
        valid_options_count = valid_options_qs.count()
        if valid_options_count != len(option_ids):
            messages.error(request, _('Invalid survey option selected.'))
            return redirect('user_survey_detail', pk=survey.pk)

        if is_edit:
            response = existing_response
            response.selected_options.all().delete()
            response.save()
        else:
            response = SurveyResponse.objects.create(survey=survey, user=request.user)

        selected_options = SurveyOption.objects.filter(id__in=option_ids)
        SurveyResponseOption.objects.bulk_create(
            [SurveyResponseOption(response=response, option=option) for option in selected_options]
        )
        if is_edit:
            messages.success(request, _('Survey updated successfully.'))
        else:
            messages.success(request, _('Survey answered successfully.'))
        return redirect('user_survey_detail', pk=survey.pk)


class UserProductCatalogView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, ListView):
    template_name = 'user/products/catalog.html'
    context_object_name = 'products'

    def get_queryset(self):
        queryset = (
            Product.objects.filter(is_active=True, is_public_listing=True)
            .select_related('category', 'supplier')
            .prefetch_related('images')
            .annotate(
                approved_reviews_count=Count('reviews', filter=Q(reviews__is_approved=True)),
                approved_avg_rating=Avg('reviews__rating', filter=Q(reviews__is_approved=True)),
                approved_reviews_others_count=Count(
                    'reviews',
                    filter=Q(reviews__is_approved=True) & ~Q(reviews__user=self.request.user),
                ),
                approved_avg_rating_others=Avg(
                    'reviews__rating',
                    filter=Q(reviews__is_approved=True) & ~Q(reviews__user=self.request.user),
                ),
            )
        )

        query = (self.request.GET.get('q') or '').strip()
        category_id = (self.request.GET.get('category') or '').strip()
        if query:
            queryset = queryset.filter(name__icontains=query)
        if category_id.isdigit():
            queryset = queryset.filter(category_id=int(category_id))

        queryset = queryset.annotate(
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

        return queryset.order_by(
            'category__display_order',
            'category__name',
            'featured_first',
            'has_manual_order',
            'display_order',
            'name',
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        products = list(context['products'])

        tried_product_ids = set(
            Order.objects.filter(
                created_by=self.request.user,
                status=Order.Status.APPROVED,
            ).values_list('items__product_id', flat=True)
        )
        tried_product_ids.update(
            Sale.objects.filter(
                Q(customer=self.request.user)
                | Q(
                    customer__isnull=True,
                    customer_name__iexact=self.request.user.username,
                ),
                is_voided=False,
            ).values_list('items__product_id', flat=True)
        )
        tried_product_ids.discard(None)

        for product in products:
            category_enabled = (
                bool(getattr(product.category, 'include_in_untried', True))
                if product.category
                else True
            )
            product.is_untried_for_user = category_enabled and product.id not in tried_product_ids
            product.user_ratings_enabled = (
                bool(getattr(product.category, 'allow_user_ratings', True))
                if product.category
                else True
            )

        grouped = []
        grouped_index = {}
        for product in products:
            key = product.category_id or 0
            if key not in grouped_index:
                category_name = product.category.name if product.category else '-'
                grouped_index[key] = {
                    'category_name': category_name,
                    'display_order': (product.category.display_order if product.category else 0),
                    'default_expanded': bool(product.category.default_expanded) if product.category else False,
                    'image': (product.category.image if product.category else None),
                    'products': [],
                }
                grouped.append(grouped_index[key])
            grouped_index[key]['products'].append(product)

        context['categories'] = Category.objects.order_by('display_order', 'name')
        context['query'] = (self.request.GET.get('q') or '').strip()
        context['filter_category'] = (self.request.GET.get('category') or '').strip()
        context['grouped_products'] = grouped
        context.update(_build_cart_summary(self.request))
        return context


class UserCartDetailView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, TemplateView):
    template_name = 'user/products/cart.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_build_cart_summary(self.request))
        profile, _created = StoreUserProfile.objects.get_or_create(user=self.request.user)
        context['current_balance'] = profile.current_balance
        return context


class UserCartAddView(LoginRequiredMixin, NonStaffRequiredMixin, View):
    def post(self, request):
        product_id = (request.POST.get('product_id') or '').strip()
        purchase_mode = (request.POST.get('purchase_mode') or 'units').strip()

        if not product_id.isdigit():
            messages.error(request, _('Invalid product or quantity.'))
            return redirect('user_products_catalog')

        product = get_object_or_404(Product, id=int(product_id), is_active=True, is_public_listing=True)

        if product.purchase_options == Product.PurchaseOptions.UNITS_ONLY and purchase_mode == 'amount':
            messages.error(request, _('This product only allows quantity purchase.'))
            return redirect('user_products_catalog')

        if product.purchase_options == Product.PurchaseOptions.AMOUNT_ONLY and purchase_mode != 'amount':
            messages.error(request, _('This product only allows amount purchase.'))
            return redirect('user_products_catalog')

        if purchase_mode == 'amount':
            amount_raw = (request.POST.get('amount') or '').strip().replace(',', '.')
            try:
                amount_value = Decimal(amount_raw)
            except (InvalidOperation, TypeError):
                messages.error(request, _('Invalid product or quantity.'))
                return redirect('user_products_catalog')

            if amount_value <= 0:
                messages.error(request, _('Amount must be greater than zero.'))
                return redirect('user_products_catalog')

            if product.price <= 0:
                messages.error(request, _('Invalid product or quantity.'))
                return redirect('user_products_catalog')

            quantity = (amount_value / product.price).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
            if quantity <= 0:
                messages.error(request, _('Amount is too low to buy one unit.'))
                return redirect('user_products_catalog')
        else:
            quantity_raw = (request.POST.get('quantity') or '').strip()
            quantity = _parse_positive_quantity(quantity_raw)
            if quantity is None:
                messages.error(request, _('Invalid product or quantity.'))
                return redirect('user_products_catalog')

        cart = _read_cart(request)
        new_quantity = cart.get(product_id, Decimal('0.00')) + Decimal(quantity)
        if product.stock < new_quantity:
            messages.error(request, _('Not enough stock for the requested quantity.'))
            return redirect('user_products_catalog')

        cart[product_id] = _serialize_quantity(new_quantity)
        _write_cart(request, cart)
        messages.success(request, _('Product added to cart.'))
        return redirect('user_products_catalog')


class UserCartUpdateView(LoginRequiredMixin, NonStaffRequiredMixin, View):
    def post(self, request):
        product_id = (request.POST.get('product_id') or '').strip()
        quantity_raw = (request.POST.get('quantity') or '').strip()
        if not product_id.isdigit():
            messages.error(request, _('Invalid product or quantity.'))
            return redirect('user_cart_detail')

        quantity = _parse_positive_quantity(quantity_raw)
        cart = _read_cart(request)
        if product_id not in cart:
            return redirect('user_cart_detail')

        if quantity is None:
            cart.pop(product_id, None)
            _write_cart(request, cart)
            messages.success(request, _('Product removed from cart.'))
            return redirect('user_cart_detail')

        product = get_object_or_404(Product, id=int(product_id), is_active=True, is_public_listing=True)
        if product.stock < quantity:
            messages.error(request, _('Not enough stock for the requested quantity.'))
            return redirect('user_cart_detail')

        cart[product_id] = _serialize_quantity(quantity)
        _write_cart(request, cart)
        messages.success(request, _('Cart updated successfully.'))
        return redirect('user_cart_detail')


class UserCartRemoveView(LoginRequiredMixin, NonStaffRequiredMixin, View):
    def post(self, request):
        product_id = (request.POST.get('product_id') or '').strip()
        cart = _read_cart(request)
        if product_id in cart:
            cart.pop(product_id, None)
            _write_cart(request, cart)
            messages.success(request, _('Product removed from cart.'))
        return redirect('user_cart_detail')


class UserCartClearView(LoginRequiredMixin, NonStaffRequiredMixin, View):
    def post(self, request):
        _write_cart(request, {})
        messages.success(request, _('Cart cleared successfully.'))
        return redirect('user_cart_detail')


class UserCartSubmitOrderView(LoginRequiredMixin, NonStaffRequiredMixin, View):
    def post(self, request):
        cart_summary = _build_cart_summary(request)
        if not cart_summary['cart_items']:
            messages.error(request, _('Your cart is empty.'))
            return redirect('user_products_catalog')

        items_data = []
        for item in cart_summary['cart_items']:
            items_data.append({'product': item['product'], 'quantity': item['quantity']})

        try:
            order = create_order(
                created_by=request.user,
                customer_name=request.user.username,
                items_data=items_data,
            )
        except ValidationError as exc:
            messages.error(request, exc.message)
            return redirect('user_cart_detail')

        _write_cart(request, {})
        messages.success(request, _('Order submitted for admin approval.'))
        return redirect('user_order_detail', pk=order.pk)


class UserProductDetailView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, TemplateView):
    template_name = 'user/products/detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = get_object_or_404(
            Product.objects.select_related('category', 'supplier').prefetch_related('images'),
            id=self.kwargs['product_id'],
            is_active=True,
            is_public_listing=True,
        )
        approved_reviews = ProductReview.objects.filter(
            product=product,
            is_approved=True,
        ).select_related('user').order_by('-updated_at')
        product_sheet_fields = ProductSheetField.objects.filter(product=product).order_by('id')
        product_sheet_urls = ProductSheetUrl.objects.filter(product=product).order_by('id')
        category_allows_ratings = (
            bool(getattr(product.category, 'allow_user_ratings', True))
            if product.category
            else True
        )
        can_review = _can_review_product(self.request.user, product) and category_allows_ratings
        has_user_review = ProductReview.objects.filter(product=product, user=self.request.user).exists()
        context['product'] = product
        context['approved_reviews'] = approved_reviews
        context['product_sheet_fields'] = product_sheet_fields
        context['product_sheet_urls'] = product_sheet_urls
        context['can_review'] = can_review
        context['category_allows_ratings'] = category_allows_ratings
        context['show_pending_review_card'] = can_review and not has_user_review
        context['has_product_sheet'] = (
            product_sheet_fields.exists() or product_sheet_urls.exists()
        )
        return context


class UserProductReviewCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, View):
    template_name = 'user/products/review_form.html'

    @staticmethod
    def _build_context(product, form, review, current_user):
        approved_reviews = ProductReview.objects.filter(
            product=product,
            is_approved=True,
        ).exclude(user=current_user).only('rating', 'message', 'updated_at').order_by('-updated_at')
        return {
            'product': product,
            'form': form,
            'review': review,
            'approved_reviews': approved_reviews,
            'product_cover_image': product.images.first(),
        }

    def get(self, request, product_id):
        product = get_object_or_404(
            Product.objects.prefetch_related('images'),
            id=product_id,
            is_active=True,
            is_public_listing=True,
        )
        if product.category and not product.category.allow_user_ratings:
            messages.error(request, _('Reviews are disabled for this category.'))
            return redirect('user_product_detail', product_id=product.id)
        if not _can_review_product(request.user, product):
            messages.error(request, _('You can only review products you consumed before.'))
            return redirect('user_products_catalog')

        review = ProductReview.objects.filter(product=product, user=request.user).first()
        form = ProductReviewForm(instance=review)
        return render(request, self.get_template_names()[0], self._build_context(product, form, review, request.user))

    def post(self, request, product_id):
        product = get_object_or_404(
            Product.objects.prefetch_related('images'),
            id=product_id,
            is_active=True,
            is_public_listing=True,
        )
        if product.category and not product.category.allow_user_ratings:
            messages.error(request, _('Reviews are disabled for this category.'))
            return redirect('user_product_detail', product_id=product.id)
        if not _can_review_product(request.user, product):
            messages.error(request, _('You can only review products you consumed before.'))
            return redirect('user_products_catalog')

        review = ProductReview.objects.filter(product=product, user=request.user).first()
        form = ProductReviewForm(request.POST, instance=review)
        if form.is_valid():
            review_obj = form.save(commit=False)
            review_obj.product = product
            review_obj.user = request.user
            review_obj.is_approved = False
            review_obj.save()
            messages.success(request, _('Review submitted for admin approval.'))
            return redirect('user_products_catalog')
        return render(request, self.get_template_names()[0], self._build_context(product, form, review, request.user))


class UserBalanceRequestListCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, NonStaffRequiredMixin, View):
    template_name = 'user/balance/requests.html'

    @staticmethod
    def _month_start(value: date) -> date:
        return date(value.year, value.month, 1)

    @staticmethod
    def _next_month(value: date) -> date:
        if value.month == 12:
            return date(value.year + 1, 1, 1)
        return date(value.year, value.month + 1, 1)

    def _build_monthly_fee_lists(self, profile, monthly_settings):
        if not profile.monthly_fee_enabled or not monthly_settings or not monthly_settings.is_active:
            return [], []

        today = timezone.localdate()
        current_month = self._month_start(today)

        if profile.monthly_fee_enabled_at:
            start_month = self._month_start(profile.monthly_fee_enabled_at)
        elif profile.monthly_fee_last_charged_month:
            start_month = self._month_start(profile.monthly_fee_last_charged_month)
        else:
            start_month = current_month

        if start_month > current_month:
            return [], []

        paid_until = (
            self._month_start(profile.monthly_fee_last_charged_month)
            if profile.monthly_fee_last_charged_month
            else None
        )
        paid_months = []
        pending_months = []

        month_cursor = start_month
        while month_cursor <= current_month:
            if paid_until and month_cursor <= paid_until:
                paid_months.append(month_cursor)
            else:
                pending_months.append(month_cursor)
            month_cursor = self._next_month(month_cursor)

        current_year = today.year
        paid_months = [month for month in paid_months if month.year == current_year]
        pending_months = [month for month in pending_months if month.year == current_year]

        paid_months.sort(reverse=True)
        pending_months.sort(reverse=True)
        return paid_months, pending_months

    def _build_balance_entries(self, requests_qs, balance_logs_qs):
        entries = []

        for req in requests_qs:
            entries.append(
                {
                    'kind': 'request',
                    'created_at': req.created_at,
                    'amount': req.amount,
                    'title': _('Top-up request'),
                    'status': req.get_status_display(),
                    'status_key': req.status,
                    'detail': req.reviewed_by.username if req.reviewed_by else '-',
                }
            )

        for log in balance_logs_qs:
            entries.append(
                {
                    'kind': 'log',
                    'created_at': log.created_at,
                    'amount': log.amount_delta,
                    'title': _('Balance log'),
                    'status': log.get_source_display(),
                    'status_key': 'log',
                    'detail': log.changed_by.username if log.changed_by else _('System'),
                }
            )

        entries.sort(key=lambda entry: entry['created_at'], reverse=True)
        return entries

    def get(self, request):
        profile, created = StoreUserProfile.objects.get_or_create(user=request.user)
        monthly_settings = MonthlyFeeSettings.objects.first()
        form = BalanceRequestForm()
        requests = BalanceRequest.objects.filter(user=request.user)
        balance_logs = BalanceLog.objects.filter(user=request.user).exclude(
            source=BalanceLog.Source.BALANCE_REQUEST_APPROVAL
        ).select_related('changed_by')
        balance_entries = self._build_balance_entries(requests, balance_logs)
        movements_limit = _get_recent_movements_limit(request.user)
        if movements_limit is not None:
            balance_entries = balance_entries[:movements_limit]
        monthly_paid_months, monthly_pending_months = self._build_monthly_fee_lists(profile, monthly_settings)
        return render(
            request,
            self.get_template_names()[0],
            {
                'profile': profile,
                'form': form,
                'requests': requests,
                'balance_logs': balance_logs,
                'balance_entries': balance_entries,
                'monthly_settings': monthly_settings,
                'monthly_due_months': (
                    months_due_for_profile(profile)
                    if monthly_settings and monthly_settings.is_active
                    else 0
                ),
                'monthly_paid_months': monthly_paid_months,
                'monthly_pending_months': monthly_pending_months,
            },
        )

    def post(self, request):
        profile, created = StoreUserProfile.objects.get_or_create(user=request.user)
        monthly_settings = MonthlyFeeSettings.objects.first()
        form = BalanceRequestForm(request.POST)
        requests = BalanceRequest.objects.filter(user=request.user)
        balance_logs = BalanceLog.objects.filter(user=request.user).exclude(
            source=BalanceLog.Source.BALANCE_REQUEST_APPROVAL
        ).select_related('changed_by')
        balance_entries = self._build_balance_entries(requests, balance_logs)
        movements_limit = _get_recent_movements_limit(request.user)
        if movements_limit is not None:
            balance_entries = balance_entries[:movements_limit]
        monthly_paid_months, monthly_pending_months = self._build_monthly_fee_lists(profile, monthly_settings)
        if form.is_valid():
            balance_request = form.save(commit=False)
            balance_request.user = request.user
            balance_request.save()
            messages.success(request, _('Balance request submitted for admin approval.'))
            return redirect('user_balance_requests')
        return render(
            request,
            self.get_template_names()[0],
            {
                'profile': profile,
                'form': form,
                'requests': requests,
                'balance_logs': balance_logs,
                'balance_entries': balance_entries,
                'monthly_settings': monthly_settings,
                'monthly_due_months': (
                    months_due_for_profile(profile)
                    if monthly_settings and monthly_settings.is_active
                    else 0
                ),
                'monthly_paid_months': monthly_paid_months,
                'monthly_pending_months': monthly_pending_months,
            },
        )
