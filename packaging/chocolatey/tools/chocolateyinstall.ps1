$ErrorActionPreference = 'Stop'

$version  = '0.6.0'
$packageName = 'copilot-voice'
$url      = "https://github.com/aryasuneesh/copilot-voice/releases/download/v$version/CopilotVoiceSetup.exe"
$checksum = '2D3E7A9B525B94A2AA4101673E5BB0FC7CA6A91E5BD87E02F197131E5ED8541A'

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
