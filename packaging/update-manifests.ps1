# Stamp a version and the installer's SHA256 into the Chocolatey and winget
# manifests. Run after building, before publishing. Hand-edited hashes go stale
# silently and both package managers then refuse to install.
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$InstallerPath
)
$ErrorActionPreference = 'Stop'

# $PSScriptRoot is not always populated in param defaults, so resolve it here.
$root = $PSScriptRoot
if (-not $root) { $root = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $InstallerPath) { $InstallerPath = Join-Path $root '..\dist\CopilotVoiceSetup.exe' }

if (-not (Test-Path $InstallerPath)) { throw "Installer not found: $InstallerPath" }
$sha = (Get-FileHash $InstallerPath -Algorithm SHA256).Hash
Write-Host "version $Version"
Write-Host "sha256  $sha"

function Set-Content-Utf8($Path, $Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding $false))
}

# --- Chocolatey ---
$nuspec = "$root\chocolatey\copilot-voice.nuspec"
$text = (Get-Content $nuspec -Raw) -replace '<version>[^<]+</version>', "<version>$Version</version>"
Set-Content-Utf8 $nuspec $text

$install = "$root\chocolatey\tools\chocolateyinstall.ps1"
$text = Get-Content $install -Raw
$text = $text -replace "\`$version\s*=\s*'[^']*'", "`$version  = '$Version'"
$text = $text -replace "\`$checksum\s*=\s*'[^']*'", "`$checksum = '$sha'"
Set-Content-Utf8 $install $text

# --- winget ---
Get-ChildItem "$root\winget\*.yaml" | ForEach-Object {
    $text = Get-Content $_.FullName -Raw
    $text = $text -replace 'PackageVersion: .*', "PackageVersion: $Version"
    $text = $text -replace 'InstallerSha256: .*', "InstallerSha256: $sha"
    $text = $text -replace 'releases/download/v[^/]+/', "releases/download/v$Version/"
    $text = $text -replace 'ReleaseDate: .*', "ReleaseDate: $(Get-Date -Format 'yyyy-MM-dd')"
    Set-Content-Utf8 $_.FullName $text
}

Write-Host "`nmanifests updated. To publish:"
Write-Host "  choco pack packaging\chocolatey\copilot-voice.nuspec"
Write-Host "  choco push copilot-voice.$Version.nupkg --source https://push.chocolatey.org/"
Write-Host "  winget validate packaging\winget"
Write-Host "  wingetcreate submit packaging\winget   # opens a PR to winget-pkgs"

