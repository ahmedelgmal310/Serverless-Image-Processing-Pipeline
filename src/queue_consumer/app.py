"""SQS consumer - turns an S3 upload event into a Step Functions execution.

SQS sits between S3 and the workflow so a burst of uploads, or a temporary
Step Functions throttle, is absorbed by the queue instead of being dropped.
Messages that keep failing land in the DLQ after three receives.
"""

import json
import logging
import os
import urllib.parse

import boto3
from botocore.exceptions import ClientError

LOG = logging.getLogger()
LOG.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]

sfn = boto3.client("stepfunctions")


def image_id_from_key(key):
    """uploads/<imageId>/<filename> -> <imageId>"""
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != "uploads" or not parts[1]:
        raise ValueError(f"Unexpected object key layout: {key}")
    return parts[1]


def start_execution(bucket, key, size):
    image_id = image_id_from_key(key)
    payload = {
        "imageId": image_id,
        "bucket": bucket,
        "key": key,
        "sizeBytes": size,
    }
    try:
        sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            # Deterministic name: S3 can deliver the same event more than once,
            # and a duplicate name is rejected instead of processing twice.
            name=image_id,
            input=json.dumps(payload),
        )
        LOG.info("Started execution for %s", image_id)
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ExecutionAlreadyExists":
            LOG.info("Execution %s already exists - duplicate event ignored", image_id)
            return
        raise


def lambda_handler(event, context):
    failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])

            # S3 sends one of these when the notification is first configured.
            if body.get("Event") == "s3:TestEvent":
                LOG.info("Ignoring s3:TestEvent")
                continue

            for s3_record in body.get("Records", []):
                s3_info = s3_record["s3"]
                bucket = s3_info["bucket"]["name"]
                key = urllib.parse.unquote_plus(s3_info["object"]["key"])
                size = s3_info["object"].get("size", 0)
                start_execution(bucket, key, size)

        except Exception:
            LOG.exception("Failed to handle message %s", message_id)
            failures.append({"itemIdentifier": message_id})

    # Only the failed messages go back on the queue; the rest are deleted.
    return {"batchItemFailures": failures}
