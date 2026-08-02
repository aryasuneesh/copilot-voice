# Technical notes

Implementation detail for contributors. Users want the [README](../README.md).

## How the Copilot key is intercepted

The Copilot key does not send a scan code of its own. Firmware sends a chord —
commonly **LeftShift + LeftWin + F23** — and Windows acts on that chord *below*
`WH_KEYBOARD_LL`. A low-level hook therefore controls only what applications
see, not what the shell does. Swallowing F23 is not enough: the shell reacts to
the LWin that leads the chord and opens Search or Copilot anyway.

`ChordFilter` handles this by withholding LWin as well:

1. LWin arrives — swallow it, start a ~30 ms timer.
2. LShift arrives while waiting — swallow that too.
3. F23 arrives inside the window — cancel the timer, discard the whole chord,
   start dictation.
4. The timer expires instead — this was an ordinary Win press, so replay the
   withheld keys. Start and Win+shortcuts keep working, delayed imperceptibly.

Three constraints worth knowing before changing this code:

**A key can have more than one scan code.** F23 maps to both `134` and `110`,
and the press and release need not use the same one. Hooking only the captured
code leaves half the key running into Windows. `trigger_scan_codes()` binds
every code a key maps to.

**A slow hook callback is silently unhooked.** Windows removes a low-level hook
that exceeds `LowLevelHooksTimeout` and
[gives the process no way to detect it](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc).
Transcribing inside the callback makes the key work exactly once. The hook only
enqueues; a worker thread records and transcribes.

**The filter is a global suppressing hook.** Every keystroke passes through it,
so it must stay allocation-free and do no I/O. It is the first thing to suspect
if input misbehaves.

**Never inject a key while holding the filter's lock.** `SendInput` re-enters
the hook callback synchronously on the same thread. With a non-reentrant lock
that deadlocks the listener thread while it is still holding back a withheld
LWin — the Windows key then stops working for the life of the process.
`handle()` decides under the lock, returns the keys to inject, and sends them
after releasing it. `handle()` also fails open: an exception passes the event
through rather than swallowing it.

Because this is the most invasive part of the app, it is opt-out.
`intercept_chord: false` (or unticking **Stop Windows opening Search** in the
tray) binds the trigger key plainly and never touches a modifier. Windows may
then open Search alongside dictation, which is a far smaller problem than a
broken Win key. `use_chord_filter()` is the single place that decides.

### The Windows-level alternative

`HKCU\Software\Microsoft\Windows\Shell\BrandedKey\BrandedKeyChoiceType` backs
*Settings → Personalization → Text input → Customize Copilot key*. Setting it
to `NoneSelected` reportedly makes the key inert, which would let the app hook
it cleanly without the chord filter. Recent Windows builds protect that key with
`UCPD.sys`, so the write may be refused. The app does not modify it.

## Linux

Key interception works differently, and worse.

`keyboard`'s Linux backend never grabs the input device, so `suppress=True` is
silently ignored — key presses **cannot** be swallowed. The desktop still sees
them. On GNOME, the Copilot chord's leading Super press opens the Activities
overview as well as starting dictation. `ChordFilter` is therefore Windows-only;
on Linux the app binds the key plainly, because the filter's replay would inject
a second Win press on top of the one the desktop already saw.

Recommend a spare key (F13, Right Ctrl, a macro key) on Linux. The setup wizard
says so on the key-capture page.

**Root is required.** `keyboard` raises
`ImportError: You must be root to use this library on linux` at import time,
based on the effective uid alone. The `.deb` installs a udev rule granting the
`input` group access to `/dev/input` and `/dev/uinput`, but that only satisfies
the *device permissions* — the library's uid check rejects a normal user
regardless, so the desktop entry launches through `pkexec`.

Dropping the root requirement means replacing `keyboard` on Linux with direct
`evdev` + `uinput` access, which would then genuinely work as an `input`-group
user. That is the right fix and is not done yet.

What does work well: reading keys and injecting the paste both go through the
kernel (`/dev/input`, `/dev/uinput`), so they function under X11 **and**
Wayland, where XTEST-based tools fail. Clipboard goes through `wl-copy` or
`xclip`.

The tray icon needs an AppIndicator implementation
(`libayatana-appindicator3-1`), which the package depends on.

## Models

Models are stored in the app's own config directory as plain files, **not** the
HuggingFace cache. The cache stores `model.bin` as a symlink into `blobs/`,
which an elevated Windows process may fail to open.

`faster_whisper.download_model` hardcodes a disabled progress bar, so
`download_model()` calls `snapshot_download` directly with a `tqdm` subclass
that aggregates byte counts across files to drive the progress bar.

## Packaging notes

`--windowed` PyInstaller builds set `sys.stdout` and `sys.stderr` to `None`.
Any library that prints then raises `AttributeError`, killing whatever worker
thread it happened on with no visible error. The module redirects both to
`os.devnull` at import time.

The version appears in several files and the selftest fails if they disagree,
because a mismatch ships package manifests pointing at a release tag that does
not exist. Bump all of them together:

- `__version__` in `copilot_voice.py`
- `AppVersion` in `installer.iss`
- `version` in `pyproject.toml`
- `<version>` in `packaging/chocolatey/copilot-voice.nuspec`

winget's `ManifestVersion` is the *schema* version — leave it alone.

## Building

```bash
pip install keyboard sounddevice numpy pyperclip pystray pillow faster-whisper
python copilot_voice.py             # run from source (elevated on Windows)
python copilot_voice.py --selftest  # config, chord filter, tail buffer, versions
```

Diagnostics: `--keytest` shows every key event and whether it was suppressed;
`--diag` reports startup and model-load timings. Both write to the log file in
the config directory.

> `--keytest` records every key event while it is open. Close it before typing
> anything sensitive, and check the log before attaching it to an issue.

**Windows installer** — needs [Inno Setup](https://jrsoftware.org/isinfo.php)
(`winget install JRSoftware.InnoSetup`):

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

**Debian package** — on Ubuntu/Debian, needs `python3-tk`, `xclip` or
`wl-clipboard`, and `libayatana-appindicator3-1`:

```bash
./build-linux.sh 0.6.0
```

**Everything** — [`release.yml`](../.github/workflows/release.yml) builds the
installer, the `.deb` and the Python wheel on every `v*` tag, each on its own
runner, and attaches them to the release.

## Publishing

Cut the GitHub release first: winget and Chocolatey both verify the installer's
SHA256 against the published asset.

```powershell
gh release download v0.6.0 -p CopilotVoiceSetup.exe -D dist
powershell -File packaging\update-manifests.ps1 -Version 0.6.0
```

That stamps the released installer's hash into both manifests. Then:

```powershell
choco pack packaging\chocolatey\copilot-voice.nuspec
choco push copilot-voice.0.6.0.nupkg --source https://push.chocolatey.org/
winget validate packaging\winget
wingetcreate submit packaging\winget
python -m build && twine upload dist/*.whl dist/*.tar.gz
```
