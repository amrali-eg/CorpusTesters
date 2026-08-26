# Independent review request: encoding-conversion audit methodology

You are being asked to review an audit, not to admire it. Two things are wanted:

1. **Critique the methodology.** Where is it measuring the wrong thing, measuring
   the right thing badly, or drawing a conclusion the evidence does not support?
2. **Suggest robustness improvements** for the tool under test — refusing
   conversions, backups, confirmation prompts, anything that reduces the chance
   of a user losing text without noticing.

Please argue with the choices below rather than summarising them. Where you
disagree, say what you would do instead and what it would cost.

---

## 1. The system under test

**EncodingChecker (EC)** — a Windows tool (GUI + CLI, .NET 10) that detects a
text file's character encoding and converts it to a chosen target, in place.

Its detection pipeline, in order:

1. `UnicodeDetector` — BOM inspection, then structural validation of BOM-less
   UTF-8/16/32 candidates.
2. **UTF.unknown** — a C# port of Mozilla's universal charset detector, for
   legacy code pages.
3. `TextValidation` — independent strict-decode validation of whatever step 1
   or 2 claimed. A candidate that cannot decode the sampled bytes is rejected.

Conversion is decode → re-encode through strict codecs, written to a temporary
file, verified, then atomically installed. There is no raw-byte path: every
encoding goes through decode/re-encode.

A sibling tool, **LineEndingNormalizer (LEN)**, shares the same detector but
never changes a file's encoding. It is not the subject of this audit.

## 2. What the audit asks

Per file, one question:

```
strict-decode(original bytes, reference codec + BOM)
        ==
strict-decode(converted bytes, target codec)
```

Deliberate choices, all of which are open to challenge:

- **Exact Unicode code-point equality.** No NFC/NFD, no case folding, no newline
  or whitespace normalization, no visual comparison.
- **Strict decoding throughout.** No replacement characters, no best-fit
  substitution. A decode that cannot complete is a failure, not a degraded
  success.
- **A BOM is an encoding artifact, not content**, and is stripped from both
  sides before comparison.
- **Compatibility metadata is never used to establish ground truth.** One corpus
  ships an `AlsoValidAs` field recording which answers a *detector* may
  acceptably give; using it here would let a wrong decode pass as correct.
- **Source corpora are read-only.** Each is copied to a working directory and
  only the copy is converted; the originals are verified byte-identical to their
  published SHA-256 after every run.

## 3. Corpora and ground truth

Four public corpora, 5,078 files:

| Corpus | Files | Ground truth |
|---|---:|---|
| UnicodeTestSuite v3.0 | 1,367 | `Manifest.csv` — the encoding each fixture was written with, plus a per-file SHA-256 |
| chardet `test-data` | 3,166 | `CATALOG.md`; directory named `{encoding}` or `{encoding}-{language}` |
| charset-normalizer `char-dataset` | 478 | Parent directory name |
| UTF.unknown 2.6 tests | 67 | Parent directory name |

Ground truth is never derived from filenames. Files present on disk but absent
from a corpus's own manifest or catalogue are recorded as having no ground truth
rather than guessed at.

## 4. The four metrics, reported separately

They are never blended into one "accuracy" number, on the grounds that a single
figure averages silent data loss against files that merely happened to be ASCII.

| # | Metric | Question |
|---|---|---|
| 1 | Detection accuracy | Did EC name the codec that wrote the bytes? |
| 2 | Strict-decoding correctness | Does EC refuse bytes its chosen codec cannot represent, rather than substituting? |
| 3 | Codec conformance | Where EC named the right codec, does its mapping table agree with the reference? |
| 4 | End-to-end text preservation | Is the output the same text as the input? |

**Detection is compared by .NET code-page identity, not by label string.** Python
and .NET spell the same codec differently often enough (`cp949` vs
`ks_c_5601-1987`, `ibm866` vs `cp866`, `utf-16le` vs `utf-16`) that string
comparison scored spelling disagreements as detection errors — worth about ten
percentage points on its own.

Detection mismatches are further split into **byte-equivalent labellings** and
substantive misdetections. A labelling is byte-equivalent when re-encoding the
reference text with the codec EC named reproduces the file exactly, so nothing
can be lost by the disagreement — a pure-ASCII UTF-8 file reported as `us-ascii`
is the common case. This is established by re-encoding, never by trusting corpus
compatibility metadata.

Outcomes where no authoritative ground truth exists (repository metadata,
sidecar directories, encodings with no available codec, corpus self-contradiction)
are **reported but never scored**, on the grounds that counting them as either
pass or fail would be a claim the evidence does not support.

## 5. Two experiments worth scrutinising

### PHASE 0 — establishing what the build actually does

The audit never assumes a codec is strict because a fallback was assigned to it.
Before any file is judged, each codec is probed with bytes and characters it
cannot represent, and its real behaviour recorded.

This is what the audit was built on top of and what it first found:

```
EUC-JP bytes carrying a JIS X 0212 sequence (SS3 0x8F)
   GetDecoder() then assign .Fallback   ->  mangled text, no exception
   GetEncoding(cp, fb, fb).GetDecoder() ->  DecoderFallbackException
```

Assigning `Decoder.Fallback` **after** `GetDecoder()` has no effect for encodings
from .NET's `CodePagesEncodingProvider`: those codecs take their fallbacks from
the parent `Encoding` at construction, so the assignment is silently ignored.
EC shipped that pattern. Files whose bytes its own codec could not represent were
converted with substituted characters and reported as successfully converted.

EC's own SHA-256 verification could not catch it: it hashes decoded source
against decoded target, so both sides pass through the *same* lossy decoder and
agree. It proves `lossy source decode == output decode`, not `original text ==
output text`. Fixed in EC v3.6.0.

Measured blast radius: **4 files out of 5,078** — a latent correctness hole, not
mass corruption. It rarely fired because detection usually picks a codec that
*can* decode the bytes.

### Forced-reference experiment

For every file that did not pass, the audit re-decodes it with the authoritative
codec, bypassing detection, in two decoder constructions — the assign-after
pattern, and fallbacks supplied up front. The pair separates three causes that
look identical in an aggregate:

| Outcome | Meaning |
|---|---|
| PASS in both | The codec was right all along — a **detection** error |
| DIFFERS in both | The codecs genuinely disagree — a **conformance** difference |
| DIFFERS / strict throws | A strict codec refuses; the shipped pattern silently altered the text |
| No .NET codec | .NET cannot construct the reference encoding at all |

## 6. What the instrument got wrong

This section exists because an audit that reports only the defects it found in
its target, and none in itself, should not be trusted. Every one of these was
found in a single day, and each made a **correct** tool look wrong.

**Ground-truth parse.** The chardet corpus names directories
`{encoding}-{language}`. The resolver took the longest prefix that resolves as a
codec — and `utf-16-be` is *both* a valid codec name and a valid
encoding+language pair. It means UTF-16 **Belarusian**; `be` is the ISO 639-1
code, tagging fourteen other directories in that corpus. Six little-endian files
were read backwards, produced `U+FFFE` and mojibake, and looked exactly like a
corpus mislabel. An issue was nearly filed against the corpus maintainers.

**Scope.** A sidecar directory (`cp864-ar/_logical_source/`, documented in the
corpus's own `CATALOG.md` as the logical-order UTF-8 source for its shaped cp864
files) inherited the enclosing encoding, inventing a ground truth the corpus
never claimed.

**Empty output.** A file containing nothing but a BOM correctly converts to zero
bytes; the empty result was never decoded, left uncompared, and fell through to a
divergence classification.

**Incomparable files compared.** A file EC *declined to identify* is left
untouched, so reading its bytes as the target codec compares the reference
against the *source* text and always differs — which says nothing about
conversion fidelity.

The common shape: **treating the harness's reading of a corpus as the corpus's
claim**, without checking how the corpus is meant to be read. In both
ground-truth cases the corpus's own tooling read it correctly; only this audit
did not.

There is now an integrity check that re-derives what can be re-derived rather
than trusting the run: every file judged exactly once and nothing invented; each
row's outcome agreeing with the evidence on that row; every file whose text
differs carrying an outcome that explains it; a random sample decoded again
straight from the original bytes through a separate code path, so a wrong decode
cannot hide behind its own self-consistent hash; and any declared token that is
itself a valid codec but resolved to a different one flagged unless explicitly
acknowledged. All invariants currently hold across 5,078 rows.

Independently, the audit's per-file detection results were cross-validated
against two separately written test harnesses over the same corpora. They agree
exactly on what the detector reported for every catalogued file — all ten Unicode
classes and 1,355 legacy files identical — with differences only for files one
judges and the others deliberately skip, reconciling to the exact count.

## 7. What the audit currently concludes

Over the files EC actually **rewrote** (a file it skipped or left byte-identical
cannot have lost anything):

| Source | Rewritten | Text preserved | Changed |
|---|---:|---:|---:|
| Unicode + ASCII | 1,832 | **1,832 (100.00%)** | 0 |
| Legacy code page | 2,021 | 1,602 (79.27%) | 419 |
| No .NET codec exists | 112 | 21 (18.75%) | 76 |

The four metrics: detection 3,756/4,961 exact (75.7%), 84.5% including
byte-equivalent labellings, 80.5% excluding encodings .NET cannot represent;
strict-decoding 5,020/5,020; 89 codec divergences; text preservation
4,101/4,741 (86.5%).

The residual risk is **detection, not conversion**. Single-byte code pages are
mutually decodable — `windows-1252` text is perfectly valid `iso-8859-1` text —
and nothing in the bytes says which was intended. Forced to the correct codec,
those files convert exactly.

The 89 divergences are dominated by known Microsoft-vs-Unicode mapping
differences in the CJK code pages (`U+301C` wave dash vs `U+FF5E` fullwidth
tilde, and similar), which are properties of .NET's tables rather than of EC.

**Recoverability.** Of every rewritten file whose text changed, 99.2% (635/640)
could be recovered by re-encoding the output with the codec EC used. Only 5 were
unrecoverable — 4 Big5, 1 cp865. But recovery requires knowing *which* codec was
used, and that is recorded only in the conversion report.

## 8. Known weaknesses, offered rather than defended

- **The reference oracle is Python's codec registry.** Where .NET and Python
  disagree, the audit calls it a "codec conformance" difference and implicitly
  treats Python as correct. It is not obvious that it is. ICU or the Unicode
  Consortium mapping tables are alternative oracles that would reclassify some of
  the 89 divergences.
- **One corpus's convention leaks into scoring.** chardet treats `gb2312` as an
  alias of `gb18030`; this audit uses Python's distinct strict `gb2312`, so the
  `A1 AA` → `U+2015`/`U+2014` difference is scored as a divergence where
  chardet's own tooling would not see one.
- **Corpus distribution is not real-world distribution.** Four public test
  corpora over-represent hard cases by construction. No claim is made that these
  ratios predict a user's file tree.
- **The audit tests EC's converter, not LEN's writer.** The detector is shared,
  so detection results transfer; the normalization path does not.
- **Binary fixtures** are scored on whether the detector correctly declined them,
  but contribute to no metric denominator — arguably an inconsistency.
- **The recoverability figure assumes the conversion report was kept.** Without
  it, the codec used is unknown and the 99.2% is theoretical.

## 9. Questions for you

On methodology:

1. Is **exact code-point equality with no normalization** the right fidelity
   criterion, or is it strict to the point of flagging differences no user would
   consider loss?
2. Is **Python's codec registry** an acceptable reference oracle? If not, what
   should the audit compare against, and how should existing divergences be
   reclassified?
3. Are these **the right four metrics**? What is missing, and what is redundant?
4. Is **byte-equivalence** a legitimate mitigation, or does it let the tool off
   for a real labelling error?
5. How should encodings with **no available codec** be scored? They are currently
   excluded from an "adjusted" figure and reported separately.
6. Is **excluding unscored outcomes** honest, or does it flatter the result?
7. What class of defect would this design **structurally fail to detect**? The
   instrument's own defects listed in §6 were found by hand, not by the design.

On EC robustness — the practical half:

8. EC converts whenever the detector names anything. Should it **refuse** when
   confidence is low, when the candidate is a single-byte code page that several
   others would decode identically, or when the file is large enough that a
   mistake is expensive?
9. Would requiring **two independent detectors to agree** before converting be
   worth the reduced coverage?
10. Backup is now on by default in the GUI and persisted (v3.7.0); the CLI keeps
    it opt-in. Given that recovery needs the codec name, should EC **record the
    detected encoding alongside the backup** — a sidecar, an alternate data
    stream, or a filename convention — so recovery is deterministic rather than
    dependent on keeping a report?
11. Should EC offer a **dry-run diff** showing which characters would change,
    rather than a binary convert/skip?
12. Are there **verification steps** it should perform that it currently does
    not? Its post-write check hashes decoded content on both sides, which — as
    §5 shows — cannot catch a defect in the decoder itself.
13. What would you add to make a **wrong conversion loud rather than silent**?
    The most dangerous outcome in this whole exercise was not an error; it was a
    success message on a file that had quietly changed.

---

*Supporting material: the audit harness, its integrity checker, per-file
evidence (~45 columns per file), and the full methodology are at
https://github.com/amrali-eg/CorpusTesters. The tool under test is at
https://github.com/amrali-eg/EncodingChecker.*
