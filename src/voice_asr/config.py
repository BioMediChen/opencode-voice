from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config.toml"


@dataclass
class AsrConfig:
    model: str = "models/sensevoice/model.int8.onnx"
    tokens: str = "models/sensevoice/tokens.txt"
    num_threads: int = 8
    language: str = "auto"
    use_itn: bool = True


@dataclass
class AudioConfig:
    device: str = ""
    min_duration: float = 0.25
    silence_threshold: float = 0.01
    pre_roll: float = 0.15
    beep: bool = True


@dataclass
class HotkeyConfig:
    key: str = "f8"
    mouse: list[str] = field(default_factory=list)


@dataclass
class CommandsConfig:
    stop: list[str] = field(
        default_factory=lambda: ["停止", "停下来", "停", "停一下", "停下", "取消", "别做了", "暂停", "stop", "cancel"]
    )


@dataclass
class OpenCodeConfig:
    directory: str = ""
    session_id: str = ""
    session_mode: str = "auto"  # auto / directory / pinned
    allow_fallback: bool = False


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8756


@dataclass
class Config:
    asr: AsrConfig = field(default_factory=AsrConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    hotkey: HotkeyConfig = field(default_factory=HotkeyConfig)
    commands: CommandsConfig = field(default_factory=CommandsConfig)
    opencode: OpenCodeConfig = field(default_factory=OpenCodeConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    terms: dict[str, str] = field(default_factory=dict)
    root: Path = ROOT

    def path(self, value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else self.root / p


def _merge(target: object, data: dict) -> None:
    for key, value in data.items():
        if not hasattr(target, key):
            continue
        current = getattr(target, key)
        if isinstance(current, dict) and isinstance(value, dict):
            current.update(value)
        elif isinstance(value, dict) and not isinstance(current, (str, int, float, bool)):
            _merge(current, value)
        else:
            setattr(target, key, value)


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    config = Config()
    raw_path = Path(path or os.environ.get("VOICE_CONFIG") or DEFAULT_CONFIG)
    if raw_path.is_file():
        with raw_path.open("rb") as fh:
            data = tomllib.load(fh)
        for section, values in data.items():
            if not hasattr(config, section) or not isinstance(values, dict):
                continue
            target = getattr(config, section)
            if isinstance(target, dict):
                target.update(values)
            else:
                _merge(target, values)
        if not config.opencode.directory:
            config.opencode.directory = str(raw_path.parent)
    else:
        config.opencode.directory = str(ROOT)
    return config
