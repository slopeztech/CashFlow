from django.contrib.auth.mixins import UserPassesTestMixin


class StaffRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_staff


class NonStaffRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return not self.request.user.is_staff


class ResponsiveTemplateMixin:
    def _is_mobile_request(self):
        user_agent = (self.request.META.get('HTTP_USER_AGENT') or '').lower()
        mobile_markers = ('mobile', 'android', 'iphone', 'ipad')
        return any(marker in user_agent for marker in mobile_markers)

    def get_template_names(self):
        if hasattr(super(), 'get_template_names'):
            base_templates = super().get_template_names()
        else:
            template_name = getattr(self, 'template_name', None)
            if not template_name:
                raise AttributeError('ResponsiveTemplateMixin requires template_name or parent get_template_names().')
            base_templates = [template_name]
        suffix = '_mobile' if self._is_mobile_request() else '_desktop'
        resolved = []
        for template_name in base_templates:
            if template_name.endswith('.html'):
                resolved.append(f"{template_name[:-5]}{suffix}.html")
            else:
                resolved.append(f"{template_name}{suffix}")
        return resolved
