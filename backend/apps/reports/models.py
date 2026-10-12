from django.conf import settings
from django.db import models

from apps.core.models import TenantModel


class SavedReportFilter(TenantModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    report_key = models.CharField(max_length=60)
    name = models.CharField(max_length=80)
    params = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["name"]


class ReportSchedule(TenantModel):
    FREQ = [("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly")]
    report_key = models.CharField(max_length=60)
    name = models.CharField(max_length=80)
    params = models.JSONField(default=dict, blank=True)
    frequency = models.CharField(max_length=10, choices=FREQ, default="weekly")
    format = models.CharField(max_length=5, default="xlsx")
    recipients = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
