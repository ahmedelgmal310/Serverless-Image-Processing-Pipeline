"""POST /uploads - hand the browser a pre-signed PUT URL.

The browser uploads straight to S3, so the image bytes never travel through
API Gateway or Lambda. That keeps us under the 10 MB API Gateway payload limit
and costs nothing per megabyte.
"""

import json
import logging
import os
import re
import time
import uuid

import boto3
from botocore.config import Config

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

SOURCE_BUCKET = os.environ["SOURCE_BUCKET"]
TABLE_NAME = os.environ["TABLE_NAME"]
URL_EXPIRY_SECONDS = int(os.environ.get("URL_EXPIRY_SECONDS", "300"))
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", "10485760"))
RECORD_TTL_DAYS = 30

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

# Signature v4 is required for presigned URLs that carry a Content-Type
# condition in every region.
s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
table = boto3.resource("dynamodb").Table(TABLE_NAME)

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
}


def respond(status, body):
    return {"statusCode": status, "headers": CORS_HEADERS, "body": json.dumps(body)}


def safe_name(filename, content_type):
    """Strip anything that could escape the key prefix or confuse S3."""
    stem = os.path.basename(filename or "upload")
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem)[:80] or "upload"
    if "." not in stem:
        stem += ALLOWED_CONTENT_TYPES[content_type]
    return stem


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return respond(400, {"message": "Request body must be valid JSON."})

    content_type = (body.get("contentType") or "").lower().strip()
    size_bytes = body.get("sizeBytes")

    if content_type not in ALLOWED_CONTENT_TYPES:
        return respond(
            415,
            {
                "message": "Unsupported image type.",
                "allowed": sorted(ALLOWED_CONTENT_TYPES),
            },
        )

    if not isinstance(size_bytes, int) or size_bytes <= 0:
        return respond(400, {"message": "sizeBytes must be a positive integer."})

    if size_bytes > MAX_UPLOAD_BYTES:
        return respond(
            413,
            {
                "message": "File is too large.",
                "maxBytes": MAX_UPLOAD_BYTES,
            },
        )

    image_id = uuid.uuid4().hex
    key = f"uploads/{image_id}/{safe_name(body.get('filename'), content_type)}"
    now = int(time.time())

    upload_url = s3.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": SOURCE_BUCKET,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=URL_EXPIRY_SECONDS,
    )

    # Written before the upload so a failed or abandoned upload is still
    # visible as a PENDING record rather than vanishing silently.
    table.put_item(
        Item={
            "imageId": image_id,
            "status": "PENDING",
            "uploadedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "originalKey": key,
            "contentType": content_type,
            "declaredSizeBytes": size_bytes,
            "expiresAt": now + RECORD_TTL_DAYS * 86400,
        }
    )

    LOG.info("Issued upload URL for %s (%s bytes)", key, size_bytes)

    return respond(
        201,
        {
            "imageId": image_id,
            "uploadUrl": upload_url,
            "key": key,
            "expiresInSeconds": URL_EXPIRY_SECONDS,
            "requiredHeaders": {"Content-Type": content_type},
        },
    )
