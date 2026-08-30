"""EC end-to-end conversion forensic audit.

Answers, per file, the core invariant:

    strict-decode(original .bak bytes, authoritative reference codec + BOM)
                  ==
    strict-decode(converted file bytes, UTF-8)

Exact Unicode code-point equality. No normalization of any kind, no
replacement fallbacks, and AlsoValidAs is never consulted: it records which
encodings are an acceptable *detector* answer, not which codec wrote the
bytes, so using it here would let a wrong decode pass as correct.

Source corpora are never touched. The audit copies a read-only source into a
working directory it creates itself, and refuses to convert anything else.

Usage:
    python audit.py --corpus uts3 --source <original> --work <copy>
                    [--target utf-8] [--strict] [--forced-reference]
                    [--label baseline] [--out <dir>]
"""
from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

AUDIT_VERSION = "1.0.0"

REPO = Path(__file__).resolve().parent
ECDIAG = REPO / "ECDiag" / "bin" / "Release" / "net8.0" / "ECDiag.exe"
# Where EncodingChecker is checked out and built. Set EC_REPO if your clone
# lives elsewhere; EC_EXE is derived from it unless set explicitly.
EC_REPO = Path(os.environ.get(
    "EC_REPO", REPO.parent.parent / "EncodingChecker-master"))
EC_EXE = Path(os.environ.get(
    "EC_EXE",
    EC_REPO / "sources" / "EncodingChecker" / "bin" / "Release"
    / "net10.0-windows" / "EncodingChecker.exe"))
EC_ASSEMBLY = EC_EXE.with_suffix(".dll")

# Exit codes (section 22).
EXIT_OK = 0
EXIT_REGRESSION = 1
EXIT_INFRASTRUCTURE = 2

# Encodings probed in PHASE 0. Anything a corpus declares is added at runtime.
PHASE0_BASELINE = [
    "utf-8", "us-ascii", "utf-16le", "utf-16be", "utf-32le", "utf-32be",
    "euc-jp", "shift_jis", "big5", "gb18030", "gb2312", "euc-kr", "iso-2022-kr",
    "windows-1252", "iso-8859-1", "koi8-r",
]

# EC never descends into these (DirectoryTraversal.ExcludedDirectoryNames), and
# never reads ".bak"/temp files. Mirrored here so files EC declines by design
# are reported as OutOfScope instead of masquerading as MissingConvertedFile.
EC_EXCLUDED_DIRS = {
    ".git", ".svn", ".hg", ".vs", ".idea", "bin", "obj",
    "node_modules", "packages", "dist", "build", "target",
}

# Repository housekeeping that ships inside the corpora but declares no
# reference encoding. Converting it would prove nothing either way.
OUT_OF_SCOPE_NAMES = {
    "readme.md", "catalog.md", "claude.md", ".gitignore", ".gitattributes",
    "license", "license.md", "license-corpus", "pyproject.toml",
}
OUT_OF_SCOPE_DIRS = {"scripts"}

# Corpus buckets that explicitly declare "no encoding" rather than naming one.
NO_REFERENCE_TOKENS = {"none", "none-none"}

# Directory-name tokens that are not codec names.
CHARDET_ALIASES = {
    "shift-jis": "shift_jis", "ks_c_5601-1987": "euc_kr",
    "x-mac-ce": "mac_latin2", "x-mac-cyrillic": "mac_cyrillic",
    "hz-gb-2312": "hz", "x-cp50227": "iso2022_cn", "utf-8-sig": "utf-8-sig",
}


# --------------------------------------------------------------------------
# Evidence records
# --------------------------------------------------------------------------

@dataclass
class InventoryRow:
    Corpus: str
    RelativePath: str
    OriginalSize: int
    OriginalSha256: str
    OriginalLastWriteUtc: str
    ReferenceEncodingDeclared: str
    ReferenceEncoding: str
    ReferenceBOM: str
    ReferenceMetadataSource: str


@dataclass
class AuditRow:
    Corpus: str = ""
    RelativePath: str = ""

    ReferenceEncodingDeclared: str = ""
    ReferenceEncoding: str = ""
    ReferenceBOM: str = ""
    ReferenceMetadataSource: str = ""

    ReferenceCodePage: str = ""
    DetectedEncoding: str = ""
    DetectedCodePage: str = ""
    DetectedBOM: str = ""
    DetectionMatch: str = ""
    DetectionBasis: str = ""
    DetectionLabelExact: str = ""
    DetectionTextEquivalent: str = ""

    # Detection taxonomy: one of ExactMatch, TextEquivalent,
    # StructurallyAmbiguous, Misdetection, NoDotNetCodec, NoReference,
    # NotIdentified. Separated from the pass/fail above because "the detector
    # was wrong" and "the bytes could not identify the file" are different
    # claims and were previously one number.
    DetectionOutcome: str = ""
    PlausibleCodecCount: int = -1
    DistinctReadings: int = -1
    PlausibleCodecs: str = ""
    ReferenceConstraint: float = -1.0

    ConversionStatus: str = ""
    ConversionErrorStage: str = ""
    ConversionErrorType: str = ""
    ConversionErrorMessage: str = ""

    # EC's stable machine-readable reason for a non-success outcome, and the
    # source codec this audit supplied through -From (empty when EC detected).
    ECReasonCode: str = ""
    ECExplicitSource: str = ""

    # "True" when the corpus's reference codec is one EC can name under no
    # spelling, so EC was never given the chance to convert from it.
    ECSourceUnsupported: str = ""

    OriginalByteLength: int = 0
    BackupByteLength: int = 0
    ConvertedByteLength: int = 0
    OriginalSha256: str = ""
    BackupSha256: str = ""
    ConvertedSha256: str = ""
    BackupIntegrity: str = ""
    ByteIdentical: str = ""

    ReferenceTextLength: int = -1
    ConvertedTextLength: int = -1
    TextIdentical: str = ""
    ReferenceTextSha256: str = ""
    ConvertedTextSha256: str = ""

    # PHASE 0 / defect evidence.
    DecoderStrictness: str = ""
    EncoderStrictness: str = ""
    ECProductionTextSha256: str = ""
    ECStrictDecodeOutcome: str = ""
    ECImplementationDefect: str = ""

    FailureCategory: str = ""

    FirstDifferenceIndex: int = -1
    ReferenceCodePoint: str = ""
    ConvertedCodePoint: str = ""
    ReferenceChar: str = ""
    ConvertedChar: str = ""
    ContextBefore: str = ""
    ContextAfter: str = ""

    ForcedReferenceOutcome: str = ""
    CorpusRoundTrip: str = ""


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    """SHA-256 over code points as little-endian uint32. No normalization."""
    digest = hashlib.sha256()
    for ch in text:
        digest.update(ord(ch).to_bytes(4, "little"))
    return digest.hexdigest()


def strip_bom(text: str) -> str:
    """A BOM is an encoding artifact, not text content."""
    return text[1:] if text.startswith("\ufeff") else text


# Byte-order pairs, for telling an endianness mislabel from a corrupt fixture.
ENDIAN_PARTNER = {
    "utf-16-be": "utf-16-le", "utf-16-le": "utf-16-be",
    "utf-32-be": "utf-32-le", "utf-32-le": "utf-32-be",
}


def opposite_endian_decodes(data: bytes, codec: str) -> bool:
    """True when the other byte order reads this file and the declared one cannot."""
    partner = ENDIAN_PARTNER.get(codec)
    if partner is None:
        return False
    try:
        return not data.decode(partner).startswith("\ufffe")
    except (UnicodeDecodeError, LookupError):
        return False


def resolve_codec(name: str) -> str | None:
    """Canonical Python codec for a declared name, or None.

    A trailing parenthetical is an annotation, not part of the codec name:
    UTF.unknown's corpus ships "windows-1252 (latin1)".
    """
    if not name:
        return None
    token = name.strip().lower()
    if token.endswith(")") and "(" in token:
        token = token[:token.rindex("(")].strip()
    token = CHARDET_ALIASES.get(token, token)
    try:
        # The registry's own canonical name, so aliases of one codec (ibm866 /
        # cp866) compare equal instead of looking like different encodings.
        return codecs.lookup(token).name
    except LookupError:
        return None


# Python codec names .NET does not recognise under any mechanical rewrite.
# Keyed by the name with all separators removed, so "mac_roman" and
# "mac-roman" - both of which Python uses - resolve identically.
NET_NAME_HINTS = {
    "cp949": "ks_c_5601-1987",
    "maclatin2": "x-mac-ce",
    "maccyrillic": "x-mac-cyrillic",
    "macroman": "macintosh",
    "macgreek": "x-mac-greek",
    "macturkish": "x-mac-turkish",
    "maciceland": "x-mac-icelandic",
    "hz": "hz-gb-2312",
    "cp932": "shift_jis",
}


def net_name_candidates(*names: str) -> list[str]:
    """Spellings of a codec that .NET might recognise, best guess first.

    Python and .NET disagree on spelling far more often than on identity
    ("cp949" vs "ks_c_5601-1987"), and comparing raw labels would score those
    disagreements as detection errors. Every candidate is offered to .NET and
    the first one it can construct decides the code page.
    """
    out: list[str] = []
    seen: set[str] = set()

    def push(candidate: str) -> None:
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)

    for name in names:
        if not name:
            continue
        base = name.strip()
        low = base.lower()
        push(base)
        push(NET_NAME_HINTS.get(low.replace("_", "").replace("-", ""), ""))
        push(low.replace("_", "-"))
        push(low.replace("-", "_"))

        # "utf-16-le" -> "utf-16le"; .NET rejects the fully separated form.
        push(re.sub(r"[-_](le|be)$", r"\1", low))

        # "utf-8-sig" names a BOM policy rather than a codec. The BOM is
        # recorded separately, so what has to resolve is the codec underneath.
        if low.endswith("-sig"):
            push(low[:-4])

        # "iso2022_jp" -> "iso-2022-jp", "iso8859-15" -> "iso-8859-15".
        if m := re.fullmatch(r"iso(\d{4})[-_]?(\w+)", low):
            push(f"iso-{m.group(1)}-{m.group(2)}")
        if m := re.fullmatch(r"cp(\d+)", low):
            push(f"windows-{m.group(1)}")
            push(f"ibm{m.group(1)}")
        if m := re.fullmatch(r"(?:windows|ibm)[-_]?(\d+)", low):
            push(f"cp{m.group(1)}")

    return out


def resolve_directory_codec(
        directory: str,
        language_codes: frozenset[str] = frozenset()) -> tuple[str | None, str]:
    """chardet-style '{encoding}' or '{encoding}-{language}' directory names.

    Returns (canonical codec, declared token).

    The trailing language tag has to be stripped before the longest prefix
    wins, because two directory names are both a valid codec AND a valid
    encoding+language pair: 'utf-16-be' is UTF-16 Belarusian, not UTF-16
    Big Endian, and likewise 'utf-32-be'. Taking the longest match there reads
    little-endian files as big-endian, which looks exactly like a corpus
    mislabel and is not one.

    `language_codes` is derived from the corpus itself - a trailing segment is
    treated as a language only where it also tags directories whose encoding
    prefix is unambiguous - so nothing depends on a hardcoded language list.
    """
    token = directory.strip().lower()
    if token in ("none-none", ""):
        return None, directory

    parts = token.split("-")

    if len(parts) > 1 and parts[-1] in language_codes:
        resolved = resolve_codec("-".join(parts[:-1]))
        if resolved:
            return resolved, directory

    for cut in range(len(parts), 0, -1):
        candidate = "-".join(parts[:cut])
        resolved = resolve_codec(candidate)
        if resolved:
            return resolved, directory

    return None, directory


def discover_language_codes(root: Path) -> frozenset[str]:
    """Trailing segments the corpus itself uses as language tags.

    A segment counts as a language only when it tags a directory whose encoding
    prefix resolves without needing that segment - so 'be' qualifies because it
    also appears on windows-1251-be and friends, while a segment that only ever
    appears in one ambiguous name does not.
    """
    counts: Counter = Counter()

    for path in root.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        parts = path.name.lower().split("-")
        for cut in range(len(parts) - 1, 0, -1):
            if resolve_codec("-".join(parts[:cut])):
                counts["-".join(parts[cut:])] += 1
                break

    # Two independent directories is enough to establish a tag as a language
    # rather than part of a codec name.
    return frozenset(tag for tag, n in counts.items() if n >= 2)


# --------------------------------------------------------------------------
# Ground-truth resolvers (section 4 / appendix C)
# --------------------------------------------------------------------------

class ReferenceResolver:
    """Authoritative reference encoding per corpus. Independent of EC."""

    def __init__(self, corpus: str, root: Path):
        self.corpus = corpus
        self.root = root
        self.manifest: dict[str, dict[str, str]] = {}
        self.conflicts: list[tuple[str, str, str]] = []

        self.language_codes: frozenset[str] = frozenset()
        if corpus == "chardet" and root.is_dir():
            self.language_codes = discover_language_codes(root)

        if corpus == "uts3":
            manifest_path = root / "Manifest.csv"
            if not manifest_path.is_file():
                raise FileNotFoundError(f"{manifest_path} not found")
            with manifest_path.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    self.manifest[row["RelativePath"]] = row

    def resolve(self, rel: Path) -> tuple[str, str | None, str, str]:
        """Returns (declared, canonical codec or None, BOM, metadata source)."""
        posix = rel.as_posix()

        lowered = [p.lower() for p in rel.parts]
        if EC_EXCLUDED_DIRS.intersection(lowered[:-1]):
            return "", None, "", "ExcludedByEC"

        if self.corpus == "uts3":
            # The manifest is the authoritative fixture inventory: membership
            # decides scope, so no filename heuristic can drop a real fixture.
            row = self.manifest.get(posix)
            if row is None:
                return "", None, "", "NotInManifest"

            declared = row["Encoding"]
            bom = row.get("BOM", "")

            if declared == "Binary":
                # No writing codec exists for the binary fixtures.
                return declared, None, bom, "Manifest"

            canonical = resolve_codec(declared)

            # Cross-check against the filename token, which the corpus
            # guarantees at index 4. Disagreement is MetadataConflict, never
            # a guess about which source to trust.
            source = "Manifest"
            stem = rel.name.rsplit(".", 1)[0]
            tokens = stem.split("_")
            if len(tokens) >= 5 and rel.name.startswith("DOC"):
                from_name = resolve_codec(tokens[4])
                if from_name and canonical and from_name != canonical:
                    self.conflicts.append((posix, declared, tokens[4]))
                    return declared, None, bom, "MetadataConflict"
                if from_name:
                    source = "Manifest+Filename"

            return declared, canonical, bom, source

        # The manifest-less corpora carry repository housekeeping alongside the
        # fixtures. Those files declare no encoding, so they are out of scope
        # rather than audit failures.
        if (OUT_OF_SCOPE_DIRS.intersection(lowered[:-1])
                or rel.name.lower() in OUT_OF_SCOPE_NAMES):
            return "", None, "", "OutOfScope"

        # A leading underscore marks a sidecar directory rather than fixtures of
        # the enclosing encoding: chardet's cp864-ar/_logical_source holds the
        # logical-order UTF-8 text its shaped cp864 files were produced from, as
        # its CATALOG.md documents. Inheriting the parent's encoding here would
        # invent a ground truth the corpus never claimed.
        if any(part.startswith("_") for part in rel.parts[:-1]):
            return "", None, "", "SidecarDirectory"

        if self.corpus == "chardet":
            # This repo's CATALOG.md defines the top-level directory as
            # "{encoding}" or "{encoding}-{language}".
            if len(rel.parts) < 2:
                return "", None, "", "OutOfScope"     # loose file at the root
            token = rel.parts[0]
            source = "TopLevelDirectory"
        else:
            # charsetnormalizer / utfunknown26: immediate parent directory.
            token = rel.parent.name
            if not token:
                return "", None, "", "OutOfScope"
            source = "ParentDirectory"

        # An explicit "no encoding" bucket is a declaration, not a lookup
        # failure, and must not be conflated with a codec the audit lacks.
        if token.strip().lower() in NO_REFERENCE_TOKENS:
            return token, None, "", "NoReferenceEncoding"

        canonical, declared = resolve_directory_codec(token, self.language_codes)
        return declared, canonical, "", source


# --------------------------------------------------------------------------
# ECDiag bridge
# --------------------------------------------------------------------------

def run_ecdiag(mode: str, items: list[dict], encodings: list[str] | None = None) -> list[dict]:
    if not ECDIAG.is_file():
        raise RuntimeError(f"ECDiag not built: {ECDIAG}")

    request = {"Mode": mode, "Items": items, "Encodings": encodings or []}
    proc = subprocess.run(
        [str(ECDIAG)], input=json.dumps(request),
        capture_output=True, text=True, encoding="utf-8")

    if proc.returncode != 0:
        raise RuntimeError(f"ECDiag failed ({proc.returncode}): {proc.stderr[:400]}")

    return json.loads(proc.stdout) if proc.stdout.strip() else []


def run_ecdiag_batched(mode: str, items: list[dict], batch: int = 400) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(items), batch):
        out.extend(run_ecdiag(mode, items[i:i + batch]))
    return out


# --------------------------------------------------------------------------
# Phases
# --------------------------------------------------------------------------

def prepare_working_copy(source: Path, work: Path) -> None:
    """Copy the read-only source. The audit only ever converts its own copy."""
    if not source.is_dir():
        raise FileNotFoundError(f"source corpus not found: {source}")
    if work.exists():
        shutil.rmtree(work)
    work.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, work)


def build_inventory(corpus: str, work: Path, resolver: ReferenceResolver) -> list[InventoryRow]:
    rows: list[InventoryRow] = []
    for path in sorted(p for p in work.rglob("*") if p.is_file()):
        rel = path.relative_to(work)
        if rel.name.endswith(".bak"):
            continue
        data = path.read_bytes()
        declared, canonical, bom, source = resolver.resolve(rel)
        rows.append(InventoryRow(
            Corpus=corpus,
            RelativePath=rel.as_posix(),
            OriginalSize=len(data),
            OriginalSha256=sha256_bytes(data),
            OriginalLastWriteUtc=datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc).isoformat(),
            ReferenceEncodingDeclared=declared,
            ReferenceEncoding=canonical or "",
            ReferenceBOM=bom,
            ReferenceMetadataSource=source,
        ))
    return rows


# EC names codecs the IANA way; Python's canonical names often differ, and
# .NET's alias table is not self-consistent - "cp1252" resolves, "cp1251" does
# not. A hard-coded translation table would rot silently against a .NET upgrade,
# so ask EC itself once per codec and cache the answer.
_EC_CODEC_CACHE: dict[str, str | None] = {}


def _ec_codec_candidates(name: str) -> list[str]:
    """Plausible EC spellings of a Python canonical codec name, best first."""
    candidates = [name, name.replace("_", "-")]

    page = re.fullmatch(r"cp(\d+)", name)
    if page:
        number = int(page.group(1))
        if 1250 <= number <= 1258:
            candidates.append(f"windows-{number}")
        candidates.append(f"ibm{number}")

    # Python names several CJK codecs by code page where EC uses the charset
    # name. Verified against EC rather than assumed: cp932 resolves under no
    # generated spelling without these, and those files would then fall back to
    # detection and be refused, quietly costing measurable coverage.
    # Python spells the Unicode variants with a separator EC does not use, and
    # names the BOM variant separately. Missing these cost 1,344 files: they were
    # marked unmeasurable when EC supports every one of them natively.
    DIRECT = {
        "utf-16-le": "utf-16le", "utf-16-be": "utf-16be",
        "utf-32-le": "utf-32le", "utf-32-be": "utf-32be",
        "utf-8-sig": "utf-8",
        "mac-cyrillic": "x-mac-cyrillic", "mac-roman": "macintosh",
        "mac-latin2": "x-mac-ce", "mac-greek": "x-mac-greek",
        "mac-turkish": "x-mac-turkish", "mac-iceland": "x-mac-icelandic",
        "hz": "hz-gb-2312",
    }
    if name in DIRECT:
        candidates.append(DIRECT[name])

    BY_CODE_PAGE = {
        "cp932": "shift_jis",
        "cp936": "gb2312",
        "cp949": "ks_c_5601-1987",
        "cp950": "big5",
        "cp874": "windows-874",
        "cp65001": "utf-8",
    }
    if name in BY_CODE_PAGE:
        candidates.append(BY_CODE_PAGE[name])

    # "iso2022_jp" -> "iso-2022-jp". The plain underscore swap yields
    # "iso2022-jp", which EC does not accept.
    stateful = re.fullmatch(r"iso2022[-_](.+)", name)
    if stateful:
        candidates.append("iso-2022-" + stateful.group(1).replace("_", "-"))

    part = re.fullmatch(r"iso8859-(\d+)", name)
    if part:
        candidates.append(f"iso-8859-{part.group(1)}")

    if name.startswith("mac_"):
        candidates.append("x-mac-" + name[4:])

    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


# EC converts these automatically; a legacy source is what needs naming.
_UNAIDED = {"ascii", "us-ascii", "utf-8", "utf-8-sig",
            "utf-16", "utf-16-le", "utf-16-be", "utf-16le", "utf-16be",
            "utf-32", "utf-32-le", "utf-32-be", "utf-32le", "utf-32be"}


def _ec_converts_unaided(codec: str) -> bool:
    """Whether EC converts from this codec without being told the source."""
    return codec.lower().replace("_", "-") in _UNAIDED


def resolve_ec_codec(name: str, probe_dir: Path) -> str | None:
    """The spelling EC accepts for this codec, or None if it accepts none."""
    if name in _EC_CODEC_CACHE:
        return _EC_CODEC_CACHE[name]

    resolved: str | None = None
    for candidate in _ec_codec_candidates(name):
        proc = subprocess.run(
            [str(EC_EXE), "-BasePath", str(probe_dir), "-Target", "utf-8",
             "-From", candidate, "-Quiet"],
            capture_output=True, text=True)

        # Require positive evidence, not the absence of a rejection message.
        # EC exits 0 having processed nothing when the name is good. Anything
        # else - a usage error, or a crash such as .NET refusing to construct
        # UTF-7 at all - means this spelling is unusable, whatever it printed.
        if proc.returncode == 0:
            resolved = candidate
            break

    _EC_CODEC_CACHE[name] = resolved
    return resolved


# Windows caps a command line near 32 KB. Stay well inside it: exceeding it
# would fail the batch rather than convert fewer files, so the margin is cheap.
INCLUDE_BATCH_BUDGET = 24_000


def _batch_includes(relatives: list[str]):
    """Split relative paths into batches that fit one command line."""
    batch: list[str] = []
    size = 0

    for rel in relatives:
        cost = len(rel) + 12          # "-Include", separators, quoting
        if batch and size + cost > INCLUDE_BATCH_BUDGET:
            yield batch
            batch, size = [], 0
        batch.append(rel)
        size += cost

    if batch:
        yield batch


def _merge_reports(report: Path, results: dict[str, dict[str, str]]) -> None:
    """Write one combined report at the documented path."""
    if not results:
        return

    fields = [key for key in next(iter(results.values())) if not key.startswith("_")]

    with report.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for rel in sorted(results):
            writer.writerow({key: results[rel].get(key, "") for key in fields})


def run_conversion(work: Path, target: str, report: Path,
                   inventory: list[InventoryRow],
                   explicit_source: bool = True) -> dict[str, dict[str, str]]:
    """Convert the working copy with backups and read EC's reports.

    From v3.9.0 EC converts automatically only from Unicode and ASCII; legacy
    text is refused unless the caller names the source codec. Running detection
    alone therefore measures nothing about conversion - every legacy file comes
    back refused and untouched - so by default the audit supplies each corpus's
    own reference codec through -From, grouped so one run carries one codec.

    That is a deliberate change of question. Detection-only asks "what does EC
    do unaided?", and the answer is now "declines, safely". Supplying the codec
    asks "when told what the bytes are, does EC preserve the text?", which is
    the property the corpora can actually adjudicate. Pass --no-explicit-source
    to ask the first question instead.

    Files whose reference codec is unknown, or which EC cannot name, are still
    run on detection alone.
    """
    probe_dir = report.parent / "_ec-codec-probe"
    probe_dir.mkdir(parents=True, exist_ok=True)

    groups: dict[str, list[str]] = defaultdict(list)
    for inv in inventory:
        codec = inv.ReferenceEncoding if explicit_source else ""
        groups[codec or ""].append(inv.RelativePath)

    if not groups:
        raise RuntimeError("EC was never run: the inventory is empty.")

    results: dict[str, dict[str, str]] = {}
    part = 0

    for codec in sorted(groups):
        ec_codec = resolve_ec_codec(codec, probe_dir) if codec else None

        for batch in _batch_includes(sorted(groups[codec])):
            part += 1
            part_report = report.with_name(
                f"{report.stem}.{part:04d}{report.suffix}")

            command = [str(EC_EXE), "-BasePath", str(work),
                       "-Target", target, "-Backup",
                       "-Report", str(part_report), "-Quiet"]

            for rel in batch:
                command += ["-Include", rel]

            if ec_codec:
                command += ["-From", ec_codec]

            proc = subprocess.run(command, capture_output=True, text=True)

            if not part_report.is_file():
                raise RuntimeError(
                    f"EC produced no report for "
                    f"{codec or '(detection only)'} "
                    f"(exit {proc.returncode}): {proc.stderr[:400]}")

            with part_report.open(encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    rel = Path(row["File"]).relative_to(work).as_posix()
                    # Underscore-prefixed keys are audit bookkeeping and are
                    # stripped before the merged report is written.
                    row["_ExplicitSource"] = ec_codec or ""
                    # A reference codec EC cannot name is a limit on what this
                    # audit can ask, not a finding about EC.
                    # Only when EC actually needed the name. EC converts
                    # Unicode and ASCII sources unaided, so an unresolvable
                    # spelling costs nothing there and the comparison stands.
                    row["_SourceUnsupported"] = str(
                        bool(codec) and explicit_source and ec_codec is None
                        and not _ec_converts_unaided(codec))
                    results[rel] = row

    _merge_reports(report, results)
    return results


def phase0(encodings: list[str]) -> dict[str, dict]:
    """Determine what the EC build's codecs actually do (PHASE 0)."""
    probes = run_ecdiag("strictness", [], encodings=sorted(set(encodings)))
    return {p["Encoding"]: p for p in probes}


def constraint_on(data: bytes, codec: str, trials: int = 48,
                  limit: int = 64 * 1024) -> float:
    """How tightly this codec constrains *these particular bytes*.

    Returns the fraction of single-byte mutations the codec rejects. A file
    whose validity survives every mutation was never evidence of anything - any
    byte sequence would have decoded. A file where a third of mutations break
    the decode is valid for a reason, and that reason is evidence.

    Measured per file rather than per codec on purpose. "Single-byte codecs are
    unconstrained" is a useful generalisation and a false rule: windows-1252
    leaves 0x81, 0x8D, 0x8F, 0x90 and 0x9D undefined, so a byte sequence
    containing one of them *is* structural evidence against it. Asking the
    question of the actual bytes catches that; asking it of the codec does not.
    """
    sample = data[:limit]
    if not sample:
        return 0.0

    rnd = random.Random(0xC0DEC)          # fixed: the answer must not drift
    rejected = 0
    for _ in range(trials):
        index = rnd.randrange(len(sample))
        mutated = bytearray(sample)
        mutated[index] ^= rnd.choice((0x80, 0x40, 0x20, 0x01))
        try:
            bytes(mutated).decode(codec)
        except (UnicodeDecodeError, LookupError, ValueError):
            rejected += 1
    return rejected / trials


# Below this, a codec has effectively no hold on the input: nearly any byte
# sequence decodes, so the file being valid says nothing about which codec
# wrote it. Deliberately low - the claim "the bytes could not have decided"
# should be made only when they clearly could not.
CONSTRAINT_FLOOR = 0.10


def is_structure_bearing(codec: str) -> bool:
    """Whether this codec constrains the *sequence* of bytes, not just their values.

    A multi-byte codec imposes structure: valid EUC-JP or UTF-8 is improbable by
    accident, so a file decoding cleanly under one is real evidence for it. A
    single-byte codec imposes almost none - it maps 256 values independently, so
    nearly every byte sequence is "valid" and being valid says nothing.

    That difference is what separates a detector that ignored available evidence
    from one facing a genuinely undecidable choice, and it is why single-byte
    code pages are the hazard: windows-1252 text is perfectly good iso-8859-1
    text and no amount of looking at the bytes will say which was meant.

    Determined by asking the codec, not from a list, so a codec this audit has
    never seen is still classified correctly.
    """
    try:
        # A non-ASCII character needing more than one byte means the encoding
        # carries sequence structure.
        for ch in ("é", "世", "Ж"):
            try:
                if len(ch.encode(codec)) > 1:
                    return True
            except (UnicodeEncodeError, LookupError):
                continue
    except Exception:
        return False
    return False


def plausible_codecs(data: bytes, candidates: tuple[str, ...],
                     limit: int = 64 * 1024) -> list[str]:
    """Codecs that decode these bytes without error, in candidate order.

    The evidence available to a detector, expressed as a set. A byte sequence
    that decodes cleanly under eight single-byte code pages carries no
    *structural* signal distinguishing them - only statistical heuristics can
    separate those eight, and heuristics are exactly what is unreliable on short
    or ASCII-heavy input.

    This is what makes "the detector was wrong" and "the file could not be
    identified from its bytes" different claims rather than one number.

    Capped at `limit` bytes: a codec that decodes the first 64 KiB cleanly is
    plausible enough for this purpose, and decoding whole large files under
    forty codecs would dominate the run.
    """
    sample = data[:limit]
    if not sample:
        return []

    found: list[str] = []
    for codec in candidates:
        try:
            sample.decode(codec)
        except (UnicodeDecodeError, LookupError, ValueError):
            continue
        found.append(codec)
    return found


def distinguishable_by_text(data: bytes, codecs_: list[str],
                            limit: int = 64 * 1024) -> int:
    """How many *distinct texts* the plausible codecs produce.

    Two codecs that decode the same bytes to the same text are not a hazard, so
    ambiguity is counted in distinct readings rather than in candidate names.
    """
    sample = data[:limit]
    texts = set()
    for codec in codecs_:
        try:
            texts.add(sample.decode(codec))
        except (UnicodeDecodeError, LookupError, ValueError):
            continue
    return len(texts)


def net_name_for(strictness: dict[str, dict], *names: str) -> str | None:
    """First spelling of these names that .NET can actually construct."""
    for candidate in net_name_candidates(*names):
        probe = strictness.get(candidate)
        if probe and probe.get("Available"):
            return candidate
    return None


def code_page_of(strictness: dict[str, dict], *names: str) -> int | None:
    """First .NET code page any spelling of these names resolves to."""
    for candidate in net_name_candidates(*names):
        probe = strictness.get(candidate)
        if probe and probe.get("Available") and probe.get("CodePage"):
            return int(probe["CodePage"])
    return None


# --------------------------------------------------------------------------
# Classification (section 28 precedence)
# --------------------------------------------------------------------------

def first_difference(a: str, b: str) -> tuple[int, str, str, str, str, str, str]:
    limit = min(len(a), len(b))
    idx = next((i for i in range(limit) if a[i] != b[i]), limit)

    def at(text: str, i: int) -> tuple[str, str]:
        if i >= len(text):
            return "", ""
        ch = text[i]
        return f"U+{ord(ch):04X}", ch

    ref_cp, ref_ch = at(a, idx)
    con_cp, con_ch = at(b, idx)
    before = a[max(0, idx - 20):idx]
    after = a[idx + 1:idx + 21]
    return idx, ref_cp, con_cp, ref_ch, con_ch, before, after


def classify_detection(row: AuditRow, inv: InventoryRow,
                       detected_codec: str | None, reference_text: str | None,
                       original_bytes: bytes,
                       candidates: tuple[str, ...]) -> None:
    """Place the detection result in the taxonomy, and record the evidence.

    Seven outcomes, because a single accuracy figure was averaging together
    situations that call for different responses:

      ExactMatch             the codec that wrote the file, under any spelling
      TextEquivalent         a different codec, same resulting text
      StructurallyAmbiguous  a different reading, but nothing in the bytes could
                             have told the detector which was right
      Misdetection           a different reading where the bytes did carry a
                             distinguishing signal
      NoDotNetCodec          the reference encoding has no code page at all, so
                             no detector on this platform could have named it
      NoReference            no authoritative encoding to judge against
      NotIdentified          the detector named nothing and the file was skipped
    """
    if not inv.ReferenceEncoding:
        row.DetectionOutcome = "NoReference"
        return

    if not row.ReferenceCodePage:
        # EC cannot construct this encoding, so naming it was never possible.
        row.DetectionOutcome = "NoDotNetCodec"
        return

    if not detected_codec:
        row.DetectionOutcome = "NotIdentified"
        return

    if row.DetectionMatch == "True":
        row.DetectionOutcome = "ExactMatch"
        return

    if row.DetectionTextEquivalent == "True":
        row.DetectionOutcome = "TextEquivalent"
        return

    # The readings differ. Whether that is the detector's fault depends on
    # whether the bytes carried anything to distinguish them.
    plausible = plausible_codecs(original_bytes, candidates)
    row.PlausibleCodecCount = len(plausible)
    row.DistinctReadings = distinguishable_by_text(original_bytes, plausible)
    row.PlausibleCodecs = ",".join(plausible[:8])

    # Whether the bytes carried evidence is asked of the bytes, per file.
    #
    # Two independent signals count as evidence. Either the codec EC chose
    # cannot actually read the whole file - it passed on a sample and fails on
    # the rest - or the reference codec demonstrably constrains this input, so
    # the file being valid under it was not an accident.
    try:
        original_bytes.decode(detected_codec)
        detected_accepts = True
    except (UnicodeDecodeError, LookupError, ValueError):
        detected_accepts = False

    row.ReferenceConstraint = round(
        constraint_on(original_bytes, inv.ReferenceEncoding), 3)

    evidence_available = (not detected_accepts
                          or row.ReferenceConstraint >= CONSTRAINT_FLOOR)

    row.DetectionOutcome = ("Misdetection" if evidence_available
                            else "StructurallyAmbiguous")


def classify(row: AuditRow) -> str:
    """Deterministic precedence. SilentDecodeLoss outranks MappingDifference."""
    # Scope first: a file the audit has no standing to judge must never be
    # scored, in either direction.
    if row.ReferenceMetadataSource in ("OutOfScope", "NotInManifest",
                                      "ExcludedByEC", "SidecarDirectory"):
        return "OutOfScope"
    if row.ReferenceMetadataSource == "NoReferenceEncoding":
        return "NoReferenceEncoding"

    if row.ReferenceMetadataSource == "MetadataConflict":
        return "MetadataConflict"
    if not row.ReferenceEncoding and row.ReferenceEncodingDeclared \
            and row.ReferenceEncodingDeclared != "Binary":
        # The corpus names a codec this audit cannot construct (euc-tw,
        # viscii). Ground truth is unavailable, so no verdict is possible.
        return "UnknownReferenceEncoding"
    if row.ECSourceUnsupported == "True":
        # The mirror of the case above: the codec exists here, but EC can name
        # it under no spelling, so EC was never asked to convert from it. Since
        # v3.9.0 EC needs the source named for legacy text; when it cannot
        # accept the name, the file falls back to detection and the comparison
        # measures the detector, not the conversion.
        #
        # Reported and never scored, in both directions. Scoring the failures
        # would blame EC for a conversion it was never offered - 145 UTF-7
        # files landed as NoOpMislabeled this way - and scoring the passes
        # while excluding the failures would be the more flattering half of the
        # same mistake.
        return "ECCodecUnsupported"
    if row.BackupIntegrity == "Mismatch":
        return "BackupIntegrityFailure"
    if row.BackupIntegrity == "Missing":
        return "MissingBackup"
    if row.ConversionStatus == "MissingConvertedFile":
        return "MissingConvertedFile"
    if row.FailureCategory in ("ReferenceDecodeError", "CorpusByteOrderMislabel"):
        return row.FailureCategory
    if row.ConversionStatus == "Skipped":
        return "UnknownEncoding"
    if row.ConversionStatus == "Refused":
        # EC declined by policy and left the file byte-identical. Nothing was
        # converted, so there is no conversion to judge - and the text was
        # never compared, so any verdict about fidelity here would be invented.
        return "RefusedByPolicy"
    if row.ConversionStatus == "Error":
        stage = row.ConversionErrorStage
        return {"Decode": "DecodeError", "Encode": "EncodeError",
                "Write": "WriteError"}.get(stage, "DecodeError")

    if row.TextIdentical == "True":
        if row.ByteIdentical == "True" and row.DetectionMatch == "False":
            # Bytes unchanged and text still matches: harmless, but the file
            # was not genuinely re-encoded.
            return "NoOpMislabeled"
        return "PASS"

    # Text differs.
    if row.ByteIdentical == "True":
        return "NoOpMislabeled"

    # EC accepted bytes a correctly-constructed strict decoder rejects.
    if row.ECStrictDecodeOutcome == "Throws" and row.DetectionMatch == "True":
        return "SilentDecodeLoss"

    if row.DetectionMatch == "False":
        return "Misdetection"

    return "MappingDifference"


# --------------------------------------------------------------------------
# Main audit
# --------------------------------------------------------------------------

def audit_corpus(args, out_dir: Path) -> tuple[list[AuditRow], dict, dict]:
    work = Path(args.work).resolve()
    source = Path(args.source).resolve()

    prepare_working_copy(source, work)

    resolver = ReferenceResolver(args.corpus, work)
    inventory = build_inventory(args.corpus, work, resolver)

    report_csv = out_dir / "logs" / "ec-conversion-report.csv"
    report_csv.parent.mkdir(parents=True, exist_ok=True)
    ec_results = run_conversion(
        work, args.target, report_csv, inventory,
        explicit_source=not args.no_explicit_source)

    # PHASE 0 probes every spelling in play - baseline, what each corpus
    # declares, and what EC actually answered - so code-page identity can be
    # established for both sides of the detection comparison.
    universe = list(PHASE0_BASELINE)
    for inv in inventory:
        universe += net_name_candidates(inv.ReferenceEncodingDeclared,
                                        inv.ReferenceEncoding)
    for res in ec_results.values():
        label = res.get("Encoding") or ""
        if label and not label.startswith("("):
            universe += net_name_candidates(label)

    strictness = phase0(universe)

    # The candidate space a detector is choosing from: encodings this corpus
    # set actually contains, that both Python can decode with and .NET can
    # construct. Deliberately corpus-derived rather than a hand-written list,
    # so it cannot drift from what is being measured.
    candidate_codecs = tuple(sorted({
        r.ReferenceEncoding for r in inventory
        if r.ReferenceEncoding and code_page_of(
            strictness, r.ReferenceEncodingDeclared, r.ReferenceEncoding)
    }))

    # EC's own decode of each backup, in both constructions. The difference
    # between them is what proves silent decode loss instead of inferring it.
    prod_items, strict_items = [], []
    for inv in inventory:
        if not inv.ReferenceEncoding:
            continue
        bak = work / (inv.RelativePath + ".bak")
        if bak.is_file():
            prod_items.append({"Path": str(bak), "ForcedEncoding": None,
                               "ForcedBom": False, "DecoderMode": "production"})
            strict_items.append({"Path": str(bak), "ForcedEncoding": None,
                                 "ForcedBom": False, "DecoderMode": "strict"})

    production = {d["Path"]: d for d in run_ecdiag_batched("pipeline", prod_items)}
    strict_run = {d["Path"]: d for d in run_ecdiag_batched("pipeline", strict_items)}

    rows: list[AuditRow] = []

    for inv in inventory:
        row = AuditRow(
            Corpus=inv.Corpus,
            RelativePath=inv.RelativePath,
            ReferenceEncodingDeclared=inv.ReferenceEncodingDeclared,
            ReferenceEncoding=inv.ReferenceEncoding,
            ReferenceBOM=inv.ReferenceBOM,
            ReferenceMetadataSource=inv.ReferenceMetadataSource,
            OriginalByteLength=inv.OriginalSize,
            OriginalSha256=inv.OriginalSha256,
        )

        path = work / inv.RelativePath
        bak = work / (inv.RelativePath + ".bak")
        ec = ec_results.get(inv.RelativePath)

        row.ConversionStatus = ec["Result"] if ec else "MissingConvertedFile"
        if ec:
            row.DetectedEncoding = ec["Encoding"]
            row.DetectedBOM = "BOM" if ec["BOM"] == "Yes" else "NoBOM"
            row.ECReasonCode = ec.get("ReasonCode", "")
            row.ECExplicitSource = ec.get("_ExplicitSource", "")
            row.ECSourceUnsupported = ec.get("_SourceUnsupported", "")

        if not path.is_file():
            row.ConversionStatus = "MissingConvertedFile"

        current = path.read_bytes() if path.is_file() else b""
        row.ConvertedByteLength = len(current)
        # Hash unconditionally: a zero-byte result is a real outcome with a
        # real digest, and recording "" for it made an unchanged empty file
        # look like its hashes disagreed.
        row.ConvertedSha256 = sha256_bytes(current)

        if bak.is_file():
            backup_bytes = bak.read_bytes()
            row.BackupByteLength = len(backup_bytes)
            row.BackupSha256 = sha256_bytes(backup_bytes)
            row.BackupIntegrity = ("Verified" if row.BackupSha256 == inv.OriginalSha256
                                   else "Mismatch")
            original_bytes = backup_bytes
        elif row.ConversionStatus == "Converted":
            row.BackupIntegrity = "Missing"
            original_bytes = current
        else:
            row.BackupIntegrity = "NotApplicable"
            original_bytes = current

        row.ByteIdentical = str(original_bytes == current)

        for candidate in net_name_candidates(inv.ReferenceEncodingDeclared,
                                             inv.ReferenceEncoding):
            probe = strictness.get(candidate)
            if probe and probe.get("Available"):
                row.DecoderStrictness = probe.get("DecoderStrictness", "")
                row.EncoderStrictness = probe.get("EncoderStrictness", "")
                break

        # Detection match is codec identity, never compatibility: the same
        # codec under two names is a match, a different codec that happens to
        # decode these particular bytes is not.
        detected_codec = resolve_codec(row.DetectedEncoding) if row.DetectedEncoding else None
        ref_cp = code_page_of(strictness, inv.ReferenceEncodingDeclared,
                              inv.ReferenceEncoding)
        det_cp = code_page_of(strictness, row.DetectedEncoding)
        row.ReferenceCodePage = str(ref_cp or "")
        row.DetectedCodePage = str(det_cp or "")

        if inv.ReferenceEncoding:
            row.DetectionLabelExact = str(detected_codec == inv.ReferenceEncoding)
            if ref_cp is not None and det_cp is not None:
                row.DetectionMatch = str(ref_cp == det_cp)
                row.DetectionBasis = "CodePage"
            else:
                # No .NET code page for one side; fall back to the codec
                # registry's canonical name, which is weaker but honest.
                row.DetectionMatch = str(detected_codec == inv.ReferenceEncoding)
                row.DetectionBasis = "CodecName"

        # Reference text: the authoritative codec only.
        reference_text = None
        if inv.ReferenceEncoding:
            try:
                decoded = original_bytes.decode(inv.ReferenceEncoding)

                # U+FFFE leading the decode is a byte-order mark read in the
                # wrong order: the corpus has declared the opposite endianness
                # to the one the file was written in. It is a noncharacter, so
                # it cannot legitimately open a text file, and the declared
                # codec is not authoritative for this file. Judging EC against
                # it would score a correct detection as a failure.
                if decoded.startswith("￾"):
                    row.FailureCategory = "CorpusByteOrderMislabel"
                    row.ConversionErrorMessage = (
                        f"declared {inv.ReferenceEncodingDeclared}, but the "
                        f"decode opens with U+FFFE - the file is the opposite "
                        f"byte order")
                else:
                    reference_text = strip_bom(decoded)
                    row.ReferenceTextLength = len(reference_text)
                    row.ReferenceTextSha256 = hash_text(reference_text)
            except (UnicodeDecodeError, LookupError) as exc:
                # The declared codec cannot read its own file. If the opposite
                # byte order can, the label is an endianness mistake rather
                # than a corrupt fixture - worth saying which, because the two
                # call for different action from the corpus maintainer.
                if opposite_endian_decodes(original_bytes, inv.ReferenceEncoding):
                    row.FailureCategory = "CorpusByteOrderMislabel"
                    row.ConversionErrorMessage = (
                        f"declared {inv.ReferenceEncodingDeclared}, which cannot "
                        f"decode the file, but the opposite byte order can")
                else:
                    row.FailureCategory = "ReferenceDecodeError"
                    row.ConversionErrorMessage = str(exc)[:200]

        prod = production.get(str(bak))
        strict_res = strict_run.get(str(bak))

        if prod:
            row.ECProductionTextSha256 = prod.get("TextSha256") or ""
            if prod.get("FailureStage"):
                row.ConversionErrorStage = prod["FailureStage"]
                row.ConversionErrorType = prod.get("ExceptionType") or ""
                if not row.ConversionErrorMessage:
                    row.ConversionErrorMessage = (prod.get("ExceptionMessage") or "")[:200]

        if strict_res:
            row.ECStrictDecodeOutcome = (
                "Throws" if strict_res.get("FailureStage") == "Decode" else "Accepts")

        if row.ECStrictDecodeOutcome == "Throws" and prod and not prod.get("FailureStage"):
            row.ECImplementationDefect = "NonStrictDecoderAcceptedInvalidBytes"

        # An empty output is a real result, not a missing one: a file holding
        # nothing but a BOM correctly converts to zero bytes when the target
        # carries no preamble. Skipping the decode here left it uncompared and
        # it fell through to MappingDifference.
        converted_text = None
        if (row.ConversionStatus == "Converted" and not current
                and row.FailureCategory != "ReferenceDecodeError"):
            converted_text = ""
            row.ConvertedTextLength = 0
            row.ConvertedTextSha256 = hash_text("")
        elif current and row.FailureCategory != "ReferenceDecodeError":
            try:
                converted_text = strip_bom(current.decode("utf-8"))
                row.ConvertedTextLength = len(converted_text)
                row.ConvertedTextSha256 = hash_text(converted_text)
            except UnicodeDecodeError as exc:
                row.ConversionErrorStage = row.ConversionErrorStage or "Decode"
                if not row.ConversionErrorMessage:
                    row.ConversionErrorMessage = str(exc)[:200]

        # A file EC declined to identify cannot be compared: its bytes are
        # untouched, so reading them as the target codec compares the reference
        # against the *source* text and always differs, which says nothing about
        # conversion fidelity. "Unchanged" is a definite result - EC recognised
        # the file and found it already in the target encoding - so it is
        # compared like any conversion.
        if (reference_text is not None and converted_text is not None
                and row.ConversionStatus in ("Converted", "Unchanged")):
            identical = reference_text == converted_text
            row.TextIdentical = str(identical)
            if not identical:
                (row.FirstDifferenceIndex, row.ReferenceCodePoint,
                 row.ConvertedCodePoint, row.ReferenceChar, row.ConvertedChar,
                 row.ContextBefore, row.ContextAfter) = first_difference(
                    reference_text, converted_text)

        def reproduces_original(codec: str) -> bool:
            """Does encoding the reference text with this codec give the file back?"""
            try:
                encoded = reference_text.encode(codec)
            except (UnicodeEncodeError, LookupError):
                return False
            prefix = original_bytes[:len(original_bytes) - len(encoded)]
            return encoded == original_bytes or prefix + encoded == original_bytes

        # Corpus round-trip: does the declared codec reproduce the bytes?
        if inv.ReferenceEncoding and reference_text is not None:
            row.CorpusRoundTrip = str(reproduces_original(inv.ReferenceEncoding))

        # A labelling difference that cannot lose anything: decoding the
        # original bytes with the codec EC named yields the same text as the
        # reference codec does. A pure-ASCII UTF-8 file reported as us-ascii is
        # the common case.
        #
        # This tests the decode direction deliberately. The obvious alternative
        # - re-encode the reference text with the detected codec and compare
        # bytes - proves a round-trip property, not that decoding those bytes
        # gives the same text, and the two come apart wherever a mapping is
        # many-to-one. Decoding is also what EC actually does, so it is the
        # property that determines whether the label mattered.
        if (row.DetectionMatch == "False" and detected_codec
                and reference_text is not None):
            try:
                row.DetectionTextEquivalent = str(
                    strip_bom(original_bytes.decode(detected_codec)) == reference_text)
            except (UnicodeDecodeError, LookupError):
                row.DetectionTextEquivalent = "False"

        classify_detection(row, inv, detected_codec, reference_text,
                           original_bytes, candidate_codecs)

        # Binary fixtures declare "not text", so no reference codec exists and
        # the invariant cannot be evaluated. Recorded explicitly rather than
        # passed silently.
        if inv.ReferenceEncodingDeclared == "Binary" and not inv.ReferenceEncoding:
            if row.ConversionStatus == "Skipped":
                row.FailureCategory = "UnknownEncoding"
            elif row.ConversionStatus == "Refused":
                row.FailureCategory = "RefusedByPolicy"
            elif row.ConversionStatus == "Error":
                row.FailureCategory = "DecodeError"
            elif row.ByteIdentical == "True":
                row.FailureCategory = "NoOpCorrect"
            else:
                row.FailureCategory = "Misdetection"
        else:
            row.FailureCategory = row.FailureCategory or classify(row)

        rows.append(row)

    # Forced-reference diagnostic, both modes (section 26 + revised plan).
    if args.forced_reference:
        apply_forced_reference(rows, work, strictness)

    write_inventory(inventory, out_dir)
    return rows, strictness, {"conflicts": resolver.conflicts,
                              "ec_report": str(report_csv)}


def apply_forced_reference(rows: list[AuditRow], work: Path,
                           strictness: dict[str, dict]) -> None:
    """Bypass detection and decode with the authoritative codec.

    Mode A replicates the assign-after-construction pattern EC shipped with;
    Mode B supplies the fallbacks up front, which actually takes effect.
    Comparing the two separates "EC picked the wrong codec" from "EC's codec
    is not strict" from "the codecs genuinely differ". Mode A is deliberately
    pinned to the old construction rather than tracking EC, so that the same
    yardstick measures both the baseline and the fixed build.
    """
    targets = [r for r in rows
               if r.ReferenceEncoding
               and r.FailureCategory not in ("PASS", "NoOpCorrect")]

    if not targets:
        return

    items_a, items_b = [], []
    for row in targets:
        # .NET must be able to construct the forced codec, and it spells many
        # of them differently from Python. Without this the forced run fails at
        # construction and proves nothing.
        forced = net_name_for(strictness, row.ReferenceEncodingDeclared,
                              row.ReferenceEncoding)
        if forced is None:
            row.ForcedReferenceOutcome = "NoDotNetCodec"
            continue

        bak = work / (row.RelativePath + ".bak")
        source_file = bak if bak.is_file() else work / row.RelativePath
        for items, mode in ((items_a, "production"), (items_b, "strict")):
            items.append({"Path": str(source_file),
                          "ForcedEncoding": forced,
                          "ForcedBom": row.ReferenceBOM == "BOM",
                          "DecoderMode": mode})

    result_a = {d["Path"]: d for d in run_ecdiag_batched("forced", items_a)}
    result_b = {d["Path"]: d for d in run_ecdiag_batched("forced", items_b)}

    for row in targets:
        bak = work / (row.RelativePath + ".bak")
        key = str(bak if bak.is_file() else work / row.RelativePath)
        a, b = result_a.get(key), result_b.get(key)

        if row.ForcedReferenceOutcome == "NoDotNetCodec":
            continue

        if a is None or b is None:
            row.ForcedReferenceOutcome = "NotRun"
            continue

        a_ok = not a.get("FailureStage") and a.get("TextSha256") == row.ReferenceTextSha256
        b_ok = not b.get("FailureStage") and b.get("TextSha256") == row.ReferenceTextSha256

        # Labelled by construction, not by build: mode A always replicates the
        # assign-after-GetDecoder() pattern, so it stays a fixed yardstick
        # across the baseline and fixed runs instead of shifting with EC.
        row.ForcedReferenceOutcome = (
            f"assignAfter={'PASS' if a_ok else (a.get('FailureStage') or 'DIFFERS')};"
            f"strictUpFront={'PASS' if b_ok else (b.get('FailureStage') or 'DIFFERS')}")


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

PRIMARY_OUTCOMES = [
    "PASS", "NoOpCorrect", "NoOpMislabeled", "RefusedByPolicy",
    "Misdetection", "MappingDifference",
    "SilentDecodeLoss", "UnknownEncoding", "DecodeError", "EncodeError",
    "WriteError", "ReferenceDecodeError", "MetadataConflict",
    "BackupIntegrityFailure", "MissingBackup", "MissingConvertedFile",
    "CorpusRoundTripFailure", "UnknownReferenceEncoding", "ECCodecUnsupported",
    "AuditInfrastructureFailure", "OutOfScope", "NoReferenceEncoding",
    "CorpusByteOrderMislabel",
]

# Outcomes where the audit has no authoritative ground truth. These are
# reported, never scored: counting them as either pass or fail would be a
# claim the evidence does not support.
UNSCORED_OUTCOMES = {
    "OutOfScope", "NoReferenceEncoding", "UnknownReferenceEncoding",
    "ECCodecUnsupported",
    "MetadataConflict", "CorpusByteOrderMislabel",
    # EC declining to convert is a policy decision, not a conversion outcome.
    # Scoring it as either a pass or a failure would assert something the
    # evidence cannot support: nothing was written, and nothing was compared.
    "RefusedByPolicy",
}

# Outcomes that indicate the audit itself, not EC, did not hold up.
INFRASTRUCTURE_OUTCOMES = {
    "BackupIntegrityFailure", "MissingBackup", "MissingConvertedFile",
    "AuditInfrastructureFailure",
}


def write_inventory(inventory: list[InventoryRow], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "inventory.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(asdict(inventory[0]).keys()))
        writer.writeheader()
        for row in inventory:
            writer.writerow(asdict(row))


def write_audit(rows: list[AuditRow], out_dir: Path) -> None:
    path = out_dir / "audit.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(asdict(rows[0]).keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_summary_csv(rows: list[AuditRow], out_dir: Path) -> None:
    """Aggregation by reference/detected encoding pair (section 17)."""
    pairs: dict[tuple[str, str], Counter] = defaultdict(Counter)

    for row in rows:
        key = (row.ReferenceEncodingDeclared or "(none)",
               row.DetectedEncoding or "(undetected)")
        bucket = pairs[key]
        bucket["FileCount"] += 1
        bucket[row.ConversionStatus] += 1
        if row.TextIdentical == "True":
            bucket["TextIdenticalCount"] += 1
        elif row.TextIdentical == "False":
            bucket["TextDifferentCount"] += 1
        bucket[row.FailureCategory] += 1

    path = out_dir / "summary.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "ReferenceEncoding", "DetectedEncoding", "FileCount", "ConvertedCount",
            "SkippedCount", "ErrorCount", "TextIdenticalCount", "TextDifferentCount",
            "MisdetectionCount", "MappingDifferenceCount", "SilentDecodeLossCount"])
        for (ref, det), c in sorted(pairs.items()):
            writer.writerow([
                ref, det, c["FileCount"], c["Converted"], c["Skipped"], c["Error"],
                c["TextIdenticalCount"], c["TextDifferentCount"],
                c["Misdetection"], c["MappingDifference"], c["SilentDecodeLoss"]])


def write_metadata_summary(rows: list[AuditRow], out_dir: Path) -> None:
    """Directory/codec consistency (appendix M)."""
    seen: dict[str, list[int, str]] = {}
    for row in rows:
        key = row.ReferenceEncodingDeclared or "(none)"
        entry = seen.setdefault(key, [0, row.ReferenceEncoding])
        entry[0] += 1

    path = out_dir / "metadata-summary.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ReferenceEncodingDeclared", "FileCount", "Resolved",
                         "CanonicalEncoding"])
        for declared, (count, canonical) in sorted(seen.items()):
            writer.writerow([declared, count, str(bool(canonical)), canonical])


# Every file ends with exactly one ConversionStatus, so these partition a run.
# Defined once and used by both the summary table and the reconciliation check,
# so the two cannot drift: the pre-v3.9.0 table listed statuses by hand, and
# when EC introduced "Refused" the table silently dropped every refused file
# while still printing shares as though it had not.
COVERAGE_STATUSES = ("Converted", "Unchanged", "Skipped", "Refused", "Error",
                     "MissingConvertedFile")


def coverage_rows(rows: list[AuditRow]) -> list[tuple[str, int]]:
    """(label, count) per operational outcome, accounting for every file."""
    status = Counter(r.ConversionStatus for r in rows)
    rewritten = sum(1 for r in rows
                    if r.ConversionStatus == "Converted"
                    and r.ByteIdentical == "False")

    return [
        ("Converted — bytes rewritten", rewritten),
        ("Converted — bytes already identical", status["Converted"] - rewritten),
        ("Already in the target encoding", status["Unchanged"]),
        ("Skipped — encoding not identified", status["Skipped"]),
        ("Refused — EC declined to convert", status["Refused"]),
        ("Failed — conversion attempted and errored", status["Error"]),
        ("No converted file found", status["MissingConvertedFile"]),
    ]


def reconcile(rows: list[AuditRow]) -> tuple[dict, list[str]]:
    """Section 21. Counts must add up exactly, or the audit says so."""
    status = Counter(r.ConversionStatus for r in rows)
    outcome = Counter(r.FailureCategory for r in rows)
    problems: list[str] = []

    total = len(rows)
    accounted = sum(status.values())
    if accounted != total:
        problems.append(f"status counts {accounted} != {total} files")

    unclassified = [r for r in rows if r.FailureCategory not in PRIMARY_OUTCOMES]
    if unclassified:
        problems.append(f"{len(unclassified)} file(s) have no primary outcome")

    if sum(outcome.values()) != total:
        problems.append("outcome counts do not sum to the file count")

    # A status this audit does not model would otherwise be misfiled by
    # classify() and vanish from the coverage table. Say so instead.
    unmodelled = sorted({r.ConversionStatus for r in rows} - set(COVERAGE_STATUSES))
    if unmodelled:
        problems.append(
            "EC reported status(es) this audit does not model: "
            + ", ".join(unmodelled))

    covered = sum(count for _, count in coverage_rows(rows))
    if covered != total:
        problems.append(
            f"operational coverage accounts for {covered} of {total} files")

    text_diff = sum(1 for r in rows if r.TextIdentical == "False")
    causes = sum(outcome[k] for k in
                 ("Misdetection", "MappingDifference", "SilentDecodeLoss", "NoOpMislabeled"))
    if text_diff > causes:
        problems.append(
            f"{text_diff} text mismatches but only {causes} classified causes")

    return {"status": dict(status), "outcome": dict(outcome),
            "files": total, "textDifferent": text_diff}, problems


def write_summary_md(rows: list[AuditRow], strictness: dict, recon: dict,
                     args, out_dir: Path) -> None:
    outcome = Counter(r.FailureCategory for r in rows)
    status = Counter(r.ConversionStatus for r in rows)

    # Restrict to codecs this corpus actually exercised, and count each code
    # page once: the probe set deliberately contains many alias spellings of
    # the same codec, which would otherwise inflate this.
    in_play = {r.ReferenceCodePage for r in rows if r.ReferenceCodePage}
    in_play |= {r.DetectedCodePage for r in rows if r.DetectedCodePage}

    nonstrict_cp: dict[int, str] = {}
    for name, p in sorted(strictness.items()):
        if (p.get("Available") and p.get("DecoderStrictness") == "NonStrict"
                and str(p["CodePage"]) in in_play):
            nonstrict_cp.setdefault(int(p["CodePage"]), name)
    nonstrict = [nonstrict_cp[cp] for cp in sorted(nonstrict_cp)]

    defects = sum(1 for r in rows if r.ECImplementationDefect)

    lines: list[str] = []
    add = lines.append

    add(f"# EC conversion audit — {args.corpus} ({args.label})")
    add("")

    # Whether the build under test is affected is decided by what EC actually
    # did, not by the PHASE 0 pattern probe: that probe characterises the
    # assign-after-construction idiom and is unchanged by any EC fix, so
    # gating on it would flag a corrected build as defective.
    silent_loss = [r for r in rows
                   if r.ECImplementationDefect and r.ConversionStatus != "Error"]

    if silent_loss:
        add("> **This build silently loses content.**")
        add(f"> {len(silent_loss)} file(s) were reported as converted even though")
        add("> their bytes cannot be represented by the codec EC decoded them with.")
        add("> The results below are current production behaviour, decoder-side")
        add("> data loss included.")
        add("")
    elif nonstrict:
        add("> No silent decode loss observed: every file whose bytes its codec")
        add("> cannot represent was refused rather than converted.")
        add("")

    scored = [r for r in rows if r.FailureCategory not in UNSCORED_OUTCOMES]
    unscored = len(rows) - len(scored)

    explicit = Counter(r.ECExplicitSource for r in rows if r.ECExplicitSource)
    unusable = sorted({r.ReferenceEncoding for r in rows
                       if r.ReferenceEncoding and not r.ECExplicitSource})

    if explicit:
        add(f"Source encoding was supplied to EC for "
            f"{sum(explicit.values())} of {len(rows)} file(s) using the corpus's "
            f"own reference codec, across {len(explicit)} codec(s). EC converts "
            f"automatically only from Unicode and ASCII, so this is what makes "
            f"conversion fidelity measurable at all; detection accuracy below is "
            f"still measured from what EC reported.")

        if unusable:
            add("")
            add(f"EC accepts no spelling of {len(unusable)} reference codec(s), so "
                f"those files were run on detection alone and will be refused "
                f"rather than converted: {', '.join(f'`{c}`' for c in unusable)}. "
                f"This is a gap in what the audit can measure, not an EC failure.")
    else:
        add("EC was run on detection alone. From v3.9.0 that refuses all legacy "
            "text, so the metrics below describe unaided behaviour rather than "
            "conversion fidelity.")
    add("")

    add("## Four independent metrics")
    add("")
    add("Reported separately and deliberately not combined: a single accuracy")
    add("number would average silent data loss away against files that merely")
    add("happened to be ASCII.")
    add("")
    compared = sum(1 for r in scored if r.TextIdentical in ("True", "False"))
    detected = [r for r in scored if r.DetectionMatch in ("True", "False")]
    det_ok = sum(1 for r in detected if r.DetectionMatch == "True")
    identical = sum(1 for r in scored if r.TextIdentical == "True")
    rt = [r for r in scored if r.CorpusRoundTrip in ("True", "False")]
    rt_ok = sum(1 for r in rt if r.CorpusRoundTrip == "True")

    add("| # | Metric | Question it answers | Result |")
    add("|---|---|---|---|")
    equivalent = sum(1 for r in detected
                     if r.DetectionMatch == "False"
                     and r.DetectionTextEquivalent == "True")

    add(f"| 1 | Detection accuracy | Did EC name the codec that wrote the bytes? | "
        f"{det_ok}/{len(detected)}"
        f"{f' ({100*det_ok/len(detected):.2f}%)' if detected else ''} |")
    refused = sum(1 for r in rows
                  if r.ECStrictDecodeOutcome == "Throws"
                  and r.ConversionStatus == "Error")

    if silent_loss:
        strict_verdict = f"**FAILED** — {len(silent_loss)} file(s) converted silently"
    elif refused:
        strict_verdict = f"passed — {refused} refused"
    else:
        strict_verdict = "passed — no such input in this corpus"

    add(f"| 2 | Strict-decoding correctness | Does EC reject bytes its chosen codec "
        f"cannot represent? | {strict_verdict} |")
    add(f"| 3 | Codec conformance | Where EC named the right codec, does its "
        f"mapping table agree with the reference? | "
        f"{outcome['MappingDifference']} divergence(s) |")
    # Refused files are absent from this denominator because nothing was
    # written and nothing was compared. Left unqualified, "1/1 (100%)" over a
    # run that converted nothing reads as success; the count of refusals beside
    # it is what stops the ratio being mistaken for coverage.
    # Counted over every row, not `scored`: RefusedByPolicy is deliberately
    # unscored, so counting refusals within `scored` would always yield zero
    # and the qualifier would never appear where it is most needed.
    refused_files = sum(1 for r in rows if r.ConversionStatus == "Refused")
    preservation = (f"{identical}/{compared}"
                    f"{f' ({100 * identical / compared:.2f}%)' if compared else ''}")
    if refused_files:
        preservation += (f" over the files converted; {refused_files} refused "
                         f"and not comparable")

    add(f"| 4 | End-to-end text preservation | Is the output the same text as the "
        f"input? | {preservation} |")
    add("")
    if equivalent:
        substantive = len(detected) - det_ok - equivalent
        add(f"Of the {len(detected) - det_ok} detection mismatches, {equivalent} are "
            f"text-equivalent labellings — decoding the original bytes with the "
            f"codec EC named yields the same text as the reference codec, so the "
            f"disagreement cannot lose anything (a pure-ASCII UTF-8 file reported as "
            f"us-ascii is the usual case). That leaves {substantive} substantive "
            f"misdetections, established by decoding rather than from corpus metadata.")
        add("")

    add(f"Corpus round-trip control (does the declared codec reproduce the corpus "
        f"bytes?): {rt_ok}/{len(rt)}. A failure here is a corpus defect, not an EC one.")
    add("")

    # ---- Detection quality --------------------------------------------
    detection = Counter(r.DetectionOutcome for r in rows)
    judged = sum(detection[k] for k in
                 ("ExactMatch", "TextEquivalent", "StructurallyAmbiguous",
                  "Misdetection"))

    add("## Detection quality")
    add("")
    add("Split seven ways rather than reported as one percentage, because these")
    add("call for different responses and averaging them hides the ones that")
    add("matter.")
    add("")
    add("| Outcome | Files | Meaning |")
    add("|---|---:|---|")
    add(f"| `ExactMatch` | {detection['ExactMatch']} | "
        f"The codec that wrote the file, under any spelling |")
    add(f"| `TextEquivalent` | {detection['TextEquivalent']} | "
        f"A different codec, identical resulting text — nothing can be lost |")
    add(f"| `StructurallyAmbiguous` | {detection['StructurallyAmbiguous']} | "
        f"A different reading, but the bytes could not have said which is right |")
    add(f"| `Misdetection` | {detection['Misdetection']} | "
        f"A different reading where the bytes did carry a distinguishing signal |")
    add(f"| `NoDotNetCodec` | {detection['NoDotNetCodec']} | "
        f"No code page exists, so no detector on this platform could name it |")
    add(f"| `NotIdentified` | {detection['NotIdentified']} | "
        f"The detector named nothing; the file was left untouched |")
    add(f"| `NoReference` | {detection['NoReference']} | "
        f"No authoritative encoding to judge against |")
    add("")

    if judged:
        good = detection["ExactMatch"] + detection["TextEquivalent"]
        add(f"Of the {judged} files where a detector could be judged at all, "
            f"**{good} ({100 * good / judged:.1f}%)** were named exactly or "
            f"named as a codec giving identical text.")
        add("")
        add(f"The remaining {judged - good} split into "
            f"**{detection['StructurallyAmbiguous']} structurally ambiguous** and "
            f"**{detection['Misdetection']} substantive**. That distinction is the "
            f"point: single-byte code pages map 256 values independently, so a file "
            f"valid in `windows-1252` is equally valid in `iso-8859-1` and no "
            f"amount of byte inspection decides between them. A multi-byte encoding "
            f"does constrain its sequences, so failing to recognise one is a "
            f"detector error rather than an impossible choice.")
        add("")

    # ---- Operational coverage -----------------------------------------
    add("## Operational coverage")
    add("")
    add("What happened to the files, independent of whether it was correct. A")
    add("reader deciding whether to run this tool needs both: a skipped file")
    add("carries no risk of losing text and every risk of not doing the job.")
    add("")
    coverage = coverage_rows(rows)
    add("| Outcome | Files | Share |")
    add("|---|---:|---:|")
    for label, count in coverage:
        add(f"| {label} | {count} | {100 * count / len(rows):.1f}% |")
    covered = sum(count for _, count in coverage)
    add(f"| **Total** | **{covered}** | **{100 * covered / len(rows):.1f}%** |")
    add("")

    if covered != len(rows):
        add(f"> **This table does not account for all {len(rows)} files.** "
            f"That is a defect in this audit, not a finding about EC.")
        add("")

    refusals = Counter(r.ECReasonCode or "(none)" for r in rows
                       if r.ConversionStatus == "Refused")
    if refusals:
        add("EC refuses by policy rather than failing, so refusals carry a stable")
        add("reason code. They are reported and never scored: nothing was written,")
        add("and nothing was compared.")
        add("")
        add("| Reason code | Files |")
        add("|---|---:|")
        for code, count in refusals.most_common():
            add(f"| `{code}` | {count} |")
        add("")

    # ---- Text-loss risk -----------------------------------------------
    add("## Text-loss risk")
    add("")
    add("Only a file that was actually rewritten can have lost anything, so this")
    add("is measured over those alone. Counting skipped files as successes would")
    add("flatter it; counting them as failures would misattribute a coverage gap")
    add("to the converter.")
    add("")
    rewritten_rows = [r for r in rows
                      if r.ConversionStatus == "Converted"
                      and r.ByteIdentical == "False"]
    changed = sum(1 for r in rewritten_rows if r.TextIdentical == "False")
    preserved = sum(1 for r in rewritten_rows if r.TextIdentical == "True")
    uncompared = len(rewritten_rows) - changed - preserved

    add(f"- Rewritten: {len(rewritten_rows)}")
    add(f"- Text preserved: {preserved}")
    add(f"- Text changed: {changed}")
    if uncompared:
        add(f"- Not comparable (no reference codec): {uncompared}")
    if preserved + changed:
        add(f"- **Preservation among rewritten and comparable files: "
            f"{100 * preserved / (preserved + changed):.2f}%**")
    add("")

    # Elsewhere in the corpus, text differs on files that were never rewritten -
    # a UTF-7 file EC left alone still holds UTF-7 escapes rather than text.
    # That is a coverage failure, not a conversion one, and is counted there.
    untouched_diff = sum(1 for r in rows
                         if r.TextIdentical == "False" and r not in rewritten_rows)
    if untouched_diff:
        add(f"A further {untouched_diff} file(s) differ from their reference text "
            f"without having been rewritten - EC left the bytes alone, so the "
            f"difference is that the file was never converted, not that "
            f"conversion damaged it. Counted under coverage rather than here.")
        add("")

    # ---- BOM policy ----------------------------------------------------
    bom_rows = [r for r in rows if r.ReferenceBOM in ("BOM", "NoBOM")
                and r.DetectedBOM in ("BOM", "NoBOM")]
    if bom_rows:
        bom_ok = sum(1 for r in bom_rows if r.ReferenceBOM == r.DetectedBOM)
        add("## BOM policy")
        add("")
        add("Reported separately from text preservation, because they are")
        add("independent properties: converting UTF-8 with a BOM to UTF-8 without")
        add("one preserves every character while changing the serialization.")
        add("")
        add(f"- Files with a declared BOM state: {len(bom_rows)}")
        add(f"- Detected BOM state matches the reference: {bom_ok} "
            f"({100 * bom_ok / len(bom_rows):.1f}%)")
        add("")

    add("## Corpus totals")
    add("")
    add(f"- Files discovered: {len(rows)}")
    add(f"- Scored: {len(scored)}")
    add(f"- Unscored (no authoritative ground truth): {unscored}")
    add(f"- Compared: {compared}")
    add("")

    if unscored:
        add("### Why files are unscored")
        add("")
        add("| Reason | Files |")
        add("|---|---:|")
        for name in sorted(UNSCORED_OUTCOMES):
            if outcome.get(name):
                add(f"| {name} | {outcome[name]} |")
        add("")

    add("## Outcomes")
    add("")
    add("| Outcome | Files |")
    add("|---|---:|")
    for name in PRIMARY_OUTCOMES:
        if outcome.get(name):
            add(f"| {name} | {outcome[name]} |")
    add("")

    add("## PHASE 0 — actual codec strictness of this build")
    add("")
    add("Determined empirically by feeding each codec bytes (and characters) it")
    add("cannot represent, never assumed from the fact that a fallback was")
    add("assigned. `NotTestable` means no probe was rejected even by a correctly")
    add("constructed strict codec — the normal result for a single-byte code page,")
    add("where every one of the 256 bytes maps to a character and there is")
    add("therefore nothing for a decoder to reject.")
    add("")

    by_cp: dict[int, tuple[str, dict]] = {}
    for name, p in sorted(strictness.items()):
        if not p.get("Available"):
            continue
        cp = int(p["CodePage"])
        if str(cp) not in in_play:
            continue
        if cp not in by_cp or len(name) < len(by_cp[cp][0]):
            by_cp[cp] = (name, p)

    def label(value: str) -> str:
        return "NotTestable" if value == "Unknown" else value

    add("| Code page | Encoding | Decoder | Encoder |")
    add("|---:|---|---|---|")
    for cp in sorted(by_cp):
        name, p = by_cp[cp]
        add(f"| {cp} | {name} | {label(p['DecoderStrictness'])} "
            f"| {label(p['EncoderStrictness'])} |")
    add("")

    if nonstrict:
        add(f"Codecs where the idiom fails: {', '.join(nonstrict)}.")
        add("")
        add("For these, assigning `Decoder.Fallback = ExceptionFallback` after")
        add("`GetDecoder()` has no effect: the codec has already taken its")
        add("fallbacks from the parent encoding, so invalid bytes are silently")
        add("substituted instead of raising. The fallbacks must be passed to")
        add("`Encoding.GetEncoding(codePage, encoderFallback, decoderFallback)`")
        add("instead. This table characterises the *idiom*, which is fixed")
        add("across runs on purpose so both builds are measured against the")
        add("same yardstick; whether the build under test is affected is stated")
        add("at the top of this report.")
        add("")

    if defects:
        add("### InternalVerificationBlindSpot")
        add("")
        add(f"{defects} file(s) were accepted by EC's production decoder but rejected")
        add("by a correctly constructed strict decoder over the same bytes.")
        add("")
        add("EC's content-digest verification is downstream of the decoder, so it")
        add("hashes the already-lossy decoded source and the decoded output. It")
        add("therefore proves only `lossy source decode == output decode`, not")
        add("`original encoded text == output text`, and cannot detect this loss.")
        add("")

    diffs = Counter(
        (r.ReferenceCodePoint, r.ConvertedCodePoint) for r in rows
        if r.TextIdentical == "False" and r.ReferenceCodePoint)
    if diffs:
        add("## Top divergence signatures")
        add("")
        add("| Reference | Converted | Files |")
        add("|---|---|---:|")
        for (ref, con), n in diffs.most_common(15):
            add(f"| {ref} | {con} | {n} |")
        add("")

    by_ref = defaultdict(Counter)
    for row in rows:
        by_ref[row.ReferenceEncodingDeclared or "(none)"][row.FailureCategory] += 1
    add("## By reference encoding")
    add("")
    add("| Reference encoding | Files | PASS | Misdetection | MappingDifference | SilentDecodeLoss |")
    add("|---|---:|---:|---:|---:|---:|")
    for ref in sorted(by_ref):
        c = by_ref[ref]
        add(f"| {ref} | {sum(c.values())} | {c['PASS']} | {c['Misdetection']} "
            f"| {c['MappingDifference']} | {c['SilentDecodeLoss']} |")
    add("")

    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_details_json(rows: list[AuditRow], out_dir: Path) -> None:
    details = []
    for row in rows:
        if row.TextIdentical == "False" or row.ECImplementationDefect:
            details.append({
                "file": row.RelativePath,
                "referenceEncoding": row.ReferenceEncoding,
                "detectedEncoding": row.DetectedEncoding,
                "category": row.FailureCategory,
                "decoderStrictness": row.DecoderStrictness,
                "ecImplementationDefect": row.ECImplementationDefect or None,
                "ecStrictDecodeOutcome": row.ECStrictDecodeOutcome,
                "forcedReference": row.ForcedReferenceOutcome,
                "firstDifference": None if row.FirstDifferenceIndex < 0 else {
                    "index": row.FirstDifferenceIndex,
                    "reference": row.ReferenceCodePoint,
                    "converted": row.ConvertedCodePoint,
                    "contextBefore": row.ContextBefore,
                    "contextAfter": row.ContextAfter,
                },
            })
    (out_dir / "audit-details.json").write_text(
        json.dumps(details, indent=2, ensure_ascii=False), encoding="utf-8")


def write_run_json(args, strictness: dict, recon: dict, extra: dict,
                   out_dir: Path) -> None:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=EC_REPO,
            capture_output=True, text=True).stdout.strip()
        # A dirty tree means the binary under test is not the commit named
        # above, which matters when comparing a baseline run to a fixed one.
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=EC_REPO,
            capture_output=True, text=True).stdout.strip())
    except Exception:
        commit, dirty = "", None

    nonstrict = sorted(e for e, p in strictness.items()
                       if p.get("DecoderStrictness") == "NonStrict")

    run = {
        "TimestampUtc": datetime.now(timezone.utc).isoformat(),
        "AuditVersion": AUDIT_VERSION,
        "Label": args.label,
        "Corpus": args.corpus,
        "CorpusRoot": str(Path(args.source).resolve()),
        "WorkingRoot": str(Path(args.work).resolve()),
        "OutputRoot": str(out_dir),
        "TargetEncoding": args.target,
        "IncludeSubfolders": True,
        "BackupEnabled": True,
        "ForcedReference": bool(args.forced_reference),
        "Strict": bool(args.strict),
        "ECExecutable": str(EC_EXE),
        "ECGitCommit": commit,
        "ECGitTreeDirty": dirty,
        # The .exe is only the apphost launcher and is byte-identical across
        # builds; the managed assembly beside it is what actually changes, so
        # that is what identifies the build under test.
        "ECAssemblySha256": (sha256_bytes(EC_ASSEMBLY.read_bytes())
                             if EC_ASSEMBLY.is_file() else ""),
        "ECAssemblyBuiltUtc": (datetime.fromtimestamp(
            EC_ASSEMBLY.stat().st_mtime, timezone.utc).isoformat()
            if EC_ASSEMBLY.is_file() else ""),
        "DotNetVersion": subprocess.run(
            ["dotnet", "--version"], capture_output=True, text=True).stdout.strip(),
        "PythonVersion": sys.version.split()[0],
        "OSVersion": platform.platform(),
        "CommandLine": " ".join(sys.argv),
        "Phase0": {
            "DecoderStrictnessNonStrict": nonstrict,
            "ProductionDecoderStrict": not nonstrict,
            "Probes": strictness,
        },
        "Reconciliation": recon,
        "MetadataConflicts": extra.get("conflicts", []),
    }
    (out_dir / "run.json").write_text(
        json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True,
                        choices=["uts3", "chardet", "charsetnormalizer", "utfunknown26"])
    parser.add_argument("--source", required=True, help="read-only original corpus")
    parser.add_argument("--work", required=True, help="working copy the audit creates")
    parser.add_argument("--out", required=True, help="output directory")
    parser.add_argument("--target", default="utf-8")
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--forced-reference", action="store_true")
    parser.add_argument(
        "--no-explicit-source", action="store_true",
        help="run EC on detection alone. Since v3.9.0 that refuses all legacy "
             "text, so it measures unaided behaviour rather than conversion "
             "fidelity.")
    args = parser.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        rows, strictness, extra = audit_corpus(args, out_dir)
    except Exception as exc:                      # audit infrastructure failure
        print(f"AuditInfrastructureFailure: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INFRASTRUCTURE

    if not rows:
        print("AuditInfrastructureFailure: no files discovered", file=sys.stderr)
        return EXIT_INFRASTRUCTURE

    recon, problems = reconcile(rows)

    write_audit(rows, out_dir)
    write_summary_csv(rows, out_dir)
    write_metadata_summary(rows, out_dir)
    write_summary_md(rows, strictness, recon, args, out_dir)
    write_details_json(rows, out_dir)
    write_run_json(args, strictness, recon, extra, out_dir)

    outcome = Counter(r.FailureCategory for r in rows)
    print(f"[{args.corpus}/{args.label}] {len(rows)} files")
    for name in PRIMARY_OUTCOMES:
        if outcome.get(name):
            print(f"    {name:<26} {outcome[name]}")

    if problems:
        print("\n  reconciliation problems:", file=sys.stderr)
        for p in problems:
            print(f"    {p}", file=sys.stderr)

    if args.strict:
        if problems or sum(outcome[k] for k in INFRASTRUCTURE_OUTCOMES):
            return EXIT_INFRASTRUCTURE
        if sum(outcome[k] for k in ("Misdetection", "MappingDifference",
                                    "SilentDecodeLoss", "NoOpMislabeled")):
            return EXIT_REGRESSION

    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
