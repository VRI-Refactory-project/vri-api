from django.core.management.base import BaseCommand

from apps.realtime.departures import expire_departures
from apps.realtime.timeouts import fail_stale_calls


class Command(BaseCommand):
    help = (
        "Move video calls forward: fail calls that never connected in time, and settle sessions whose "
        "departure grace window has ended. Run it every minute or so."
    )

    def handle(self, *args, **options):
        failed = fail_stale_calls()
        settled = expire_departures()
        self.stdout.write(f"failed={failed} settled={settled}")
