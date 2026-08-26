"""Negative controls for the audit: deliberately break things and demand it notice.

Every check the audit performs is an assertion that something is *right*. None of
them fail when the audit itself is wrong, which is how three defects shipped in a
single day - each producing an internally consistent, entirely false result.

These are the opposite: inputs whose correct verdict is known in advance, most of
them wrong on purpose. If the audit reports success for any of them, the audit is
broken, and that is the only thing this file can tell you.

Needs no corpus and no EncodingChecker build, so it runs on every push.

    python tools/test_audit_mutations.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "audit"))

import audit  # noqa: E402


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []
        self._mark = 0

    def expect(self, label: str, actual, wanted) -> None:
        if actual == wanted:
            self.passed += 1
        else:
            self.failures.append(f"{label}\n      wanted {wanted!r}\n      got    {actual!r}")

    def section(self, name: str) -> None:
        print(f"\n{name}")

    def report(self, label: str) -> None:
        failed = len(self.failures) - self._mark
        mark = "OK  " if failed == 0 else "FAIL"
        suffix = "" if failed == 0 else f"  ({failed} control(s) failed)"
        print(f"  {mark} {label}{suffix}")


# ---------------------------------------------------------------------------
# Ground-truth resolution
#
# The defect that read six UTF-16 Belarusian files as big-endian lived here. A
# directory name that is *also* a codec name must not win over the corpus's own
# encoding-plus-language convention.
# ---------------------------------------------------------------------------

def check_ground_truth(c: Checker) -> None:
    c.section("Ground-truth resolution")

    # Derived the way audit.py derives it: a tag counts as a language when it
    # also marks directories whose encoding prefix resolves without it.
    langs = frozenset({"be", "bg", "zh", "en", "ja", "ru", "de"})

    cases = [
        # The regression itself, in both forms.
        ("utf-16-be", "utf-16", "UTF-16 Belarusian, not big-endian"),
        ("utf-32-be", "utf-32", "UTF-32 Belarusian, not big-endian"),
        # Plain codec names with no language tag must survive untouched.
        ("utf-16", "utf-16", "no language tag"),
        ("iso-8859-1", "iso8859-1", "digits are not a language tag"),
        ("koi8-r", "koi8-r", "single-letter suffix is part of the codec"),
        # Ordinary encoding+language pairs.
        ("big5-zh", "big5", "language stripped"),
        ("windows-1252-en", "cp1252", "language stripped"),
        ("shift_jis-ja", "shift_jis", "language stripped"),
        # A codec Python has but .NET does not: the audit still resolves it,
        # because "no .NET code page" is a separate fact recorded elsewhere.
        ("hp-roman8-en", "hp-roman8", "resolvable here, unsupported by .NET"),
        # Nothing resolvable on either side.
        ("euc-tw-zh", None, "no codec available at all"),
        ("viscii-vi", None, "no codec available at all"),
        ("none-none", None, "explicit no-encoding bucket"),
    ]

    for token, wanted, why in cases:
        got, _ = audit.resolve_directory_codec(token, langs)
        c.expect(f"{token!r} -> {wanted!r} ({why})", got, wanted)

    # Without the language evidence the ambiguous name must fall back to the
    # longest codec match. Pinned so the fallback stays visible rather than
    # quietly becoming the only path again.
    got, _ = audit.resolve_directory_codec("utf-16-be", frozenset())
    c.expect("no language evidence -> longest match", got, "utf-16-be")

    c.report("directory names resolve as their corpus intends")


# ---------------------------------------------------------------------------
# Mutations that must never be reported as preserved
#
# Each pair is (original bytes, what a conversion produced). Every one differs
# in text, so the audit must say so. A PASS here means the comparison is blind.
# ---------------------------------------------------------------------------

def check_mutations(c: Checker) -> None:
    c.section("Mutations the comparison must catch")

    text = "Hello, 世界! Grüße — naïve café.\nSecond line.\n"

    mutations = [
        ("one character substituted", text.replace("café", "cafe")),
        ("one character dropped", text.replace("世", "")),
        ("one character inserted", text.replace("Hello", "Hellro")),
        ("em dash to hyphen", text.replace("—", "-")),
        ("combining form instead of precomposed", text.replace("ü", "ü")),
        ("trailing newline removed", text.rstrip("\n")),
        ("CRLF instead of LF", text.replace("\n", "\r\n")),
        ("leading space added", " " + text),
        ("NBSP instead of space", text.replace(" ", " ", 1)),
        ("fullwidth tilde for wave dash", text + "〜" ),
    ]

    reference = audit.hash_text(text)

    for label, mutated in mutations:
        # The audit compares decoded text, and summarises with this hash.
        c.expect(f"{label} is detected", audit.hash_text(mutated) != reference, True)
        c.expect(f"{label} differs by equality", mutated != text, True)

    # The combining-form case is the one a normalizing comparison would miss.
    # It is listed above and must fail equality, because the audit deliberately
    # applies no normalization of any kind.
    nfc = text
    nfd = text.replace("ü", "ü")
    c.expect("NFD form is not treated as equal to NFC", nfc == nfd, False)

    # An unchanged text must still compare equal, or the mutation checks above
    # would pass for the wrong reason.
    c.expect("identical text compares equal", audit.hash_text(text), reference)

    c.report("every deliberate text change is reported as a difference")


# ---------------------------------------------------------------------------
# Byte-level corruption of real encodings
#
# The reference decode has to reject these rather than substitute, which is the
# whole premise of the strictness argument.
# ---------------------------------------------------------------------------

def check_corruption(c: Checker) -> None:
    c.section("Byte-level corruption")

    sample = "日本語のテキストです。"

    cases = [
        ("utf-8", sample),
        ("shift_jis", sample),
        ("euc_jp", sample),
        ("big5", "這是繁體中文字。"),
        ("gb18030", "这是简体中文文本。"),
    ]

    for codec, content in cases:
        good = content.encode(codec)

        # Round trip has to hold before corrupting it, or the case proves nothing.
        c.expect(f"{codec}: clean round trip", good.decode(codec), content)

        # Truncating the final multi-byte sequence must not decode cleanly to
        # the same text.
        truncated = good[:-1]
        try:
            still_same = truncated.decode(codec) == content
        except UnicodeDecodeError:
            still_same = False
        c.expect(f"{codec}: truncated tail is not silently accepted", still_same, False)

        # A byte flipped to an illegal lead must either raise or change the text.
        flipped = bytearray(good)
        flipped[0] ^= 0x80
        try:
            changed = bytes(flipped).decode(codec) != content
        except UnicodeDecodeError:
            changed = True
        c.expect(f"{codec}: flipped lead byte is not silently accepted", changed, True)

    c.report("corrupted bytes are rejected or reported as different")


# ---------------------------------------------------------------------------
# Edge cases that produced wrong verdicts before
# ---------------------------------------------------------------------------

def check_edge_cases(c: Checker) -> None:
    c.section("Edge cases with a known history")

    # A file holding nothing but a BOM converts correctly to zero bytes. This
    # was left uncompared and fell through to a divergence classification.
    c.expect("BOM-only decodes to empty text", audit.strip_bom("﻿"), "")
    c.expect("empty text hashes consistently",
             audit.hash_text(""), audit.hash_text(""))
    c.expect("empty and non-empty do not collide",
             audit.hash_text("") == audit.hash_text("x"), False)

    # A BOM is an encoding artifact and must not count as content, in every
    # encoding that carries one.
    for codec in ("utf-8-sig", "utf-16", "utf-32"):
        decoded = "abc".encode(codec).decode(codec)
        c.expect(f"{codec}: BOM stripped from comparison",
                 audit.strip_bom(decoded), "abc")

    # A reversed BOM means the declared byte order is wrong. This must be
    # recognised, not decoded as content.
    le_bytes = "﻿IPAC France".encode("utf-16-le")
    c.expect("LE bytes read as BE open with U+FFFE",
             le_bytes.decode("utf-16-be").startswith("￾"), True)
    c.expect("opposite byte order is detected",
             audit.opposite_endian_decodes(le_bytes, "utf-16-be"), True)
    c.expect("correctly declared byte order is not flagged",
             audit.opposite_endian_decodes(le_bytes, "utf-16-le"), False)

    # Codec aliases must compare equal, or spelling differences read as errors.
    for a, b in (("ibm866", "cp866"), ("windows-1252", "cp1252"),
                 ("latin1", "iso8859-1")):
        c.expect(f"{a} and {b} resolve alike",
                 audit.resolve_codec(a), audit.resolve_codec(b))

    # And genuinely different codecs must not.
    c.expect("utf-16-le and utf-16-be stay distinct",
             audit.resolve_codec("utf-16-le") == audit.resolve_codec("utf-16-be"),
             False)

    c.report("previously mishandled edge cases behave correctly")


def main() -> int:
    c = Checker()

    check_ground_truth(c)
    check_mutations(c)
    check_corruption(c)
    check_edge_cases(c)

    print()
    if c.failures:
        print(f"{len(c.failures)} negative control(s) FAILED - the audit would not "
              f"have noticed:\n")
        for line in c.failures:
            print(f"  - {line}")
        return 1

    print(f"All {c.passed} negative controls hold: every deliberate defect is caught.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
