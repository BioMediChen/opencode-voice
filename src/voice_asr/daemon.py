from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from pynput import keyboard, mouse

from .asr import SenseVoice
from .audio import Recorder, list_input_devices
from .config import Config, load_config
from .corrections import apply_corrections, build_rules
from .opencode_client import OpenCodeClient, OpenCodeError

log = logging.getLogger("voice.daemon")

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows
    winsound = None


def beep(freq: int, ms: int) -> None:
    if winsound is None:
        return
    try:
        winsound.Beep(freq, ms)
    except RuntimeError:
        pass


def setup_logging(root: Path, verbose: bool = False) -> None:
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file_handler = logging.handlers.RotatingFileHandler(
        logs / "voice.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=[console, file_handler],
    )


def parse_hotkey(name: str):
    key = name.strip().lower()
    if hasattr(keyboard.Key, key):
        return getattr(keyboard.Key, key)
    if len(key) == 1:
        return keyboard.KeyCode.from_char(key)
    raise ValueError(f"unsupported hotkey: {name!r} (use f1..f12, caps_lock, scroll_lock, or a single character)")


MOUSE_BUTTONS = {
    "left": mouse.Button.left,
    "middle": mouse.Button.middle,
    "right": mouse.Button.right,
    "x1": mouse.Button.x1,
    "x2": mouse.Button.x2,
    "back": mouse.Button.x1,
    "forward": mouse.Button.x2,
}


def parse_mouse_buttons(names: list[str]) -> list:
    buttons = []
    for name in names:
        button = MOUSE_BUTTONS.get(name.strip().lower())
        if button is None:
            raise ValueError(f"unsupported mouse button: {name!r} (use x1/x2/middle/back/forward)")
        buttons.append(button)
    return buttons


class VoiceDaemon:
    def __init__(self, config: Config) -> None:
        self.config = config
        root = config.root
        self.asr = SenseVoice(
            model=config.path(config.asr.model),
            tokens=config.path(config.asr.tokens),
            num_threads=config.asr.num_threads,
            language=config.asr.language,
            use_itn=config.asr.use_itn,
        )
        self.recorder = Recorder(
            device=config.audio.device,
            threshold=config.audio.silence_threshold,
            pre_roll=config.audio.pre_roll,
        )
        self.client = OpenCodeClient(
            directory=config.opencode.directory or str(root),
            session_id=config.opencode.session_id,
            allow_fallback=config.opencode.allow_fallback,
            session_mode=config.opencode.session_mode,
        )
        self.hotkey = parse_hotkey(config.hotkey.key)
        self.mouse_buttons = parse_mouse_buttons(config.hotkey.mouse)
        self._term_rules = build_rules(config.terms)
        self.recording = False
        self.busy = False
        self.started_at = time.time()
        self.last_text = ""
        self.last_raw_text = ""
        self.last_error = ""
        self.last_sent_at = 0.0
        self._session: dict = {}
        self._session_lock = threading.Lock()
        self._waiters: list[queue.Queue] = []
        self._waiters_lock = threading.Lock()
        self._work: queue.Queue[tuple[np.ndarray, float]] = queue.Queue()
        self._server: ThreadingHTTPServer | None = None
        self._listener: keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener | None = None
        self._triggers: set[str] = set()
        self._trigger_lock = threading.Lock()
        self._stopping = threading.Event()

    # -- session -----------------------------------------------------------
    def refresh_session(self) -> dict:
        try:
            session = self.client.resolve_session()
            with self._session_lock:
                self._session = session
            log.info("target session: %s  %r  (%s)", session["id"], session.get("title", ""), self._session_dir(session))
            return session
        except OpenCodeError as exc:
            log.error("cannot resolve session: %s", exc)
            self.last_error = str(exc)
            raise

    @staticmethod
    def _session_dir(session: dict) -> str:
        return (session.get("location") or {}).get("directory", "")

    def snapshot_session(self) -> dict:
        with self._session_lock:
            session = dict(self._session)
        return {
            "id": session.get("id"),
            "title": session.get("title"),
            "directory": self._session_dir(session),
        }

    # -- capture -----------------------------------------------------------
    def press(self, trigger: str) -> None:
        with self._trigger_lock:
            self._triggers.add(trigger)
            already = self.recording
        if already:
            return
        try:
            self.refresh_session()
            self.recorder.start()
            self.recording = True
            if self.config.audio.beep:
                beep(880, 45)
            print(f"\n[recording:{trigger}] ... release to transcribe", flush=True)
        except Exception as exc:  # noqa: BLE001 - keep the hotkey alive
            self._triggers.clear()
            self.recording = False
            self.last_error = str(exc)
            log.error("cannot start recording: %s", exc)
            if self.config.audio.beep:
                beep(300, 120)

    def release(self, trigger: str) -> None:
        with self._trigger_lock:
            self._triggers.discard(trigger)
            if not self.recording or self._triggers:
                return
            self.recording = False
        audio, seconds = self.recorder.stop()
        if self.config.audio.beep:
            beep(620, 45)
        if seconds < self.config.audio.min_duration:
            log.info("ignored %.2fs (min %.2fs)", seconds, self.config.audio.min_duration)
            print(f"[skip] too short ({seconds:.2f}s)", flush=True)
            return
        self.busy = True
        self._work.put((audio, seconds))
        print(f"[asr] {seconds:.2f}s ...", flush=True)

    def worker(self) -> None:
        while not self._stopping.is_set():
            try:
                audio, seconds = self._work.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                text, elapsed = self.asr.transcribe(audio)
                if not text:
                    print("[asr] empty result, dropped", flush=True)
                    continue
                print(f"[asr] {elapsed:.2f}s  {text}", flush=True)
                self.last_raw_text = text
                text = self.fix_terms(text)
                self.last_text = text
                self.handle_text(text)
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                log.exception("transcription failed")
                print(f"[error] {exc}", flush=True)
            finally:
                self.busy = False

    def fix_terms(self, text: str) -> str:
        fixed, fired = apply_corrections(text, self.config.terms, self._term_rules)
        if fired:
            log.info("terms fixed %s -> %s", text, fixed)
        return fixed

    @staticmethod
    def _normalize_command(text: str) -> str:
        table = str.maketrans("", "", "，。！？、；：,.!?;: \t\r\n\"'“”‘’")
        return text.translate(table).lower()

    def handle_text(self, text: str, resume: bool | None = None, session_id: str | None = None) -> None:
        with self._waiters_lock:
            waiters = list(self._waiters)
        if waiters:
            for waiter in waiters:
                waiter.put({"text": text})
            print("[listen] delivered to tool call (not submitted)", flush=True)
            return
        stop_phrases = {self._normalize_command(phrase) for phrase in self.config.commands.stop}
        if self._normalize_command(text) in stop_phrases:
            try:
                self.client.interrupt(session_id)
                self.last_sent_at = time.time()
                if self.config.audio.beep:
                    beep(440, 80)
                log.info("stop phrase %r -> interrupt sent", text)
                print(f"[stop] interrupted current turn ({text})", flush=True)
            except OpenCodeError as exc:
                self.last_error = str(exc)
                log.error("interrupt failed: %s", exc)
                print(f"[error] {exc}", flush=True)
            return
        try:
            payload = self.client.send_prompt(text, session_id=session_id, resume=resume)
            self.last_sent_at = time.time()
            data = payload.get("data") or {}
            used = data.get("sessionID") or data.get("sessionId")
            if used:
                try:
                    with self._session_lock:
                        self._session = self.client.get_session(used)
                except OpenCodeError:
                    pass
            log.debug("prompt response: %s", json.dumps(payload)[:300])
            session = self.snapshot_session()
            print(f"[sent -> {session.get('id')}] {session.get('title', '')}", flush=True)
        except OpenCodeError as exc:
            self.last_error = str(exc)
            log.error("send failed: %s", exc)
            print(f"[error] {exc}", flush=True)

    # -- waiters -----------------------------------------------------------
    def wait_for_speech(self, timeout: float) -> str | None:
        waiter: queue.Queue = queue.Queue(maxsize=1)
        with self._waiters_lock:
            self._waiters.append(waiter)
        try:
            print(f"[listen] waiting up to {timeout:.0f}s for speech ...", flush=True)
            item = waiter.get(timeout=max(1.0, timeout))
            return item.get("text")
        except queue.Empty:
            return None
        finally:
            with self._waiters_lock:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)

    # -- http --------------------------------------------------------------
    def status(self) -> dict:
        return {
            "ok": True,
            "model": self.config.asr.model,
            "language": self.config.asr.language,
            "hotkey": self.config.hotkey.key,
            "mouse": list(self.config.hotkey.mouse),
            "session_mode": self.client.session_mode,
            "triggers_down": sorted(self._triggers),
            "recording": self.recording,
            "busy": self.busy,
            "waiting_for_speech": len(self._waiters),
            "session": self.snapshot_session(),
            "last_text": self.last_text,
            "last_raw_text": self.last_raw_text,
            "last_error": self.last_error,
            "last_sent_at": self.last_sent_at,
            "uptime_seconds": round(time.time() - self.started_at, 1),
        }

    def serve(self) -> None:
        daemon = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args) -> None:  # noqa: A003
                log.debug("http: " + fmt, *args)

            def _send(self, code: int, payload: dict) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict:
                length = int(self.headers.get("Content-Length") or 0)
                if not length:
                    return {}
                try:
                    return json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    return {}

            def do_GET(self) -> None:  # noqa: N802
                if self.path.rstrip("/") in ("", "/status"):
                    self._send(200, daemon.status())
                else:
                    self._send(404, {"ok": False, "error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                path = self.path.rstrip("/")
                body = self._body()
                if path == "/listen":
                    timeout = float(body.get("timeout", 30))
                    text = daemon.wait_for_speech(min(timeout, 120))
                    self._send(200, {"ok": True, "text": text, "timed_out": text is None})
                elif path == "/submit":
                    text = str(body.get("text", "")).strip()
                    if not text:
                        self._send(400, {"ok": False, "error": "text is required"})
                        return
                    try:
                        resume = body.get("resume")
                        daemon.handle_text(daemon.fix_terms(text), resume=resume, session_id=body.get("session_id"))
                        self._send(200, {"ok": True})
                    except OpenCodeError as exc:
                        self._send(500, {"ok": False, "error": str(exc)})
                elif path == "/interrupt":
                    try:
                        daemon.client.interrupt()
                        self._send(200, {"ok": True})
                    except OpenCodeError as exc:
                        self._send(500, {"ok": False, "error": str(exc)})
                else:
                    self._send(404, {"ok": False, "error": "not found"})

        server = ThreadingHTTPServer((self.config.server.host, self.config.server.port), Handler)
        server.daemon_threads = True
        self._server = server
        log.info("control server on http://%s:%d", self.config.server.host, self.config.server.port)
        server.serve_forever(poll_interval=0.2)

    # -- lifecycle ---------------------------------------------------------
    def run(self) -> None:
        print("\n=== OpenCode voice control ===")
        print(f"hotkey : hold [{self.config.hotkey.key.upper()}] to talk, release to send")
        if self.config.hotkey.mouse:
            print(f"mouse  : hold {', '.join(self.config.hotkey.mouse)} to talk")
        print(f"session: {self.config.opencode.directory} (auto latest)")
        self.refresh_session()
        threading.Thread(target=self.serve, name="http", daemon=True).start()
        threading.Thread(target=self.worker, name="asr-worker", daemon=True).start()

        def on_press(key) -> None:
            if key == self.hotkey:
                self.press("key")

        def on_release(key) -> None:
            if key == self.hotkey:
                self.release("key")

        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self._listener.start()

        if self.mouse_buttons:
            buttons = set(self.mouse_buttons)

            def on_click(x, y, button, pressed) -> None:
                if button not in buttons:
                    return
                name = getattr(button, "name", str(button))
                if pressed:
                    self.press(f"mouse:{name}")
                else:
                    self.release(f"mouse:{name}")

            self._mouse_listener = mouse.Listener(on_click=on_click)
            self._mouse_listener.start()
            print(f"mouse listener: {', '.join(self.config.hotkey.mouse)}")

        print("ready. Ctrl+C to stop.\n")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\nstopping ...")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            self._listener.stop()
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="OpenCode offline voice control daemon")
    parser.add_argument("--config", help="path to config.toml")
    parser.add_argument("--device", help="input device index or name substring")
    parser.add_argument("--hotkey", help="push-to-talk key, e.g. f8")
    parser.add_argument("--list-devices", action="store_true", help="list input devices and exit")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.list_devices:
        for device in list_input_devices():
            print(f"[{device['index']:>2}] {device['name']}  ({device['channels']}ch @ {device['default_rate']}Hz)")
        return

    config = load_config(args.config)
    if args.device:
        config.audio.device = args.device
    if args.hotkey:
        config.hotkey.key = args.hotkey
    setup_logging(config.root, args.verbose)
    VoiceDaemon(config).run()


if __name__ == "__main__":
    main()
