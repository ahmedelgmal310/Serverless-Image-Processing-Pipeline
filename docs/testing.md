# Testing and verification

Every command below assumes the stack is deployed and these two variables are set:

```bash
STACK=imagepipe
REGION=us-east-1
```

Pull the resource names out of the stack once:

```bash
aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table
```

---

## 1. Happy path (the demo)

1. Open the `SiteUrl` output in a browser.
2. Drop in a JPEG or PNG under 10 MB.
3. The five pipeline steps tick over in order; results appear in a few seconds.

**What to check:** three renditions are shown, the reported original dimensions match the
file you uploaded, and the watermarked copy carries the caption in the bottom-right corner.

Confirm the same thing from the CLI:

```bash
IMAGE_ID=<the id shown in the UI>
API=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" --output text)

curl -s "$API/images/$IMAGE_ID" | python -m json.tool
```

Expected: `"status": "COMPLETED"` and four entries under `variants`
(thumbnail, medium, watermarked — plus the `urls` map the UI uses).

---

## 2. The workflow actually ran in parallel

```bash
SM=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='StateMachineArn'].OutputValue" --output text)

aws stepfunctions list-executions --state-machine-arn $SM --max-items 1 --region $REGION
```

Open that execution in the Step Functions console. The two `Resize` branches inside
`GenerateVariants` have the **same start timestamp** — that is the `Parallel` state doing
its job, not two sequential calls.

---

## 3. Failure path, DLQ and alarms

Upload something that is not an image, straight to the source bucket so the browser's
own validation is bypassed:

```bash
SRC=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='SourceBucketName'].OutputValue" --output text)

echo "this is definitely not a JPEG" > fake.jpg
aws s3 cp fake.jpg "s3://$SRC/uploads/testfail001/fake.jpg" --region $REGION
```

**Expected sequence:**

1. `Validate` raises `ValidationError` — it cannot decode the bytes.
2. The task's `Catch` routes to `MarkFailed`.
3. `MarkFailed` writes `status = FAILED` plus the reason to DynamoDB.
4. `NotifyFailure` publishes to SNS (check your inbox if you subscribed).
5. The execution ends in `FailExecution`, so the `imagepipe-workflow-failures` alarm fires.

Read the record back:

```bash
TABLE=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='MetadataTableName'].OutputValue" --output text)

aws dynamodb get-item --table-name $TABLE --region $REGION \
  --key '{"imageId":{"S":"testfail001"}}' --output json
```

> Note the `Validate` step fails *cleanly*, so the SQS message is consumed successfully and
> never reaches the DLQ — that is correct behaviour. The DLQ catches infrastructure-level
> failures instead: throttles, timeouts, permission errors. To see it fill up, temporarily
> remove the `StepFunctionsExecutionPolicy` from `QueueConsumerFunction`, redeploy, upload
> an image, and watch three failed receives land the message in
> `imagepipe-processing-dlq`. Restore the policy afterwards.

---

## 4. Idempotency — duplicate events do not double-process

S3 event delivery is at-least-once. Force a second event for an object that has already
been processed:

```bash
aws s3 cp "s3://$SRC/uploads/$IMAGE_ID/photo.jpg" "s3://$SRC/uploads/$IMAGE_ID/photo.jpg" \
  --metadata-directive REPLACE --region $REGION
```

Then read the consumer's logs:

```bash
aws logs tail /aws/lambda/$STACK-queue-consumer --since 5m --region $REGION
```

**Expected:** `Execution <id> already exists - duplicate event ignored`, and no second
execution in the Step Functions list. The execution name is the image ID, so AWS rejects
the duplicate for us — no locking, no dedup table.

---

## 5. CloudFront is really caching the renditions

```bash
SITE=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='SiteUrl'].OutputValue" --output text)

curl -sI "$SITE/images/$IMAGE_ID/thumbnail.jpg" | grep -i -E 'x-cache|cache-control'
curl -sI "$SITE/images/$IMAGE_ID/thumbnail.jpg" | grep -i -E 'x-cache'
```

**Expected:** `x-cache: Miss from cloudfront` the first time, `Hit from cloudfront` the
second, and `cache-control: public, max-age=31536000, immutable` on both.

Check that the bucket itself is *not* publicly reachable:

```bash
DEST=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='DestinationBucketName'].OutputValue" --output text)

curl -sI "https://$DEST.s3.$REGION.amazonaws.com/images/$IMAGE_ID/thumbnail.jpg" | head -1
```

**Expected:** `HTTP/1.1 403 Forbidden`. Only CloudFront's OAC signature gets through.

---

## 6. The pre-signed URL is genuinely bounded

Request one and inspect it:

```bash
curl -s -X POST "$API/uploads" \
  -H 'Content-Type: application/json' \
  -d '{"filename":"test.jpg","contentType":"image/jpeg","sizeBytes":123456}' \
  | python -m json.tool
```

The returned `uploadUrl` contains `X-Amz-Expires=300` and is bound to one key. Try
uploading with the wrong content type and S3 returns `403 SignatureDoesNotMatch`:

```bash
URL=<uploadUrl from above>
curl -s -o /dev/null -w '%{http_code}\n' -X PUT "$URL" \
  -H 'Content-Type: image/png' --data-binary @some-photo.jpg
```

**Expected:** `403`.

Oversized and unsupported files are rejected before a URL is ever issued:

```bash
curl -s -X POST "$API/uploads" -H 'Content-Type: application/json' \
  -d '{"filename":"big.jpg","contentType":"image/jpeg","sizeBytes":99999999}'
# -> 413, "File is too large."

curl -s -X POST "$API/uploads" -H 'Content-Type: application/json' \
  -d '{"filename":"doc.pdf","contentType":"application/pdf","sizeBytes":1000}'
# -> 415, "Unsupported image type."
```

---

## 7. Concurrency

Fire twenty uploads at once and confirm nothing is dropped:

```bash
for i in $(seq 1 20); do
  aws s3 cp sample.jpg "s3://$SRC/uploads/load$i/sample.jpg" --region $REGION &
done
wait

aws stepfunctions list-executions --state-machine-arn $SM --region $REGION \
  --query 'length(executions)'
```

**Expected:** twenty executions, all `SUCCEEDED`, and an empty DLQ:

```bash
DLQ=$(aws cloudformation describe-stacks --stack-name $STACK --region $REGION \
  --query "Stacks[0].Outputs[?OutputKey=='DeadLetterQueueUrl'].OutputValue" --output text)

aws sqs get-queue-attributes --queue-url $DLQ --region $REGION \
  --attribute-names ApproximateNumberOfMessages
```

---

## 8. Tracing

Open **X-Ray → Service map** after a few uploads. The map shows
API Gateway → Lambda → DynamoDB for the upload handshake, and the Step Functions branch
for processing, with average latency on each edge. The `resize` functions dominate —
which is why they are the two functions given 1536 MB rather than the 512 MB default.
