"""Step 1 - validate the upload before spending any processing money on it.

A file arriving in the bucket claims to be an image; this step proves it by
decoding the header, and rejects decompression bombs by pixel count rather
than by file size (a 2 MB PNG can expand to gigabytes in memory).
"""

import io
import logging
import os
import time

import boto3
from PIL import Image

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ["TABLE_NAME"]
MAX_PIXELS = int(os.environ.get("MAX_PIXELS", "40000000"))
SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "MPO"}

s3 = boto3.client("s3")
table = boto3.resource("dynamodb").Table(TABLE_NAME)

# Pillow's own bomb guard, expressed in the same units we report on.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ValidationError(Exception):
    """Raised for input the pipeline will never be able to process."""


def lambda_handler(event, context):
    image_id = event["imageId"]
    bucket = event["bucket"]
    key = event["key"]

    head = s3.head_object(Bucket=bucket, Key=key)
    size_bytes = head["ContentLength"]
    content_type = head.get("ContentType", "application/octet-stream")

    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    try:
        with Image.open(io.BytesIO(body)) as probe:
            image_format = probe.format
            width, height = probe.size
            probe.verify()  # consumes the file object, so re-open below if needed
    except Exception as exc:
        raise ValidationError(f"Not a decodable image: {exc}") from exc

    if image_format not in SUPPORTED_FORMATS:
        raise ValidationError(f"Unsupported image format: {image_format}")

    if width * height > MAX_PIXELS:
        raise ValidationError(
            f"Image is {width}x{height} = {width * height} pixels, "
            f"above the {MAX_PIXELS} limit."
        )

    table.update_item(
        Key={"imageId": image_id},
        UpdateExpression=(
            "SET #s = :s, #f = :f, #w = :w, #h = :h, "
            "sizeBytes = :b, contentType = :c, validatedAt = :t"
        ),
        ExpressionAttributeNames={
            "#s": "status",
            "#f": "format",
            "#w": "width",
            "#h": "height",
        },
        ExpressionAttributeValues={
            ":s": "PROCESSING",
            ":f": image_format,
            ":w": width,
            ":h": height,
            ":b": size_bytes,
            ":c": content_type,
            ":t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )

    LOG.info("Validated %s: %s %sx%s (%s bytes)", key, image_format, width, height, size_bytes)

    return {
        "imageId": image_id,
        "bucket": bucket,
        "key": key,
        "contentType": content_type,
        "format": image_format,
        "width": width,
        "height": height,
        "sizeBytes": size_bytes,
    }
