<#
  Builds the Pillow Lambda layer.

  Pillow ships compiled C extensions, so a plain `pip install` on Windows or
  macOS produces binaries Lambda cannot load. The flags below tell pip to
  download the Linux x86_64 wheel regardless of the machine running this
  script, which means no Docker is required.

  Usage:  .\scripts\build-layer.ps1
#>

$ErrorActionPreference = "Stop"

$root      = Split-Path -Parent $PSScriptRoot
$target    = Join-Path $root "layers\pillow\python"
$reqs      = Join-Path $root "scripts\pillow-requirements.txt"

Write-Host "Building Pillow layer -> $target" -ForegroundColor Cyan

if (Test-Path $target) {
    Remove-Item -Recurse -Force $target
}
New-Item -ItemType Directory -Force -Path $target | Out-Null

python -m pip install `
    --platform manylinux2014_x86_64 `
    --platform manylinux_2_28_x86_64 `
    --implementation cp `
    --python-version 3.12 `
    --only-binary=:all: `
    --target $target `
    --requirement $reqs

if ($LASTEXITCODE -ne 0) {
    throw "pip failed with exit code $LASTEXITCODE"
}

# Test and metadata directories add megabytes to the layer for no runtime benefit.
Get-ChildItem -Path $target -Directory -Filter "*.dist-info" |
    ForEach-Object { Remove-Item -Recurse -Force $_.FullName }

$sizeMb = [math]::Round(((Get-ChildItem -Recurse $target | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
Write-Host "Layer ready: $sizeMb MB" -ForegroundColor Green
