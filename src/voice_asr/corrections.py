from __future__ import annotations

import re

_LATIN = re.compile(r"[A-Za-z0-9_]")

# 中文用户说英文标识符时，ASR 常见的音近写法。配置里只写标准说法，
# 这里自动展开，避免每遇到一种新写法都要手工补。
HEADS = ["use", "us", "u", "you", "优", "优思", "优斯"]
TAILS = {
    "state": ["state", "states", "stay", "stat"],
    "effect": ["effect", "effects", "affect", "affects"],
    "ref": ["ref", "refs"],
    "memo": ["memo", "memor"],
    "context": ["context", "contexts"],
    "reducer": ["reducer", "reduce"],
    "callback": ["callback", "call back"],
}


def expand_variant(variant: str) -> list[str]:
    """Expand the head/tail word of a two-word hook name into its sound-alikes."""
    parts = variant.split()
    if len(parts) != 2:
        return [variant]
    head, tail = parts
    heads = HEADS if head.lower() in HEADS else [head]
    tails = TAILS.get(tail.lower(), [tail])
    expanded = {variant}
    for new_head in heads:
        for new_tail in tails:
            expanded.add(f"{new_head} {new_tail}")
    return sorted(expanded, key=len, reverse=True)


def _variant_pattern(variant: str) -> re.Pattern[str]:
    """Build a tolerant pattern for one spoken variant.

    Words are separated by optional whitespace, so ``use state`` also matches
    ``usstay`` and ``优 effect`` matches ``优effect``. Latin edges get word
    boundaries so ``state`` never matches inside ``useState``.
    """
    parts = [re.escape(part) for part in variant.split()]
    body = r"\s*".join(parts)
    return re.compile(rf"(?<!{_LATIN.pattern}){body}(?!{_LATIN.pattern})", re.IGNORECASE)


def build_rules(terms: dict[str, object]) -> list[tuple[re.Pattern[str], str]]:
    rules: list[tuple[re.Pattern[str], str]] = []
    for target, variants in terms.items():
        if isinstance(variants, str):
            variants = [variants]
        if not isinstance(variants, (list, tuple)):
            continue
        for variant in variants:
            variant = str(variant).strip()
            if not variant:
                continue
            for spoken in expand_variant(variant):
                rules.append((_variant_pattern(spoken), target))
    # Longer variants first so "use state" wins over a bare "state" alias.
    rules.sort(key=lambda rule: len(rule[0].pattern), reverse=True)
    return rules


def apply_corrections(
    text: str, terms: dict[str, object], rules: list[tuple[re.Pattern[str], str]] | None = None
) -> tuple[str, list[tuple[str, str]]]:
    """Replace common mis-hearings of code identifiers.

    ``terms`` maps the canonical spelling to a list of spoken variants, e.g.
    ``{"useState": ["use state", "us stay", "优 state"]}``.
    """
    fired: list[tuple[str, str]] = []
    result = text
    for pattern, target in rules if rules is not None else build_rules(terms):
        patched, count = pattern.subn(target, result)
        if count:
            fired.append((pattern.pattern, target))
            result = patched
    return result, fired
