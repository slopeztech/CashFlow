from decimal import Decimal, InvalidOperation

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db.models import Avg, Count, F, Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from core.forms import (
    CategoryForm,
    ProductForm,
    ProductSheetFieldForm,
    ProductSheetUrlForm,
    SupplierForm,
)
from core.image_processing import optimize_uploaded_image
from core.webviews.mixins import ResponsiveTemplateMixin, StaffRequiredMixin
from inventory.models import (
    Category,
    Product,
    ProductImage,
    ProductReview,
    ProductSheetField,
    ProductSheetUrl,
    ProductStockAdjustmentLog,
    Supplier,
)
from sales.models import Order, OrderItem, SaleItem


class ProductListView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, ListView):
    template_name = 'admin/products/list.html'
    model = Product
    context_object_name = 'products'

    STATUS_FILTERS = {'active', 'inactive', 'all'}

    def _get_status_filter(self):
        status_filter = (self.request.GET.get('status') or 'active').strip().lower()
        if status_filter not in self.STATUS_FILTERS:
            return 'active'
        return status_filter

    def _apply_category_filter(self, queryset):
        category_id = (self.request.GET.get('category') or '').strip()
        if category_id.isdigit():
            return queryset.filter(category_id=int(category_id))
        return queryset

    def get_queryset(self):
        queryset = Product.objects.select_related('category', 'supplier').annotate(
            approved_avg_rating=Avg('reviews__rating', filter=Q(reviews__is_approved=True)),
        ).order_by('category__display_order', 'category__name', 'name')

        queryset = self._apply_category_filter(queryset)

        status_filter = self._get_status_filter()
        if status_filter == 'active':
            queryset = queryset.filter(is_active=True)
        elif status_filter == 'inactive':
            queryset = queryset.filter(is_active=False)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filtered_queryset = context['products']
        low_stock_products = filtered_queryset.filter(stock__lte=F('min_stock')).order_by('stock', 'id', 'name')

        base_queryset = self._apply_category_filter(Product.objects.all())

        context['products_total'] = base_queryset.count()
        context['products_low_stock'] = low_stock_products.count()
        context['products_low_stock_items'] = low_stock_products
        context['products_active'] = base_queryset.filter(is_active=True).count()
        context['products_inactive'] = base_queryset.filter(is_active=False).count()
        context['categories'] = Category.objects.annotate(products_count=Count('products')).order_by('display_order', 'name')
        context['filter_category'] = (self.request.GET.get('category') or '').strip()
        context['active_status_filter'] = self._get_status_filter()
        return context


class ProductCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, CreateView):
    template_name = 'admin/products/form.html'
    model = Product
    form_class = ProductForm
    success_url = reverse_lazy('product_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        self._save_new_images(self.object)
        messages.success(self.request, _('Product saved successfully.'))
        return response

    def _save_new_images(self, product):
        for uploaded_file in self.request.FILES.getlist('new_images'):
            optimized_file = optimize_uploaded_image(uploaded_file, crop_size=(1200, 1200), max_bytes=512 * 1024)
            ProductImage.objects.create(product=product, image=optimized_file)


class ProductUpdateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    template_name = 'admin/products/form.html'
    model = Product
    form_class = ProductForm
    success_url = reverse_lazy('product_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        self._remove_selected_images(self.object)
        self._save_new_images(self.object)
        messages.success(self.request, _('Product updated successfully.'))
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['product_images'] = self.object.images.all()
        context['product_reviews'] = ProductReview.objects.filter(product=self.object).select_related('user')
        return context

    def _save_new_images(self, product):
        for uploaded_file in self.request.FILES.getlist('new_images'):
            optimized_file = optimize_uploaded_image(uploaded_file, crop_size=(1200, 1200), max_bytes=512 * 1024)
            ProductImage.objects.create(product=product, image=optimized_file)

    def _remove_selected_images(self, product):
        image_ids = [image_id for image_id in self.request.POST.getlist('remove_images') if image_id.isdigit()]
        if not image_ids:
            return
        images = ProductImage.objects.filter(product=product, id__in=image_ids)
        for product_image in images:
            if product_image.image:
                product_image.image.delete(save=False)
            product_image.delete()


class ProductDeleteView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, DeleteView):
    template_name = 'admin/products/confirm_delete.html'
    model = Product
    success_url = reverse_lazy('product_list')

    def form_valid(self, form):
        product = self.object
        if not product.is_active and not product.is_public_listing:
            messages.info(self.request, _('Product is already deactivated.'))
            return HttpResponseRedirect(self.get_success_url())

        product.is_active = False
        product.is_public_listing = False
        product.save(update_fields=['is_active', 'is_public_listing', 'updated_at'])
        messages.success(self.request, _('Product deactivated successfully. Historical records were preserved.'))
        return HttpResponseRedirect(self.get_success_url())


class ProductStockAdjustView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/products/stock.html'

    def _build_context(self, product, stock_delta=''):
        return {
            'product': product,
            'stock_delta': stock_delta,
            'stock_adjustment_logs': ProductStockAdjustmentLog.objects.filter(product=product).select_related('adjusted_by')[:20],
        }

    def get(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        return render(
            request,
            self.get_template_names()[0],
            self._build_context(product),
        )

    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        delta_raw = (request.POST.get('stock_delta') or '').strip().replace(',', '.')

        try:
            stock_delta = Decimal(delta_raw)
        except (TypeError, ValueError, InvalidOperation):
            messages.error(request, _('Invalid stock adjustment value.'))
            return render(
                request,
                self.get_template_names()[0],
                self._build_context(product, request.POST.get('stock_delta', '')),
            )

        new_stock = product.stock + stock_delta
        if new_stock < 0:
            messages.error(request, _('Stock cannot be negative.'))
            return render(
                request,
                self.get_template_names()[0],
                self._build_context(product, request.POST.get('stock_delta', '')),
            )

        previous_stock = product.stock
        product.stock = new_stock
        product.save(update_fields=['stock', 'updated_at'])
        ProductStockAdjustmentLog.objects.create(
            product=product,
            adjusted_by=request.user if request.user.is_authenticated else None,
            previous_stock=previous_stock,
            adjustment=stock_delta,
            new_stock=new_stock,
        )
        messages.success(request, _('Stock updated successfully.'))
        return HttpResponseRedirect(reverse('product_list'))


class ProductSheetFieldListCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/products/sheet.html'

    def get(self, request, pk):
        product = get_object_or_404(Product.objects.select_related('category', 'supplier'), pk=pk)
        edit_id = (request.GET.get('edit') or '').strip()
        editing_field = None
        if edit_id.isdigit():
            editing_field = ProductSheetField.objects.filter(product=product, pk=int(edit_id)).first()

        form = ProductSheetFieldForm(instance=editing_field)
        url_form = ProductSheetUrlForm()
        fields = ProductSheetField.objects.filter(product=product).order_by('id')
        sheet_urls = ProductSheetUrl.objects.filter(product=product).order_by('id')
        return render(
            request,
            self.get_template_names()[0],
            {
                'product': product,
                'form': form,
                'url_form': url_form,
                'sheet_fields': fields,
                'sheet_urls': sheet_urls,
                'editing_field': editing_field,
            },
        )

    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        field_id = (request.POST.get('field_id') or '').strip()
        editing_field = None
        if field_id.isdigit():
            editing_field = ProductSheetField.objects.filter(product=product, pk=int(field_id)).first()

        form = ProductSheetFieldForm(request.POST, instance=editing_field)
        if form.is_valid():
            sheet_field = form.save(commit=False)
            sheet_field.product = product
            sheet_field.save()
            if editing_field:
                messages.success(request, _('Product sheet field updated successfully.'))
            else:
                messages.success(request, _('Product sheet field created successfully.'))
            return HttpResponseRedirect(reverse('product_sheet', kwargs={'pk': product.pk}))

        fields = ProductSheetField.objects.filter(product=product).order_by('id')
        sheet_urls = ProductSheetUrl.objects.filter(product=product).order_by('id')
        return render(
            request,
            self.get_template_names()[0],
            {
                'product': product,
                'form': form,
                'url_form': ProductSheetUrlForm(),
                'sheet_fields': fields,
                'sheet_urls': sheet_urls,
                'editing_field': editing_field,
            },
        )


class ProductSheetFieldDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk, field_id):
        product = get_object_or_404(Product, pk=pk)
        field = get_object_or_404(ProductSheetField, pk=field_id, product=product)
        field.delete()
        messages.success(request, _('Product sheet field deleted successfully.'))
        return HttpResponseRedirect(reverse('product_sheet', kwargs={'pk': product.pk}))


class ProductSheetUrlCreateView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk):
        product = get_object_or_404(Product, pk=pk)
        form = ProductSheetUrlForm(request.POST)
        if form.is_valid():
            sheet_url = form.save(commit=False)
            sheet_url.product = product
            sheet_url.save()
            messages.success(request, _('Product sheet URL created successfully.'))
        else:
            messages.error(request, _('Invalid URL.'))
        return HttpResponseRedirect(reverse('product_sheet', kwargs={'pk': product.pk}))


class ProductSheetUrlDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk, url_id):
        product = get_object_or_404(Product, pk=pk)
        sheet_url = get_object_or_404(ProductSheetUrl, pk=url_id, product=product)
        sheet_url.delete()
        messages.success(request, _('Product sheet URL deleted successfully.'))
        return HttpResponseRedirect(reverse('product_sheet', kwargs={'pk': product.pk}))


class ProductInfoView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, DetailView):
    template_name = 'admin/products/info.html'
    model = Product
    context_object_name = 'product'

    def get_queryset(self):
        return Product.objects.select_related('category', 'supplier')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product = self.object

        sale_items = list(
            SaleItem.objects.filter(product=product)
            .select_related('sale', 'sale__customer')
        )
        order_items = list(
            OrderItem.objects.filter(product=product, order__status=Order.Status.APPROVED)
            .select_related('order', 'order__created_by')
        )

        sold_quantity_sales = sum(item.quantity for item in sale_items)
        sold_quantity_orders = sum(item.quantity for item in order_items)
        sold_quantity_total = sold_quantity_sales + sold_quantity_orders

        sales_revenue = sum((item.quantity * item.unit_price for item in sale_items), Decimal('0.00'))
        orders_revenue = sum((item.quantity * item.unit_price for item in order_items), Decimal('0.00'))
        total_revenue = sales_revenue + orders_revenue

        users_quantities = {}
        for item in sale_items:
            user = item.sale.customer
            if user is None:
                continue
            users_quantities[user.id] = {
                'user': user,
                'quantity': users_quantities.get(user.id, {}).get('quantity', 0) + item.quantity,
            }

        for item in order_items:
            user = item.order.created_by
            users_quantities[user.id] = {
                'user': user,
                'quantity': users_quantities.get(user.id, {}).get('quantity', 0) + item.quantity,
            }

        users_tried_count = len(users_quantities)
        avg_quantity_per_user = (sold_quantity_total / users_tried_count) if users_tried_count else 0
        top_buyers = sorted(users_quantities.values(), key=lambda row: row['quantity'], reverse=True)[:5]

        sale_dates = [item.sale.created_at for item in sale_items]
        order_dates = [item.order.approved_at or item.order.updated_at for item in order_items]
        all_dates = [date_value for date_value in sale_dates + order_dates if date_value is not None]
        last_movement_at = max(all_dates) if all_dates else None

        reviews = ProductReview.objects.filter(product=product).select_related('user').order_by('-created_at')
        approved_reviews = reviews.filter(is_approved=True)
        avg_rating = approved_reviews.aggregate(value=Avg('rating'))['value']
        reviewed_user_ids = set(reviews.values_list('user_id', flat=True))
        users_without_review_count = len(set(users_quantities.keys()) - reviewed_user_ids)

        context.update(
            {
                'sold_quantity_total': sold_quantity_total,
                'sold_quantity_sales': sold_quantity_sales,
                'sold_quantity_orders': sold_quantity_orders,
                'total_revenue': total_revenue,
                'users_tried_count': users_tried_count,
                'avg_quantity_per_user': avg_quantity_per_user,
                'top_buyers': top_buyers,
                'last_movement_at': last_movement_at,
                'reviews': reviews,
                'reviews_total': reviews.count(),
                'reviews_approved_count': approved_reviews.count(),
                'reviews_pending_count': reviews.filter(is_approved=False).count(),
                'avg_rating': avg_rating,
                'users_without_review_count': users_without_review_count,
            }
        )
        return context


class CategoryListCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/products/categories.html'

    def get(self, request):
        categories = Category.objects.annotate(products_count=Count('products')).order_by('display_order', 'name')
        form = CategoryForm()
        return self._render(request, form=form, categories=categories)

    def post(self, request):
        categories = Category.objects.annotate(products_count=Count('products')).order_by('display_order', 'name')
        form = CategoryForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, _('Category created successfully.'))
            return HttpResponseRedirect(reverse_lazy('category_list'))
        return self._render(request, form=form, categories=categories)

    def _render(self, request, form, categories):
        return render(
            request,
            self.get_template_names()[0],
            {
                'form': form,
                'categories': categories,
                'categories_untried_enabled_count': categories.filter(include_in_untried=True).count(),
            },
        )


class CategoryUpdateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, UpdateView):
    template_name = 'admin/products/category_form.html'
    model = Category
    form_class = CategoryForm
    success_url = reverse_lazy('category_list')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _('Category updated successfully.'))
        return response


class CategoryDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk):
        category = get_object_or_404(Category, pk=pk)
        if category.products.exists():
            messages.error(request, _('Category cannot be deleted because it has associated products.'))
            return HttpResponseRedirect(reverse_lazy('category_list'))
        category.delete()
        messages.success(request, _('Category deleted successfully.'))
        return HttpResponseRedirect(reverse_lazy('category_list'))


class SupplierListCreateView(ResponsiveTemplateMixin, LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = 'admin/products/suppliers.html'

    def get(self, request):
        suppliers = Supplier.objects.annotate(products_count=Count('products')).order_by('name')
        form = SupplierForm()
        return self._render(request, form=form, suppliers=suppliers)

    def post(self, request):
        suppliers = Supplier.objects.annotate(products_count=Count('products')).order_by('name')
        form = SupplierForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, _('Supplier created successfully.'))
            return HttpResponseRedirect(reverse_lazy('supplier_list'))
        return self._render(request, form=form, suppliers=suppliers)

    def _render(self, request, form, suppliers):
        return render(
            request,
            self.get_template_names()[0],
            {'form': form, 'suppliers': suppliers},
        )


class SupplierDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk):
        supplier = get_object_or_404(Supplier, pk=pk)
        if supplier.products.exists():
            messages.error(request, _('Supplier cannot be deleted because it has associated products.'))
            return HttpResponseRedirect(reverse_lazy('supplier_list'))
        supplier.delete()
        messages.success(request, _('Supplier deleted successfully.'))
        return HttpResponseRedirect(reverse_lazy('supplier_list'))
