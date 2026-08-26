"""Cross-corpus roll-up over one or more audit runs.

Reads the per-file audit.csv evidence produced by audit.py and answers the
questions that only make sense across corpora: how far the decoder-strictness
defect actually reaches, and which codecs account for the divergences.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

PRIMARY = ["PASS", "NoOpMislabeled", "Misdetection", "MappingDifference",
           "SilentDecodeLoss", "UnknownEncoding", "DecodeError", "EncodeError",
           "ReferenceDecodeError", "OutOfScope", "NoReferenceEncoding",
           "UnknownReferenceEncoding", "MetadataConflict",
           "BackupIntegrityFailure", "MissingBackup", "MissingConvertedFile"]


def load(run_root: Path) -> dict[str, list[dict]]:
    corpora: dict[str, list[dict]] = {}
    for path in sorted(run_root.glob("*/audit.csv")):
        with path.open(encoding="utf-8", newline="") as fh:
            corpora[path.parent.name] = list(csv.DictReader(fh))
    return corpora


def main(argv: list[str]) -> int:
    run_root = Path(argv[1] if len(argv) > 1 else "runs/baseline")
    corpora = load(run_root)
    if not corpora:
        print(f"no audit.csv under {run_root}", file=sys.stderr)
        return 2

    all_rows: list[dict] = []
    print(f"=== {run_root} ===\n")
    print(f"{'corpus':<20} {'files':>6} {'defect':>7} {'throws':>7} "
          f"{'backup':>7} {'roundtrip':>10}")
    for name, rows in corpora.items():
        all_rows += rows
        defect = sum(1 for r in rows if r["ECImplementationDefect"])
        throws = sum(1 for r in rows if r["ECStrictDecodeOutcome"] == "Throws")
        bad_backup = sum(1 for r in rows if r["BackupIntegrity"] == "Mismatch")
        rt_bad = sum(1 for r in rows if r["CorpusRoundTrip"] == "False")
        print(f"{name:<20} {len(rows):>6} {defect:>7} {throws:>7} "
              f"{bad_backup:>7} {rt_bad:>10}")

    print("\n=== outcomes (all corpora) ===")
    outcome = Counter(r["FailureCategory"] for r in all_rows)
    for key in PRIMARY:
        if outcome.get(key):
            print(f"  {key:<26} {outcome[key]:>5}")
    other = {k: v for k, v in outcome.items() if k not in PRIMARY}
    for key, value in sorted(other.items()):
        print(f"  {key:<26} {value:>5}  (unlisted)")

    print("\n=== files EC's decoder accepted but a strict decoder rejects ===")
    defects = [r for r in all_rows if r["ECImplementationDefect"]]
    if not defects:
        print("  none")
    by_codec = Counter((r["ReferenceEncoding"], r["FailureCategory"])
                       for r in defects)
    for (codec, cat), n in sorted(by_codec.items(), key=lambda x: -x[1]):
        print(f"  {codec:<16} {cat:<22} {n:>5}")

    print("\n=== text-differing files by reference codec ===")
    diff = [r for r in all_rows if r["TextIdentical"] == "False"]
    by_ref: dict[str, Counter] = defaultdict(Counter)
    for r in diff:
        by_ref[r["ReferenceEncoding"] or "(none)"][r["FailureCategory"]] += 1
    for codec in sorted(by_ref, key=lambda c: -sum(by_ref[c].values()))[:20]:
        counts = ", ".join(f"{k}={v}" for k, v in by_ref[codec].most_common())
        print(f"  {codec:<16} {sum(by_ref[codec].values()):>5}   {counts}")

    print("\n=== most common divergence signatures ===")
    sig = Counter((r["ReferenceCodePoint"], r["ConvertedCodePoint"],
                   r["ReferenceEncoding"])
                  for r in diff if r["ReferenceCodePoint"])
    for (ref, con, codec), n in sig.most_common(20):
        print(f"  {codec:<14} {ref:<8} -> {con:<8} {n:>5}")

    print("\n=== forced-reference outcomes ===")
    forced = Counter(r["ForcedReferenceOutcome"] for r in all_rows
                     if r["ForcedReferenceOutcome"])
    for value, n in forced.most_common():
        print(f"  {value:<44} {n:>5}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
