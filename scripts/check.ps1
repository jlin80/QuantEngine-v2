# Ejecuta la batería completa de calidad local (igual que CI).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "== Ruff ==" -ForegroundColor Cyan
ruff check .
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "== Black ==" -ForegroundColor Cyan
black --check .
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "== MyPy ==" -ForegroundColor Cyan
mypy
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "== Pytest ==" -ForegroundColor Cyan
$env:QE_ENVIRONMENT = "testing"
pytest --cov
exit $LASTEXITCODE
