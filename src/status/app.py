"""GET /images and GET /images/{imageId} - read job state back for the UI.

Variant keys are returned as CloudFront paths, not S3 URLs: the processed
bucket is private and only reachable through the distribution.
"""

import decimal
import json
import logging
import os

import boto3
from boto3.dynamodb.conditions import Key

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ["TABLE_NAME"]
CDN_PATH_PREFIX = os.environ.get("CDN_PATH_PREFIX", "/images")
STATUS_INDEX = "status-uploadedAt-index"
VALID_STATUSES = {"PENDING", "PROCESSING", "COMPLETED", "FAILED"}

table = boto3.resource("dynamodb").Table(TABLE_NAME)

CORS_HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    # The record changes while processing runs, so the browser must not cache it.
    "Cache-Control": "no-store",
}


class DecimalEncoder(json.JSONEncoder):
    """DynamoDB hands back Decimal; JSON does not know what to do with it."""

    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return int(o) if o % 1 == 0 else float(o)
        return super().default(o)


def respond(status, body):
    return {
        "statusCode": status,
        "headers": CORS_HEADERS,
        "body": json.dumps(body, cls=DecimalEncoder),
    }


def to_public_url(s3_key):
    """images/<id>/medium.jpg -> /images/<id>/medium.jpg on the CDN."""
    return f"{CDN_PATH_PREFIX.rstrip('/')}/{s3_key.split('/', 1)[1]}"


def decorate(item):
    """Add browser-ready URLs next to the raw S3 keys."""
    urls = {}
    for variant in item.get("variants", []) or []:
        if variant.get("key"):
            urls[variant["variant"]] = to_public_url(variant["key"])
    watermarked = item.get("watermarked") or {}
    if watermarked.get("key"):
        urls["watermarked"] = to_public_url(watermarked["key"])
    item["urls"] = urls
    return item


def lambda_handler(event, context):
    path_params = event.get("pathParameters") or {}
    image_id = path_params.get("imageId")

    if image_id:
        result = table.get_item(Key={"imageId": image_id})
        item = result.get("Item")
        if not item:
            return respond(404, {"message": "No such image.", "imageId": image_id})
        return respond(200, decorate(item))

    query = event.get("queryStringParameters") or {}
    status = (query.get("status") or "COMPLETED").upper()
    if status not in VALID_STATUSES:
        return respond(
            400, {"message": "Unknown status.", "valid": sorted(VALID_STATUSES)}
        )

    try:
        limit = min(max(int(query.get("limit", "24")), 1), 100)
    except ValueError:
        limit = 24

    result = table.query(
        IndexName=STATUS_INDEX,
        KeyConditionExpression=Key("status").eq(status),
        ScanIndexForward=False,  # newest first
        Limit=limit,
    )

    items = [decorate(item) for item in result.get("Items", [])]
    return respond(200, {"status": status, "count": len(items), "items": items})
