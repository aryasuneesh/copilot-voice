# Build CopilotVoice.exe, then wrap it in an installer.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --quiet --upgrade pyinstaller pystray pillow
python copilot_voice.py --make-ico

pyinstaller --noconfirm --clean --windowed --name CopilotVoice --icon icon.ico `
  --collect-all faster_whisper --collect-all ctranslate2 `
  --collect-all tokenizers --collect-all onnxruntime `
  copilot_voice.py

$iscc = Get-ChildItem "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $iscc) {
  Write-Host "Inno Setup missing. Install it, then re-run:"
  Write-Host "  winget install JRSoftware.InnoSetup"
  exit 1
}
& $iscc.FullName installer.iss
Write-Host "done -> dist\CopilotVoiceSetup.exe"
