from django.core.management.base import BaseCommand

from apps.service_requests.services import sweep


class Command(BaseCommand):
    help = (
        "Move the request queue forward: take back offers nobody answered in time, offer waiting "
        "requests to free interpreters (emergencies first), and mark requests that waited too long "
        "as failed. Run it every minute or so."
    )

    def handle(self, *args, **options):
        result = sweep()
        self.stdout.write(
            "expired={expired} offered={offered} failed={failed}".format(**result)
        )
