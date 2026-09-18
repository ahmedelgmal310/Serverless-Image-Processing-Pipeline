<#
  Tears the whole stack down.

  CloudFormation refuses to delete a bucket that still holds objects, so the
  three buckets are emptied first. Run this when you are done demoing - an idle
  stack still accrues S3 storage and CloudFront requests.

  Usage:  .\scripts\cleanup.ps1 -StackName imagepipe -Region us-east-1
#>

param(
    [string] $StackName = "imagepipe",
    [string] $Region    = "us-east-1",
    [switch] $Force
)

$ErrorActionPreference = "Stop"

if (-not $Force) {
    Write-Host "This permanently deletes stack '$StackName' in $Region," -ForegroundColor Yellow
    Write-Host "including every uploaded and processed image." -ForegroundColor Yellow
    $answer = Read-Host "Type the stack name to confirm"
    if ($answer -ne $StackName) {
        Write-Host "Cancelled - nothing was deleted."
        exit 0
    }
}

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

Step "Reading stack outputs"
$outputsJson = aws cloudformation describe-stacks `
    --stack-name $StackName --region $Region `
    --query "Stacks[0].Outputs" --output json
$outputs = @{}
($outputsJson | ConvertFrom-Json) | ForEach-Object { $outputs[$_.OutputKey] = $_.OutputValue }

foreach ($key in @("SourceBucketName", "DestinationBucketName", "WebsiteBucketName")) {
    $bucket = $outputs[$key]
    if ($bucket) {
        Step "Emptying s3://$bucket"
        aws s3 rm "s3://$bucket" --recursive --region $Region --only-show-errors
    }
}

Step "Deleting the stack (this takes a few minutes - CloudFront is slow)"
aws cloudformation delete-stack --stack-name $StackName --region $Region
aws cloudformation wait stack-delete-complete --stack-name $StackName --region $Region

Write-Host "`nStack '$StackName' deleted." -ForegroundColor Green
