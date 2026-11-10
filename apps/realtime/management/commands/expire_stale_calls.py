from django.core.management.base import BaseCommand

from apps.realtime.timeouts import fail_stale_calls


class Command(BaseCommand):
    help = (
        "Mark video calls as failed when they have been connecting longer than "
        "CALL_CONNECT_TIMEOUT_SECONDS. Run it every minute or so."
    )

    def handle(self, *args, **options):
        failed = fail_stale_calls()
        self.stdout.write(f"failed={failed}")
