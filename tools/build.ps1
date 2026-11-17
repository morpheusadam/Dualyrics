<#
.SYNOPSIS
    Build Dualyrics into a single windowed executable and a release zip.

.DESCRIPTION
    Output:
      dist\Dualyrics.exe                     the app (one file, no console window)
      dist\Dualyrics-<version>-windows-x64.zip   Dualyrics.exe + README.md + LICENSE + CHANGELOG.md
      dist\SHA256SUMS.txt                    SHA-256 of the exe and the zip

    Uses .venv\Scripts\python.exe when it exists, otherwise the python on PATH.
    pip honours the HTTPS_PROXY / HTTP_PROXY environment variables if you need a proxy.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Install
#>
[CmdletBinding()]
param(
    [switch]$Install,    # pip install -r requirements-build.txt first
    [switch]$SkipTests,  # do not run pytest before building
    [switch]$NoZip       # build the exe only
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) { $python = $venvPython } else { $python = "python" }

function Invoke-Step([string]$what, [scriptblock]$cmd) {
    Write-Host "==> $what" -ForegroundColor Cyan
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

$initText = Get-Content (Join-Path $root "dualyrics\__init__.py") -Raw
if ($initText -notmatch '__version__\s*=\s*"([^"]+)"') { throw "version not found in dualyrics\__init__.py" }
$version = $Matches[1]
Write-Host "Dualyrics $version, python: $python"

if ($Install) {
    Invoke-Step "Install build requirements" { & $python -m pip install --disable-pip-version-check -r requirements-build.txt }
}

if (-not $SkipTests) {
    Invoke-Step "Run tests" { & $python -m pytest -q }
}

Invoke-Step "PyInstaller" { & $python -m PyInstaller --noconfirm --clean --log-level WARN Dualyrics.spec }

$exe = Join-Path $root "dist\Dualyrics.exe"
if (-not (Test-Path $exe)) { throw "build finished but $exe is missing" }
$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "Built $exe ($sizeMb MB)" -ForegroundColor Green

if (-not $NoZip) {
    $zip = Join-Path $root "dist\Dualyrics-$version-windows-x64.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    $files = @($exe, (Join-Path $root "README.md"), (Join-Path $root "LICENSE"), (Join-Path $root "CHANGELOG.md")) |
        Where-Object { Test-Path $_ }
    Compress-Archive -Path $files -DestinationPath $zip -CompressionLevel Optimal
    $zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host "Packed $zip ($zipMb MB)" -ForegroundColor Green
    $sums = foreach ($f in @($exe, $zip)) {
        "{0}  {1}" -f (Get-FileHash $f -Algorithm SHA256).Hash.ToLower(), (Split-Path $f -Leaf)
    }
    $sumsFile = Join-Path $root "dist\SHA256SUMS.txt"
    [System.IO.File]::WriteAllLines($sumsFile, [string[]]$sums)
    Write-Host "Wrote $sumsFile"
}
