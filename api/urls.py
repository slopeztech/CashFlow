from django.urls import include, path
from rest_framework.routers import DefaultRouter

from api.views import ProductViewSet, SaleViewSet, StoreUserProfileViewSet

router = DefaultRouter()
router.register(r'products', ProductViewSet, basename='product')
router.register(r'sales', SaleViewSet, basename='sale')
router.register(r'profiles', StoreUserProfileViewSet, basename='profile')

urlpatterns = [
    path('', include(router.urls)),
]
