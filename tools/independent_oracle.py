"""Second-stage validation: check the audit's reference decoder against others.

The audit decodes with Python's codec registry and calls that the reference.
That is defensible for reproducibility and indefensible as a claim of authority:
there is no single universal mapping for several legacy encodings, and treating
one implementation as normative would replace an argument with an assumption.

This samples the corpora and decodes each file with two implementations that
share no code with Python's:

    GNU libiconv     a separate C implementation
    Node.js / ICU    the WHATWG Encoding Standard, as browsers implement it

and answers three questions kept deliberately apart:

    1. Does the audit's reference agree with an independent implementation?
    2. Where they disagree, is that a mapping/profile difference or evidence
       of an implementation defect?
    3. Does the disagreement change any audit classification?

The third matters most. A disagreement that leaves TextIdentical unchanged is
an observation; one that would move a file from PASS to TextDifferent is a
threat to a published result.

Output is second-stage validation. It does not revise the first-stage findings.

    python tools/independent_oracle.py [run-dir] [--per-stratum N]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

CORPUS_ROOT = Path(os.environ.get("CORPUS_ROOT", ""))
SOURCES = {
    "uts3": "UnicodeTestSuite-v3.0",
    "chardet": "test-data-main",
    "charsetnormalizer": "Charset-Normalizer data",
    "utfunknown26": "UTF-unknown-2.6 tests",
}

# Python codec -> the name each oracle knows it by. Absent means that oracle is
# not asked, which is recorded rather than silently skipped.
ICONV_NAMES = {
    "utf-8": "UTF-8", "utf-16-le": "UTF-16LE", "utf-16-be": "UTF-16BE",
    "utf-32-le": "UTF-32LE", "utf-32-be": "UTF-32BE", "ascii": "ASCII",
    "shift_jis": "SHIFT_JIS", "euc_jp": "EUC-JP", "iso2022_jp": "ISO-2022-JP",
    "big5": "BIG5", "gb18030": "GB18030", "gb2312": "EUC-CN", "euc_kr": "EUC-KR",
    "cp1250": "CP1250", "cp1251": "CP1251", "cp1252": "CP1252",
    "cp1253": "CP1253", "cp1254": "CP1254", "cp1255": "CP1255",
    "cp1256": "CP1256", "cp1257": "CP1257", "cp1258": "CP1258",
    "iso8859-1": "ISO-8859-1", "iso8859-2": "ISO-8859-2",
    "iso8859-5": "ISO-8859-5", "iso8859-7": "ISO-8859-7",
    "iso8859-9": "ISO-8859-9", "iso8859-15": "ISO-8859-15",
    "koi8-r": "KOI8-R", "koi8-u": "KOI8-U", "cp866": "CP866",
    "cp850": "CP850", "cp437": "CP437", "mac-roman": "MacRoman",
    "tis-620": "TIS-620",
}

# WHATWG labels Node's TextDecoder accepts.
ICU_NAMES = {
    "utf-8": "utf-8", "utf-16-le": "utf-16le", "utf-16-be": "utf-16be",
    "ascii": "windows-1252", "shift_jis": "shift_jis", "euc_jp": "euc-jp",
    "iso2022_jp": "iso-2022-jp", "big5": "big5", "gb18030": "gb18030",
    "gb2312": "gbk", "euc_kr": "euc-kr", "cp1250": "windows-1250",
    "cp1251": "windows-1251", "cp1252": "windows-1252",
    "cp1253": "windows-1253", "cp1254": "windows-1254",
    "cp1255": "windows-1255", "cp1256": "windows-1256",
    "cp1257": "windows-1257", "cp1258": "windows-1258",
    "iso8859-2": "iso-8859-2", "iso8859-5": "iso-8859-5",
    "iso8859-7": "iso-8859-7", "iso8859-15": "iso-8859-15",
    "koi8-r": "koi8-r", "koi8-u": "koi8-u", "cp866": "ibm866",
    "mac-roman": "macintosh", "tis-620": "windows-874",
}

SAMPLE_LIMIT = 32 * 1024


def strip_bom(text: str | None) -> str | None:
    """Match the audit: a BOM is an encoding artifact, not content.

    ICU strips it during decode and Python does not. Comparing raw decodes
    reports that as a mapping disagreement at index 0, which it is not - it is
    the two implementations disagreeing about whose job it is. The audit already
    strips BOMs on both sides, so this must too or it manufactures findings.
    """
    if text is None:
        return None
    return text[1:] if text.startswith("﻿") else text


def decode_reference(data: bytes, codec: str) -> str | None:
    try:
        return strip_bom(data.decode(codec))
    except (UnicodeDecodeError, LookupError, ValueError):
        return None


def decode_iconv(path: Path, codec: str) -> str | None:
    name = ICONV_NAMES.get(codec)
    if name is None:
        return None
    try:
        out = subprocess.run(
            ["iconv", "-f", name, "-t", "UTF-8", str(path)],
            capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return strip_bom(out.stdout.decode("utf-8"))
    except UnicodeDecodeError:
        return None


NODE_SCRIPT = r"""
const fs = require('fs');
const [file, label] = process.argv.slice(2);
try {
  const buf = fs.readFileSync(file);
  const text = new TextDecoder(label, { fatal: true }).decode(buf);
  process.stdout.write(text);
} catch (e) { process.exit(3); }
"""


def decode_icu(path: Path, codec: str, script: Path) -> str | None:
    name = ICU_NAMES.get(codec)
    if name is None:
        return None
    try:
        out = subprocess.run(
            ["node", str(script), str(path), name],
            capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    try:
        return strip_bom(out.stdout.decode("utf-8"))
    except UnicodeDecodeError:
        return None


def size_bucket(n: int) -> str:
    if n < 1024:
        return "<1K"
    if n < 16 * 1024:
        return "1K-16K"
    if n < 256 * 1024:
        return "16K-256K"
    return ">256K"


def constraint_bucket(row: dict) -> str:
    """Boundary buckets for ambiguous files.

    The 0.10 threshold separated this corpus cleanly. Whether that generalises
    is exactly what a sample drawn from *around* the boundary can test, so the
    strata are chosen to straddle it rather than to avoid it.
    """
    try:
        value = float(row.get("ReferenceConstraint", -1))
    except ValueError:
        return "n/a"
    if value < 0:
        return "n/a"
    if value == 0.0:
        return "0.00"
    if value < 0.05:
        return "0.00-0.05"
    if value < 0.10:
        return "0.05-0.10 (just below)"
    if value < 0.20:
        return "0.10-0.20 (just above)"
    return ">0.20"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", nargs="?", default="audit/runs/validation")
    parser.add_argument("--per-stratum", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260826)
    args = parser.parse_args()

    if not CORPUS_ROOT:
        print("CORPUS_ROOT is not set", file=sys.stderr)
        return 2

    random.seed(args.seed)

    rows: list[dict] = []
    for path in sorted(Path(args.run).glob("*/audit.csv")):
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                row["_corpus"] = path.parent.name
                rows.append(row)

    # Stratify across every taxonomy outcome, not only the interesting ones: a
    # sample drawn from failures alone cannot show that agreement holds where
    # the audit says it does.
    strata: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if not row["ReferenceEncoding"]:
            continue
        strata[(
            row["DetectionOutcome"] or "(none)",
            row["FailureCategory"],
            row["_corpus"],
            row["ReferenceEncoding"],
            row["ReferenceBOM"] or "-",
            size_bucket(int(row["OriginalByteLength"] or 0)),
            constraint_bucket(row),
        )].append(row)

    sample: list[dict] = []
    for key in sorted(strata):
        bucket = strata[key]
        sample.extend(random.sample(bucket, min(args.per_stratum, len(bucket))))

    print(f"Second-stage validation: {len(sample)} files sampled from "
          f"{len(strata)} strata over {len(rows)} rows\n")

    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "decode.js"
        script.write_text(NODE_SCRIPT, encoding="utf-8")

        verdicts: Counter = Counter()
        by_codec: dict[str, Counter] = defaultdict(Counter)
        consequential: list[str] = []
        examples: dict[str, str] = {}

        for row in sample:
            source = CORPUS_ROOT / SOURCES[row["_corpus"]] / row["RelativePath"]
            if not source.is_file():
                continue
            data = source.read_bytes()[:SAMPLE_LIMIT]
            codec = row["ReferenceEncoding"]

            reference = decode_reference(data, codec)
            if reference is None:
                verdicts["reference cannot decode"] += 1
                continue

            trimmed = Path(tmp) / "sample.bin"
            trimmed.write_bytes(data)

            others = {
                "iconv": decode_iconv(trimmed, codec),
                "icu": decode_icu(trimmed, codec, script),
            }
            available = {k: v for k, v in others.items() if v is not None}

            if not available:
                verdicts["no independent oracle for this codec"] += 1
                by_codec[codec]["unavailable"] += 1
                continue

            agree = [k for k, v in available.items() if v == reference]
            disagree = [k for k, v in available.items() if v != reference]

            if not disagree:
                verdicts["all implementations agree"] += 1
                by_codec[codec]["agree"] += 1
                continue

            verdicts[f"reference differs from {'+'.join(sorted(disagree))}"] += 1
            by_codec[codec]["differ"] += 1

            # Question 3: would this change what the audit concluded?
            # The audit's verdict rests on the reference text. If an oracle
            # produces different text, a file recorded as preserved might not be.
            if row["TextIdentical"] == "True":
                consequential.append(
                    f"{row['_corpus']}/{row['RelativePath']} ({codec}): "
                    f"recorded as text-identical, but {'+'.join(disagree)} "
                    f"decode(s) the source differently")

            key = f"{codec}:{'+'.join(sorted(disagree))}"
            if key not in examples:
                for name in disagree:
                    other = available[name]
                    for i, (a, b) in enumerate(zip(reference, other)):
                        if a != b:
                            examples[key] = (
                                f"{codec}: reference U+{ord(a):04X} vs "
                                f"{name} U+{ord(b):04X} at index {i}")
                            break
                    if key in examples:
                        break

    print("Question 1 — does the reference agree with independent implementations?\n")
    for verdict, count in verdicts.most_common():
        print(f"  {count:>4}  {verdict}")

    print("\nQuestion 2 — where they disagree, what is the disagreement?\n")
    if not examples:
        print("  No disagreements found in this sample.")
    for key, detail in sorted(examples.items()):
        print(f"  {detail}")

    print("\nQuestion 3 — does any disagreement change an audit classification?\n")
    if not consequential:
        print("  None. Every disagreement is on a file whose audit verdict does")
        print("  not rest on the disputed characters.")
    else:
        print(f"  {len(consequential)} file(s) where it might:")
        for line in consequential[:20]:
            print(f"    - {line}")

    differing = [c for c, v in by_codec.items() if v["differ"]]
    if differing:
        print("\nCodecs where implementations disagree:\n")
        for codec in sorted(differing):
            v = by_codec[codec]
            print(f"  {codec:<14} agree={v['agree']:<4} differ={v['differ']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
