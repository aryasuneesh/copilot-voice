$ErrorActionPreference = 'Stop'

$version  = '0.6.0'
$packageName = 'copilot-voice'
$url      = "https://github.com/aryasuneesh/copilot-voice/releases/download/v$version/CopilotVoiceSetup.exe"
$checksum = '278EE872D9121FE3FB4EE36798C00930F46B403FDDF4372F6858F7F2E673DE94'

$packageArgs = @{
  packageName    = $packageName
  fileType       = 'exe'
  url            = $url
  checksum       = $checksum
  checksumType   = 'sha256'
  # Inno Setup silent switches; 3010 = success, reboot requested
  silentArgs     = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'
  validExitCodes = @(0, 3010)
  softwareName   = 'Copilot Voice*'
}

Install-ChocolateyPackage @packageArgs

Write-Host "Copilot Voice installed. Launch it to run the setup wizard."
