"""Make the key pair Web Push needs. Run it once, then put the two values in your environment."""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Command(BaseCommand):
    help = "Print a new key pair for Web Push (browser notifications)."

    def handle(self, *args, **options):
        key = ec.generate_private_key(ec.SECP256R1())
        private = _b64(key.private_numbers().private_value.to_bytes(32, "big"))
        public = _b64(
            key.public_key().public_bytes(
                serialization.Encoding.X962,
                serialization.PublicFormat.UncompressedPoint,
            )
        )
        self.stdout.write(f"WEB_PUSH_VAPID_PUBLIC_KEY={public}")
        self.stdout.write(f"WEB_PUSH_VAPID_PRIVATE_KEY={private}")
