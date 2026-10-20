from django.core.management.base import BaseCommand
from apps.service_requests.services import expire_stale_queued_requests


class Command(BaseCommand):
    help = "Fails queued requests that have exceeded MAX_QUEUE_WAIT_SECONDS."

    def handle(self, *args, **options):
        expired_count = expire_stale_queued_requests()
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully expired {expired_count} stale queued request(s)."
            )
        )
