#!/usr/bin/env bash
#
# Tears the whole stack down (Linux/macOS equivalent of cleanup.ps1).
#
# CloudFormation refuses to delete a bucket that still holds objects, so the
# three buckets are emptied first. Run this when you are done demoing.
#
#   STACK_NAME=imagepipe REGION=us-east-1 ./scripts/cleanup.sh

set -euo pipefail

STACK_NAME="${STACK_NAME:-imagepipe}"
REGION="${REGION:-us-east-1}"

step() { printf '\n==> %s\n' "$1"; }

echo "This permanently deletes stack '$STACK_NAME' in $REGION,"
echo "including every uploaded and processed image."
read -r -p "Type the stack name to confirm: " answer
[ "$answer" = "$STACK_NAME" ] || { echo "Cancelled - nothing was deleted."; exit 0; }

step "Reading stack outputs"
outputs="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs' --output json)"

get_output() {
  python3 -c "import json,sys;print(next((o['OutputValue'] for o in json.load(sys.stdin) if o['OutputKey']=='$1'),''))" <<<"$outputs"
}

for key in SourceBucketName DestinationBucketName WebsiteBucketName; do
  bucket="$(get_output "$key")"
  if [ -n "$bucket" ]; then
    step "Emptying s3://$bucket"
    aws s3 rm "s3://$bucket" --recursive --region "$REGION" --only-show-errors
  fi
done

step "Deleting the stack (this takes a few minutes - CloudFront is slow)"
aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION"

printf '\nStack %s deleted.\n' "$STACK_NAME"
