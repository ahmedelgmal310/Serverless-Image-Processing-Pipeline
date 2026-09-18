"""Step 2 - produce one rendition of the original.

Called once per variant, in parallel, by the Step Functions Parallel state.
Output always lands in the processed bucket as a progressive JPEG with a
one-year immutable cache header, because the key never changes once written.
"""

import io
import logging
import os

import boto3
from PIL import Image, ImageOps

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

DEST_BUCKET = os.environ["DEST_BUCKET"]
JPEG_QUALITY = int(os.environ.get("JPEG_QUALITY", "85"))

s3 = boto3.client("s3")


def to_rgb(image):
    """Flatten transparency onto white so the result can be saved as JPEG."""
    if image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def lambda_handler(event, context):
    image_id = event["imageId"]
    bucket = event["bucket"]
    key = event["key"]
    variant = event["variant"]
    max_width = int(event["maxWidth"])
    max_height = int(event["maxHeight"])
    mode = event.get("mode", "contain")

    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    with Image.open(io.BytesIO(body)) as source:
        # Phone cameras store orientation in EXIF rather than rotating pixels.
        source = ImageOps.exif_transpose(source)
        image = to_rgb(source)

        if mode == "cover":
            # Fill the box exactly, cropping the overflow - square thumbnails.
            image = ImageOps.fit(
                image, (max_width, max_height), method=Image.Resampling.LANCZOS
            )
        else:
            # Fit inside the box, never upscaling past the original.
            image.thumbnail(
                (max_width, max_height), resample=Image.Resampling.LANCZOS
            )

        buffer = io.BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )
        out_width, out_height = image.size

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

    LOG.info(
        "Wrote %s (%sx%s, %s bytes) from %s", dest_key, out_width, out_height, len(payload), key
    )

    return {
        "variant": variant,
        "bucket": DEST_BUCKET,
        "key": dest_key,
        "width": out_width,
        "height": out_height,
        "sizeBytes": len(payload),
    }
