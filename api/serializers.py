from django.core.exceptions import ValidationError as DjangoValidationError
from django.contrib.auth.models import User
from rest_framework import serializers

from customers.models import StoreUserProfile
from inventory.models import Product
from sales.models import Sale, SaleItem
from sales.services import create_sale, update_sale


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = '__all__'


class StoreUserProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = StoreUserProfile
        fields = ['id', 'user', 'username', 'email', 'current_balance', 'phone', 'address', 'created_at', 'updated_at']
        read_only_fields = ['user', 'username', 'email', 'current_balance', 'created_at', 'updated_at']


class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = SaleItem
        fields = ['id', 'product', 'product_name', 'quantity', 'unit_price', 'subtotal']
        read_only_fields = ['unit_price', 'subtotal']


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True)
    seller = serializers.PrimaryKeyRelatedField(read_only=True)
    customer = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_staff=False)
    )
    customer_username = serializers.CharField(source='customer.username', read_only=True)

    class Meta:
        model = Sale
        fields = [
            'id',
            'seller',
            'customer',
            'customer_username',
            'customer_name',
            'total_amount',
            'created_at',
            'items',
        ]
        read_only_fields = ['customer_name', 'total_amount', 'created_at']

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError('A sale must include at least one item.')
        return value

    def create(self, validated_data):
        request = self.context.get('request')
        items_data = validated_data.pop('items', [])
        try:
            return create_sale(
                seller=request.user,
                customer=validated_data.get('customer'),
                items_data=items_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', [])
        try:
            return update_sale(
                sale=instance,
                customer=validated_data.get('customer', instance.customer),
                items_data=items_data,
            )
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
