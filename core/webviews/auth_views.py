from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import RedirectView

from core.forms import CashFlowAuthenticationForm
from core.webviews.mixins import ResponsiveTemplateMixin


class LoginPageView(ResponsiveTemplateMixin, LoginView):
    template_name = 'shared/auth/login.html'
    redirect_authenticated_user = True
    authentication_form = CashFlowAuthenticationForm


class DashboardView(LoginRequiredMixin, RedirectView):
    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        if self.request.user.is_staff:
            return reverse('admin_dashboard')
        return reverse('user_dashboard')


def logout_view(request):
    logout(request)
    return redirect('login')
