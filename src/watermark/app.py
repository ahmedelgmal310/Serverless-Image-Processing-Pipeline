"""Step 3 - burn a watermark onto the medium rendition.

Reads from the processed bucket rather than the original: the medium variant
is already EXIF-corrected, colour-converted and a sane size, so this step
stays cheap regardless of how large the upload was.
"""

import io
import logging
import os

import boto3
from PIL import Image, ImageDraw, ImageFont

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

DEST_BUCKET = os.environ["DEST_BUCKET"]
WATERMARK_TEXT = os.environ.get("WATERMARK_TEXT", "Manara SAA")
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "85"))

s3 = boto3.client("s3")


def load_font(size):
    """Pillow >= 10.1 can scale the built-in font; older builds cannot.

    Using the bundled font keeps the layer small - no .ttf to ship.
    """
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def lambda_handler(event, context):
    image_id = event["imageId"]
    source_key = event["sourceKey"]
    variant = event.get("variant", "watermarked")

    body = s3.get_object(Bucket=DEST_BUCKET, Key=source_key)["Body"].read()

    with Image.open(io.BytesIO(body)) as source:
        image = source.convert("RGBA")
        width, height = image.size

        # Scale the text with the image so it reads the same at any size.
        font_size = max(16, width // 28)
        font = load_font(font_size)

        overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        left, top, right, bottom = draw.textbbox((0, 0), WATERMARK_TEXT, font=font)
        text_width = right - left
        text_height = bottom - top

        padding = max(8, font_size // 2)
        box_width = text_width + padding * 2
        box_height = text_height + padding * 2
        box_x = width - box_width - padding
        box_y = height - box_height - padding

        # Translucent plate first, so the text stays readable on light images.
        draw.rounded_rectangle(
            [box_x, box_y, box_x + box_width, box_y + box_height],
            radius=max(4, padding // 2),
            fill=(0, 0, 0, 110),
        )
        draw.text(
            (box_x + padding - left, box_y + padding - top),
            WATERMARK_TEXT,
            font=font,
            fill=(255, 255, 255, 235),
        )

        composed = Image.alpha_composite(image, overlay).convert("RGB")

        buffer = io.BytesIO()
        composed.save(
            buffer,
            format="JPEG",
            quality=JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )
        out_width, out_height = composed.size

    payload = buffer.getvalue()
    dest_key = f"images/{image_id}/{variant}.jpg"

    s3.put_object(
        Bucket=DEST_BUCKET,
        Key=dest_key,
        Body=payload,
        ContentType="image/jpeg",
        CacheControl="public, max-age=31536000, immutable",
        Metadata={"imageid": image_id, "variant": variant},
    )

    LOG.info("Watermarked %s -> %s (%s bytes)", source_key, dest_key, len(payload))

    return {
        "variant": variant,
        "bucket": DEST_BUCKET,
        "key": dest_key,
        "width": out_width,
        "height": out_height,
        "sizeBytes": len(payload),
    }
