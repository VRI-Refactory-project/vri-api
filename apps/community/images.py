"""Photos sent in a chat: validate the upload and shrink it.

Like profile photos, they are kept in the database (see MessageImage) because the free Render disk is
wiped on every deploy, so each photo is re-encoded as a JPEG that fits in 1600 pixels to keep it light.
"""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

from apps.users.photos import ALLOWED_FORMATS, MAX_PIXELS, InvalidPhoto

# Phone cameras produce pictures of several megabytes, so this is more generous than for avatars.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_SIDE = 1600


def process_chat_image(uploaded_file):
    """Return (JPEG bytes, width, height) for an upload, or raise InvalidPhoto."""
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        raise InvalidPhoto("That photo is too large. Choose one under 8 MB.")
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

    # Turn the picture upright the way the camera meant, then drop transparency and animation.
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=82, optimize=True)
    return out.getvalue(), image.width, image.height
