# CorpusTesters

Corpus-based testing for the character-encoding detection and conversion used by
**[EncodingChecker](https://github.com/amrali-eg/EncodingChecker)** and
**[LineEndingNormalizer](https://github.com/amrali-eg/LineEndingNormalizer)**.

Both tools have to answer two separate questions correctly, and this repository
measures them separately, because passing one says nothing about the other:

| | Question | Where |
|---|---|---|
| **Detection** | Does the detector *name* the right encoding? | `UnicodeSuiteTester`, `ChardetDataTester` |
| **Conversion** | Does converting the file *preserve the text* exactly? | [`audit/`](audit/) |

A detector can name an encoding correctly and the conversion still lose content;
a conversion can round-trip perfectly on a file the detector mislabelled. Neither
number substitutes for the other, and this repository never blends them.

**Results below are reproducible.** Every figure comes from a committed harness
run against public corpora — see [Reproducing the results](#reproducing-the-results).

---

## What is being tested

The detection pipeline shared by both tools, run exactly as the tools run it:

1. **`UnicodeDetector`** — BOM inspection, then structural validation of BOM-less
   UTF-8/16/32 candidates.
2. **[UTF.unknown](https://github.com/CharsetDetector/UTF-unknown)** — a port of
   Mozilla's universal charset detector, for legacy code pages.
3. **`TextValidation`** — independent strict-decode validation of whatever step 1
   or 2 claimed. A candidate that cannot decode the bytes is rejected.

Plus, for the audit only, `EncodingConverter` — the actual file conversion.

### Targets

| Target | Version | Role |
|---|---|---|
| [EncodingChecker](https://github.com/amrali-eg/EncodingChecker) | v3.6.0 | Detects and converts file encodings |
| [LineEndingNormalizer](https://github.com/amrali-eg/LineEndingNormalizer) | v1.4.0 | Normalizes line endings without changing encoding |

Both embed the same detector. `TextValidation.cs` is byte-identical across
EncodingChecker, LineEndingNormalizer and this repository's `CorpusTesting`
apart from the namespace — see [Shared code](#shared-code).

### Corpora

None are vendored here; all four are public and independently maintained.

| Corpus | Source | Files | Ground truth |
|---|---|---|---|
| **UnicodeTestSuite v3.0** | https://github.com/amrali-eg/UnicodeTestSuite | 1,367 | `Manifest.csv` — records the encoding each fixture was written with, plus a SHA-256 per file |
| **chardet `test-data`** | https://github.com/chardet/test-data | 3,166 | `CATALOG.md` — top-level directory named `{encoding}` or `{encoding}-{language}` |
| **charset-normalizer `char-dataset`** | https://github.com/Ousret/char-dataset | 478 | Parent directory name |
| **UTF.unknown 2.6 tests** | https://github.com/CharsetDetector/UTF-unknown | 67 | Parent directory name |

**Ground truth is never derived from filenames.** Files present on disk but
absent from the corpus's own manifest or catalogue are skipped as having no
ground truth rather than guessed at. For UnicodeTestSuite the manifest entry is
additionally cross-checked against the filename's encoding token; a disagreement
is reported as a metadata conflict rather than silently resolved in either
direction. (Across all four corpora, zero conflicts were found.)

---

## Results — detection

Scored per **encoding class**: the ten Unicode variants (each BOM and BOM-less
form separately), plus `Ascii` and `Legacy`. Micro-averaged multi-class confusion
matrix — every class the detector can claim gets its own TP/FP/FN/TN, and
accuracy, FPR and FNR are summed over all of them.

`Ascii` and `Legacy` were once a single "non-Unicode" bucket, which made three
very different outcomes indistinguishable: correctly identified as a legacy code
page, correctly identified as ASCII, and nothing identified at all. The pipeline
genuinely answers all three, so each is scored on its own.

### Summary

| Corpus | Processed | Accuracy | FPR | FNR | Mismatches | Errors |
|---|---:|---:|---:|---:|---:|---:|
| UnicodeTestSuite v3.0 | 1,359 | **96.62%** | 0.11% | 2.23% | 46 | 0 |
| chardet `test-data` | 3,137 | **89.26%** | 0.46% | 10.77% | 337 | 0 |

8 UTS files and 29 chardet files were skipped as having no ground truth
(repository metadata, uncatalogued data).

### Per class — UnicodeTestSuite v3.0

| Class | Accuracy | TP | FN | FP | FNR | FPR |
|---|---:|---:|---:|---:|---:|---:|
| `utf-8` | 100.00% | 168 | 0 | 0 | 0.00% | 0.00% |
| `utf-8-bom` | 99.93% | 101 | 0 | 1 | 0.00% | 0.08% |
| `utf-16LE` | 99.93% | 76 | 0 | 1 | 0.00% | 0.08% |
| `utf-16LE-bom` | 99.93% | 77 | 0 | 1 | 0.00% | 0.08% |
| `utf-16BE` | 99.93% | 76 | 0 | 1 | 0.00% | 0.08% |
| `utf-16BE-bom` | 99.93% | 76 | 0 | 1 | 0.00% | 0.08% |
| `utf-32LE` | 100.00% | 67 | 0 | 0 | 0.00% | 0.00% |
| `utf-32LE-bom` | 99.93% | 67 | 0 | 1 | 0.00% | 0.08% |
| `utf-32BE` | 100.00% | 67 | 0 | 0 | 0.00% | 0.00% |
| `utf-32BE-bom` | 99.93% | 67 | 0 | 1 | 0.00% | 0.08% |
| `ascii` | 100.00% | 119 | 0 | 0 | 0.00% | 0.00% |
| `legacy` | 97.13% | 310 | 29 | 10 | 8.55% | 0.98% |

The Unicode families are effectively solved on this corpus, and the shape of the
46 mismatches is worth stating precisely, because it is favourable:

- **All 17 false positives are `Binary` fixtures** — files `11_InvalidUnicode/`
  and `13_Binary/` declare as not-text, which the detector nonetheless named.
  Deliberate bait: a file consisting of nothing but a valid BOM, alternating
  bytes that look like UTF-16, ASCII sprinkled with NULs.
- **All 29 false negatives were reported as `(undetected)`** — 22 single-byte
  legacy files, 6 EUC-JP, 1 GB18030, on which the detector declined to answer.

So on this corpus the detector never mislabels a real text file as the *wrong*
encoding. Every error is either a claim about a file that is not text, or a
refusal to claim anything. Refusing is the safe failure: EncodingChecker skips
what it cannot name, leaving the file untouched.

### Per class — chardet `test-data`

| Class | Accuracy | TP | FN | FP | FNR | FPR |
|---|---:|---:|---:|---:|---:|---:|
| `utf-8` | 99.97% | 267 | 1 | 0 | 0.37% | 0.00% |
| `utf-8-bom` | 100.00% | 151 | 0 | 0 | 0.00% | 0.00% |
| `utf-16LE` / `-bom` | 100.00% | 154 / 205 | 0 | 0 | 0.00% | 0.00% |
| `utf-16BE` / `-bom` | 100.00% | 153 / 15 | 0 | 0 | 0.00% | 0.00% |
| `utf-32LE` / `-bom` | 100.00% | 153 / 153 | 0 | 0 | 0.00% | 0.00% |
| `utf-32BE` / `-bom` | 100.00% | 153 / 1 | 0 | 0 | 0.00% | 0.00% |
| `ascii` | 94.96% | 32 | 0 | 158 | 0.00% | 5.09% |
| `legacy` | 89.29% | 1,355 | 336 | 0 | 19.87% | 0.00% |

This corpus is deliberately hostile: it is built from real-world legacy text in
encodings chosen to be hard. The `ascii` FP and `legacy` FN columns are largely
the same files counted from both sides — all 336 legacy false negatives were
reported either as `ascii` (157) or as nothing at all (179).

Of the 158 `ascii` false positives, **145 are UTF-7**, whose bytes are entirely
ASCII by design: UTF-7 encodes non-ASCII characters as `+...-` escape sequences
made of ASCII bytes. Nothing in the byte stream distinguishes such a file from
plain ASCII, and .NET has no UTF-7 code page to name it with even if it did. The
remaining 13 are 12 `hp-roman8` files and one `utf-8` file whose sampled region
happens to contain no non-ASCII byte.

This matters for what follows: converting a file the detector called `ascii`
never corrupts anything — the bytes are left alone. But a UTF-7 file left alone
still contains UTF-7 escapes rather than real text, which the audit records
separately as `NoOpMislabeled` rather than as either a pass or a corruption.

---

## Results — conversion audit

Per file, the audit answers one question:

```
strict-decode(original bytes, reference codec + BOM)
    == strict-decode(converted bytes, target codec)
```

Exact Unicode code-point equality. No normalization of any kind — no NFC/NFD, no
case folding, no newline or whitespace normalization. No replacement characters,
no best-fit substitution: a decode that cannot complete is a failure, not a
degraded success.

### Four metrics, reported separately

A single blended "accuracy" would average silent data loss against files that
merely happened to be ASCII, so these are never combined:

| Corpus | Files | Detection (exact codec) | + byte-equivalent | Text preserved | Codec divergences |
|---|---:|---:|---:|---:|---:|
| UnicodeTestSuite v3.0 | 1,367 | 1011/1300 (77.8%) | 92.8% | 1206/1270 (**95.0%**) | 1 |
| chardet `test-data` | 3,166 | 2265/3131 (72.3%) | 79.0% | 2427/2951 (**82.2%**) | 45 |
| charset-normalizer | 478 | 418/469 (89.1%) | 94.5% | 399/459 (**86.9%**) | 44 |
| UTF.unknown 2.6 | 67 | 62/64 (96.9%) | 96.9% | 62/62 (**100.0%**) | 0 |
| **All** | **5,078** | **3756/4964 (75.7%)** | **84.3%** | **4094/4742 (86.3%)** | **90** |

Strict-decoding correctness — does the tool refuse bytes its chosen codec cannot
represent, rather than substituting? — is **5,023 / 5,023** as of EncodingChecker
v3.6.0. It was 5,022 / 5,023 before; see [What this found](#what-this-found).

### Outcomes across all 5,078 files

| Outcome | Files | Meaning |
|---|---:|---|
| `PASS` | 3,988 | Converted, text identical |
| `Misdetection` | 424 | Wrong codec named; text differs |
| `UnknownEncoding` | 262 | Detector named nothing; file left untouched |
| `NoOpMislabeled` | 251 | Bytes unchanged but the label was wrong — mostly UTF-7 reported as `us-ascii` |
| `CodecDivergence` | 90 | Right codec, but .NET and the reference implementation map it differently |
| `OutOfScope` | 37 | Repository metadata, not corpus fixtures |
| `NoReferenceEncoding` | 16 | Corpus explicitly declares "no encoding" |
| `ReferenceDecodeError` | 6 | The corpus's own declared codec cannot decode the file |
| `UnknownReferenceEncoding` | 2 | No Python codec exists (`euc-tw`, `viscii`) |
| `DecodeError` | 2 | Refused at decode — the correct outcome for unrepresentable bytes |

The last four are reported but **never scored**: without authoritative ground
truth, counting them as either pass or fail would be a claim the evidence does
not support.

### Detection accuracy is split three ways

75.7% exact is the strict number, but it conflates two different things:

- **Byte-equivalent labellings (428 files).** Re-encoding the text with the codec
  the detector named reproduces the file byte-for-byte, so the disagreement
  cannot lose anything — a pure-ASCII UTF-8 file reported as `us-ascii` is the
  common case. Established by re-encoding, **not** by trusting the corpus's own
  compatibility metadata.
- **No .NET codec at all (297 files).** `hp-roman8`, `kz1048`, `ptcp154`,
  `iso-8859-10/14/16`, `cp720`, `utf-7` and others have no .NET code page, so no
  detector could have named them. Excluding those raises the total to **80.5%**.

### Where the remaining errors actually are

The 90 codec divergences are dominated by known Microsoft-vs-Unicode mapping
differences in the Japanese code pages, not by detector error:

| Reference | Expected | .NET produced | Files |
|---|---|---|---:|
| `shift_jis` | U+301C wave dash | U+FF5E fullwidth tilde | 30 |
| `euc_jp` | U+301C wave dash | U+FF5E fullwidth tilde | 24 |
| `shift_jis` | U+2212 minus | U+FF0D fullwidth hyphen | 13 |
| `gb2312` | U+2015 horizontal bar | U+2014 em dash | 10 |
| `euc_jp` | U+2212 minus | U+FF0D fullwidth hyphen | 8 |

These are properties of .NET's code-page tables, not bugs in either tool.

### Forced-reference experiment

For every file that did not pass, the audit re-decodes it with the authoritative
codec, bypassing detection entirely. This separates three causes that look
identical in an aggregate:

| Outcome | Files | What it proves |
|---|---:|---|
| `PASS` in both modes | 569 | The codec was right all along — a pure **detection** error |
| `DIFFERS` in both modes | 106 | The codecs genuinely disagree — a **conformance** difference |
| `DIFFERS` / strict `Decode` | 20 | A strict codec refuses; the old idiom silently altered the text |
| `NoDotNetCodec` | 276 | .NET has no codec for the reference encoding |

---

## What this found

The audit was built to check conversion fidelity and found a latent correctness
defect in the process.

Assigning `Decoder.Fallback` or `Encoder.Fallback` **after** calling
`GetDecoder()`/`GetEncoder()` has no effect for encodings from
`CodePagesEncodingProvider`. Those codecs take their fallbacks from the parent
`Encoding` at construction, so the assignment is silently ignored and unmappable
input is *replaced* instead of raising:

```
EUC-JP bytes carrying a JIS X 0212 sequence (SS3 0x8F)
   GetDecoder() then assign .Fallback   ->  mangled text, no exception
   GetEncoding(cp, fb, fb).GetDecoder() ->  DecoderFallbackException
```

Two consequences:

- **Conversion** reported files as successfully converted while substituting
  characters. The existing SHA-256 verification could not catch it: it hashes
  decoded source against decoded target, so both sides pass through the *same*
  lossy decoder and agree.
- **Validation** — the gate that independently checks the detector's answer —
  confirmed codecs that could not read the file, because the substituted
  characters still looked like text.

Fixed in EncodingChecker
[#36](https://github.com/amrali-eg/EncodingChecker/pull/36) (v3.6.0) and
LineEndingNormalizer
[#9](https://github.com/amrali-eg/LineEndingNormalizer/pull/9) (v1.4.0), and in
this repository's copy.

**Measured blast radius: 4 files out of 5,078.** Stated plainly because the
honest magnitude matters more than a dramatic one — this was a latent hole, not
mass corruption. It rarely fired because detection usually picks a codec that
*can* decode the bytes. The forced-reference experiment shows the exposure: 20
files decode differently under the old idiom and are correctly refused under the
new one.

Before/after across the full corpus set: **8 files changed outcome, all
improvements, zero regressions.**

---

## Reproducing the results

### Requirements

- .NET SDK 8.0+ (this repository) and 10.0+ (to build EncodingChecker)
- Python 3.10+ (the audit driver)
- The four corpora, cloned anywhere; set `CORPUS_ROOT` to their parent directory

Expected layout under `CORPUS_ROOT`:

```
UnicodeTestSuite-v3.0/     from amrali-eg/UnicodeTestSuite (release v3.0)
test-data-main/            from chardet/test-data
Charset-Normalizer data/   from Ousret/char-dataset
UTF-unknown-2.6 tests/     from CharsetDetector/UTF-unknown (tests/Data)
```

### Detection testers

```bash
dotnet build CorpusTesters.slnx -c Release

export CORPUS_ROOT=/path/to/corpora     # or set it in the Windows environment
scan_UTS3.0.cmd
scan_chardet_data.cmd
```

Or directly:

```bash
UnicodeSuiteTester.exe <CorpusFolder> [FilenamePattern] [-report <ReportFile>]
ChardetDataTester.exe  <CorpusFolder> [FilenamePattern] [-report <ReportFile>]
```

`FilenamePattern` defaults to `*`. Without `-report`, a timestamped report is
written next to the executable (`UTS_Report_<yyyyMMdd_HHmmss>.txt`,
`Chardet_Report_<yyyyMMdd_HHmmss>.txt`). Console output and report file are
identical, summary first. Reports are gitignored — they are generated output.

### Conversion audit

```bash
export CORPUS_ROOT=/path/to/corpora
export EC_REPO=/path/to/EncodingChecker    # if not a sibling of this repo

cd audit
./run-all.sh baseline
python tools/summarize.py runs/baseline
```

To compare two builds of EncodingChecker — which is how the defect above was
quantified — audit each and join them per file:

```bash
./run-all.sh fixed
python tools/compare.py runs/baseline runs/fixed --out reports
```

`compare.py` joins on `(corpus, path)` rather than comparing totals, because
totals that are unchanged can still conceal files moving in both directions.

Each run writes per-file evidence (`audit.csv`, ~45 columns), a pre-conversion
inventory with SHA-256 per file, first-difference detail for every mismatch, and
a `run.json` recording the exact assembly hash, git commit, and tree state of the
build under test. Full detail in [`audit/README.md`](audit/README.md).

### Safety

The audit never writes to a source corpus. Each is copied into a working
directory the harness creates and deletes, and only the copy is converted. This
is verified after every run: all 1,359 UnicodeTestSuite fixtures stay
byte-identical to their manifest SHA-256, and the other three corpora are checked
for stray `.bak`/`.tmp` artifacts.

---

## Projects

| Project | Purpose |
|---|---|
| `CorpusTesting` | Shared library: the detection pipeline, ground-truth models, classification, statistics, report writing |
| `UnicodeSuiteTester` | Detection tester for UnicodeTestSuite v3.0 |
| `ChardetDataTester` | Detection tester for the chardet corpus |
| `audit/` | Conversion forensic audit — Python driver plus `ECDiag`, a .NET harness exposing codec construction over JSON |

All target `net8.0`.

## Two accuracy numbers, and why they differ

The testers and the audit both report an "accuracy" for the same corpus, and they
are not the same measurement:

- The testers score the **encoding class**. Naming a legacy file `Legacy` is
  correct — UnicodeTestSuite scores **96.6%**.
- The audit scores **codec identity** by .NET code page. Naming a `windows-1252`
  file as `iso-8859-1` is wrong, even though both are `Legacy` — the same corpus
  scores **77.8%**.

Both are right about different questions. Quote whichever answers the question
being asked, and say which one it is.

## Shared code

`CorpusTesting/TextEncoding.cs`, `TextValidation.cs` and `UnicodeDetector.cs` are
copies of the same files in EncodingChecker and LineEndingNormalizer.
`TextValidation.cs` is byte-identical across all three apart from the namespace.

Nothing enforces that. A defect in one copy is almost certainly present in the
other two — which is exactly how the strict-fallback defect reached all three at
once and needed three separate fixes. When changing any of these files, check the
other two repositories before assuming the change is complete, and prefer changes
that keep `TextValidation.cs` identical across them.

## What safety this actually buys

The point of all of the above is a claim about data loss. Stated separately for
each tool and each class of input, because the guarantees genuinely differ.

### EncodingChecker — it re-encodes, so it carries real risk

Every file goes through decode and re-encode, so a wrong codec produces a wrong
file. Measured over the files EC actually **rewrote** (files it skipped or left
byte-identical cannot have lost anything):

| Source | Rewritten | Text preserved | Changed |
|---|---:|---:|---:|
| Unicode + ASCII | 1,832 | 1,825 (**99.62%**) | 3 |
| Legacy code page (.NET has a codec) | 2,022 | 1,602 (**79.23%**) | 419 |
| No .NET codec exists | 112 | 21 (18.75%) | 76 |

**Unicode and ASCII input is effectively safe.** All three failures are the same
case: BOM-less UTF-16BE detected as UTF-16LE. With a BOM, or with any UTF-8
input, no file in 5,078 lost content.

**Legacy input is where the risk lives**, and not because of the converter — the
converter is exact once it has the right codec. The failures are detection
failures: single-byte code pages are mutually decodable, so `windows-1252` text
is perfectly valid `iso-8859-1` text, and nothing in the bytes says which was
intended. The forced-reference experiment isolates this: given the correct codec,
569 of those files convert perfectly.

**Encodings .NET has no codec for cannot be handled at all** — `hp-roman8`,
`kz1048`, `ptcp154`, `iso-8859-10/14/16`, `utf-7`. EC's honest behaviour here is
to decline, and it usually does (244 of 356 untouched).

What protects you in the remaining cases: EC refuses rather than guesses when the
detector names nothing, verifies every write by re-decoding and hashing before
installing it, never rewrites in place, and can keep a `.bak`. A misdetected
single-byte file is also usually *recoverable* — the mapping is bijective, so
converting back with the right codec restores the original text.

### LineEndingNormalizer — it does not re-encode, so most of that risk does not exist

LEN never changes a file's encoding, and for legacy files **it never decodes them
at all**:

- **Unicode input** is decoded strictly, normalized, and re-encoded in the *same*
  encoding, with the result hash-verified before installation. The re-encode is
  to the encoding the file already had, so there is no codec-mismatch failure
  mode — only the strict decode, which rejects malformed input rather than
  replacing it.
- **Legacy input is scanned as raw bytes.** Only `0x0D` and `0x0A` are ever
  rewritten; every other byte is copied through untouched. No decode, no
  re-encode, so no codec can lose anything.

That byte path is gated. Single-byte encodings are verified at runtime to map
`0x0D`/`0x0A` to CR/LF, which makes byte scanning provably safe when every byte
is one character. Multi-byte encodings must appear in an explicit allowlist —
Shift-JIS, EUC-KR, EUC-JP, GBK, GB18030, Big5 — each verified not to produce
`0x0D`/`0x0A` inside a multi-byte sequence. Anything else is treated as
undetected and left alone.

The practical consequence: **a misdetection that would corrupt a file under EC is
usually harmless under LEN**, because naming the wrong single-byte code page does
not change which bytes are CR or LF. LEN's exposure is narrower by construction,
not by a better detector — it is the same detector.

### What this audit does and does not cover

It measures **EncodingChecker's converter** end to end. It does **not** exercise
LineEndingNormalizer's writer: the two share a detector, so the detection results
above apply to both, but LEN's normalization path is covered by its own test
suite rather than by these corpus runs. The safety argument for LEN's byte path
is structural — it is verified by construction and by unit tests, not by a
5,078-file conversion sweep.

## License

The corpora are the property of their respective projects and are subject to
their own licenses; none are redistributed here.
