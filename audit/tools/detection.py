"""Detection-accuracy breakdown.

A single detection percentage hides the difference between "EC identified the
wrong encoding" and "EC has no codec for this encoding at all". The second is a
coverage limit, not a detector error, and the two need separate numbers.

    python tools/detection.py runs/fixed
"""
from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

UNSCORED = {"OutOfScope", "NoReferenceEncoding", "UnknownReferenceEncoding",
            "MetadataConflict"}


def main(argv: list[str]) -> int:
    run_root = Path(argv[1] if len(argv) > 1 else "runs/fixed")

    per_corpus: dict[str, list[dict]] = {}
    for path in sorted(run_root.glob("*/audit.csv")):
        with path.open(encoding="utf-8", newline="") as fh:
            per_corpus[path.parent.name] = [
                r for r in csv.DictReader(fh)
                if r["FailureCategory"] not in UNSCORED
                and r["DetectionMatch"] in ("True", "False")]

    print(f"=== detection accuracy — {run_root} ===\n")
    header = (f"{'corpus':<20} {'files':>6} {'hit':>6} {'raw':>8} "
              f"{'noCodec':>8} {'adjusted':>9}")
    print(header)
    print("-" * len(header))

    totals = Counter()
    for corpus, rows in per_corpus.items():
        hit = sum(1 for r in rows if r["DetectionMatch"] == "True")
        # No .NET code page for the reference encoding: EC could not have named
        # it correctly under any detector.
        no_codec = sum(1 for r in rows if not r["ReferenceCodePage"])
        supported = len(rows) - no_codec
        adj_hit = sum(1 for r in rows
                      if r["DetectionMatch"] == "True" and r["ReferenceCodePage"])

        totals["files"] += len(rows)
        totals["hit"] += hit
        totals["no_codec"] += no_codec
        totals["supported"] += supported
        totals["adj_hit"] += adj_hit

        raw = f"{100 * hit / len(rows):.1f}%" if rows else "n/a"
        adj = f"{100 * adj_hit / supported:.1f}%" if supported else "n/a"
        print(f"{corpus:<20} {len(rows):>6} {hit:>6} {raw:>8} {no_codec:>8} {adj:>9}")

    print("-" * len(header))
    raw = f"{100 * totals['hit'] / totals['files']:.1f}%" if totals["files"] else "n/a"
    adj = (f"{100 * totals['adj_hit'] / totals['supported']:.1f}%"
           if totals["supported"] else "n/a")
    print(f"{'ALL':<20} {totals['files']:>6} {totals['hit']:>6} {raw:>8} "
          f"{totals['no_codec']:>8} {adj:>9}")

    print("\n  raw      = correct / all files with a reference encoding")
    print("  noCodec  = reference encoding has no .NET code page on this build")
    print("  adjusted = correct / files whose encoding EC could represent at all")

    print("\n=== encodings with no .NET codec (excluded from 'adjusted') ===")
    missing: Counter = Counter()
    for rows in per_corpus.values():
        for r in rows:
            if not r["ReferenceCodePage"]:
                missing[r["ReferenceEncoding"] or "(none)"] += 1
    for name, n in missing.most_common(30):
        print(f"  {name:<20} {n:>5}")
    if len(missing) > 30:
        print(f"  ... and {len(missing) - 30} more")

    print("\n=== biggest misdetection sources (supported encodings only) ===")
    confusion: dict[str, Counter] = defaultdict(Counter)
    for rows in per_corpus.values():
        for r in rows:
            if r["DetectionMatch"] == "False" and r["ReferenceCodePage"]:
                confusion[r["ReferenceEncoding"]][r["DetectedEncoding"]] += 1
    ranked = sorted(confusion.items(), key=lambda kv: -sum(kv[1].values()))
    for ref, counts in ranked[:15]:
        top = ", ".join(f"{k}={v}" for k, v in counts.most_common(3))
        print(f"  {ref:<18} {sum(counts.values()):>5}   {top}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
