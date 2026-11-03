from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

from .config import load_config

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_config()
BASE = f"http://{CONFIG.server.host}:{CONFIG.server.port}"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

mcp = FastMCP(
    "voice",
    instructions=(
        "语音输入已接入 OpenCode。用户按住 " + CONFIG.hotkey.key.upper() + " 或鼠标侧键说话，松开后转写文本会作为普通用户消息自动进入当前会话。"
        "当你在执行不可逆操作前需要口头确认，或想询问用户问题时，调用 `listen` 等待用户语音回答；"
        "此时该次语音不会自动提交，而是作为工具结果返回。回答用户时保持简洁，适合朗读。"
    ),
)


class DaemonUnavailable(RuntimeError):
    pass


def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def _call(path: str, payload: dict | None = None, timeout: float = 10.0) -> dict:
    try:
        with httpx.Client(timeout=timeout) as client:
            if payload is None:
                response = client.get(f"{BASE}{path}")
            else:
                response = client.post(f"{BASE}{path}", json=payload)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise DaemonUnavailable(
            f"voice daemon not reachable at {BASE} ({exc}). "
            f"start it with: uv run --project \"{ROOT}\" voice-daemon"
        ) from exc


def _ensure_daemon(wait_seconds: float = 20.0) -> None:
    if _port_open(CONFIG.server.host, CONFIG.server.port):
        return
    if os.environ.get("VOICE_NO_AUTOSTART") == "1":
        raise DaemonUnavailable(f"voice daemon not running at {BASE}")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = (log_dir / "daemon-stdout.log").open("ab")
    subprocess.Popen(
        [sys.executable, "-m", "voice_asr.daemon"],
        cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=log_file,
        creationflags=CREATE_NO_WINDOW,
    )
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if _port_open(CONFIG.server.host, CONFIG.server.port):
            time.sleep(0.4)
            return
        time.sleep(0.3)
    raise DaemonUnavailable(f"failed to start voice daemon, see {log_dir / 'voice.log'}")


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
def status() -> str:
    """查看语音守护进程状态：热键、目标会话、是否在录音、最近一次转写文本和错误。"""
    _ensure_daemon()
    return _json(_call("/status", timeout=5))


@mcp.tool()
def listen(timeout_seconds: int = 30) -> str:
    """等待用户说一句话并把转写文本返回给你（最长 120 秒）。

    这次语音不会自动提交给会话，适合在你需要用户口头确认、选择或补充信息时使用。
    返回 timed_out=true 表示用户没有在时限内说话。
    """
    _ensure_daemon()
    timeout = max(1, min(int(timeout_seconds), 120))
    return _json(_call("/listen", {"timeout": timeout}, timeout=timeout + 15))


@mcp.tool()
def interrupt() -> str:
    """打断当前会话正在执行的 agent 回合（用户说“停下/取消”时可用）。"""
    _ensure_daemon()
    return _json(_call("/interrupt", {}, timeout=15))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
