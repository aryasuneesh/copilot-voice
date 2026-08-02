$ErrorActionPreference = 'Stop'

$uninstalled = $false
[array]$key = Get-UninstallRegistryKey -SoftwareName 'Copilot Voice*'

if ($key.Count -eq 1) {
  $key | ForEach-Object {
    Uninstall-ChocolateyPackage -PackageName 'copilot-voice' -FileType 'exe' `
      -SilentArgs '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' `
      -ValidExitCodes @(0, 3010) `
      -File $_.UninstallString.Trim('"')
  }
  $uninstalled = $true
} elseif ($key.Count -eq 0) {
  Write-Warning 'Copilot Voice is already uninstalled.'
} else {
  Write-Warning "$($key.Count) matches found, uninstall manually."
  $key | ForEach-Object { Write-Warning "- $($_.DisplayName)" }
}
