#!/usr/bin/env bash
#
# One-command deploy (Linux/macOS equivalent of deploy.ps1): build the layer,
# build and deploy the stack, publish the static UI, invalidate the CDN.
#
#   STACK_NAME=imagepipe REGION=us-east-1 EMAIL=you@example.com ./scripts/deploy.sh

set -euo pipefail

STACK_NAME="${STACK_NAME:-imagepipe}"
REGION="${REGION:-us-east-1}"
EMAIL="${EMAIL:-}"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

step() { printf '\n==> %s\n' "$1"; }

for tool in aws sam; do
  command -v "$tool" >/dev/null 2>&1 || {
    echo "'$tool' is not on PATH. See README.md > Prerequisites." >&2
    exit 1
  }
done

step "Building the Pillow layer"
./scripts/build-layer.sh

[ -d "layers/pillow/python/PIL" ] || {
  echo "The Pillow layer is missing. Run ./scripts/build-layer.sh first." >&2
  exit 1
}

step "sam build"
sam build

step "sam deploy"
sam deploy \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --no-fail-on-empty-changeset \
  --parameter-overrides "ProjectName=$STACK_NAME NotificationEmail=$EMAIL"

step "Reading stack outputs"
outputs="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" --region "$REGION" \
  --query 'Stacks[0].Outputs' --output json)"

get_output() {
  python3 -c "import json,sys;print(next(o['OutputValue'] for o in json.load(sys.stdin) if o['OutputKey']=='$1'))" <<<"$outputs"
}

web_bucket="$(get_output WebsiteBucketName)"
distribution_id="$(get_output DistributionId)"
site_url="$(get_output SiteUrl)"

step "Publishing the static UI to $web_bucket"
aws s3 cp frontend/index.html "s3://$web_bucket/index.html" \
  --region "$REGION" \
  --content-type "text/html; charset=utf-8" \
  --cache-control "no-cache, must-revalidate"

# CloudFront keeps serving the previous copy until the old objects are dropped,
# so every deploy that touches an asset ends with an invalidation.
step "Invalidating the CloudFront cache"
aws cloudfront create-invalidation \
  --distribution-id "$distribution_id" \
  --paths '/*' \
  --query 'Invalidation.Id' --output text >/dev/null

printf '\nDeployed.\nLive URL: %s\n' "$site_url"
printf 'CloudFront needs a few minutes on the very first deploy.\n\n'

[ -n "$EMAIL" ] && echo "Confirm the SNS subscription in $EMAIL to receive job notifications."
