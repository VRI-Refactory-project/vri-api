from django.core.management.base import BaseCommand

from apps.service_requests.services import sweep_requests


class Command(BaseCommand):
    help = "Apply interpreter-request timeouts to every open request (same as POST /api/v1/requests/sweep/)."

    def handle(self, *args, **options):
        for name, count in sweep_requests().items():
            self.stdout.write(f"{name}: {count}")
