"""Before/after comparison of two audit runs, per file.

Joins two runs on (corpus, relative path) and reports every file whose outcome
changed, classified by whether the change is an improvement, a regression, or
merely a different way of failing. Aggregates alone cannot do this: a run whose
totals are unchanged can still have moved files in both directions.

    python tools/compare.py runs/baseline runs/fixed [--out reports]

By default, a category whose share of the common file population moves by one
percentage point or more raises an alarm and exits 2. This is deliberately a
review gate, not a claim that the newer run is wrong. Use
--allow-distribution-shift only after explaining an expected movement.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Outcomes ordered from best to worst. Used only to describe the direction of a
# transition; the transition itself is always reported verbatim.
SEVERITY = {
    "PASS": 0,
    "NoOpCorrect": 0,
    "OutOfScope": 1,
    "NoReferenceEncoding": 1,
    "UnknownReferenceEncoding": 1,
    "ECCodecUnsupported": 1,
    "MetadataConflict": 1,
    "CorpusByteOrderMislabel": 1,
    # Refusing to convert loses nothing; it is strictly safer than converting
    # to the wrong thing.
    "UnknownEncoding": 2,
    "RefusedByPolicy": 2,
    "DecodeError": 3,
    "EncodeError": 3,
    "WriteError": 3,
    "ReferenceDecodeError": 3,
    "NoOpMislabeled": 4,
    "MappingDifference": 5,
    "Misdetection": 6,
    # Silent, undetected content loss is the worst outcome available: the run
    # reports success and the damage is invisible.
    "SilentDecodeLoss": 7,
    "BackupIntegrityFailure": 8,
    "MissingBackup": 8,
    "MissingConvertedFile": 8,
    "AuditInfrastructureFailure": 8,
    "CorpusRoundTripFailure": 8,
}

METRIC_KEYS = ["DetectionAccuracy", "StrictDecoding", "CodecConformance",
               "TextPreservation"]


def load(run_root: Path) -> dict[tuple[str, str], dict]:
    rows: dict[tuple[str, str], dict] = {}
    for path in sorted(run_root.glob("*/audit.csv")):
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                rows[(path.parent.name, row["RelativePath"])] = row
    return rows


def metrics(rows: list[dict]) -> dict[str, tuple[int, int]]:
    unscored = {
        "OutOfScope", "NoReferenceEncoding", "UnknownReferenceEncoding",
        "ECCodecUnsupported", "MetadataConflict", "CorpusByteOrderMislabel",
        "RefusedByPolicy",
    }
    scored = [r for r in rows if r["FailureCategory"] not in unscored]

    detected = [r for r in scored if r["DetectionMatch"] in ("True", "False")]
    compared = [r for r in scored if r["TextIdentical"] in ("True", "False")]
    silent = sum(1 for r in scored if r["FailureCategory"] == "SilentDecodeLoss")

    return {
        "DetectionAccuracy": (
            sum(1 for r in detected if r["DetectionMatch"] == "True"), len(detected)),
        "StrictDecoding": (len(scored) - silent, len(scored)),
        "CodecConformance": (
            len(scored) - sum(1 for r in scored
                              if r["FailureCategory"] == "MappingDifference"),
            len(scored)),
        "TextPreservation": (
            sum(1 for r in compared if r["TextIdentical"] == "True"), len(compared)),
    }


def pct(pair: tuple[int, int]) -> str:
    good, total = pair
    return f"{good}/{total} ({100 * good / total:.2f}%)" if total else "n/a"


def distribution_shifts(
        before: dict[tuple[str, str], dict],
        after: dict[tuple[str, str], dict],
        threshold: float) -> list[dict]:
    """Category movements over files present in both runs.

    Reconciliation proves that every file has a category. This separately asks
    whether many files moved to another category, which is the signal a complete
    but wrong classifier can otherwise hide.
    """
    if not 0 <= threshold <= 1:
        raise ValueError("distribution threshold must be between 0 and 1")

    common = sorted(set(before) & set(after))
    if not common:
        return []

    before_counts = Counter(before[key]["FailureCategory"] for key in common)
    after_counts = Counter(after[key]["FailureCategory"] for key in common)
    total = len(common)
    shifts: list[dict] = []

    for category in sorted(set(before_counts) | set(after_counts)):
        old, new = before_counts[category], after_counts[category]
        delta = new - old
        if delta == 0:
            continue
        share_delta = delta / total
        shifts.append({
            "Category": category,
            "Before": old,
            "After": new,
            "Delta": delta,
            "ShareDelta": share_delta,
            "Alarm": abs(share_delta) >= threshold,
        })

    return shifts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--out", default="reports")
    parser.add_argument(
        "--distribution-threshold", type=float, default=0.01,
        help="alarm when a category moves by this share of common files (default: 0.01)")
    parser.add_argument(
        "--allow-distribution-shift", action="store_true",
        help="report material category movement without returning exit code 2")
    args = parser.parse_args()

    before_root, after_root = Path(args.before), Path(args.after)
    before, after = load(before_root), load(after_root)

    # A comparison that cannot find its input must fail, not report "no change".
    # load() globs */audit.csv and returns {} for a directory that is missing,
    # empty, or holds no completed corpus - and every downstream metric then
    # reads n/a while the run exits 0. That is a check reassuring its caller
    # about work it never looked at, which is the failure mode this tool exists
    # to catch in the audit itself.
    for label, root, rows in (("before", before_root, before),
                              ("after", after_root, after)):
        if not root.is_dir():
            print(f"{label} run directory does not exist: {root}", file=sys.stderr)
            return 2

        if not rows:
            print(f"{label} run directory contains no */audit.csv: {root}",
                  file=sys.stderr)
            return 2
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    keys = sorted(set(before) | set(after))
    changes: list[dict] = []
    transitions: Counter = Counter()
    direction: Counter = Counter()

    for key in keys:
        b, a = before.get(key), after.get(key)
        b_cat = b["FailureCategory"] if b else "(absent)"
        a_cat = a["FailureCategory"] if a else "(absent)"
        if b_cat == a_cat:
            continue

        b_sev = SEVERITY.get(b_cat, 9)
        a_sev = SEVERITY.get(a_cat, 9)
        verdict = ("Improved" if a_sev < b_sev
                   else "Regressed" if a_sev > b_sev
                   else "Lateral")

        transitions[(b_cat, a_cat)] += 1
        direction[verdict] += 1
        changes.append({
            "Corpus": key[0],
            "RelativePath": key[1],
            "ReferenceEncoding": (a or b)["ReferenceEncoding"],
            "DetectedEncoding": (a or b)["DetectedEncoding"],
            "Before": b_cat,
            "After": a_cat,
            "Verdict": verdict,
            "BeforeTextIdentical": b["TextIdentical"] if b else "",
            "AfterTextIdentical": a["TextIdentical"] if a else "",
        })

    csv_path = out_dir / "before-after-summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=list(changes[0].keys()) if changes else
            ["Corpus", "RelativePath", "Before", "After", "Verdict"])
        writer.writeheader()
        writer.writerows(changes)

    m_before = metrics(list(before.values()))
    m_after = metrics(list(after.values()))
    shifts = distribution_shifts(before, after, args.distribution_threshold)
    material_shifts = [shift for shift in shifts if shift["Alarm"]]

    lines: list[str] = []
    add = lines.append
    add(f"# EC conversion audit — {before_root.name} vs {after_root.name}")
    add("")

    add("## Outcome-distribution movement")
    add("")
    add(f"Alarm threshold: {100 * args.distribution_threshold:.2f} percentage points "
        "of files present in both runs.")
    add("")
    if not shifts:
        add("No category count changed.")
    else:
        add("| Category | Before | After | Delta | Share delta | Alarm |")
        add("|---|---:|---:|---:|---:|---|")
        for shift in sorted(shifts, key=lambda item: -abs(item["ShareDelta"])):
            add(f"| {shift['Category']} | {shift['Before']} | {shift['After']} "
                f"| {shift['Delta']:+d} | {100 * shift['ShareDelta']:+.2f} pp "
                f"| {'YES' if shift['Alarm'] else 'no'} |")
    add("")
    add(f"- Files in both runs: {len(set(before) & set(after))}")
    add(f"- Outcome changed: {len(changes)}")
    add(f"- Improved: {direction['Improved']} · "
        f"Regressed: {direction['Regressed']} · Lateral: {direction['Lateral']}")
    add("")

    add("## The four metrics, before and after")
    add("")
    add("| Metric | Before | After |")
    add("|---|---|---|")
    for key in METRIC_KEYS:
        add(f"| {key} | {pct(m_before[key])} | {pct(m_after[key])} |")
    add("")

    add("## Outcome transitions")
    add("")
    if not transitions:
        add("No file changed outcome.")
    else:
        add("| Before | After | Files | Verdict |")
        add("|---|---|---:|---|")
        for (b_cat, a_cat), n in sorted(transitions.items(), key=lambda x: -x[1]):
            b_sev, a_sev = SEVERITY.get(b_cat, 9), SEVERITY.get(a_cat, 9)
            verdict = ("Improved" if a_sev < b_sev
                       else "Regressed" if a_sev > b_sev else "Lateral")
            add(f"| {b_cat} | {a_cat} | {n} | {verdict} |")
    add("")

    regressions = [c for c in changes if c["Verdict"] == "Regressed"]
    add("## Regressions")
    add("")
    if not regressions:
        add("None. No file that was correct before is incorrect after.")
    else:
        add("| Corpus | File | Reference | Before | After |")
        add("|---|---|---|---|---|")
        for c in regressions[:60]:
            add(f"| {c['Corpus']} | `{c['RelativePath']}` | "
                f"{c['ReferenceEncoding']} | {c['Before']} | {c['After']} |")
        if len(regressions) > 60:
            add("")
            add(f"…and {len(regressions) - 60} more; see the CSV.")
    add("")

    by_corpus: dict[str, Counter] = defaultdict(Counter)
    for c in changes:
        by_corpus[c["Corpus"]][c["Verdict"]] += 1
    add("## By corpus")
    add("")
    add("| Corpus | Changed | Improved | Regressed | Lateral |")
    add("|---|---:|---:|---:|---:|")
    for corpus in sorted(by_corpus):
        c = by_corpus[corpus]
        add(f"| {corpus} | {sum(c.values())} | {c['Improved']} "
            f"| {c['Regressed']} | {c['Lateral']} |")
    add("")

    (out_dir / "before-after-summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")

    (out_dir / "before-after-metrics.json").write_text(json.dumps({
        "before": {k: list(v) for k, v in m_before.items()},
        "after": {k: list(v) for k, v in m_after.items()},
        "changed": len(changes),
        "direction": dict(direction),
        "transitions": {f"{b}->{a}": n for (b, a), n in transitions.items()},
        "distributionThreshold": args.distribution_threshold,
        "distributionShifts": shifts,
    }, indent=2), encoding="utf-8")

    print(f"changed={len(changes)} improved={direction['Improved']} "
          f"regressed={direction['Regressed']} lateral={direction['Lateral']}")
    for key in METRIC_KEYS:
        print(f"  {key:<20} {pct(m_before[key]):>22}  ->  {pct(m_after[key])}")
    if material_shifts:
        print("\nDISTRIBUTION ALARM:")
        for shift in material_shifts:
            print(f"  {shift['Category']}: {shift['Before']} -> {shift['After']} "
                  f"({100 * shift['ShareDelta']:+.2f} pp)")
    print(f"\nwrote {csv_path} and {out_dir / 'before-after-summary.md'}")
    return 2 if material_shifts and not args.allow_distribution_shift else 0


if __name__ == "__main__":
    raise SystemExit(main())
