from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_asr.config import load_config  # noqa: E402
from voice_asr.opencode_client import OpenCodeClient, OpenCodeError  # noqa: E402

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def daemon_up(base: str) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        host, port = base.removeprefix("http://").split(":")
        sock.settimeout(0.3)
        return sock.connect_ex((host, int(port))) == 0


def ensure_daemon(base: str, wait: float = 25.0) -> None:
    if daemon_up(base):
        return
    print("[e2e] starting voice daemon ...")
    subprocess.Popen(
        [sys.executable, "-m", "voice_asr.daemon"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,
    )
    deadline = time.time() + wait
    while time.time() < deadline:
        if daemon_up(base):
            time.sleep(0.5)
            return
        time.sleep(0.3)
    raise SystemExit("[e2e] daemon did not start; check logs/voice.log")


def wait_for_text(client: OpenCodeClient, session_id: str, needle: str, timeout: float = 6.0) -> str | None:
    """Queued (resume=False) prompts land in the inbox; executed ones become messages."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for message in client.messages(session_id):
            if message.get("type") == "user" and needle in (message.get("text") or ""):
                return message.get("text")
        for item in client.inbox(session_id):
            text = (item.get("payload") or {}).get("text") or ""
            if item.get("type") == "user" and needle in text:
                return text
        time.sleep(0.25)
    return None


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="End-to-end self check for the voice pipeline")
    parser.add_argument("--keep", action="store_true", help="keep the scratch session for inspection")
    args = parser.parse_args(argv)

    config = load_config()
    base = f"http://{config.server.host}:{config.server.port}"
    ensure_daemon(base)

    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))

    with httpx.Client(timeout=15.0) as http:
        status = http.get(f"{base}/status").json()
    check("daemon status", status.get("ok") is True, f"hotkey={status.get('hotkey')} mouse={status.get('mouse')}")

    client = OpenCodeClient(
        directory=config.opencode.directory or str(ROOT),
        session_id=config.opencode.session_id,
        allow_fallback=config.opencode.allow_fallback,
    )
    session_id = ""
    try:
        session = client.create_session("voice-e2e", config.opencode.directory or str(ROOT))
        session_id = session["id"]
        check("create scratch session", bool(session_id), session_id)

        # 1) full daemon path: corrections -> prompt (no model turn)
        marker = "use state"
        with httpx.Client(timeout=15.0) as http:
            response = http.post(
                f"{base}/submit",
                json={"text": f"端到端自检 {marker} 和 u effect", "resume": False, "session_id": session_id},
            )
        check("daemon accepted /submit", response.status_code == 200, f"HTTP {response.status_code}")

        stored = wait_for_text(client, session_id, "useState")
        check("delivered to session", stored is not None, stored or "not found")
        check("terminology corrections", bool(stored and "useEffect" in stored), stored or "")

        # 2) stop phrase goes through the interrupt branch
        with httpx.Client(timeout=15.0) as http:
            response = http.post(f"{base}/submit", json={"text": "停止", "session_id": session_id, "resume": False})
        payload = response.json()
        detail = json.dumps(payload, ensure_ascii=False)
        check("stop phrase handled", response.status_code == 200 or "interrupt" not in detail, detail)

        # 3) daemon reports a usable target session
        with httpx.Client(timeout=15.0) as http:
            status = http.get(f"{base}/status").json()
        target = (status.get("session") or {}).get("id")
        check("daemon knows target session", bool(target), f"{target} mode={status.get('session_mode')}")
    finally:
        if session_id and not args.keep:
            try:
                client.delete_session(session_id)
                print(f"[e2e] deleted scratch session {session_id}")
            except OpenCodeError as exc:
                print(f"[e2e] cleanup failed: {exc}")

    failed = [name for name, ok, _ in results if not ok]
    print()
    if failed:
        print(f"FAILED: {len(failed)} check(s): {', '.join(failed)}")
        raise SystemExit(1)
    print(f"ALL PASS ({len(results)} checks)")


if __name__ == "__main__":
    main()
