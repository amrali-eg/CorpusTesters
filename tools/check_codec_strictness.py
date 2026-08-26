"""Pin the codec behaviour the audit's conclusions rest on.

PHASE 0 of the audit establishes empirically what a build's codecs actually do
rather than assuming a codec is strict because a fallback was assigned to it.
Two facts underpin every number the audit reports, and if the platform ever
changes either one, the audit's classifications silently change meaning:

  1. For CodePagesEncodingProvider encodings, assigning Decoder.Fallback after
     GetDecoder() does NOT take effect - invalid bytes are substituted.
  2. Supplying the fallbacks to Encoding.GetEncoding(codePage, enc, dec) DOES
     take effect.

The six code pages LineEndingNormalizer treats as safe for its Unicode path
must also honour the assignment, because that whitelist is what makes its
writer safe without the rebuild.

Needs no corpus, so it can run on every push. Exits non-zero on any surprise.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ECDIAG = REPO / "audit" / "ECDiag" / "bin" / "Release" / "net8.0" / "ECDiag.exe"

# Encodings where the assign-after-construction idiom is known to fail. These
# are the ones the audit reports as exposed, and the reason TextEncoding.Strict
# exists.
EXPECT_NONSTRICT_DECODER = [
    "euc-jp", "shift_jis", "big5", "gb18030", "euc-kr", "iso-2022-kr",
]

# The code pages LineEndingNormalizer's IsUnicodeEncoding admits. Its writer and
# scanner rely on the plain assignment working for exactly these.
EXPECT_STRICT_DECODER = [
    "us-ascii", "utf-8", "utf-16le", "utf-16be", "utf-32le", "utf-32be",
]


def probe(names: list[str]) -> dict[str, dict]:
    if not ECDIAG.is_file():
        print(f"ECDiag not built: {ECDIAG}", file=sys.stderr)
        raise SystemExit(2)

    request = {"Mode": "strictness", "Items": [], "Encodings": names}
    result = subprocess.run(
        [str(ECDIAG)], input=json.dumps(request),
        capture_output=True, text=True, encoding="utf-8")

    if result.returncode != 0:
        print(f"ECDiag failed ({result.returncode}): {result.stderr[:400]}",
              file=sys.stderr)
        raise SystemExit(2)

    return {p["Encoding"]: p for p in json.loads(result.stdout)}


def main() -> int:
    probes = probe(EXPECT_NONSTRICT_DECODER + EXPECT_STRICT_DECODER)
    failures: list[str] = []

    print("Codecs whose decoder must honour an assigned fallback")
    print("(LineEndingNormalizer's Unicode path depends on this):")
    for name in EXPECT_STRICT_DECODER:
        p = probes.get(name)
        if p is None or not p.get("Available"):
            failures.append(f"{name}: not available on this build")
            print(f"  MISSING  {name}")
            continue
        actual = p["DecoderStrictness"]
        ok = actual == "Strict"
        print(f"  {'OK     ' if ok else 'FAIL   '} {name:<10} cp={p['CodePage']:<6} {actual}")
        if not ok:
            failures.append(
                f"{name}: expected Strict decoder, platform reports {actual}. "
                "LineEndingNormalizer's IsUnicodeEncoding whitelist is no longer "
                "safe without TextEncoding.Strict.")

    print()
    print("Codecs where the assign-after-construction idiom is known to fail")
    print("(this is why TextEncoding.Strict exists):")
    for name in EXPECT_NONSTRICT_DECODER:
        p = probes.get(name)
        if p is None or not p.get("Available"):
            # A code page missing entirely is a environment problem, not drift.
            print(f"  SKIP     {name:<12} not available on this runner")
            continue
        actual = p["DecoderStrictness"]
        ok = actual == "NonStrict"
        print(f"  {'OK     ' if ok else 'NOTE   '} {name:<10} cp={p['CodePage']:<6} {actual}")
        if not ok:
            # Becoming strict would be good news, but it would mean the audit's
            # PHASE 0 table and the README's explanation are out of date.
            failures.append(
                f"{name}: expected NonStrict decoder under the plain assignment, "
                f"platform reports {actual}. The platform behaviour documented in "
                "README.md and audit/README.md needs revisiting.")

    print()
    if failures:
        print("Codec strictness has changed:")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("Codec strictness is as documented.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
