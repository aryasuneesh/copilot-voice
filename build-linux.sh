#!/usr/bin/env bash
# Build copilot-voice_<version>_amd64.deb. Run on Ubuntu/Debian.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="${1:-0.6.0}"
PKG="dist/deb/copilot-voice_${VERSION}_amd64"

python3 -m pip install --quiet --upgrade pyinstaller
python3 -m pip install --quiet keyboard sounddevice numpy pyperclip pystray pillow faster-whisper

python3 -m PyInstaller --noconfirm --clean --windowed --name copilot-voice \
  --collect-all faster_whisper --collect-all ctranslate2 \
  --collect-all tokenizers --collect-all onnxruntime \
  --collect-all huggingface_hub --hidden-import tqdm.auto \
  copilot_voice.py

rm -rf "$PKG"
mkdir -p "$PKG"/{DEBIAN,opt/copilot-voice,usr/bin,usr/share/applications,lib/udev/rules.d}
cp -r dist/copilot-voice/* "$PKG/opt/copilot-voice/"

cat > "$PKG/DEBIAN/control" <<EOF
Package: copilot-voice
Version: $VERSION
Section: utils
Priority: optional
Architecture: amd64
Depends: libc6, python3-tk, xclip | wl-clipboard, libayatana-appindicator3-1 | libappindicator3-1
Maintainer: aryasuneesh <aryasuneesh3@gmail.com>
Description: Repurpose the Copilot key as local push-to-talk dictation
 Hold a key, speak, let go: Whisper transcribes on-device and the text is
 pasted into the focused window. No account, no API key, nothing leaves
 the machine.
EOF

ln -sf /opt/copilot-voice/copilot-voice "$PKG/usr/bin/copilot-voice"

cat > "$PKG/usr/share/applications/copilot-voice.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Copilot Voice
Comment=Local push-to-talk dictation
Exec=pkexec /opt/copilot-voice/copilot-voice
Terminal=false
Categories=Utility;Accessibility;
EOF

# Reading /dev/input and writing /dev/uinput normally needs root. Granting them
# to the "input" group instead means the app runs as a normal user.
cat > "$PKG/lib/udev/rules.d/70-copilot-voice.rules" <<'EOF'
KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
KERNEL=="event*", GROUP="input", MODE="0660"
EOF

cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
getent group input >/dev/null || groupadd input
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true
modprobe uinput 2>/dev/null || true
echo "uinput" > /etc/modules-load.d/copilot-voice.conf
TARGET_USER="${SUDO_USER:-}"
if [ -n "$TARGET_USER" ]; then
    usermod -aG input "$TARGET_USER" || true
    echo "Added $TARGET_USER to the 'input' group. Log out and back in, then run: copilot-voice"
else
    echo "Add yourself to the 'input' group:  sudo usermod -aG input \$USER"
fi
EOF
chmod 755 "$PKG/DEBIAN/postinst"

dpkg-deb --build --root-owner-group "$PKG"
echo "built ${PKG}.deb"
