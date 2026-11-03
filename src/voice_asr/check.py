from __future__ import annotations

import argparse
import sys
import time
import wave
from pathlib import Path

import numpy as np

from .asr import SenseVoice
from .audio import Recorder, list_input_devices
from .config import load_config


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as fh:
        rate = fh.getframerate()
        channels = fh.getnchannels()
        width = fh.getsampwidth()
        frames = fh.readframes(fh.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    data = np.frombuffer(frames, dtype=dtype).astype(np.float32)
    if width == 2:
        data /= 32768.0
    elif width == 4:
        data /= 2147483648.0
    else:
        data = (data - 128.0) / 128.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data, rate


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Voice stack smoke test")
    parser.add_argument("--config")
    parser.add_argument("--wav", help="transcribe a wav file instead of the microphone")
    parser.add_argument("--record", type=float, default=4.0, help="seconds to record from the default mic")
    parser.add_argument("--device", help="input device index or name substring")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.device:
        config.audio.device = args.device

    print("input devices:")
    for device in list_input_devices():
        print(f"  [{device['index']:>2}] {device['name']} ({device['channels']}ch @ {device['default_rate']}Hz)")

    started = time.perf_counter()
    asr = SenseVoice(
        model=config.path(config.asr.model),
        tokens=config.path(config.asr.tokens),
        num_threads=config.asr.num_threads,
        language=config.asr.language,
        use_itn=config.asr.use_itn,
    )
    print(f"model loaded in {time.perf_counter() - started:.2f}s")

    if args.wav:
        audio, rate = read_wav(Path(args.wav))
        print(f"wav: {args.wav}  {audio.size / rate:.2f}s @ {rate}Hz")
    else:
        recorder = Recorder(device=config.audio.device, threshold=config.audio.silence_threshold)
        print(f"recording {args.record:.1f}s from the microphone, speak now ...")
        recorder.start()
        time.sleep(args.record)
        audio, seconds = recorder.stop()
        rate = 16000
        print(f"captured {seconds:.2f}s after silence trim")

    text, elapsed = asr.transcribe(audio, rate)
    print(f"\ntext ({elapsed:.2f}s): {text}")


if __name__ == "__main__":
    main()
