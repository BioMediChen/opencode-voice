from __future__ import annotations

import base64
import json
import logging
import os
import re
import subprocess
from pathlib import Path

import httpx

log = logging.getLogger("voice.opencode")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_URL_RE = re.compile(r"https?://[^\s\"']+")


class OpenCodeError(RuntimeError):
    pass


def _service_password() -> str | None:
    path = Path.home() / ".config" / "opencode" / "service.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("password")
    except (OSError, ValueError):
        return None


def discover_base_url() -> str:
    explicit = os.environ.get("VOICE_OPENCODE_URL")
    if explicit:
        return explicit.rstrip("/")
    try:
        done = subprocess.run(
            ["cmd", "/c", "opencode", "service", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OpenCodeError(f"cannot run `opencode service status`: {exc}") from exc
    match = _URL_RE.search(done.stdout or "")
    if not match:
        raise OpenCodeError(f"no server URL in `opencode service status` output: {done.stdout!r}")
    return match.group(0).rstrip("/")


class OpenCodeClient:
    def __init__(self, directory: str, session_id: str = "", allow_fallback: bool = False) -> None:
        self.directory = str(Path(directory).resolve())
        self.pinned_session = session_id.strip()
        self.allow_fallback = allow_fallback
        self._base_url: str | None = None
        self._client: httpx.Client | None = None

    # -- transport ---------------------------------------------------------
    def _auth(self) -> dict[str, str]:
        password = _service_password()
        if not password:
            raise OpenCodeError("cannot read OpenCode service password (~/.config/opencode/service.json)")
        token = base64.b64encode(f"opencode:{password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def connect(self) -> None:
        if self._base_url is None:
            self._base_url = discover_base_url()
            log.info("OpenCode server: %s", self._base_url)
        if self._client is None:
            self._client = httpx.Client(base_url=self._base_url, headers=self._auth(), timeout=60.0)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        self.connect()
        assert self._client is not None
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            # Service may have restarted on another port; rediscover once.
            self._base_url = None
            self._client = None
            self.connect()
            assert self._client is not None
            response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise OpenCodeError(f"{method} {path} -> {response.status_code}: {response.text[:500]}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"raw": response.text}

    # -- sessions ----------------------------------------------------------
    def _list_sessions(self, limit: int = 50) -> list[dict]:
        payload = self._request("GET", "/api/session", params={"limit": limit})
        return payload.get("data") or []

    def resolve_session(self) -> dict:
        if self.pinned_session:
            payload = self._request("GET", f"/api/session/{self.pinned_session}")
            return payload["data"]
        sessions = self._list_sessions()
        if not sessions:
            raise OpenCodeError("no OpenCode sessions found; open a session in the TUI first")

        target = os.path.normcase(os.path.normpath(self.directory))

        def in_target(session: dict) -> bool:
            location = (session.get("location") or {}).get("directory") or ""
            return bool(location) and os.path.normcase(os.path.normpath(location)) == target

        candidates = [s for s in sessions if in_target(s)]
        if not candidates:
            if not self.allow_fallback:
                raise OpenCodeError(
                    f"no session for {self.directory}; open one there, or set opencode.session_id / allow_fallback=true"
                )
            candidates = sessions
        return max(candidates, key=lambda s: (s.get("time") or {}).get("updated", 0))

    def send_prompt(self, text: str, session_id: str | None = None) -> dict:
        text = text.strip()
        if not text:
            raise OpenCodeError("empty prompt")
        session = session_id or self.resolve_session()["id"]
        payload = self._request("POST", f"/api/session/{session}/prompt", json={"text": text})
        log.info("prompt sent to %s (%d chars)", session, len(text))
        return payload

    def interrupt(self, session_id: str | None = None) -> dict:
        session = session_id or self.resolve_session()["id"]
        return self._request("POST", f"/api/session/{session}/interrupt")
