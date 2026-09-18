"""Final step - write the outcome to DynamoDB.

Handles both endings of the workflow: the success path records every variant
that was produced, the failure path records why it stopped so the UI can show
something better than a spinner that never resolves.
"""

import json
import logging
import os
import time

import boto3

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

TABLE_NAME = os.environ["TABLE_NAME"]
MAX_CAUSE_CHARS = 2000

table = boto3.resource("dynamodb").Table(TABLE_NAME)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def summarise_error(error):
    """Step Functions hands us {Error, Cause}; Cause can be a huge JSON blob."""
    if not isinstance(error, dict):
        return {"error": str(error)[:MAX_CAUSE_CHARS]}

    cause = error.get("Cause", "")
    if isinstance(cause, str):
        try:
            parsed = json.loads(cause)
            cause = parsed.get("errorMessage") or cause
        except (json.JSONDecodeError, AttributeError):
            pass

    return {
        "error": str(error.get("Error", "UnknownError"))[:256],
        "cause": str(cause)[:MAX_CAUSE_CHARS],
    }


def handle_failure(event):
    image_id = event["imageId"]
    details = summarise_error(event.get("error"))

    table.update_item(
        Key={"imageId": image_id},
        UpdateExpression="SET #s = :s, failedAt = :t, errorDetail = :e",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "FAILED",
            ":t": now_iso(),
            ":e": details,
        },
    )

    LOG.warning("Marked %s FAILED: %s", image_id, details)
    return {"imageId": image_id, "status": "FAILED", **details}


def handle_success(event):
    image_id = event["imageId"]
    variants = list(event.get("variants") or [])

    watermarked = event.get("watermarked")
    if watermarked:
        variants.append(watermarked)

    table.update_item(
        Key={"imageId": image_id},
        UpdateExpression=(
            "SET #s = :s, completedAt = :t, variants = :v, "
            "variantCount = :n REMOVE errorDetail"
        ),
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": "COMPLETED",
            ":t": now_iso(),
            ":v": variants,
            ":n": len(variants),
        },
    )

    LOG.info("Marked %s COMPLETED with %s variants", image_id, len(variants))
    return {
        "imageId": image_id,
        "status": "COMPLETED",
        "variantCount": len(variants),
    }


def lambda_handler(event, context):
    if event.get("status") == "FAILED":
        return handle_failure(event)
    return handle_success(event)
