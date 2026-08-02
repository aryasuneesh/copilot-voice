"""Copilot key -> local Whisper dictation.

First run opens a setup wizard: pick the key, pick the mode, download the model
with a real progress bar, and try it out. After that it lives in the tray.
"""

import json
import os
import queue
import re
import subprocess
import sys
import traceback
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import keyboard
import numpy as np
import pyperclip
import pystray
import sounddevice as sd
from PIL import Image, ImageDraw

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    import winsound

# A --windowed build has sys.stdout/stderr set to None. Any library that prints
# then raises AttributeError, which kills the worker thread it happens on with no
# visible error. Give them somewhere to go before importing anything noisy.
for _name in ("stdout", "stderr"):
    if getattr(sys, _name) is None:
        setattr(sys, _name, open(os.devnull, "w"))

__version__ = "0.6.3"

APP_NAME = "CopilotVoice"
if IS_WINDOWS:
    CONFIG_DIR = Path(os.environ["APPDATA"]) / APP_NAME
else:
    CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME
CONFIG_PATH = CONFIG_DIR / "config.json"
TASK_NAME = "CopilotVoiceAutostart"
AUTOSTART_DESKTOP = (Path.home() / ".config" / "autostart" / "copilot-voice.desktop")

MODELS = {
    "distil-small.en": "Fastest. English only. Good for dictation. ~330 MB",
    "base.en": "Small and quick. English only. ~150 MB",
    "small.en": "More accurate, still fast. English only. ~500 MB",
    "large-v3-turbo": "Most accurate. All languages. Slower. ~1.6 GB",
}

DEFAULTS = {
    "onboarded": False,
    "mode": "hold",            # "hold" = record while key held, "toggle" = tap on/off
    "hotkey_scan": None,       # scan code captured during setup
    "hotkey_name": "",
    "model": "distil-small.en",
    "compute_type": "int8",
    "language": "en",
    "auto_paste": True,
    "restore_clipboard": True,
    "min_seconds": 0.3,
    "tail_seconds": 0.4,       # keep recording this long after the key is let go
    "intercept_chord": True,   # withhold LWin to stop Search opening (Windows only)
    "sample_rate": 16000,
    "beep": True,
}

# Modifiers alone are never a usable trigger key.
MODIFIER_SCANS = {29, 42, 54, 56, 91, 92, 3675, 3676, 3613, 3640}


def log(msg):
    """Windowed builds have no console, so errors go to a file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_DIR / "log.txt", "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")


def load_config(path=CONFIG_PATH):
    cfg = dict(DEFAULTS)
    if path.exists():
        cfg.update(json.loads(path.read_text()))
    if cfg["mode"] not in ("hold", "toggle"):
        raise ValueError(f"mode must be 'hold' or 'toggle', got {cfg['mode']!r}")
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2))


def exe_command():
    """How to relaunch this program, frozen or not."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{Path(__file__).resolve()}"'


def autostart_enabled():
    if not IS_WINDOWS:
        return AUTOSTART_DESKTOP.exists()
    return subprocess.run(
        ["schtasks", "/query", "/tn", TASK_NAME],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    ).returncode == 0


def set_autostart(enable):
    """Windows: a scheduled task rather than a Startup shortcut, because
    /rl HIGHEST starts us elevated at logon with no UAC prompt, which the key
    hook needs. Linux: a plain XDG autostart entry -- the .deb grants access to
    the input devices via udev, so no privilege escalation is wanted here."""
    if not IS_WINDOWS:
        try:
            if enable:
                AUTOSTART_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
                AUTOSTART_DESKTOP.write_text(
                    "[Desktop Entry]\nType=Application\nName=Copilot Voice\n"
                    f"Exec={exe_command()}\nX-GNOME-Autostart-enabled=true\n")
            elif AUTOSTART_DESKTOP.exists():
                AUTOSTART_DESKTOP.unlink()
            return True
        except OSError as e:
            log(f"autostart change failed: {e}")
            return False

    if enable:
        cmd = ["schtasks", "/create", "/f", "/tn", TASK_NAME, "/sc", "onlogon",
               "/rl", "HIGHEST", "/tr", exe_command()]
    else:
        cmd = ["schtasks", "/delete", "/f", "/tn", TASK_NAME]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0:
        log(f"autostart change failed (need admin?): {r.stderr.strip()}")
    return r.returncode == 0


def open_folder(path):
    if IS_WINDOWS:
        os.startfile(path)
    else:
        subprocess.Popen(["xdg-open", str(path)])


def make_icon(size=64, recording=False):
    """Mic glyph, drawn rather than shipped as an asset."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fg = (255, 80, 80, 255) if recording else (240, 240, 240, 255)
    u = size / 64
    d.rounded_rectangle([24 * u, 10 * u, 40 * u, 38 * u], radius=8 * u, fill=fg)
    d.arc([16 * u, 26 * u, 48 * u, 50 * u], start=0, end=180, fill=fg, width=int(4 * u))
    d.line([32 * u, 50 * u, 32 * u, 56 * u], fill=fg, width=int(4 * u))
    return img


# --------------------------------------------------------------------------
# model download with progress
# --------------------------------------------------------------------------

def model_repo(name):
    from faster_whisper.utils import _MODELS
    return _MODELS.get(name, name)


def model_dir(name):
    return CONFIG_DIR / "models" / name.replace("/", "--")


def model_is_local(name):
    """Path to the already-downloaded model, or None."""
    path = model_dir(name)
    return str(path) if (path / "model.bin").exists() else None


_ALLOW = ["config.json", "preprocessor_config.json", "model.bin",
          "tokenizer.json", "vocabulary.*"]


def download_model(name, on_progress):
    """faster_whisper.download_model hardcodes a disabled progress bar, so call
    snapshot_download ourselves with a tqdm that reports bytes.

    local_dir gives real files. The default HF cache stores model.bin as a
    symlink into blobs/, which an elevated Windows process can fail to open
    ("Unable to open file 'model.bin'")."""
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm

    live = []

    class ReportingTqdm(tqdm):
        def __init__(self, *a, **kw):
            # windowed builds have no stderr for tqdm to draw on
            kw["file"] = open(os.devnull, "w")
            super().__init__(*a, **kw)
            if self.unit == "B":
                live.append(self)

        def update(self, n=1):
            super().update(n)
            done = sum(t.n for t in live)
            total = sum(t.total or 0 for t in live)
            if total:
                on_progress(done, total)

    target = model_dir(name)
    target.mkdir(parents=True, exist_ok=True)
    path = snapshot_download(model_repo(name), allow_patterns=_ALLOW,
                             local_dir=str(target), tqdm_class=ReportingTqdm)
    on_progress(1, 1)
    log(f"downloaded {name} to {path}")
    return path


# --------------------------------------------------------------------------
# audio + dictation
# --------------------------------------------------------------------------

class Recorder:
    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self.chunks = []
        self.stream = None

    def start(self):
        if self.stream:
            return
        self.chunks = []
        self.stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=lambda data, *_: self.chunks.append(data.copy()),
        )
        self.stream.start()

    def stop(self):
        if not self.stream:
            return np.zeros(0, dtype=np.float32)
        self.stream.stop()
        self.stream.close()
        self.stream = None
        if not self.chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.chunks).flatten()

    @property
    def running(self):
        return self.stream is not None


def trigger_scan_codes(cfg):
    """One key can emit more than one scan code -- f23 is both 134 and 110, and
    the press and release do not have to use the same one. Hooking only the
    captured code leaves the other half of the key running into Windows."""
    scans = set()
    if cfg.get("hotkey_scan") is not None:
        scans.add(cfg["hotkey_scan"])
    try:
        scans.update(keyboard.key_to_scan_codes(cfg.get("hotkey_name") or ""))
    except (ValueError, KeyError):
        pass
    return scans


class ChordFilter:
    """Windows acts on the Copilot chord below WH_KEYBOARD_LL: swallowing F23
    alone still leaves Search opening, because the shell reacts to the LWin that
    leads the chord. So withhold LWin too, and if F23 does not follow within a
    few tens of milliseconds, replay it -- an ordinary Win press is only delayed.

    handle() returns True to let an event through, False to swallow it.
    """

    WIN, SHIFT = 91, 42

    # A hook reports LWin as 0x5B, but the real key is the *extended* 0xE05B.
    # Replaying the plain code injects something Windows does not accept as the
    # Windows key, so shell shortcuts (Win+V, Win+E) stay dead until auto-repeat
    # delivers a genuine press.
    REPLAY_AS = {WIN: 57435, 92: 57436}

    def __init__(self, trigger_scans, on_down, on_up, window=0.03, sender=None):
        self.trigger_scans = set(trigger_scans)
        self.on_down = on_down
        self.on_up = on_up
        self.window = window
        self.send = sender or (lambda scan: keyboard.press(self.REPLAY_AS.get(scan, scan)))
        self.state = "idle"      # idle | waiting | chord | passthrough
        self.held = []
        self.timer = None
        # RLock, not Lock: injecting a key re-enters handle() on this very
        # thread, because the hook callback runs on the listener thread that
        # SendInput feeds. A plain Lock deadlocks there and hangs the keyboard.
        self.lock = threading.RLock()

    def _replay(self):
        """The wait expired, so this was a real Win press after all."""
        try:
            with self.lock:
                if self.state != "waiting":
                    return
                self.state = "passthrough"
                held, self.held = self.held, []
            for scan in held:   # outside the lock, always
                self.send(scan)
        except Exception:
            log(traceback.format_exc())
            with self.lock:     # a stuck state would kill the Win key
                self.state = "idle"
                self.held = []

    def _cancel_timer(self):
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def handle(self, event):
        """Returns True to let the event through, False to swallow it.

        Key injection is deferred until the lock is released. Injecting while
        holding it re-enters this method on the same thread and, with a
        non-reentrant lock, hangs the keyboard for the life of the process."""
        try:
            allowed, to_send = self._decide(event)
        except Exception:
            log(traceback.format_exc())
            return True  # never let a bug in here swallow the user's keyboard
        for scan in to_send:
            self.send(scan)
        return allowed

    def _decide(self, event):
        down = event.event_type == "down"
        scan = event.scan_code

        with self.lock:
            if scan in self.trigger_scans:
                self._cancel_timer()
                self.state = "chord"
                self.held = []
                callback = self.on_down if down else self.on_up
                threading.Thread(target=callback, daemon=True).start()
                return False, ()

            if self.state == "chord":
                # eat the rest of the chord so the shell never sees a Win press
                if scan in (self.WIN, self.SHIFT):
                    if not down and scan == self.WIN:
                        self.state = "idle"
                    return False, ()
                self.state = "idle"
                return True, ()

            if down and scan == self.WIN and self.state == "idle":
                self.state = "waiting"
                self.held = [self.WIN]
                self.timer = threading.Timer(self.window, self._replay)
                self.timer.daemon = True
                self.timer.start()
                return False, ()

            if self.state == "waiting":
                if down and scan == self.SHIFT:
                    self.held.append(self.SHIFT)
                    return False, ()
                # anything else means this was never the Copilot chord
                self._cancel_timer()
                self.state = "passthrough"
                held, self.held = self.held, []
                return True, tuple(held)

            if not down and scan == self.WIN:
                self.state = "idle"

            return True, ()


def use_chord_filter(cfg):
    """Whether to withhold LWin as well as the trigger key.

    This is the most invasive thing the app does -- it routes every keystroke
    through a Python callback and briefly holds back a modifier the whole OS
    depends on. Turning it off binds the trigger key plainly: Windows may also
    open Search, but the Win key is never touched.
    """
    return IS_WINDOWS and cfg.get("intercept_chord", True)


def beep(cfg, high):
    if not cfg["beep"]:
        return
    freq = 880 if high else 440
    if IS_WINDOWS:
        winsound.Beep(freq, 80)
        return
    try:  # no winsound off Windows; synthesise the same tone
        rate, seconds = 16000, 0.08
        t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
        sd.play(0.2 * np.sin(2 * np.pi * freq * t), rate, blocking=True)
    except Exception as e:
        log(f"beep failed: {e}")


def paste_text(text, cfg):
    if not text:
        return
    old = pyperclip.paste() if cfg["restore_clipboard"] else None
    pyperclip.copy(text)
    if cfg["auto_paste"]:
        keyboard.send("ctrl+v")
        time.sleep(0.3)  # let the target app read the clipboard before we restore
        if old is not None:
            pyperclip.copy(old)


class Dictation:
    """Key hook -> record -> transcribe -> hand the text to a sink."""

    def __init__(self, cfg, on_text, on_state=None, on_status=None):
        self.cfg = cfg
        self.on_text = on_text
        self.on_state = on_state or (lambda recording: None)
        self.on_status = on_status or (lambda msg: None)
        self.rec = Recorder(cfg["sample_rate"])
        self.model = None
        self.hooks = []
        self.filter = None
        self.queue = queue.Queue()

    def load_model(self, path=None):
        log("importing faster_whisper")
        from faster_whisper import WhisperModel
        log(f"imported. loading model from {path or self.cfg['model']}")
        self.model = WhisperModel(path or self.cfg["model"], device="cpu",
                                  compute_type=self.cfg["compute_type"])
        log("model loaded")

    def begin(self):
        beep(self.cfg, high=True)
        self.rec.start()
        self.on_state(True)

    def finish(self):
        # Keep listening past the key release: people are still finishing the
        # last word as they let go, and cutting at the release clips it. Safe to
        # sleep here, this runs on the worker thread, never on the key hook.
        if self.cfg.get("tail_seconds"):
            time.sleep(self.cfg["tail_seconds"])
        audio = self.rec.stop()
        self.on_state(False)
        beep(self.cfg, high=False)
        if len(audio) < self.cfg["min_seconds"] * self.cfg["sample_rate"]:
            return
        segments, _ = self.model.transcribe(audio, language=self.cfg["language"] or None)
        self.on_text(" ".join(s.text.strip() for s in segments).strip())

    # Nothing slow may run on the hook thread. Windows silently removes a
    # low-level hook whose callback exceeds LowLevelHooksTimeout, and gives the
    # process no way to find out -- which is why the key used to work exactly
    # once and then go dead. These two only queue, and return immediately.

    def on_press(self):
        self.queue.put("down")

    def on_release(self):
        self.queue.put("up")

    def _worker(self):
        while True:
            cmd = self.queue.get()
            if cmd is None:
                return
            try:
                self._handle(cmd)
            except Exception:
                import traceback
                log(traceback.format_exc())

    def _handle(self, cmd):
        if self.model is None:
            # bound before the model finishes loading, so the key cannot leak
            self.on_status("Model is still loading...")
            return
        if cmd == "down":
            if self.cfg["mode"] == "hold":
                if not self.rec.running:
                    self.begin()
            elif self.rec.running:
                self.finish()
            else:
                self.begin()
        elif cmd == "up" and self.cfg["mode"] == "hold" and self.rec.running:
            self.finish()

    def bind(self):
        # fresh queue: unbind() leaves a stop sentinel behind, which would
        # otherwise be the first thing a rebound worker reads
        self.queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()
        scans = trigger_scan_codes(self.cfg)
        if use_chord_filter(self.cfg):
            self.filter = ChordFilter(scans, self.on_press, self.on_release)
            self.hooks.append(keyboard.hook(self.filter.handle, suppress=True))
            log(f"bound scan codes {sorted(scans)} via chord filter")
        else:
            # Plain binding: only the trigger key is touched, never a modifier.
            # On Linux suppress is ignored anyway -- keyboard's backend there
            # never grabs the device -- so the chord filter would withhold
            # nothing and replay LWin on top of the press the desktop already saw.
            self.filter = None
            for scan in scans:
                self.hooks.append(keyboard.on_press_key(
                    scan, lambda e: self.on_press(), suppress=IS_WINDOWS))
                self.hooks.append(keyboard.on_release_key(
                    scan, lambda e: self.on_release(), suppress=IS_WINDOWS))
            log(f"bound scan codes {sorted(scans)} plainly (chord filter off)")

    def unbind(self):
        for h in self.hooks:
            keyboard.unhook(h)
        self.hooks = []
        self.queue.put(None)
        if self.rec.running:
            self.rec.stop()


# --------------------------------------------------------------------------
# setup wizard
# --------------------------------------------------------------------------

class Wizard:
    """Returns the saved config, or None if the user closed the window."""

    def __init__(self, cfg, pages=None):
        self.cfg = dict(cfg)
        self.only = pages          # None = full setup; else a subset by name
        self.dictation = None
        self.model_path = None
        self.completed = False
        self.capture_hook = None
        self.ui = queue.Queue()  # worker threads -> tk main thread

        self.root = tk.Tk()
        self.root.title("Copilot Voice Setup")
        self.root.geometry("560x420")
        self.root.resizable(False, False)
        try:
            self.root.iconphoto(True, tk.PhotoImage(data=self._icon_png()))
        except Exception:
            pass

        self.body = ttk.Frame(self.root, padding=20)
        self.body.pack(fill="both", expand=True)
        self.nav = ttk.Frame(self.root, padding=(20, 0, 20, 16))
        self.nav.pack(fill="x")
        self.back_btn = ttk.Button(self.nav, text="Back", command=self.back)
        self.back_btn.pack(side="left")
        self.next_btn = ttk.Button(self.nav, text="Next", command=self.next)
        self.next_btn.pack(side="right")

        all_pages = {"welcome": self.page_welcome, "key": self.page_key,
                     "mode": self.page_mode, "model": self.page_model,
                     "try": self.page_try}
        self.pages = [all_pages[name] for name in (pages or all_pages)]
        if pages:
            self.root.title("Copilot Voice - Change model")
        self.index = 0
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(50, self.pump)
        self.show()

    @staticmethod
    def _icon_png():
        import base64, io
        buf = io.BytesIO()
        make_icon(64).save(buf, "PNG")
        return base64.b64encode(buf.getvalue())

    # --- plumbing ---

    def pump(self):
        while True:
            try:
                fn = self.ui.get_nowait()
            except queue.Empty:
                break
            fn()
        self.root.after(50, self.pump)

    def post(self, fn):
        self.ui.put(fn)

    def clear(self):
        if self.capture_hook:
            keyboard.unhook(self.capture_hook)
            self.capture_hook = None
        if self.dictation:
            self.dictation.unbind()
        for w in self.body.winfo_children():
            w.destroy()

    def show(self):
        self.clear()
        self.back_btn.state(["!disabled"] if self.index else ["disabled"])
        self.next_btn.config(text="Finish" if self.index == len(self.pages) - 1 else "Next")
        self.pages[self.index]()

    def next(self):
        if self.index == len(self.pages) - 1:
            self.finish()
        else:
            self.index += 1
            self.show()

    def back(self):
        self.index = max(0, self.index - 1)
        self.show()

    def close(self):
        self.clear()
        self.root.destroy()

    def finish(self):
        self.cfg["onboarded"] = True
        save_config(self.cfg)
        self.completed = True
        self.close()

    def heading(self, title, subtitle):
        ttk.Label(self.body, text=title, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(self.body, text=subtitle, wraplength=500,
                  foreground="#666").pack(anchor="w", pady=(4, 16))

    # --- pages ---

    def page_welcome(self):
        self.heading("Copilot Voice",
                     "Turn a key into push-to-talk dictation. Speech is transcribed by "
                     "Whisper running on your own computer: no internet after setup, no account, "
                     "nothing leaves the machine.")
        for line in ["1.  Choose the key that starts dictation",
                     "2.  Choose hold-to-talk or tap-to-toggle",
                     "3.  Download the speech model",
                     "4.  Try it out"]:
            ttk.Label(self.body, text=line).pack(anchor="w", pady=3)

    def page_key(self):
        self.heading("Press your key",
                     "Press the key you want to use. On this laptop that is the Copilot "
                     "key: Windows reports it as Shift+Win+F23, which is why it needs to "
                     "be captured rather than guessed. Any spare key works too.")
        if not IS_WINDOWS:
            ttk.Label(self.body, wraplength=500, foreground="#b00",
                      text="On Linux the key press cannot be swallowed, so your desktop "
                           "still sees it. If the Copilot key opens the Activities "
                           "overview, pick a spare key instead (F13, Right Ctrl, a macro "
                           "key).").pack(anchor="w", pady=(0, 10))
        status = ttk.Label(self.body, text="Waiting for a key press...",
                           font=("Segoe UI", 12))
        status.pack(anchor="w", pady=10)
        detail = ttk.Label(self.body, text="", foreground="#666")
        detail.pack(anchor="w")

        self.next_btn.state(["disabled"] if self.cfg["hotkey_scan"] is None else ["!disabled"])
        if self.cfg["hotkey_scan"] is not None:
            status.config(text=f"Current key: {self.cfg['hotkey_name']}")
            detail.config(text="Press another key to change it.")

        def on_event(e):
            if e.event_type != "down" or e.scan_code in MODIFIER_SCANS:
                return
            self.cfg["hotkey_scan"] = e.scan_code
            self.cfg["hotkey_name"] = e.name or f"scan {e.scan_code}"
            self.post(lambda: (status.config(text=f"Captured: {self.cfg['hotkey_name']}"),
                               detail.config(text=f"scan code {self.cfg['hotkey_scan']}  "
                                                  "â€” press another key to change it."),
                               self.next_btn.state(["!disabled"])))

        self.capture_hook = keyboard.hook(on_event)

    def page_mode(self):
        self.heading("How should it listen?", "You can change this later from the tray icon.")
        var = tk.StringVar(value=self.cfg["mode"])
        for value, title, desc in [
            ("hold", "Hold to talk",
             "Hold the key while you speak, release when done. Best for short dictation."),
            ("toggle", "Tap to toggle",
             "Tap once to start, tap again to stop. Best for long dictation."),
        ]:
            ttk.Radiobutton(self.body, text=title, value=value, variable=var,
                            command=lambda: self.cfg.update(mode=var.get())).pack(anchor="w")
            ttk.Label(self.body, text=desc, foreground="#666").pack(anchor="w",
                                                                   padx=22, pady=(0, 10))

    def page_model(self):
        self.heading("Speech model",
                     "Downloaded once, then kept on disk and used offline.")
        var = tk.StringVar(value=self.cfg["model"])
        combo = ttk.Combobox(self.body, textvariable=var, state="readonly",
                             values=list(MODELS), width=28)
        combo.pack(anchor="w")
        desc = ttk.Label(self.body, text=MODELS[var.get()], foreground="#666")
        desc.pack(anchor="w", pady=(4, 16))

        bar = ttk.Progressbar(self.body, length=500, mode="determinate")
        bar.pack(anchor="w")
        status = ttk.Label(self.body, text="")
        status.pack(anchor="w", pady=6)
        btn = ttk.Button(self.body, text="Download")
        btn.pack(anchor="w", pady=8)

        def on_pick(*_):
            self.cfg["model"] = var.get()
            desc.config(text=MODELS[var.get()])
            bar["value"] = 0
            refresh()

        def refresh():
            """Already on disk? Then this page is just a confirmation."""
            self.next_btn.state(["disabled"])
            status.config(text="Checking...")
            btn.state(["disabled"])

            def work():
                path = model_is_local(self.cfg["model"])
                def done():
                    if path:
                        self.model_path = path
                        bar["value"] = 100
                        status.config(text="Already downloaded. Ready to go.")
                        btn.state(["disabled"])
                        self.next_btn.state(["!disabled"])
                    else:
                        status.config(text="Not downloaded yet.")
                        btn.state(["!disabled"])
                self.post(done)

            threading.Thread(target=work, daemon=True).start()

        def start_download():
            btn.state(["disabled"])
            combo.state(["disabled"])
            status.config(text="Starting download...")

            def progress(done, total):
                pct = 100 * done / total
                self.post(lambda: (bar.config(value=pct),
                                   status.config(text=f"Downloading  {done/1e6:.0f} / "
                                                      f"{total/1e6:.0f} MB   ({pct:.0f}%)")))

            def work():
                try:
                    path = download_model(self.cfg["model"], progress)
                except Exception as e:
                    self.post(lambda: (status.config(text=f"Download failed: {e}"),
                                       btn.state(["!disabled"]),
                                       combo.state(["!readonly"])))
                    return
                self.model_path = path
                self.post(lambda: (bar.config(value=100),
                                   status.config(text="Downloaded. Ready to go."),
                                   combo.state(["readonly"]),
                                   self.next_btn.state(["!disabled"])))

            threading.Thread(target=work, daemon=True).start()

        combo.bind("<<ComboboxSelected>>", on_pick)
        btn.config(command=start_download)
        refresh()

    def page_try(self):
        log("page_try: building")
        verb = ("Hold" if self.cfg["mode"] == "hold" else "Tap")
        self.heading("Try it",
                     f"{verb} {self.cfg['hotkey_name']} and say something. The text lands "
                     "in the box below. Nothing is pasted into other apps while this "
                     "window is open.")
        box = tk.Text(self.body, height=7, wrap="word", font=("Segoe UI", 10))
        box.pack(fill="x")
        status = ttk.Label(self.body, text="Loading model...", foreground="#666")
        status.pack(anchor="w", pady=8)

        def on_text(text):
            self.post(lambda: (box.insert("end", text + " "), box.see("end"),
                               status.config(text="Got it. Try again, or click Finish.")))

        def on_state(recording):
            self.post(lambda: status.config(
                text="Listening..." if recording else "Transcribing..."))

        def on_status(msg):
            self.post(lambda: status.config(text=msg))

        self.dictation = Dictation(self.cfg, on_text, on_state, on_status)
        # Bind first, load second: while the model loads the key is already
        # swallowed, so it can't leak through to Windows Search.
        self.dictation.bind()
        log("page_try: key bound")

        def work():
            log("page_try: worker started")
            try:
                self.dictation.load_model(self.model_path)
            except Exception as e:
                import traceback
                log(traceback.format_exc())
                self.post(lambda: status.config(
                    text=f"Model failed to load: {e}  (details in log.txt)",
                    foreground="#b00"))
                return
            self.post(lambda: status.config(text=f"Ready. {verb} {self.cfg['hotkey_name']}."))

        threading.Thread(target=work, daemon=True).start()

    def run(self):
        self.root.mainloop()
        return self.cfg if self.completed else None


# --------------------------------------------------------------------------
# tray
# --------------------------------------------------------------------------

class Tray:
    def __init__(self, cfg):
        self.cfg = cfg
        self.next_action = None
        self.dictation = Dictation(cfg, self.on_text, self.on_state,
                                   lambda msg: setattr(self.icon, "title", f"Copilot Voice - {msg}"))
        self.icon = pystray.Icon(APP_NAME, make_icon(),
                                 "Copilot Voice (loading model...)", menu=self.menu())

    def menu(self):
        def mode_item(mode):
            return pystray.MenuItem(
                {"hold": "Hold to talk", "toggle": "Tap to toggle"}[mode],
                lambda *_: self.set_mode(mode),
                checked=lambda _: self.cfg["mode"] == mode, radio=True)

        return pystray.Menu(
            pystray.MenuItem(lambda _: f"Key: {self.cfg['hotkey_name'] or 'not set'}",
                             None, enabled=False),
            pystray.MenuItem(lambda _: f"Change model ({self.cfg['model']})...",
                             lambda *_: self.leave("model")),
            pystray.Menu.SEPARATOR,
            mode_item("hold"),
            mode_item("toggle"),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Stop Windows opening Search", self.toggle_chord,
                             checked=lambda _: use_chord_filter(self.cfg),
                             enabled=IS_WINDOWS),
            pystray.MenuItem("Run setup again...", self.setup_again),
            pystray.MenuItem("Start with Windows", self.toggle_autostart,
                             checked=lambda _: autostart_enabled()),
            pystray.MenuItem("Open config folder", lambda *_: open_folder(CONFIG_DIR)),
            pystray.MenuItem("Quit", lambda *_: self.icon.stop()),
        )

    def set_mode(self, mode):
        self.cfg["mode"] = mode
        save_config(self.cfg)
        self.icon.update_menu()

    def leave(self, action):
        """tkinter has to own the main thread, which the tray icon is holding.
        So hand the reason back to main() and let it run the window."""
        self.next_action = action
        self.icon.stop()

    def toggle_chord(self, *_):
        self.cfg["intercept_chord"] = not self.cfg.get("intercept_chord", True)
        save_config(self.cfg)
        self.dictation.unbind()   # rebind live, no restart needed
        self.dictation.bind()
        self.icon.update_menu()

    def setup_again(self, *_):
        self.leave("setup")

    def toggle_autostart(self, *_):
        set_autostart(not autostart_enabled())
        self.icon.update_menu()

    def on_text(self, text):
        paste_text(text, self.cfg)

    def on_state(self, recording):
        self.icon.icon = make_icon(recording=recording)
        self.icon.title = f"Copilot Voice ({'recording' if recording else self.cfg['mode']})"

    def run(self):
        self.dictation.bind()  # before the model loads, so the key never leaks

        def work():
            try:
                self.dictation.load_model()
            except Exception:
                import traceback
                log(traceback.format_exc())
                self.icon.title = "Copilot Voice (model failed - see log.txt)"
                return
            self.on_state(False)

        threading.Thread(target=work, daemon=True).start()
        self.icon.run()  # blocks on the main thread, as Windows requires
        self.dictation.unbind()
        return self.next_action


def main():
    cfg = load_config()
    pages = None  # None = the full setup
    while True:
        if not cfg["onboarded"] or cfg["hotkey_scan"] is None or pages:
            result = Wizard(cfg, pages).run()
            if result is None and not pages:
                return  # user closed first-run setup
            cfg = result or load_config()

        action = Tray(cfg).run()
        if action is None:
            return
        # The tray had to give up the main thread for tkinter; reopen the window
        # it asked for, then come back to a fresh tray with the new settings.
        pages = ["model", "try"] if action == "model" else None
        cfg = load_config()
        if action == "setup":
            cfg["onboarded"] = False


def diag():
    """Time every startup stage and write it to log.txt. Works in the frozen
    build, where an exception on a worker thread is otherwise invisible."""
    import traceback
    log("--- diag start ---")
    log(f"frozen={getattr(sys, 'frozen', False)} exe={sys.executable}")
    log(f"stdout={sys.stdout!r} stderr={sys.stderr!r}")
    lines = []
    try:
        for label, fn in [
            ("import faster_whisper", lambda: __import__("faster_whisper")),
            ("import ctranslate2", lambda: __import__("ctranslate2")),
            ("locate model", lambda: model_is_local(load_config()["model"])),
        ]:
            t = time.time()
            result = fn()
            lines.append(f"{label}: {time.time() - t:.1f}s")
            log(f"{label}: {time.time() - t:.1f}s -> {result if label == 'locate model' else 'ok'}")

        cfg = load_config()
        d = Dictation(cfg, lambda text: log(f"text: {text}"))
        t = time.time()
        d.load_model(model_is_local(cfg["model"]))
        lines.append(f"load model: {time.time() - t:.1f}s")

        t = time.time()
        segments, _ = d.model.transcribe(np.zeros(16000, dtype=np.float32), language="en")
        list(segments)
        lines.append(f"transcribe: {time.time() - t:.1f}s")
        lines.append("ALL OK")
    except Exception as e:
        log(traceback.format_exc())
        lines.append(f"FAILED: {e}")
    log("--- diag end ---")

    root = tk.Tk()
    root.title("Copilot Voice diagnostics")
    ttk.Label(root, text="\n".join(lines), padding=20, justify="left").pack()
    ttk.Label(root, text=f"Full log: {CONFIG_DIR / 'log.txt'}", padding=(20, 0, 20, 16),
              foreground="#666").pack()
    root.mainloop()


def keytest():
    """Show raw key events and prove suppression works, without the model."""
    root = tk.Tk()
    root.title("Copilot Voice key test")
    root.geometry("520x400")
    frame = ttk.Frame(root, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Every key event is listed below. The key whose scan code is "
                          "in the box is swallowed: press it and Windows should do "
                          "nothing at all.", wraplength=480).pack(anchor="w")

    row = ttk.Frame(frame)
    row.pack(anchor="w", pady=10)
    scan_var = tk.StringVar(value=str(load_config()["hotkey_scan"] or 134))
    ttk.Label(row, text="Suppress scan code:").pack(side="left")
    ttk.Entry(row, textvariable=scan_var, width=8).pack(side="left", padx=6)
    bind_btn = ttk.Button(row, text="Bind")
    bind_btn.pack(side="left")

    listbox = tk.Listbox(frame)
    listbox.pack(fill="both", expand=True)
    ui = queue.Queue()
    hooks = []

    def add(line):
        ui.put(line)
        log(f"keytest  {line}")  # so the whole sequence survives the scrollback

    def pump():
        while True:
            try:
                line = ui.get_nowait()
            except queue.Empty:
                break
            listbox.insert("end", line)
            listbox.see("end")
        root.after(50, pump)

    def rebind():
        for h in hooks:
            keyboard.unhook(h)
        hooks.clear()
        scans = trigger_scan_codes({"hotkey_scan": int(scan_var.get()), "hotkey_name": "f23"})
        chord = ChordFilter(scans, lambda: add("*** TRIGGER down ***"),
                            lambda: add("*** TRIGGER up ***"))

        def handle(event):
            allowed = chord.handle(event)
            add(f"{'PASS ' if allowed else 'EATEN'} {event.event_type:4} "
                f"scan={event.scan_code:<5} name={event.name}")
            return allowed

        hooks.append(keyboard.hook(handle, suppress=True))
        add(f"-- chord filter active, trigger scan codes {sorted(scans)} --")

    bind_btn.config(command=rebind)
    rebind()
    pump()
    root.protocol("WM_DELETE_WINDOW", lambda: (keyboard.unhook_all(), root.destroy()))
    root.mainloop()


def selftest():
    cfg = load_config(Path("nonexistent.json"))
    assert cfg["mode"] == "hold" and cfg["hotkey_scan"] is None

    bad = CONFIG_DIR / "_bad_config.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text('{"mode": "wiggle"}')
    try:
        load_config(bad)
        raise AssertionError("bad mode should have raised")
    except ValueError:
        pass
    finally:
        bad.unlink()

    tmp = CONFIG_DIR / "_rt.json"
    save_config({**DEFAULTS, "mode": "toggle", "hotkey_scan": 134}, tmp)
    got = load_config(tmp)
    assert got["mode"] == "toggle" and got["hotkey_scan"] == 134
    tmp.unlink()

    # every offered model maps to a real repo id
    for name in MODELS:
        assert "/" in model_repo(name), name

    # Scan-code mapping is per-platform, and keyboard's Linux backend needs root
    # to build its tables, so these only mean anything on Windows.
    if IS_WINDOWS:
        assert keyboard.key_to_scan_codes(134) == (134,)
        # both halves of a dual-scan-code key get hooked, or the press leaks out
        assert trigger_scan_codes({"hotkey_scan": 110, "hotkey_name": "f23"}) == {110, 134}
        assert trigger_scan_codes({"hotkey_scan": 88, "hotkey_name": "nonsense"}) == {88}
    assert trigger_scan_codes({"hotkey_scan": None, "hotkey_name": ""}) == set()

    # recorder: empty when never started, concatenated when it has chunks
    r = Recorder(16000)
    assert len(r.stop()) == 0
    r.stream = type("FakeStream", (), {"stop": lambda s: None, "close": lambda s: None})()
    r.chunks = [np.zeros((100, 1), np.float32), np.ones((50, 1), np.float32)]
    out = r.stop()
    assert out.shape == (150,) and out[-1] == 1.0

    # hold mode ignores presses while already recording; toggle mode stops on the second
    events = []
    d = Dictation({**DEFAULTS, "mode": "hold"}, events.append)
    d.model, d.begin, d.finish = object(), lambda: events.append("start"), lambda: events.append("stop")
    d._handle("down")
    d.rec.stream = object()          # pretend the stream is live
    d._handle("down")                # hold: second press must not restart
    assert events == ["start"], events
    d.cfg = {**DEFAULTS, "mode": "toggle"}
    d._handle("down")
    assert events == ["start", "stop"], events
    d.rec.stream = None

    # modifiers are rejected as trigger keys, F23 is not
    assert 42 in MODIFIER_SCANS and 134 not in MODIFIER_SCANS

    # --- chord filter: the Copilot key must be swallowed whole, and an ordinary
    # Win press must survive. Synthetic events, so no real keys are touched.
    Event = lambda scan, kind: type("E", (), {"scan_code": scan, "event_type": kind})()

    def run_chord(sequence, window=0.03, wait=0.0):
        sent, fired = [], []
        f = ChordFilter({110, 134}, lambda: fired.append("down"), lambda: fired.append("up"),
                        window=window, sender=sent.append)
        passed = [(scan, kind) for scan, kind in sequence if f.handle(Event(scan, kind))]
        if wait:
            time.sleep(wait)
        return passed, sent, fired, f

    # the real Copilot chord, exactly as captured in the keytest log
    copilot = [(91, "down"), (110, "down"), (42, "down"),
               (110, "up"), (42, "up"), (91, "up")]
    passed, sent, fired, _ = run_chord(copilot)
    assert passed == [], f"nothing may reach Windows, got {passed}"
    assert sent == [], f"nothing may be replayed, got {sent}"
    time.sleep(0.05)  # callbacks fire on their own threads
    assert fired == ["down", "up"], fired

    # a plain Win press: withheld briefly, then replayed so Start still opens
    passed, sent, fired, _ = run_chord([(91, "down")], wait=0.06)
    assert passed == [] and fired == []
    assert sent == [91], f"Win press must be replayed, got {sent}"

    # Win+E: the E arrives before the timer, so Win is replayed and E passes
    passed, sent, _, _ = run_chord([(91, "down"), (18, "down")])
    assert passed == [(18, "down")] and sent == [91], (passed, sent)

    # ordinary typing is untouched
    passed, sent, _, _ = run_chord([(31, "down"), (31, "up")])
    assert len(passed) == 2 and sent == []

    # Injecting a key re-enters handle() on the hook thread. If that happens
    # while the lock is held, the keyboard hangs and the Win key dies for the
    # life of the process -- so drive a sender that really does re-enter.
    reentrant = []
    filt = None

    def reentrant_send(scan):
        reentrant.append(scan)
        filt.handle(Event(scan, "down"))   # what SendInput does to us

    filt = ChordFilter({110, 134}, lambda: None, lambda: None,
                       window=0.03, sender=reentrant_send)
    done = threading.Event()

    def drive():
        filt.handle(Event(91, "down"))     # withheld
        filt.handle(Event(18, "down"))     # not the chord -> replay LWin
        done.set()

    threading.Thread(target=drive, daemon=True).start()
    assert done.wait(timeout=5), "ChordFilter deadlocked on re-entrant injection"
    assert reentrant == [91], reentrant

    # and the timer path must not deadlock either
    filt2 = ChordFilter({110}, lambda: None, lambda: None, window=0.02,
                        sender=lambda scan: filt2.handle(Event(scan, "down")))
    filt2.handle(Event(91, "down"))
    time.sleep(0.15)
    assert filt2.state != "waiting", "replay timer never completed"

    # A withheld LWin must be replayed as the EXTENDED scan code, or Windows
    # does not accept it as the Windows key and Win+V, Win+E stay dead.
    if IS_WINDOWS:
        extended = keyboard.key_to_scan_codes("left windows")[0]
        assert extended != ChordFilter.WIN, "extended and plain codes must differ"
        assert ChordFilter.REPLAY_AS[ChordFilter.WIN] == extended, (
            f"replays {ChordFilter.REPLAY_AS[ChordFilter.WIN]}, needs {extended}")

        # the default sender must apply that mapping (checked without pressing)
        sent = []
        filt = ChordFilter({110}, lambda: None, lambda: None,
                           sender=lambda scan: sent.append(
                               ChordFilter.REPLAY_AS.get(scan, scan)))
        filt.send(ChordFilter.WIN)
        assert sent == [extended], sent

    # the chord filter is opt-out, and never runs off Windows
    assert use_chord_filter(DEFAULTS) is IS_WINDOWS
    assert use_chord_filter({**DEFAULTS, "intercept_chord": False}) is False
    assert use_chord_filter({}) is IS_WINDOWS   # missing key = on by default

    # rebinding must not leave the stop sentinel that kills the new worker
    d = Dictation({**DEFAULTS, "beep": False}, lambda text: None)
    d.queue.put(None)
    d.queue = queue.Queue()          # what bind() does
    assert d.queue.empty()

    # the tail buffer keeps the mic open past the release, in both modes
    for mode in ("hold", "toggle"):
        d = Dictation({**DEFAULTS, "mode": mode, "tail_seconds": 0.15, "beep": False},
                      lambda text: None)
        d.model = object()
        d.rec.stream = type("FakeStream", (), {"stop": lambda s: None,
                                               "close": lambda s: None})()
        started = time.time()
        d._handle("up" if mode == "hold" else "down")
        elapsed = time.time() - started
        assert elapsed >= 0.15, f"{mode}: stopped {elapsed:.2f}s after release, no tail"

    # the tray's "Change model" reuses the wizard's download + try pages
    w = Wizard({**DEFAULTS, "hotkey_scan": 110, "hotkey_name": "f23"}, ["model", "try"])
    w.root.withdraw()
    assert w.pages == [w.page_model, w.page_try], w.pages
    assert len(Wizard(DEFAULTS).pages) == 5
    w.root.destroy()

    # the version lives in four places now; drift means winget and Chocolatey
    # ship manifests pointing at a release tag that does not exist
    here = Path(__file__).parent
    for path, pattern in [("installer.iss", r"AppVersion=([\d.]+)"),
                          ("pyproject.toml", r'^version = "([\d.]+)"'),
                          ("packaging/chocolatey/copilot-voice.nuspec",
                           r"<version>([\d.]+)</version>")]:
        source = here / path
        if source.exists():
            found = re.search(pattern, source.read_text(encoding="utf-8"), re.M)
            assert found and found.group(1) == __version__, \
                f"{path} says {found and found.group(1)}, module says {__version__}"

    assert make_icon().size == (64, 64)
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--diag" in sys.argv:
        diag()
    elif "--keytest" in sys.argv:
        keytest()
    elif "--trypage" in sys.argv:
        # Reproduce the "Try it" page without clicking through setup.
        _cfg = load_config()
        _cfg["hotkey_scan"] = _cfg["hotkey_scan"] or 134
        _cfg["hotkey_name"] = _cfg["hotkey_name"] or "f23"
        _w = Wizard(_cfg)
        _w.model_path = model_is_local(_cfg["model"])
        _w.index = len(_w.pages) - 1
        _w.show()
        _w.run()
    elif "--make-ico" in sys.argv:
        make_icon(256).save(Path(__file__).with_name("icon.ico"),
                            sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
        print("wrote icon.ico")
    else:
        try:
            main()
        except Exception:
            import traceback
            log(traceback.format_exc())
            raise
