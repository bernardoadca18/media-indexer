import asyncio
import warnings
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError


class InvalidImageError(ValueError):
    pass


SUPPORTED_IMAGE_TYPES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


@dataclass(frozen=True)
class ImageTechnicalMetadata:
    width: int
    height: int
    format_name: str
    content_type: str


def _inspect(path: Path, claimed_content_type: str | None, max_pixels: int) -> ImageTechnicalMetadata:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                detected_format = image.format
                width, height = image.size
                is_animated = bool(getattr(image, "is_animated", False))
                frame_count = int(getattr(image, "n_frames", 1))
                detected_type = SUPPORTED_IMAGE_TYPES.get(detected_format or "")
                if detected_type is None:
                    raise InvalidImageError("supported image types are JPEG, PNG, and WebP")
                if claimed_content_type not in SUPPORTED_IMAGE_TYPES.values():
                    raise InvalidImageError("declared MIME type is not supported")
                if claimed_content_type != detected_type:
                    raise InvalidImageError(
                        f"declared MIME type {claimed_content_type!r} does not match "
                        f"detected {detected_type!r}"
                    )
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise InvalidImageError(
                        f"image dimensions exceed the {max_pixels} pixel limit"
                    )
                if is_animated or frame_count != 1:
                    raise InvalidImageError(
                        "animated images are not supported as static image assets"
                    )
                image.verify()
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise InvalidImageError("file is not a valid supported static image") from exc

    return ImageTechnicalMetadata(
        width=width,
        height=height,
        format_name=(detected_format or "").casefold(),
        content_type=detected_type,
    )


async def inspect_static_image(
    path: Path,
    claimed_content_type: str | None,
    *,
    max_pixels: int,
) -> ImageTechnicalMetadata:
    if max_pixels <= 0:
        raise ValueError("max_pixels must be positive")
    return await asyncio.to_thread(_inspect, path, claimed_content_type, max_pixels)
