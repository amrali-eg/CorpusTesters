"""Verify an audit run against its own evidence and against the files on disk.

The audit is the instrument that judges everything else, so it needs checking
too - three defects in it were found by hand in one session, each of which made
a correct tool look wrong. This re-derives what can be re-derived and asserts
the invariants that must hold, rather than trusting the run that produced them.

Checks, in order of how badly a failure would mislead:

  Coverage        every file on disk appears exactly once; nothing invented
  Classification  every row carries exactly one primary outcome, and the
                  outcome agrees with the evidence fields on the same row
  Hashes          text hashes are recomputed from the original bytes and
                  compared, so a wrong decode cannot hide behind its own digest
  Reconciliation  the counts add up, in both directions
  Sampling        a random sample is decoded and compared from scratch,
                  independently of the audit's own code path

    python tools/check_audit_integrity.py [run-dir] [--sample N]

Exit codes: 0 all invariants hold, 1 violations found, 2 the run is unusable.
"""
from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import os
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

CORPUS_ROOT = Path(os.environ.get("CORPUS_ROOT", ""))
SOURCES = {
    "uts3": "UnicodeTestSuite-v3.0",
    "chardet": "test-data-main",
    "charsetnormalizer": "Charset-Normalizer data",
    "utfunknown26": "UTF-unknown-2.6 tests",
}

PRIMARY_OUTCOMES = {
    "PASS", "NoOpCorrect", "NoOpMislabeled", "Misdetection", "MappingDifference",
    "SilentDecodeLoss", "UnknownEncoding", "DecodeError", "EncodeError",
    "WriteError", "ReferenceDecodeError", "MetadataConflict",
    "BackupIntegrityFailure", "MissingBackup", "MissingConvertedFile",
    "CorpusRoundTripFailure", "UnknownReferenceEncoding",
    "AuditInfrastructureFailure", "OutOfScope", "NoReferenceEncoding",
    "CorpusByteOrderMislabel",
}

# Directory names that are both a valid codec and an encoding+language pair.
# Listed explicitly so the resolution stays a deliberate, reviewed decision:
# "utf-16-be" is UTF-16 Belarusian, and reading it as big-endian silently
# corrupted six files' ground truth before this was noticed.
ACKNOWLEDGED_AMBIGUITIES = {
    ("utf-16-be", "utf-16"),
    ("utf-32-be", "utf-32"),
}

DETECTION_OUTCOMES = {
    "ExactMatch", "TextEquivalent", "StructurallyAmbiguous", "Misdetection",
    "NoDotNetCodec", "NotIdentified", "NoReference", "",
}


def structure_bearing(codec: str) -> bool:
    """Whether a codec constrains byte sequences, i.e. is multi-byte.

    Recomputed here rather than read from the audit's own output: a taxonomy
    that graded itself against its own definition would agree with itself no
    matter what the definition had become.
    """
    if not codec:
        return False
    for ch in ("é", "世", "Ж"):
        try:
            if len(ch.encode(codec)) > 1:
                return True
        except (UnicodeEncodeError, LookupError):
            continue
    return False


UNSCORED = {
    "OutOfScope", "NoReferenceEncoding", "UnknownReferenceEncoding",
    "MetadataConflict", "CorpusByteOrderMislabel", "ReferenceDecodeError",
}


def hash_text(text: str) -> str:
    digest = hashlib.sha256()
    for ch in text:
        digest.update(ord(ch).to_bytes(4, "little"))
    return digest.hexdigest()


def strip_bom(text: str) -> str:
    return text[1:] if text.startswith("﻿") else text


class Report:
    def __init__(self) -> None:
        self.violations: list[str] = []
        self.notes: list[str] = []

    def fail(self, check: str, detail: str) -> None:
        self.violations.append(f"{check}: {detail}")

    def note(self, text: str) -> None:
        self.notes.append(text)

    def section(self, name: str, failures: int, total: int) -> None:
        mark = "OK   " if failures == 0 else "FAIL "
        print(f"  {mark} {name:<34} {total - failures}/{total}")


def check_coverage(corpus: str, rows: list[dict], report: Report) -> None:
    """Every file on disk is judged exactly once, and nothing else is."""
    source = CORPUS_ROOT / SOURCES[corpus]
    if not source.is_dir():
        report.note(f"{corpus}: corpus not present, coverage not checked")
        return

    on_disk = {
        p.relative_to(source).as_posix()
        for p in source.rglob("*")
        if p.is_file()
    }
    in_run = [r["RelativePath"] for r in rows]
    seen = set(in_run)

    duplicates = [p for p, n in Counter(in_run).items() if n > 1]
    missing = on_disk - seen
    invented = seen - on_disk

    for path in sorted(duplicates)[:5]:
        report.fail("coverage", f"{corpus}: {path} appears more than once")
    for path in sorted(missing)[:5]:
        report.fail("coverage", f"{corpus}: {path} on disk but not judged")
    for path in sorted(invented)[:5]:
        report.fail("coverage", f"{corpus}: {path} judged but not on disk")

    failures = len(duplicates) + len(missing) + len(invented)
    report.section(f"{corpus}: coverage", failures, len(on_disk))


def check_row_consistency(corpus: str, rows: list[dict], report: Report) -> None:
    """Each row's outcome has to agree with the evidence on that row."""
    failures = 0

    for r in rows:
        path, outcome = r["RelativePath"], r["FailureCategory"]

        if outcome not in PRIMARY_OUTCOMES:
            report.fail("outcome", f"{corpus}/{path}: unknown outcome {outcome!r}")
            failures += 1
            continue

        # PASS must mean the text hashes actually agree.
        if outcome == "PASS":
            if r["TextIdentical"] != "True":
                report.fail("outcome", f"{corpus}/{path}: PASS but TextIdentical={r['TextIdentical']!r}")
                failures += 1
            elif r["ReferenceTextSha256"] != r["ConvertedTextSha256"]:
                report.fail("outcome", f"{corpus}/{path}: PASS but text hashes differ")
                failures += 1

        # TextIdentical must agree with the hashes it claims to summarise.
        if r["TextIdentical"] == "True" and r["ReferenceTextSha256"] != r["ConvertedTextSha256"]:
            report.fail("hashes", f"{corpus}/{path}: TextIdentical=True but hashes differ")
            failures += 1
        if r["TextIdentical"] == "False" and r["ReferenceTextSha256"] \
                and r["ReferenceTextSha256"] == r["ConvertedTextSha256"]:
            report.fail("hashes", f"{corpus}/{path}: TextIdentical=False but hashes match")
            failures += 1

        # A scored row must have had ground truth to score against - except a
        # fixture the corpus declares as Binary, which has no writing codec by
        # definition and is scored on whether the detector correctly declined
        # it rather than on text preservation.
        declared_binary = r["ReferenceEncodingDeclared"] == "Binary"
        if outcome not in UNSCORED and not r["ReferenceEncoding"] and not declared_binary:
            report.fail("scope", f"{corpus}/{path}: scored as {outcome} with no reference encoding")
            failures += 1

        # A backup that exists must match the pre-conversion hash.
        if r["BackupIntegrity"] == "Verified" and r["BackupSha256"] != r["OriginalSha256"]:
            report.fail("backup", f"{corpus}/{path}: backup marked Verified but hash differs")
            failures += 1

        # Byte-identical means exactly that.
        if r["ByteIdentical"] == "True" and r["OriginalSha256"] != r["ConvertedSha256"]:
            report.fail("bytes", f"{corpus}/{path}: ByteIdentical=True but file hashes differ")
            failures += 1

        # A converted file that differs in bytes cannot also be a no-op.
        if outcome == "NoOpMislabeled" and r["ByteIdentical"] != "True":
            report.fail("outcome", f"{corpus}/{path}: NoOpMislabeled but bytes changed")
            failures += 1

        # --- detection taxonomy -------------------------------------------
        detection = r.get("DetectionOutcome", "")

        if detection not in DETECTION_OUTCOMES:
            report.fail("detection",
                        f"{corpus}/{path}: unknown detection outcome {detection!r}")
            failures += 1

        # Each outcome asserts something checkable about the same row.
        if detection == "ExactMatch" and r["DetectionMatch"] != "True":
            report.fail("detection",
                        f"{corpus}/{path}: ExactMatch but DetectionMatch="
                        f"{r['DetectionMatch']!r}")
            failures += 1

        if detection == "TextEquivalent":
            if r["DetectionMatch"] != "False":
                report.fail("detection",
                            f"{corpus}/{path}: TextEquivalent but the codecs match")
                failures += 1
            elif r["DetectionTextEquivalent"] != "True":
                report.fail("detection",
                            f"{corpus}/{path}: TextEquivalent without equivalent text")
                failures += 1

        # Ambiguity is a claim that neither codec constrained the bytes. A
        # multi-byte encoding on either side contradicts it.
        if detection == "StructurallyAmbiguous":
            for codec in (r["ReferenceEncoding"], r["DetectedEncoding"]):
                if structure_bearing(codec):
                    report.fail("detection",
                                f"{corpus}/{path}: StructurallyAmbiguous but "
                                f"{codec!r} is multi-byte and does constrain the bytes")
                    failures += 1
                    break

        if detection == "NoDotNetCodec" and r["ReferenceCodePage"]:
            report.fail("detection",
                        f"{corpus}/{path}: NoDotNetCodec but a code page is recorded")
            failures += 1

        if detection == "NoReference" and r["ReferenceEncoding"]:
            report.fail("detection",
                        f"{corpus}/{path}: NoReference but a reference codec is recorded")
            failures += 1

        # Nothing should be left uncompared without a reason.
        if (outcome not in UNSCORED and not declared_binary and outcome not in
                ("UnknownEncoding", "DecodeError", "EncodeError", "WriteError",
                 "MissingConvertedFile", "MissingBackup", "BackupIntegrityFailure")
                and r["TextIdentical"] not in ("True", "False")):
            report.fail("comparison",
                        f"{corpus}/{path}: {outcome} but text was never compared")
            failures += 1

    report.section(f"{corpus}: row consistency", failures, len(rows))


def check_hashes_independently(corpus: str, rows: list[dict], sample: int,
                               report: Report) -> None:
    """Recompute the reference text and its hash straight from the bytes.

    The audit records a hash of what it decoded. If the decode itself was
    wrong, that hash is self-consistent and proves nothing - so this decodes
    again here, from the original file, with no shared code.
    """
    source = CORPUS_ROOT / SOURCES[corpus]
    if not source.is_dir():
        return

    candidates = [r for r in rows if r["ReferenceEncoding"] and r["ReferenceTextSha256"]]
    if not candidates:
        report.section(f"{corpus}: independent hashes", 0, 0)
        return

    chosen = random.sample(candidates, min(sample, len(candidates)))
    failures = 0

    for r in chosen:
        original = source / r["RelativePath"]
        if not original.is_file():
            continue
        try:
            text = strip_bom(original.read_bytes().decode(r["ReferenceEncoding"]))
        except (UnicodeDecodeError, LookupError) as exc:
            report.fail("independent",
                        f"{corpus}/{r['RelativePath']}: recorded a reference hash "
                        f"but the codec cannot decode it now ({type(exc).__name__})")
            failures += 1
            continue

        if hash_text(text) != r["ReferenceTextSha256"]:
            report.fail("independent",
                        f"{corpus}/{r['RelativePath']}: reference hash does not "
                        f"reproduce from the original bytes")
            failures += 1
        if len(text) != int(r["ReferenceTextLength"]):
            report.fail("independent",
                        f"{corpus}/{r['RelativePath']}: reference length "
                        f"{r['ReferenceTextLength']} but recomputed {len(text)}")
            failures += 1

    report.section(f"{corpus}: independent hashes", failures, len(chosen))


def check_ground_truth(corpus: str, rows: list[dict], report: Report) -> None:
    """The declared encoding must resolve to the codec the row claims.

    This is the class of defect that read UTF-16 Belarusian as big-endian: a
    directory name parsed into the wrong codec, self-consistently, for every
    file underneath it.
    """
    failures = 0
    pairs: dict[tuple[str, str], int] = Counter()

    for r in rows:
        if not r["ReferenceEncoding"]:
            continue
        pairs[(r["ReferenceEncodingDeclared"], r["ReferenceEncoding"])] += 1

    for (declared, resolved), n in sorted(pairs.items()):
        # A declared token that is itself a valid codec must resolve to that
        # codec, unless a language suffix was legitimately stripped.
        try:
            direct = codecs.lookup(declared.strip().lower()).name
        except LookupError:
            continue
        if direct != resolved:
            if (declared.lower(), resolved) in ACKNOWLEDGED_AMBIGUITIES:
                report.note(
                    f"{corpus}: {declared!r} resolves to {resolved!r} for {n} "
                    f"file(s) - acknowledged language suffix, not a codec")
                continue
            report.fail(
                "ground-truth",
                f"{corpus}: {declared!r} is itself the codec {direct!r} but "
                f"resolved to {resolved!r} for {n} file(s) - confirm this is a "
                f"language suffix and not a codec being renamed")
            failures += 1

    report.section(f"{corpus}: ground truth", failures, max(len(pairs), 1))


def check_reconciliation(corpus: str, rows: list[dict], report: Report) -> None:
    """Counts must add up in both directions."""
    failures = 0
    outcomes = Counter(r["FailureCategory"] for r in rows)

    if sum(outcomes.values()) != len(rows):
        report.fail("reconcile", f"{corpus}: outcomes do not sum to row count")
        failures += 1

    # Every file whose text differs must carry an outcome that explains why.
    # The converse does not hold: NoOpMislabeled covers both a wrong label with
    # consequences (UTF-7 left as ASCII) and a harmless one, so counting it as a
    # text-difference cause in aggregate would not reconcile.
    diff_causes = {"Misdetection", "MappingDifference", "SilentDecodeLoss",
                   "NoOpMislabeled"}
    unexplained = [r for r in rows
                   if r["TextIdentical"] == "False"
                   and r["FailureCategory"] not in diff_causes]
    for r in unexplained[:5]:
        report.fail("reconcile",
                    f"{corpus}/{r['RelativePath']}: text differs but outcome is "
                    f"{r['FailureCategory']}, which does not explain a difference")
    failures += len(unexplained)

    report.section(f"{corpus}: reconciliation", failures, 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", nargs="?", default="audit/runs/validation")
    parser.add_argument("--sample", type=int, default=150)
    parser.add_argument("--seed", type=int, default=20260826)
    args = parser.parse_args()

    random.seed(args.seed)

    run = Path(args.run)
    paths = sorted(run.glob("*/audit.csv"))
    if not paths:
        print(f"no audit.csv under {run}", file=sys.stderr)
        return 2

    if not CORPUS_ROOT:
        print("CORPUS_ROOT is not set; on-disk checks will be skipped.",
              file=sys.stderr)

    report = Report()
    print(f"Integrity of {run}\n")

    total_rows = 0
    for path in paths:
        corpus = path.parent.name
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        total_rows += len(rows)

        check_coverage(corpus, rows, report)
        check_row_consistency(corpus, rows, report)
        check_ground_truth(corpus, rows, report)
        check_reconciliation(corpus, rows, report)
        check_hashes_independently(corpus, rows, args.sample, report)
        print()

    for note in report.notes:
        print(f"  note: {note}")
    if report.notes:
        print()

    if report.violations:
        print(f"{len(report.violations)} violation(s):\n")
        for line in report.violations[:60]:
            print(f"  - {line}")
        if len(report.violations) > 60:
            print(f"  ... and {len(report.violations) - 60} more")
        return 1

    print(f"All invariants hold across {total_rows} rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
