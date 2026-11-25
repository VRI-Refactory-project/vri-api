from django.core.management.base import BaseCommand

from apps.interpreters import scheduling
from apps.service_requests.services import sweep


class Command(BaseCommand):
    help = (
        "Move the request queue forward: take back offers nobody answered in time, offer waiting "
        "requests to free interpreters (emergencies first), and mark requests that waited too long "
        "as failed. Run it every minute or so."
    )

    def handle(self, *args, **options):
        result = sweep()
        result.update(scheduling.sweep())
        self.stdout.write(
            "expired={expired} offered={offered} failed={failed} alerts={alerts} "
            "scheduled_offered={scheduled_offered} reminders_sent={reminders_sent} no_shows={no_shows}".format(
                **result
            )
        )
