from celery import shared_task


@shared_task
def deliver_webhook(delivery_id):
    from apps.core.tenancy import bypass_tenant

    from .models import WebhookDelivery
    from .services import WebhookService

    with bypass_tenant():
        d = WebhookDelivery.all_objects.select_related("endpoint").filter(pk=delivery_id).first()
        if d and d.status == "pending":
            WebhookService.attempt(d)


@shared_task
def retry_failed_webhooks():
    from django.utils import timezone

    from apps.core.tenancy import bypass_tenant

    from .models import WebhookDelivery
    from .services import WebhookService

    n = 0
    with bypass_tenant():
        for d in WebhookDelivery.all_objects.select_related("endpoint").filter(status="pending", next_attempt_at__lte=timezone.now())[:200]:
            WebhookService.attempt(d)
            n += 1
    return n


@shared_task
def run_import(job_id):
    from apps.core.tenancy import bypass_tenant, tenant_context

    from .models import ImportJob
    from .services import ImportService

    with bypass_tenant():
        job = ImportJob.all_objects.select_related("tenant").get(pk=job_id)
    with tenant_context(job.tenant, job.started_by):
        ImportService.run(job)
