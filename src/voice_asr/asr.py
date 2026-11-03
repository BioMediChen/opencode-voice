from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import sherpa_onnx

log = logging.getLogger("voice.asr")

TARGET_RATE = 16000


class SenseVoice:
    """sherpa-onnx SenseVoice-small (zh/en/ja/ko/yue), quantized, CPU."""

    def __init__(
        self,
        model: Path,
        tokens: Path,
        num_threads: int = 8,
        language: str = "auto",
        use_itn: bool = True,
    ) -> None:
        if not model.is_file():
            raise FileNotFoundError(f"ASR model not found: {model}")
        if not tokens.is_file():
            raise FileNotFoundError(f"ASR tokens not found: {tokens}")
        started = time.perf_counter()
        self._recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model),
            tokens=str(tokens),
            num_threads=num_threads,
            language=language,
            use_itn=use_itn,
            debug=False,
            provider="cpu",
        )
        log.info("SenseVoice loaded in %.2fs (threads=%d, lang=%s)", time.perf_counter() - started, num_threads, language)

    def transcribe(self, samples: np.ndarray, sample_rate: int = TARGET_RATE) -> tuple[str, float]:
        if samples.size == 0:
            return "", 0.0
        started = time.perf_counter()
        stream = self._recognizer.create_stream()
        stream.accept_waveform(sample_rate, samples.astype(np.float32))
        self._recognizer.decode_stream(stream)
        text = stream.result.text.strip()
        elapsed = time.perf_counter() - started
        audio_seconds = samples.size / sample_rate
        log.info("ASR %.2fs audio -> %.2fs (rtf=%.2f): %s", audio_seconds, elapsed, elapsed / audio_seconds, text)
        return text, elapsed
