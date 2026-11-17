<#
.SYNOPSIS
    Build Dualyrics: a single windowed executable, a portable zip and the Windows installer.

.DESCRIPTION
    Output:
      dist\Dualyrics.exe                         the app (one file, no console window)
      dist\Dualyrics-<version>-windows-x64.zip   portable: Dualyrics.exe + README.md + LICENSE + CHANGELOG.md
      dist\Dualyrics-Setup-<version>.exe         per-user installer (Inno Setup, installer\Dualyrics.iss)
      dist\SHA256SUMS.txt                        SHA-256 of all of the above

    Uses .venv\Scripts\python.exe when it exists, otherwise the python on PATH.
    pip and the Farsi installer messages download honour the HTTPS_PROXY environment variable.

    The installer needs Inno Setup 6.3+ (ISCC.exe). It is looked up in $env:ISCC, on PATH and in the
    default install folders. Get it from https://jrsoftware.org/isdl.php, "winget install JRSoftware.InnoSetup"
    or "choco install innosetup".

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\build.ps1 -Install
#>
[CmdletBinding()]
param(
    [switch]$Install,      # pip install -r requirements-build.txt first
    [switch]$SkipTests,    # do not run pytest before building
    [switch]$NoZip,        # no portable zip
    [switch]$NoInstaller   # no Inno Setup installer
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) { $python = $venvPython } else { $python = "python" }

# Unofficial Farsi translation of the Inno Setup messages, pinned to the Inno Setup 7.1.1 source tag.
$farsiUrl = "https://raw.githubusercontent.com/jrsoftware/issrc/is-7_1_1/Files/Languages/Unofficial/Farsi.isl"
$farsiSha256 = "9d8c569388478d71702ac9e17461f24f1842b9ac1ee6cc146ba6d14964e66520"

function Invoke-Step([string]$what, [scriptblock]$cmd) {
    Write-Host "==> $what" -ForegroundColor Cyan
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

function Find-Iscc {
    $cands = @()
    if ($env:ISCC) { $cands += $env:ISCC }
    $onPath = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($onPath) { $cands += $onPath.Source }
    foreach ($base in @("$env:LOCALAPPDATA\Programs", $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ($base) { foreach ($v in "7", "6") { $cands += Join-Path $base "Inno Setup $v\ISCC.exe" } }
    }
    foreach ($c in $cands) { if ($c -and (Test-Path $c)) { return $c } }
    return $null
}

function Get-FarsiMessages([string]$dest) {
    if ((Test-Path $dest) -and (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower() -eq $farsiSha256) { return }
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $req = @{ Uri = $farsiUrl; OutFile = $dest; UseBasicParsing = $true }
    $proxy = if ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } else { $env:HTTP_PROXY }
    if ($proxy) { $req.Proxy = $proxy }
    Write-Host "Downloading Farsi.isl"
    Invoke-WebRequest @req
    $got = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $farsiSha256) { Remove-Item $dest -Force; throw "Farsi.isl checksum mismatch ($got)" }
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
$outputs = @($exe)

if (-not $NoZip) {
    $zip = Join-Path $root "dist\Dualyrics-$version-windows-x64.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    $files = @($exe, (Join-Path $root "README.md"), (Join-Path $root "LICENSE"), (Join-Path $root "CHANGELOG.md")) |
        Where-Object { Test-Path $_ }
    Compress-Archive -Path $files -DestinationPath $zip -CompressionLevel Optimal
    $zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host "Packed $zip ($zipMb MB)" -ForegroundColor Green
    $outputs += $zip
}

if (-not $NoInstaller) {
    $iscc = Find-Iscc
    if (-not $iscc) { throw "Inno Setup (ISCC.exe) not found: install it or set ISCC, or build with -NoInstaller" }
    $farsi = Join-Path $root "build\inno\Farsi.isl"
    Get-FarsiMessages $farsi
    $setup = Join-Path $root "dist\Dualyrics-Setup-$version.exe"
    if (Test-Path $setup) { Remove-Item $setup -Force }
    Invoke-Step "Inno Setup ($iscc)" { & $iscc /Q "/DAppVersion=$version" "/DFarsiIsl=$farsi" (Join-Path $root "installer\Dualyrics.iss") }
    if (-not (Test-Path $setup)) { throw "Inno Setup finished but $setup is missing" }
    $setupMb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
    Write-Host "Built $setup ($setupMb MB)" -ForegroundColor Green
    $outputs += $setup
}

$sums = foreach ($f in $outputs) {
    "{0}  {1}" -f (Get-FileHash $f -Algorithm SHA256).Hash.ToLower(), (Split-Path $f -Leaf)
}
$sumsFile = Join-Path $root "dist\SHA256SUMS.txt"
[System.IO.File]::WriteAllLines($sumsFile, [string[]]$sums)
Write-Host "Wrote $sumsFile"
