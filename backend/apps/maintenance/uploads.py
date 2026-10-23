import hashlib
import warnings
from io import BytesIO
from uuid import uuid4

from django.core.files.base import ContentFile


MAX_PHOTO_BYTES = 8 * 1024 * 1024
MAX_PHOTO_PIXELS = 20_000_000


class InvalidMaintenancePhoto(ValueError):
    pass


def sanitize_maintenance_photo(upload):
    """Decode and re-encode a small raster image, stripping metadata and unsafe payloads."""
    if upload.size > MAX_PHOTO_BYTES:
        raise InvalidMaintenancePhoto("Choose an image no larger than 8 MB.")

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise InvalidMaintenancePhoto("Photo uploads are temporarily unavailable on this server.") from exc

    allowed = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}
    try:
        upload.seek(0)
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(upload) as probe:
                image_format = probe.format
                width, height = probe.size
                if image_format not in allowed:
                    raise InvalidMaintenancePhoto("Use a JPEG, PNG, or WebP image.")
                if width < 1 or height < 1 or width * height > MAX_PHOTO_PIXELS or max(width, height) > 8000:
                    raise InvalidMaintenancePhoto("The image dimensions are too large. Choose an image under 20 megapixels.")
                probe.verify()

            upload.seek(0)
            with Image.open(upload) as source:
                normalized = ImageOps.exif_transpose(source)
                normalized.load()
                width, height = normalized.size
                if image_format == "JPEG":
                    if normalized.mode in ("RGBA", "LA"):
                        background = Image.new("RGB", normalized.size, (255, 255, 255))
                        background.paste(normalized, mask=normalized.getchannel("A"))
                        normalized = background
                    else:
                        normalized = normalized.convert("RGB")
                elif image_format == "PNG":
                    normalized = normalized.convert("RGBA" if "A" in normalized.getbands() else "RGB")
                else:
                    normalized = normalized.convert("RGBA" if "A" in normalized.getbands() else "RGB")
                output = BytesIO()
                save_options = {"optimize": True}
                if image_format in ("JPEG", "WEBP"):
                    save_options["quality"] = 88
                normalized.save(output, format=image_format, **save_options)
    except InvalidMaintenancePhoto:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidMaintenancePhoto("That file is not a valid supported image.") from exc
    finally:
        upload.seek(0)

    data = output.getvalue()
    if len(data) > MAX_PHOTO_BYTES:
        raise InvalidMaintenancePhoto("The processed image is larger than 8 MB. Choose a smaller image.")
    extension, content_type = allowed[image_format]
    sanitized = ContentFile(data, name=f"{uuid4().hex}.{extension}")
    sanitized.content_type = content_type
    sanitized.image_width = width
    sanitized.image_height = height
    sanitized.sha256 = hashlib.sha256(data).hexdigest()
    return sanitized
