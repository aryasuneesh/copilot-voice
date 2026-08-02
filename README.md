<div align="center">

# Copilot Voice

**Give the Copilot key a job you actually want.**

Hold a key, speak, let go — your words appear in whatever app you're using.
Everything runs on your machine.

[![Release](https://img.shields.io/github/v/release/aryasuneesh/copilot-voice?style=flat-square&color=2563eb)](https://github.com/aryasuneesh/copilot-voice/releases)
[![Build](https://img.shields.io/github/actions/workflow/status/aryasuneesh/copilot-voice/release.yml?style=flat-square)](https://github.com/aryasuneesh/copilot-voice/actions)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2011%20%7C%20Linux-lightgrey?style=flat-square)](#installation)
[![Python](https://img.shields.io/pypi/pyversions/copilot-voice?style=flat-square)](https://pypi.org/project/copilot-voice/)
[![Offline](https://img.shields.io/badge/cloud-none-success?style=flat-square)](#privacy)

</div>

---

## What it does

Millions of laptops ship with a Copilot key. If you don't use Copilot, it's a
wasted key in prime real estate. Copilot Voice turns it into push-to-talk
dictation powered by [Whisper](https://github.com/openai/whisper), running
locally.

- 🎙️ **Hold to talk** or **tap to toggle** — your choice
- ⚡ **Instant** — the model stays loaded, so there's no spin-up per phrase
- 🔒 **Fully offline** — no account, no API key, no telemetry
- 🪶 **Any key** — Copilot key by default, but bind F13, Right Ctrl, anything spare
- 📋 **Pastes anywhere** — the text lands in whatever window has focus
- 🎯 **Four models** — from 150 MB and fast to 1.6 GB and multilingual

## Installation

### Windows

```powershell
winget install aryasuneesh.CopilotVoice
```

```powershell
choco install copilot-voice
```

Or download the installer from [Releases](https://github.com/aryasuneesh/copilot-voice/releases).

> Windows needs administrator rights to reserve a key system-wide. The
> installer handles this for you.

### Linux (Ubuntu / Debian)

```bash
sudo apt install ./copilot-voice_*.deb
```

Launch **Copilot Voice** from your app menu; it will ask for your password,
because reading keyboard input system-wide currently requires root on Linux.

> Linux support is early. Key presses can't be intercepted the way they are on
> Windows, so bind a spare key (F13, Right Ctrl) rather than the Copilot key.
> See [Linux support](docs/TECHNICAL.md#linux).

### Python

```bash
pip install copilot-voice
copilot-voice
```

## Getting started

The first launch walks you through a four-step setup:

| Step | What happens |
|:--|:--|
| **1. Pick your key** | Press the key you want. It's captured automatically |
| **2. Pick a style** | Hold to talk, or tap to start and tap to stop |
| **3. Get a model** | Downloads with a progress bar, then works offline forever |
| **4. Try it out** | Dictate into a test box before it touches your real apps |

After setup it lives in your system tray. Right-click for settings.

**Using it:** hold your key, say your sentence, let go. A rising tone means
recording, a falling tone means it's transcribing. The text is pasted a moment
later. Recording continues briefly after you release, so trailing words aren't
clipped.

## Choosing a model

| Model | Size | Speed | Languages |
|:--|:--|:--|:--|
| `base.en` | ~150 MB | Fastest | English |
| `distil-small.en` **(default)** | ~330 MB | Very fast | English |
| `small.en` | ~500 MB | Fast | English |
| `large-v3-turbo` | ~1.6 GB | Slower | 90+ languages |

Switch any time from the tray: **Change model…**. You get the same download
progress bar and test box, and previously downloaded models switch instantly.

## Settings

Right-click the tray icon to change your dictation style, switch models, or
toggle "Start with Windows".

Everything else lives in a config file you can edit by hand — **Open config
folder** in the tray menu takes you there.

| Setting | Default | What it does |
|:--|:--|:--|
| `tail_seconds` | `0.4` | Keeps listening this long after you release the key |
| `min_seconds` | `0.3` | Ignores accidental taps shorter than this |
| `auto_paste` | `true` | Pastes automatically; set `false` for clipboard only |
| `restore_clipboard` | `true` | Puts your previous clipboard back afterwards |
| `language` | `"en"` | Set `""` to auto-detect (multilingual models only) |
| `beep` | `true` | The start and stop tones |
| `intercept_chord` | `true` | Stops Windows opening Search when you press the Copilot key. Turn off if the Windows key misbehaves |

## Privacy

Audio never leaves your computer. There is no account, no API key, no
telemetry, and no network connection at all once your model is downloaded.
Transcription happens on your CPU, and recordings are held in memory only for
as long as it takes to transcribe them — nothing is written to disk.

## Troubleshooting

<details>
<summary><b>My key still opens Search or Copilot</b></summary>

Key behaviour varies between laptop makers and Windows versions. Run the built-in
key tester and [open an issue](https://github.com/aryasuneesh/copilot-voice/issues)
with what it prints:

```powershell
"C:\Program Files\CopilotVoice\CopilotVoice.exe" --keytest
```

In the meantime, run setup again from the tray and bind a spare key instead —
F13, Right Ctrl, or a macro key all work well.
</details>

<details>
<summary><b>My Windows key is behaving strangely</b></summary>

To stop Windows opening Search, the app briefly holds back the Windows key
while it checks whether the Copilot key is being pressed. If that interferes
with anything, turn it off from the tray: untick **Stop Windows opening
Search**. The change takes effect immediately.

Your key still triggers dictation afterwards — Windows may just open Search as
well. Binding a spare key instead avoids both problems.
</details>

<details>
<summary><b>Nothing happens when I press the key</b></summary>

Make sure the app is running (check the tray) and that it was started with
administrator rights on Windows. If it was launched from the Start menu rather
than at login, it may not be elevated.
</details>

<details>
<summary><b>Windows warns me about the installer</b></summary>

The installer isn't code-signed yet, so SmartScreen shows a warning. Click
**More info → Run anyway**. The source is all here if you'd rather build it
yourself.
</details>

<details>
<summary><b>Transcription is slow or inaccurate</b></summary>

Try a different model from the tray. `base.en` is fastest, `large-v3-turbo` is
most accurate. Accuracy also improves noticeably with a headset mic over a
built-in one.
</details>

<details>
<summary><b>Something else went wrong</b></summary>

The app writes a log you can attach to an issue. Find it via **Open config
folder** in the tray menu, or run `--diag` for a startup report.
</details>

## Contributing

Issues and pull requests are welcome — especially reports from laptops the key
doesn't work on yet, since that behaviour varies by manufacturer.

- [Technical notes](docs/TECHNICAL.md) — how key interception works, platform differences
- [Building from source](docs/TECHNICAL.md#building) — Windows installer, Debian package, Python wheel

## License

[MIT](LICENSE)
