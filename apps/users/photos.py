"""Profile photos: validate an upload, shrink it, and build the public path to it.

Photos live in the database (see ProfilePhoto) because the free Render disk is wiped on every deploy.
"""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
# Photos are shown small (cards, avatars), so a 400px square is plenty and keeps the database light.
PHOTO_SIZE = (400, 400)
# A picture with this many pixels would take far too much memory to open.
MAX_PIXELS = 40_000_000

PHOTO_URL_PREFIX = "/api/v1/auth/photos/"


class InvalidPhoto(ValueError):
    """The upload is not a usable picture. The message is safe to show the user."""


def process_upload(uploaded_file):
    """Return the upload as square JPEG bytes, or raise InvalidPhoto."""
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        raise InvalidPhoto("That photo is too large. Choose one under 5 MB.")
    try:
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        image = Image.open(uploaded_file)
        if image.format not in ALLOWED_FORMATS:
            raise InvalidPhoto("Use a JPG, PNG, WebP or GIF picture.")
        image.load()
    except InvalidPhoto:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise InvalidPhoto("We could not read that file as a picture.") from None

    image = ImageOps.exif_transpose(image).convert("RGB")
    image = ImageOps.fit(image, PHOTO_SIZE, Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85, optimize=True)
    return out.getvalue()


def photo_path(user):
    """The path other screens use to show this user's photo, or None when they have none."""
    photo = getattr(user, "photo", None)
    return f"{PHOTO_URL_PREFIX}{photo.token}/" if photo else None
