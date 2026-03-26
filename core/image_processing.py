import io
import os
from typing import Iterable

from PIL import Image, ImageOps
from django.core.files.uploadedfile import InMemoryUploadedFile, UploadedFile


MAX_LIGHTWEIGHT_BYTES = 512 * 1024
DEFAULT_CROP_SIZE = (1200, 1200)
DEFAULT_QUALITY = 82
HEAVY_FILE_QUALITY = 72
MIN_QUALITY = 45


def _build_output_name(original_name: str) -> str:
    base_name, _ext = os.path.splitext(original_name or 'image')
    safe_base_name = (base_name or 'image').strip().replace(' ', '_')
    return f'{safe_base_name}.webp'


def _as_rgb_or_rgba(image: Image.Image) -> Image.Image:
    if image.mode in ('RGBA', 'LA'):
        return image.convert('RGBA')
    if image.mode == 'P':
        # Preserve transparency where present in palette-based images.
        if 'transparency' in image.info:
            return image.convert('RGBA')
        return image.convert('RGB')
    return image.convert('RGB')


def optimize_uploaded_image(
    uploaded_file: UploadedFile,
    *,
    crop_size: tuple[int, int] = DEFAULT_CROP_SIZE,
    max_bytes: int = MAX_LIGHTWEIGHT_BYTES,
) -> InMemoryUploadedFile:
    original_size = getattr(uploaded_file, 'size', 0) or 0

    uploaded_file.seek(0)
    with Image.open(uploaded_file) as source:
        source = ImageOps.exif_transpose(source)
        source = _as_rgb_or_rgba(source)

        # Normalize to a centered crop to keep a consistent visual footprint.
        processed = ImageOps.fit(source, crop_size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))

        quality = HEAVY_FILE_QUALITY if original_size > max_bytes else DEFAULT_QUALITY
        output = io.BytesIO()

        processed.save(output, format='WEBP', quality=quality, method=6)

        # If still heavy, reduce quality progressively and slightly downscale.
        while output.tell() > max_bytes and quality > MIN_QUALITY:
            quality -= 5
            processed = processed.resize(
                (max(320, int(processed.width * 0.9)), max(320, int(processed.height * 0.9))),
                Image.Resampling.LANCZOS,
            )
            output = io.BytesIO()
            processed.save(output, format='WEBP', quality=quality, method=6)

    output.seek(0)
    output_name = _build_output_name(getattr(uploaded_file, 'name', 'image.webp'))

    return InMemoryUploadedFile(
        file=output,
        field_name=getattr(uploaded_file, 'field_name', None),
        name=output_name,
        content_type='image/webp',
        size=output.getbuffer().nbytes,
        charset=None,
    )


def optimize_uploaded_images(
    uploaded_files: Iterable[UploadedFile],
    *,
    crop_size: tuple[int, int] = DEFAULT_CROP_SIZE,
    max_bytes: int = MAX_LIGHTWEIGHT_BYTES,
) -> list[InMemoryUploadedFile]:
    return [
        optimize_uploaded_image(uploaded_file, crop_size=crop_size, max_bytes=max_bytes)
        for uploaded_file in uploaded_files
    ]
