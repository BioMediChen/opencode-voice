from __future__ import annotations

import logging
import threading

import numpy as np
import sounddevice as sd
import soxr

log = logging.getLogger("voice.audio")

TARGET_RATE = 16000


def list_input_devices() -> list[dict]:
    devices = []
    for index, info in enumerate(sd.query_devices()):
        if info.get("max_input_channels", 0) > 0:
            devices.append(
                {
                    "index": index,
                    "name": info["name"],
                    "channels": info["max_input_channels"],
                    "default_rate": int(info["default_samplerate"]),
                }
            )
    return devices


def resolve_input_device(device: str) -> int | None:
    """Accept an index, or a case-insensitive substring of the device name."""
    if not device:
        return None
    if device.isdigit():
        return int(device)
    needle = device.lower()
    for index, info in enumerate(sd.query_devices()):
        if info.get("max_input_channels", 0) > 0 and needle in info["name"].lower():
            log.info("audio device -> [%d] %s", index, info["name"])
            return index
    raise ValueError(f"No input device matches {device!r}")


def trim_silence(samples: np.ndarray, rate: int, threshold: float) -> np.ndarray:
    if samples.size == 0:
        return samples
    frame = max(1, int(rate * 0.02))
    usable = (samples.size // frame) * frame
    if usable == 0:
        return samples
    frames = samples[:usable].reshape(-1, frame)
    voiced = np.sqrt((frames ** 2).mean(axis=1)) > threshold
    if not voiced.any():
        return samples[:0]
    first = int(np.argmax(voiced))
    last = len(voiced) - int(np.argmax(voiced[::-1]))
    pad = int(rate * 0.1)
    start = max(0, first * frame - pad)
    end = min(samples.size, last * frame + pad)
    return samples[start:end]


class Recorder:
    """Capture at the device's native rate and resample once on stop."""

    def __init__(self, device: str = "", threshold: float = 0.01, pre_roll: float = 0.15) -> None:
        self._device = resolve_input_device(device)
        self._threshold = threshold
        self._pre_roll = pre_roll
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._native_rate = 0
        self._lock = threading.Lock()
        self._ring: np.ndarray = np.zeros(0, dtype=np.float32)

    @property
    def active(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            info = sd.query_devices(self._device, "input")
            self._native_rate = int(info["default_samplerate"]) or 48000
            self._frames = []
            pre = int(self._native_rate * self._pre_roll)
            self._ring = np.zeros(pre, dtype=np.float32) if pre else np.zeros(0, dtype=np.float32)
            self._stream = sd.InputStream(
                device=self._device,
                channels=1,
                samplerate=self._native_rate,
                dtype="float32",
                blocksize=0,
                callback=self._callback,
            )
            self._stream.start()
            log.info("recording @ %d Hz on %s", self._native_rate, info["name"])

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("audio status: %s", status)
        self._frames.append(indata[:, 0].copy())

    def stop(self) -> tuple[np.ndarray, float]:
        with self._lock:
            stream = self._stream
            self._stream = None
            if stream is None:
                return np.zeros(0, dtype=np.float32), 0.0
            stream.stop()
            stream.close()
            chunks = self._frames
            self._frames = []
        if not chunks:
            return np.zeros(0, dtype=np.float32), 0.0
        audio = np.concatenate(chunks).astype(np.float32)
        if self._ring.size:
            audio = np.concatenate([self._ring, audio])
        if self._native_rate != TARGET_RATE:
            audio = soxr.resample(audio, self._native_rate, TARGET_RATE).astype(np.float32)
        trimmed = trim_silence(audio, TARGET_RATE, self._threshold)
        return trimmed, trimmed.size / TARGET_RATE
