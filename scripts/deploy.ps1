<#
  One-command deploy: build the layer, build and deploy the stack, publish the
  static UI, then invalidate the CDN so the new page is served immediately.

  First run:   .\scripts\deploy.ps1 -Guided
  Later runs:  .\scripts\deploy.ps1
#>

param(
    [string] $StackName = "imagepipe",
    [string] $Region    = "us-east-1",
    [string] $Email     = "",
    [switch] $Guided,
    [switch] $SkipLayer
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }

# ---------------------------------------------------------------- prerequisites
foreach ($tool in @("aws", "sam")) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "'$tool' is not on PATH. See README.md > Prerequisites."
    }
}

# ---------------------------------------------------------------- 1. the layer
if (-not $SkipLayer) {
    Step "Building the Pillow layer"
    & "$root\scripts\build-layer.ps1"
} else {
    Write-Host "Skipping layer build (-SkipLayer)."
}

if (-not (Test-Path "$root\layers\pillow\python\PIL")) {
    throw "The Pillow layer is missing. Run .\scripts\build-layer.ps1 first."
}

# ---------------------------------------------------------------- 2. the stack
Step "sam build"
sam build
if ($LASTEXITCODE -ne 0) { throw "sam build failed." }

Step "sam deploy"
$deployArgs = @(
    "deploy",
    "--stack-name", $StackName,
    "--region", $Region,
    "--capabilities", "CAPABILITY_IAM",
    "--resolve-s3",
    "--no-fail-on-empty-changeset"
)
if ($Guided) {
    $deployArgs += "--guided"
} else {
    $deployArgs += @("--parameter-overrides", "ProjectName=$StackName NotificationEmail=$Email")
}
sam @deployArgs
if ($LASTEXITCODE -ne 0) { throw "sam deploy failed." }

# ---------------------------------------------------------------- 3. outputs
Step "Reading stack outputs"
$outputsJson = aws cloudformation describe-stacks `
    --stack-name $StackName --region $Region `
    --query "Stacks[0].Outputs" --output json
$outputs = @{}
($outputsJson | ConvertFrom-Json) | ForEach-Object { $outputs[$_.OutputKey] = $_.OutputValue }

$webBucket     = $outputs["WebsiteBucketName"]
$distributionId = $outputs["DistributionId"]
$siteUrl       = $outputs["SiteUrl"]

# ---------------------------------------------------------------- 4. the UI
Step "Publishing the static UI to $webBucket"
aws s3 cp "$root\frontend\index.html" "s3://$webBucket/index.html" `
    --region $Region `
    --content-type "text/html; charset=utf-8" `
    --cache-control "no-cache, must-revalidate"
if ($LASTEXITCODE -ne 0) { throw "Uploading the UI failed." }

# CloudFront keeps serving the previous copy until the old objects are dropped,
# so every deploy that touches an asset ends with an invalidation.
Step "Invalidating the CloudFront cache"
aws cloudfront create-invalidation `
    --distribution-id $distributionId `
    --paths "/*" `
    --query "Invalidation.Id" --output text | Out-Null

# ---------------------------------------------------------------- done
Write-Host "`nDeployed." -ForegroundColor Green
Write-Host "Live URL: $siteUrl" -ForegroundColor Green
Write-Host "CloudFront needs a few minutes on the very first deploy.`n"

if ($Email) {
    Write-Host "Confirm the SNS subscription in $Email to receive job notifications." -ForegroundColor Yellow
}
