from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from voice_asr.opencode_client import OpenCodeClient  # noqa: E402


def main() -> None:
    client = OpenCodeClient(directory=r"E:\voice", allow_fallback=False)
    client.ensure_session = None  # type: ignore[attr-defined]
    session = client._request(
        "POST",
        "/api/session",
        json={"title": "voice-selftest", "location": {"directory": r"E:\voice"}},
    )["data"]
    print("created:", session["id"], session.get("title"))

    client.send_prompt("只回复 OK 两个字，不要做任何其他事情。", session_id=session["id"])
    print("prompt accepted")
    print("resolve_session ->", client.resolve_session()["id"])

    time.sleep(8)
    messages = client._request("GET", f"/api/session/{session['id']}/message")["data"]
    for message in messages:
        kind = message.get("type") or message.get("role")
        parts = message.get("content") or message.get("parts") or []
        text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict))[:120]
        print(f"- {kind}: {text}")

    client._request("DELETE", f"/api/session/{session['id']}")
    print("deleted scratch session")


if __name__ == "__main__":
    main()
