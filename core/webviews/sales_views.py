import json
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.http import Http404
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import DeleteView, DetailView, ListView

from core.forms import OrderForm, OrderItemFormSet, SaleForm, SaleItemFormSet
from core.webviews.mixins import ResponsiveTemplateMixin, StaffRequiredMixin
from customers.models import StoreUserProfile
from sales.models import Order, Product, Sale
from sales.services import create_sale, delete_order, delete_sale, update_approved_order, update_sale


def _formset_items(formset):
    items = []
    for form in formset.forms:
        if not hasattr(form, 'cleaned_data'):
            continue
        data = form.cleaned_data
        if not data or data.get('DELETE'):
            continue
        product = data.get('product')
        quantity = data.get('quantity')
        if not product or not quantity:
            continue
        items.append(
            {
                'product': product,
                'quantity': quantity,
            }
        )
    return items


def _normalize_sale_formset_post(post_data):
    mutable_data = post_data.copy()
    total_forms_raw = mutable_data.get('items-TOTAL_FORMS', '0')
    try:
        total_forms = int(total_forms_raw)
    except (TypeError, ValueError):
        total_forms = 0

    for index in range(total_forms):
        product_key = f'items-{index}-product'
        quantity_key = f'items-{index}-quantity'
        delete_key = f'items-{index}-DELETE'

        product_value = (mutable_data.get(product_key) or '').strip()
        quantity_value = (mutable_data.get(quantity_key) or '').strip()

        if not product_value:
            mutable_data[delete_key] = 'on'
            mutable_data[quantity_key] = quantity_value

    return mutable_data


def _customer_balances_json():
    users = User.objects.filter(is_staff=False).order_by('id')
    balances_by_user_id = {}
    for user in users:
        profile = getattr(user, 'store_profile', None)
        if profile is None:
            profile, _ = StoreUserProfile.objects.get_or_create(user=user)
        balances_by_user_id[user.id] = str(profile.current_balance)
    return json.dumps(balances_by_user_id)


class SaleListView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, ListView):
    template_name = 'admin/sales/list.html'
    model = Sale
    context_object_name = 'sales'

    VALID_SCOPES = {'active', 'voided'}

    def _selected_scope(self):
        scope = (self.request.GET.get('scope') or 'active').strip().lower()
        if scope not in self.VALID_SCOPES:
            return 'active'
        return scope

    def get_queryset(self):
        return Sale.objects.filter(is_voided=False)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        selected_scope = self._selected_scope()

        if selected_scope == 'voided':
            sales_queryset = Sale.objects.filter(is_voided=True).select_related('seller', 'customer')
            orders_queryset = Order.objects.filter(status=Order.Status.CANCELED).select_related('created_by', 'approved_by')
        else:
            sales_queryset = Sale.objects.filter(is_voided=False).select_related('seller', 'customer')
            orders_queryset = (
                Order.objects.filter(status=Order.Status.APPROVED)
                .annotate(money_at=Coalesce('approved_at', 'updated_at'))
                .select_related('created_by', 'approved_by')
            )

        transactions = []
        for sale in sales_queryset:
            transactions.append(
                {
                    'kind': 'sale',
                    'id': sale.id,
                    'date': sale.voided_at if selected_scope == 'voided' else sale.created_at,
                    'seller': sale.seller.username,
                    'customer': sale.customer.username if sale.customer else (sale.customer_name or '-'),
                    'total': sale.total_amount,
                    'view_url': reverse('sale_detail', kwargs={'pk': sale.pk}) if selected_scope == 'active' else None,
                    'edit_url': reverse('sale_update', kwargs={'pk': sale.pk}) if selected_scope == 'active' else None,
                    'delete_url': reverse('sale_delete', kwargs={'pk': sale.pk}) if selected_scope == 'active' else None,
                }
            )

        for order in orders_queryset:
            transactions.append(
                {
                    'kind': 'order',
                    'id': order.id,
                    'date': (order.canceled_at or order.updated_at) if selected_scope == 'voided' else order.money_at,
                    'seller': order.approved_by.username if order.approved_by else '-',
                    'customer': order.created_by.username if order.created_by else (order.customer_name or '-'),
                    'total': order.total_amount,
                    'view_url': reverse('admin_order_approval', kwargs={'pk': order.pk}) if selected_scope == 'active' else None,
                    'edit_url': reverse('admin_order_update', args=[order.id]) if selected_scope == 'active' else None,
                    'delete_url': reverse('admin_order_delete', args=[order.id]) if selected_scope == 'active' else None,
                }
            )

        customer_filters = sorted(
            {
                item['customer']
                for item in transactions
                if item['customer'] and item['customer'] != '-'
            },
            key=lambda value: value.lower(),
        )
        selected_customer = (self.request.GET.get('customer') or '').strip()
        if selected_customer:
            transactions = [item for item in transactions if item['customer'] == selected_customer]

        transactions.sort(key=lambda item: item['date'], reverse=True)

        current_datetime = timezone.localtime()
        monthly_transactions = [
            item
            for item in transactions
            if item['date']
            and item['date'].year == current_datetime.year
            and item['date'].month == current_datetime.month
        ]
        monthly_revenue = sum((item['total'] or Decimal('0.00') for item in monthly_transactions), Decimal('0.00'))
        latest_transaction = transactions[0] if transactions else None

        active_sales_count = Sale.objects.filter(is_voided=False).count()
        active_orders_count = Order.objects.filter(status=Order.Status.APPROVED).count()
        voided_sales_count = Sale.objects.filter(is_voided=True).count()
        canceled_orders_count = Order.objects.filter(status=Order.Status.CANCELED).count()

        context['sales_month_count'] = len(monthly_transactions)
        context['sales_month_revenue'] = monthly_revenue
        context['sales_total_count'] = len(transactions)
        context['sales_latest_id'] = latest_transaction['id'] if latest_transaction else None
        context['sales_latest_kind'] = latest_transaction['kind'] if latest_transaction else None
        context['sales_total_revenue'] = monthly_revenue
        context['transactions'] = transactions
        context['customer_filters'] = customer_filters
        context['selected_customer'] = selected_customer
        context['selected_scope'] = selected_scope
        context['active_transactions_count'] = active_sales_count + active_orders_count
        context['voided_transactions_count'] = voided_sales_count + canceled_orders_count
        context['transactions_empty_text'] = (
            _('No voided or canceled transactions yet.') if selected_scope == 'voided' else _('No sales yet.')
        )
        return context


class SaleDetailView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, DetailView):
    template_name = 'admin/sales/detail.html'
    model = Sale

    def get_queryset(self):
        return Sale.objects.filter(is_voided=False)


class SaleCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/sales/form.html'

    def _build_sale_form(self, request):
        customer_raw = (request.GET.get('customer') or '').strip()
        if not customer_raw:
            return SaleForm()

        try:
            customer_id = int(customer_raw)
        except (TypeError, ValueError):
            return SaleForm()

        customer = User.objects.filter(id=customer_id, is_staff=False).first()
        if not customer:
            return SaleForm()

        return SaleForm(initial={'customer': customer.id})

    def get(self, request):
        form = self._build_sale_form(request)
        formset = SaleItemFormSet()
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'create',
                'customer_balances_json': _customer_balances_json(),
            },
        )

    def post(self, request):
        normalized_post = _normalize_sale_formset_post(request.POST)
        form = SaleForm(normalized_post)
        formset = SaleItemFormSet(normalized_post)

        if form.is_valid() and formset.is_valid():
            try:
                sale = create_sale(
                    seller=request.user,
                    customer=form.cleaned_data['customer'],
                    items_data=_formset_items(formset),
                )
                messages.success(request, _('Sale created successfully.'))
                return HttpResponseRedirect(reverse('sale_detail', kwargs={'pk': sale.pk}))
            except ValidationError as exc:
                messages.error(request, exc.message)

        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'create',
                'customer_balances_json': _customer_balances_json(),
            },
        )


class SaleUpdateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/sales/form.html'

    def get(self, request, pk):
        sale = get_object_or_404(Sale.objects.filter(is_voided=False), pk=pk)
        form = SaleForm(instance=sale)
        formset = SaleItemFormSet(instance=sale)
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'update',
                'sale': sale,
                'customer_balances_json': _customer_balances_json(),
            },
        )

    def post(self, request, pk):
        sale = get_object_or_404(Sale.objects.filter(is_voided=False), pk=pk)
        normalized_post = _normalize_sale_formset_post(request.POST)
        form = SaleForm(normalized_post, instance=sale)
        formset = SaleItemFormSet(normalized_post, instance=sale)

        if form.is_valid() and formset.is_valid():
            try:
                updated_sale = update_sale(
                    sale=sale,
                    customer=form.cleaned_data['customer'],
                    items_data=_formset_items(formset),
                )
                messages.success(request, _('Sale updated successfully.'))
                return HttpResponseRedirect(reverse('sale_detail', kwargs={'pk': updated_sale.pk}))
            except ValidationError as exc:
                messages.error(request, exc.message)

        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'formset': formset,
                'mode': 'update',
                'sale': sale,
                'customer_balances_json': _customer_balances_json(),
            },
        )


class SaleDeleteView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    template_name = 'admin/sales/confirm_delete.html'
    model = Sale
    success_url = reverse_lazy('sale_list')

    def form_valid(self, form):
        delete_sale(sale=self.object, modified_by=self.request.user)
        messages.success(self.request, _('Sale voided and stock restored. Historical records were preserved.'))
        return HttpResponseRedirect(self.get_success_url())

    def get_queryset(self):
        return super().get_queryset().filter(is_voided=False)


class AdminOrderUpdateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/orders/form.html'

    def _get_order(self, pk):
        order = get_object_or_404(Order.objects.select_related('created_by'), pk=pk)
        if order.status != Order.Status.APPROVED:
            raise Http404
        return order

    def _build_formset(self, *, order, data=None):
        formset = OrderItemFormSet(data=data, instance=order)
        bound_product_ids = set()
        if data is not None:
            for form in formset.forms:
                prefix = form.prefix
                product_key = f'{prefix}-product'
                product_id = data.get(product_key)
                if product_id:
                    try:
                        bound_product_ids.add(int(product_id))
                    except (TypeError, ValueError):
                        continue

        allowed_ids = set(
            Product.objects.filter(is_active=True, is_public_listing=True).values_list('id', flat=True)
        )

        for item in order.items.all():
            allowed_ids.add(item.product_id)
        allowed_ids.update(bound_product_ids)

        products_qs = Product.objects.filter(pk__in=allowed_ids).order_by('name')
        for item_form in formset.forms:
            item_form.fields['product'].queryset = products_qs
        return formset

    def get(self, request, pk):
        order = self._get_order(pk)
        form = OrderForm(instance=order)
        formset = self._build_formset(order=order)
        current_balance = getattr(getattr(order.created_by, 'store_profile', None), 'current_balance', 0)
        context = {
            'form': form,
            'formset': formset,
            'mode': 'update',
            'order': order,
            'current_balance': current_balance,
        }
        return render(request, self.get_template_names()[0], context)

    def post(self, request, pk):
        order = self._get_order(pk)
        form = OrderForm(request.POST, instance=order)
        formset = self._build_formset(order=order, data=request.POST)

        if form.is_valid() and formset.is_valid():
            try:
                update_approved_order(
                    order=order,
                    items_data=_formset_items(formset),
                    modified_by=request.user,
                )
            except ValidationError as error:
                messages.error(request, ' '.join(error.messages))
            else:
                messages.success(request, _('Order updated successfully.'))
                return redirect('sale_list')

        current_balance = getattr(getattr(order.created_by, 'store_profile', None), 'current_balance', 0)
        context = {
            'form': form,
            'formset': formset,
            'mode': 'update',
            'order': order,
            'current_balance': current_balance,
        }
        return render(request, self.get_template_names()[0], context)


class AdminOrderDeleteView(LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    model = Order
    template_name = 'admin/orders/confirm_delete.html'
    success_url = reverse_lazy('sale_list')
    context_object_name = 'order'

    def get_queryset(self):
        return super().get_queryset().exclude(status=Order.Status.CANCELED)

    def form_valid(self, form):
        delete_order(order=self.object, modified_by=self.request.user)
        messages.success(self.request, _('Order canceled. Stock and user balance restored. Historical records were preserved.'))
        return redirect(self.success_url)
