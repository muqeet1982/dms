from celery import shared_task


@shared_task
def check_subscription_statuses():
    from .services import SubscriptionService

    return SubscriptionService.run_status_checks()


@shared_task
def send_renewal_reminders():
    from .services import SubscriptionService

    return SubscriptionService.send_reminders()
