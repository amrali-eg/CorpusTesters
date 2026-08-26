# CorpusTesters

Corpus-based testing for the encoding detection and conversion used by
[EncodingChecker](https://github.com/amrali-eg/EncodingChecker) and
[LineEndingNormalizer](https://github.com/amrali-eg/LineEndingNormalizer).

Two different questions live here, and they are deliberately kept apart:

| | Question | Where |
|---|---|---|
| **Detection testers** | Does the detector *name* the right encoding class? | `UnicodeSuiteTester`, `ChardetDataTester` |
| **Conversion audit** | Does a conversion *preserve the text* exactly? | `audit/` |

A detector can name an encoding correctly and the conversion still lose content,
and a conversion can round-trip perfectly on a file the detector mislabelled.
Neither number substitutes for the other.

## Projects

| Project | Purpose |
|---|---|
| `CorpusTesting` | Shared library: the detection pipeline (`TextEncoding`, `UnicodeDetector`, `TextValidation`), plus ground-truth models, classification, statistics and report writing |
| `UnicodeSuiteTester` | Runs the pipeline over [UnicodeTestSuite](https://github.com/amrali-eg/UnicodeTestSuite) v3.0 |
| `ChardetDataTester` | Runs the pipeline over the chardet `test-data` corpus |
| `audit/` | End-to-end conversion forensic audit — see [`audit/README.md`](audit/README.md) |

All four target `net8.0`.

## Running the detection testers

```bash
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
`Chardet_Report_<yyyyMMdd_HHmmss>.txt`). The `scan_*.cmd` scripts ask for the
fixed names `UTS_report.txt` and `Chardet_report.txt` in the repository root.
Both forms are gitignored — reports are generated output, not source.

### Ground truth

Neither tester derives ground truth from filenames.

- **UnicodeSuiteTester** reads the corpus's own `Manifest.csv`, which records the
  encoding each fixture was actually written with. Files on disk but absent from
  the manifest are skipped as having no ground truth rather than guessed at.
- **ChardetDataTester** parses the corpus's `CATALOG.md`. Repository files that
  are not catalogued test data (`README.md`, `CATALOG.md` itself) are skipped.

### What is measured

The full `TextEncoding` pipeline, exactly as the tools run it: `UnicodeDetector`,
then UtfUnknown, then independent validation.

Results are scored per **encoding class** — the ten Unicode variants (each BOM
and BOM-less form separately), plus `Ascii` and `Legacy`. `None` is not a class
of file but the absence of a claim, recorded when the pipeline names nothing.

`Ascii` and `Legacy` used to be one "non-Unicode" bucket, which made three very
different outcomes indistinguishable: correctly identified as a legacy code page,
correctly identified as ASCII, and nothing identified at all. The pipeline
genuinely answers all three, so each is scored on its own.

Reporting is a micro-averaged multi-class confusion matrix: every class the
detector can claim gets its own TP/FP/FN/TN, and accuracy, FPR and FNR are
reported over all of them. The console output and the report file are identical,
with the summary first.

## Two accuracy numbers, and why they differ

The detection testers and the audit both report an "accuracy", and they are not
the same measurement:

- The testers score the **encoding class** (`Utf8NoBom`, `Legacy`, `Ascii`, …).
  Naming a legacy file as `Legacy` is correct here.
- The audit scores **codec identity** by .NET code page — naming a `windows-1252`
  file as `iso-8859-1` is wrong there, even though both are `Legacy`.

So the testers report ~96.7% on UnicodeTestSuite where the audit reports ~77.8%,
and both are right about different questions. Quote whichever answers the
question being asked, and say which one it is.

## Relationship to the sibling repositories

`CorpusTesting/TextEncoding.cs`, `TextValidation.cs` and `UnicodeDetector.cs` are
copies of the same files in EncodingChecker and LineEndingNormalizer.
`TextValidation.cs` is byte-identical across all three apart from the namespace.

Nothing enforces that. A defect found in one copy is almost certainly present in
the other two — that is exactly how the strict-fallback defect
(EncodingChecker#36, LineEndingNormalizer#9) reached all three at once and needed
three separate fixes. When changing any of these files, check the other two
repositories before assuming the change is complete, and prefer changes that keep
`TextValidation.cs` identical across them.

## Corpora

Not vendored here. `CORPUS_ROOT` defaults to `C:/Users/Amr/Desktop/Corpus`:

| Corpus | Folder |
|---|---|
| UnicodeTestSuite v3.0 | `UnicodeTestSuite-v3.0` |
| chardet `test-data` | `test-data-main` |
| charset-normalizer | `Charset-Normalizer data` |
| UTF.unknown 2.6 | `UTF-unknown-2.6 tests` |

The audit treats all of them as read-only and converts only its own working
copies; see [`audit/README.md`](audit/README.md) for how that is enforced and
verified.
