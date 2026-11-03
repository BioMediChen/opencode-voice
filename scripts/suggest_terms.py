from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voice_asr.config import load_config  # noqa: E402
from voice_asr.corrections import expand_variant  # noqa: E402

ASR_RE = re.compile(r"ASR [\d.]+s audio -> [\d.]+s \(rtf=[\d.]+\): (.*)$")
FIXED_RE = re.compile(r"terms fixed (.*?) -> (.*)$")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_./+#-]*")


def known_words(terms: dict[str, object]) -> set[str]:
    words: set[str] = set()
    for target, variants in terms.items():
        words.update(w.lower() for w in WORD_RE.findall(target))
        if isinstance(variants, str):
            variants = [variants]
        for variant in variants or []:
            for spoken in expand_variant(str(variant)):
                words.update(w.lower() for w in WORD_RE.findall(spoken))
    return words


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Suggest new [terms] entries from voice.log")
    parser.add_argument("--log", default=str(ROOT / "logs" / "voice.log"))
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args(argv)

    log_path = Path(args.log)
    if not log_path.is_file():
        raise SystemExit(f"log not found: {log_path}")

    config = load_config()
    known = known_words(config.terms)

    counts: Counter[str] = Counter()
    examples: dict[str, str] = {}
    fixed = 0
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = FIXED_RE.search(line)
        if match:
            fixed += 1
        match = ASR_RE.search(line)
        if not match:
            continue
        text = match.group(1).strip()
        for word in WORD_RE.findall(text):
            key = word.lower().strip(".-_")
            if len(key) < 2 or key in known:
                continue
            if any(len(term) >= 3 and term in key for term in known):
                continue
            counts[word] += 1
            examples.setdefault(word, text)

    print(f"log: {log_path}")
    print(f"already handled by [terms]: {fixed} utterance(s) corrected")
    print(f"unknown latin tokens: {len(counts)}")
    if not counts:
        return

    print("\ntop candidates (add the canonical spelling + how it was heard):")
    for word, count in counts.most_common(args.limit):
        print(f"  {count:>2}x  {word:<24} e.g. {examples[word][:70]}")

    print("\nexample config entry:")
    top = counts.most_common(1)[0][0]
    print(f'  "{top}" = ["{top.lower()}"]   # 按实际听错的形式补充变体')


if __name__ == "__main__":
    main()
