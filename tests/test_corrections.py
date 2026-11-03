from __future__ import annotations

import unittest

from voice_asr.corrections import apply_corrections, build_rules, expand_variant

TERMS = {
    "useState": ["use state"],
    "useEffect": ["use effect"],
    "OpenCode": ["open code"],
    "R:\\voice": ["r 盘 voice"],
}


class CorrectionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = build_rules(TERMS)

    def fix(self, text: str) -> str:
        return apply_corrections(text, TERMS, self.rules)[0]

    def test_hook_head_variants(self) -> None:
        for spoken in ("use state", "us state", "us stay", "u state", "you state", "优思 state"):
            with self.subTest(spoken=spoken):
                self.assertEqual(self.fix(spoken), "useState")

    def test_hook_tail_variants(self) -> None:
        for spoken in ("use effect", "u effect", "you affect", "优 effect"):
            with self.subTest(spoken=spoken):
                self.assertEqual(self.fix(spoken), "useEffect")

    def test_existing_camel_case_untouched(self) -> None:
        self.assertEqual(self.fix("保留 useState 和 useEffect 不要动"), "保留 useState 和 useEffect 不要动")

    def test_word_boundary(self) -> None:
        self.assertEqual(self.fix("the state of the art"), "the state of the art")

    def test_replacement_keeps_backslashes(self) -> None:
        self.assertEqual(self.fix("打开 r 盘 voice 目录"), "打开 R:\\voice 目录")

    def test_expand_variant_covers_soundalikes(self) -> None:
        expanded = expand_variant("use state")
        self.assertIn("u stay", expanded)
        self.assertIn("优思 state", expanded)

    def test_chinese_and_latin_without_spaces(self) -> None:
        self.assertEqual(self.fix("测试us stay和优 effect。"), "测试useState和useEffect。")


if __name__ == "__main__":
    unittest.main()
