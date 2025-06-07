from django.contrib import admin
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from auditlog.mixins import LogEntryAdminMixin
from auditlog.models import LogEntry


@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin, LogEntryAdminMixin):
    list_display = [
        "source",
        "change_value",
        "timestamp"
    ]
    search_fields = [
        "source",
        "change_value",
    ]
    

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        self.request = request
        return super().get_queryset(request=request)
