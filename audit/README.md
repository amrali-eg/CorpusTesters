# EC end-to-end Unicode conversion audit

A repeatable, evidence-first audit of what EncodingChecker actually does to real
files, across four independent corpora (5,078 files).

The question it answers, per file:

```
strict-decode(original bytes, authoritative reference codec + BOM)
              ==
strict-decode(converted bytes, target codec)
```

Exact Unicode code-point equality. No normalization of any kind — no NFC/NFD, no
case folding, no newline or whitespace normalization, no visual comparison.

## Running it

```bash
./run-all.sh baseline      # audit whatever EC build is currently compiled
./run-all.sh fixed         # same, into a second labelled run
python tools/compare.py runs/baseline runs/fixed --out reports
```

Individual corpus:

```bash
python audit.py --corpus uts3 --source <read-only corpus> --work <copy> \
                --out runs/baseline/uts3 --label baseline --forced-reference
```

`--strict` turns findings into exit codes: `1` for a conversion regression, `2`
for an audit-infrastructure failure (missing backup, failed hash, unclassified
file), `0` otherwise.

### How EC is invoked, and why it changed

From EncodingChecker v3.9.0, EC converts automatically only from Unicode and
ASCII. Legacy text is refused unless the caller names the source codec, because
a legacy byte stream does not identify the codec that wrote it.

That makes detection-only runs useless as a measure of conversion: every legacy
file comes back `Refused` and byte-identical, so there is no output to compare.
An audit that kept running EC unaided would report a preservation rate computed
over the handful of Unicode files it did convert — a number that looks like
success and measures nothing.

So the audit now supplies each corpus's own reference codec through `-From`,
grouping files so that one EC run carries one codec. The question changes
deliberately:

| Invocation | Question answered |
|---|---|
| default (`-From` reference codec) | When told what the bytes are, does EC preserve the text? |
| `--no-explicit-source` | What does EC do unaided? (Answer: declines, safely.) |

Both are worth running. The first is the fidelity measurement the corpora can
actually adjudicate; the second is the operational-coverage measurement, and its
summary says plainly that it is not measuring fidelity.

EC names codecs the IANA way and Python does not, and .NET's alias table is not
self-consistent — `cp1252` resolves, `cp1251` does not. Rather than carry a
translation table that would rot against a .NET upgrade, `resolve_ec_codec`
asks EC itself once per codec and caches the answer. Reference codecs EC accepts
under no spelling are reported in the summary and fall back to detection alone.

## Why the constraint model is still here

`constraint_on`, `CONSTRAINT_FLOOR` and `is_structure_bearing` measure, per
file, how tightly a codec constrains those particular bytes. EC used to carry an
equivalent model and no longer does — v3.9.0 replaced it with a fixed rule
(Unicode and ASCII convert automatically; everything else needs an explicit
source).

This is not drift left behind by that change, and it should not be deleted to
"catch up" with EC. It is now the audit's most useful independent contribution:
EC's rule treats every legacy encoding alike, and the open question is whether
that is too broad — whether a structurally constrained codec such as ISO-2022-JP
or GB18030 carries enough evidence to convert automatically, where a single-byte
code page plainly does not. The constraint measurement is the evidence base for
answering that, and it only has weight because it is computed independently of
EC.

## Safety invariants

These are enforced by the harness, not left to discipline:

- **Source corpora are never written to.** `prepare_working_copy` copies the
  read-only source into a working directory it creates and deletes itself, and
  every conversion runs against that copy. Verified after each run: all 1,359
  UTS3 fixtures remain byte-identical to their `Manifest.csv` SHA-256, and the
  other three corpora contain no `.bak`/`.tmp` artifacts.
- **`.bak` integrity is checked before it is trusted.** Every backup is hashed
  against the pre-conversion inventory; a mismatch is `BackupIntegrityFailure`,
  never a silent pass.
- **`AlsoValidAs` is never consulted for ground truth.** It records which answers
  a *detector* may acceptably give, not which codec wrote the bytes. Using it
  here would let a wrong decode pass as correct.
- **All audit decoding is strict.** No replacement characters, no best-fit
  substitution. A decode that cannot complete is recorded as a failure.

## Ground truth, per corpus

| Corpus | Source of truth | Notes |
|---|---|---|
| `uts3` | `Manifest.csv` `Encoding` + `BOM` | Cross-checked against the filename's encoding token; disagreement is `MetadataConflict`, never a guess. Manifest membership also defines scope. |
| `chardet` | Top-level directory, `{encoding}` or `{encoding}-{language}` | Per the corpus's own `CATALOG.md`. |
| `charsetnormalizer` | Immediate parent directory | |
| `utfunknown26` | Immediate parent directory | |

Python and .NET spell the same codec differently often enough that comparing
labels would score spelling disagreements as detection errors (`cp949` vs
`ks_c_5601-1987`, `ibm866` vs `cp866`, `utf-16le` vs `utf-16`). Detection is
therefore compared by **.NET code-page identity**, resolved by offering every
plausible spelling to .NET and taking the first it can construct. Where no code
page is available on either side, the comparison falls back to the Python codec
registry's canonical name and says so in `DetectionBasis`.

## The four metrics

Reported separately, never combined into one number — a single "accuracy" figure
averages silent data loss against files that merely happened to be ASCII.

1. **Detection accuracy** — did EC name the codec that wrote the bytes?
2. **Strict-decoding correctness** — does EC reject bytes its chosen codec cannot
   represent, rather than substituting?
3. **Codec conformance** — where EC named the right codec, does its mapping table
   agree with the reference implementation?
4. **End-to-end text preservation** — is the output the same text as the input?

Detection accuracy is additionally split into exact matches, **byte-equivalent
labellings**, and substantive misdetections. A byte-equivalent labelling is one
where re-encoding the reference text with the codec EC named reproduces the file
exactly — a pure-ASCII UTF-8 file reported as `us-ascii` is the common case, and
nothing can be lost by the disagreement. This is established by re-encoding, not
by trusting compatibility metadata.

## PHASE 0 — establishing what the build actually does

The audit never assumes a codec is strict because a fallback was assigned to it.
Before any file is judged, each codec is probed with bytes and characters it
cannot represent, and its real behaviour is recorded.

This is what the audit was built to catch:

```
DECODER — EUC-JP bytes carrying a JIS X 0212 sequence (SS3 0x8F)
   GetDecoder() then assign .Fallback   ->  ・胃好，世界！   (no exception)
   GetEncoding(cp, fb, fb).GetDecoder() ->  DecoderFallbackException

ENCODER — "café" into code page 51932
   GetEncoder() then assign .Fallback   ->  63-61-66-65  ("cafe", no exception)
   GetEncoding(cp, fb, fb).GetEncoder() ->  EncoderFallbackException
```

Assigning `Decoder.Fallback`/`Encoder.Fallback` after `GetDecoder()`/
`GetEncoder()` has no effect for encodings from `CodePagesEncodingProvider`:
those codecs take their fallbacks from the parent `Encoding` when constructed.
The fallbacks must be supplied to
`Encoding.GetEncoding(codePage, encoderFallback, decoderFallback)`.

EC's own content-digest verification could not catch this: it hashes decoded
source against decoded target, so both sides pass through the same lossy decoder
and agree. Affected files were reported as `Converted`.

The PHASE 0 table characterises the *idiom* and is deliberately held constant
across runs, so the baseline and fixed builds are measured with the same
yardstick. Whether the build under test is actually affected is decided by what
EC did — files flagged `ECImplementationDefect` that EC nonetheless converted —
and is stated at the top of each report.

`NotTestable` in that table means no probe was rejected even by a correctly
constructed strict codec. For a single-byte code page every one of the 256 bytes
maps to a character, so there is nothing for a decoder to reject.

## Forced-reference experiment

For every file that did not pass, the audit re-decodes it with the authoritative
reference codec, bypassing detection, in two modes:

- `assignAfter` — replicates the assign-after-construction idiom
- `strictUpFront` — supplies the fallbacks to `GetEncoding`

The pair separates three causes that look identical in an aggregate:

| Outcome | Meaning |
|---|---|
| `assignAfter=PASS;strictUpFront=PASS` | EC simply picked the wrong codec — a detection error |
| `assignAfter=DIFFERS;strictUpFront=DIFFERS` | The codecs genuinely disagree — a conformance difference |
| `assignAfter=DIFFERS;strictUpFront=Decode` | The strict codec refuses; the shipped idiom silently altered the text |
| `NoDotNetCodec` | .NET has no codec for the reference encoding at all |

## Output layout

```
runs/<label>/<corpus>/
    audit.csv               per-file evidence (one row per file, ~45 columns)
    inventory.csv           pre-conversion state: size, SHA-256, reference codec
    summary.csv             aggregated by (reference, detected) encoding pair
    summary.md              the readable report
    metadata-summary.csv    declared vs resolved reference encodings
    audit-details.json      first-difference detail for every mismatch
    run.json                provenance: EC assembly SHA-256, commit, tree state,
                            .NET/Python/OS versions, full PHASE 0 probe results
    logs/                   EC's own conversion report, unmodified
reports/
    before-after-summary.csv / .md / -metrics.json
```

`run.json` records the SHA-256 of `EncodingChecker.dll`, not the `.exe`: the
apphost launcher is byte-identical across builds and would not identify which
code was actually tested.

## Tools

| Tool | Purpose |
|---|---|
| `tools/summarize.py <run>` | Cross-corpus roll-up: defect reach, divergence signatures, forced-reference outcomes |
| `tools/detection.py <run>` | Detection accuracy split by whether EC has a codec for the encoding at all |
| `tools/compare.py <before> <after>` | Per-file join of two runs; classifies every change as improvement, regression, or lateral |
| `ECDiag/` | .NET harness exposing EC's codec construction to the Python driver over JSON |

`tools/compare.py` joins per file rather than comparing totals, because totals
that are unchanged can still conceal files moving in both directions. It exits
`2` on a material distribution shift, and `2` when either run directory is
missing or holds no `*/audit.csv` — a comparison that cannot find its input must
fail rather than report "no change".

## Controls on the instrument

The audit judges everything else, so the question that matters about any number
it reports is not whether the number looks right. It is:

> What control proves this instrument could have reported the opposite result?

That question has a specific history. Across the v3.9.0 hardening work, most
findings were defects in the measurement rather than in EncodingChecker: a
results collector that silently dropped entries under `Parallel.ForEach`; a
smoke harness whose failure state was indistinguishable from success; a codec
probe that read *absence of a rejection message* as acceptance, so a crash
counted as support; and a classification rule that misfiled 1,344 files while
every total still reconciled. Each produced an internally consistent, entirely
false result.

The controls live in the repository root `tools/`, one level up from this
directory. Most need no corpus, which is the point — they run on every push
rather than only when someone remembers to audit:

| Control | Runs | What it would catch |
|---|---|---|
| `../tools/test_audit_mutations.py` | every push | 93 negative controls: deliberately broken inputs whose correct verdict is known in advance. Fails if the audit reports success for any of them |
| `../tools/check_codec_strictness.py` | every push | The two platform facts PHASE 0 rests on. If .NET ever changes either, every classification silently changes meaning |
| `../tools/check_codec_conformance.py` | every push | Malformed sequences constructed directly, one codec family at a time — because a corpus can only exercise the invalid bytes it happens to contain |
| `../tools/check_detector_drift.py` | every push | The detector sources copy-pasted across three repositories have diverged |
| `../tools/check_audit_integrity.py <run>` | after a run | Re-derives a completed run from the files on disk: coverage, one primary outcome per row, hashes recomputed from original bytes, reconciliation in both directions, and a random sample decoded independently of the audit's own code path |
| `../tools/independent_oracle.py` | before publishing | The audit's reference decoder disagreeing with libiconv and ICU, which share no code with Python's |

CI runs the first four across three jobs (`build`, `codec-strictness`,
`detector-drift`); the codec jobs build `ECDiag` first, and `detector-drift`
checks out all three repositories. `check_audit_integrity.py` is exercised in CI
only as `--help`, which runs its own logic without a corpus — the full check
needs a completed run and belongs to the release sequence, alongside
`independent_oracle.py`.

Two further controls live inside `tools/compare.py` rather than in that table,
and both deserve their reasoning stated: each was added after an existing check
passed while the instrument was wrong.

**Reconciliation proves completeness, not correctness.** Every file having
exactly one outcome that adds up in both directions was true throughout the
1,344-file misclassification — the files were filed consistently and wrongly.
`distribution_shifts` in `tools/compare.py` is the separate gate: it asks
whether many files moved *category* between runs, over the files present in
both, and alarms at one percentage point. A classifier that is complete but
newly wrong moves a population; that is the signature reconciliation cannot see.

**A check must be able to fail on its own input.** `compare.py` globs
`*/audit.csv`, and a directory that was missing, empty, or held no finished
corpus produced `{}` — every metric read `n/a` and the run exited `0`, a check
reassuring its caller about work it never looked at. The control for it drives
the real entry point as a subprocess rather than calling the helper, because the
defect lived in `main()`'s handling of an empty load: a control that called
`distribution_shifts` directly would have passed while the tool stayed broken.

The general form of that second lesson is the one worth carrying: a control that
tests a layer below the defect proves nothing about the layer that shipped.
