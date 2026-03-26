from django.contrib.auth.models import User
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from api.permissions import IsProfileOwnerOrStaff, IsStaffUser
from api.serializers import (
	ProductSerializer,
	SaleSerializer,
	StoreUserProfileSerializer,
)
from customers.models import StoreUserProfile
from inventory.models import Product
from sales.models import Sale
from sales.services import delete_sale


class ProductViewSet(viewsets.ModelViewSet):
	queryset = Product.objects.all()
	serializer_class = ProductSerializer
	permission_classes = [IsStaffUser]

	@action(detail=False, methods=['get'])
	def low_stock(self, request):
		queryset = self.get_queryset().filter(stock__lte=5, is_active=True)
		serializer = self.get_serializer(queryset, many=True)
		return Response(serializer.data)


class SaleViewSet(viewsets.ModelViewSet):
	queryset = Sale.objects.select_related('seller', 'customer').prefetch_related('items__product')
	serializer_class = SaleSerializer
	permission_classes = [IsStaffUser]

	def perform_destroy(self, instance):
		delete_sale(sale=instance)


class StoreUserProfileViewSet(viewsets.ModelViewSet):
	queryset = StoreUserProfile.objects.select_related('user')
	serializer_class = StoreUserProfileSerializer
	permission_classes = [IsProfileOwnerOrStaff]

	def get_queryset(self):
		user = self.request.user
		queryset = StoreUserProfile.objects.select_related('user')
		if user.is_staff:
			return queryset
		return queryset.filter(user=user)

	def perform_create(self, serializer):
		user = self.request.user
		if user.is_staff:
			user_id = self.request.data.get('user')
			if user_id:
				try:
					target_user = User.objects.get(pk=int(user_id))
				except (User.DoesNotExist, TypeError, ValueError):
					raise ValidationError({'user': 'Invalid user.'})
			else:
				target_user = user
		else:
			target_user = user

		if StoreUserProfile.objects.filter(user=target_user).exists():
			raise ValidationError({'user': 'Profile already exists for this user.'})

		serializer.save(user=target_user)
