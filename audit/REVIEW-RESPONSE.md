# Response to the independent methodology review

Reply to the review of [`REVIEW-REQUEST.md`](REVIEW-REQUEST.md). Methodology
corrections are kept separate from findings about the tool under test, because
conflating them is how an audit turns into a defence of its own implementation.

Every figure here comes from a run of the current harness; the commands to
reproduce them are at the end.

---

## 1. Executive outcome

Eight of the review's points were accepted and implemented, two accepted and
queued, two declined with reasons.

**The strongest result is not a better number.** It is that the original
detector metric conflated three different phenomena — exact codec
identification, observationally equivalent alternative labels, and genuine
detector errors — and that reclassification shows most of the apparent detection
errors were not substantive errors.

Two of the review's points found real defects rather than merely improving
presentation: the equivalence test was proved correct only by coincidence, and
the conformance suite exposed a .NET strictness gap no corpus run could have
revealed.

---

## 2. What the review got right

| § | Point | Outcome |
|---|---|---|
| 4 | Byte-equivalence proves a round-trip property, not that decoding gives the same text | **Correct** — fixed |
| 7 | Corpus silence is not decoder safety | **Correct** — found a real gap |
| 15 | No existing check fails when the audit itself is wrong | **Correct** — 63 controls added |
| 3 / 9 | Python's registry is an implementation oracle, not a normative one; "divergence" asserts fault | **Correct** — renamed and re-evidenced |
| 5 | Detection denominator mixes incomparable situations | **Correct** — seven outcomes |
| 11 / 12 | Coverage and risk are different questions | **Correct** — separate axes |
| 2 | BOM policy is independent of text preservation | **Correct** — reported separately |
| 13 | Binary fixtures belong in coverage, not scored as detection failures | **Correct** |
| 19 | Requiring two detectors to agree is not obviously safer | **Agreed with the reviewer's own disagreement** |
| 28 | CLI backup on by default | **Declined**, §8 |

---

## 3. Methodology changes implemented

**Byte-equivalence → text-equivalence.** Now tests
`decode(original_bytes, detected_codec) == reference_text`. The two agreed
exactly on these corpora — the same 434 files, complete overlap — so no figure
moved and no conclusion had ever been wrong. That is the corpus being kind
rather than the test being right. `39b13b2`

**Negative controls.** 63 inputs whose correct verdict is known in advance and
most of which are wrong on purpose. Verified to bite: reintroducing the
`utf-16-be` defect fails two controls and names the exact wrong resolution.
`4dfa6b2`

**Codec conformance suite.** 36 malformed sequences constructed directly across
thirteen codecs, run through both the reference implementation and the codec
construction EC uses. `dc1f884`

**Detection taxonomy and coverage reporting.** Seven outcomes; coverage, text-loss
risk and BOM policy as separate axes. `f56494c`

**Mapping differences no longer named as divergences.** `MappingDifference`
replaces `CodecDivergence`, and the three situations previously reported as one
number are separated with the provoking bytes. `4dfa6b2`

---

## 4. What the audit itself got wrong

Four defects were found in the instrument, each producing an internally
consistent and entirely false result, and each making a **correct** tool look
wrong.

**Corpus-prefix parsing.** `utf-16-be` is UTF-16 *Belarusian*; `be` is the ISO
639-1 code tagging fourteen other directories. Taking the longest codec match
read six little-endian files backwards. An issue was nearly filed against the
corpus maintainers.

**Sidecar inheritance.** `cp864-ar/_logical_source/` holds the logical-order
UTF-8 source for the shaped cp864 files, exactly as the corpus's `CATALOG.md`
says. Treating those two files as cp864 invented a ground truth never claimed.

**BOM-only files.** A file holding nothing but a BOM correctly converts to zero
bytes; the empty result was never decoded and fell through to a divergence.

**Files that were never converted, compared anyway.** A file EC declined to
identify is left untouched, so reading its bytes as the target codec compares
the reference against the *source* text and always differs.

### The failed discriminator

The first attempt at structural ambiguity is worth recording in full, because
it produced plausible-looking numbers from a broken test.

```
Initial approach
    count how many corpus-derived codecs decode the bytes

Failure
    codec availability in the test corpus became a hidden variable

    euc_jp -> windows-1257     classified as ambiguous, though EUC-JP
                               structure is real evidence

    iso8859-1 -> x-mac-ce      classified as misdetection only because
                               x-mac-ce was absent from the candidate set

Second approach
    multi-byte vs single-byte

Failure
    too broad as a general rule. Single-byte does not mean unconstrained:
    windows-1252 leaves 0x81, 0x8D, 0x8F, 0x90 and 0x9D undefined, so a byte
    sequence containing one of them is structural evidence against it.

Corrected approach
    measure the constraint the codec places on *these particular bytes*:
    the fraction of single-byte mutations of this file that the codec rejects
```

Measured per file, multi-byte codecs on real content reject 17–38% of mutations;
single-byte codecs on the same test reject 0%. A `windows-1252` file containing
an undefined byte would measure non-zero and be classified accordingly — which
is precisely the case the multi-byte rule would have got wrong.

The first error was caught by inspecting the resulting *pairs* rather than the
totals; the second by the reviewer.

---

## 5. What the corrected data now shows

| Detection outcome | Files |
|---|---:|
| `ExactMatch` | 3,756 |
| `TextEquivalent` | 401 |
| `StructurallyAmbiguous` | 262 |
| `Misdetection` | 69 |
| `NoDotNetCodec` | 297 |
| `NotIdentified` | 177 |
| `NoReference` | 116 |
| **Total** | **5,078** |

```
Reference-grounded / judgeable      4,488
Exact or text-equivalent            4,157 / 4,488 = 92.6%
Substantive misdetections              69 / 4,488
```

**92.6% is not "detection accuracy."** It is the *exact-or-text-equivalent
identification rate among judgeable files*. The distinction matters: a file
whose reference is UTF-8 and whose detected encoding is ASCII is text-safe to
convert, and that is not the same claim as EC having identified the source
encoding correctly. Three properties are kept independent throughout —
`DetectionIdentity`, `TextEquivalence`, `ConversionFidelity`.

`StructurallyAmbiguous` is claimed conservatively and per file: the observed
bytes do not distinguish the candidates, and both are valid under strict
decoding. It is not inferred from both codecs being single-byte.

### The 69 substantive misdetections

| Reference | Detected | Files | Signal that existed | Text preserved |
|---|---|---:|---|---|
| `gb2312` | `gb18030` | 11 | reference codec constrains these bytes | no |
| `euc_jp` | `windows-1257` | 9 | reference codec constrains these bytes | no |
| `euc_jp` | `windows-1252` | 8 | reference codec constrains these bytes | no |
| `gb18030` | `windows-1252` | 8 | reference codec constrains these bytes | no |
| `gb2312` | `windows-1252` | 7 | reference codec constrains these bytes | no |
| `shift_jis` | `windows-1252` | 6 | reference codec constrains these bytes | no |
| `euc_kr` | `windows-1252` | 4 | reference codec constrains these bytes | no |
| `gb18030` | `euc-jp` | 4 | reference codec constrains these bytes | no |
| `euc_jp` | `iso-8859-3` | 3 | reference codec constrains these bytes | no |
| `big5` | `windows-1252` | 2 | reference codec constrains these bytes | no |
| `cp865` | `windows-1257` | 1 | **detected codec cannot read the whole file** | no |
| others | | 6 | reference codec constrains these bytes | no |

68 of the 69 came out with different text; one was not comparable. The dominant
shape is a multi-byte encoding read as single-byte: the file's sequence
structure was available evidence and was not used. The `cp865` case is the other
signal — the detected codec passed on EC's sample and fails on the full file.

### Other corrected figures

```
Text preservation among rewritten files    3,455 / 3,950 = 87.47%
BOM state preserved                        1,300 / 1,300
Silent-decoder defect                      4 / 5,078 observed
Mapping differences                        89
```

---

## 6. EC defects confirmed

**Strict fallback construction.** Assigning `Decoder.Fallback` after
`GetDecoder()` has no effect for `CodePagesEncodingProvider` encodings; the
codec takes its fallbacks from the parent `Encoding` at construction. EC shipped
that pattern, so files whose bytes its own codec could not represent were
converted with substituted characters and reported as converted. EC's SHA-256
verification could not catch it — it hashes decoded source against decoded
target, so both sides pass through the same lossy decoder and agree.

Fixed in v3.6.0. Observed blast radius: **4 files of 5,078**.

**.NET ISO-2022-JP strictness gap**, found by the conformance suite the review
asked for. Code page 50220 accepts a truncated `ESC $` and an unknown
designator `ESC $ Z` that the reference rejects. It passes the bytes through as
literal characters — `61 62 63 1B 24` decodes to five characters, one per byte
— so nothing is dropped and the sequence re-encodes to itself. A strictness gap,
not data loss: EC converts such a file rather than declining it. Recorded as an
acknowledged platform property; any *unlisted* gap fails the build.

---

## 7. Remaining methodological limitation

The reference oracle is still Python's codec registry. §16 is
**accepted — pending independent validation; the current results must not be
interpreted as establishing that Python's mappings are universally
authoritative.**

The 89 mapping differences are reported without assigning fault, and separated
into two published character maps of one encoding (86 files), a different
encoding answering to the same name (`tis-620` resolving to Windows-874, 2
files), and a revised vendor table (1 file). A stratified independent-oracle
validation is being added separately and will be appended as a second-stage
finding.

The revised risk statement, which the reviewer's critique produced:

> The dominant observed end-to-end risk is source-encoding identification and
> ambiguity, while codec implementation differences and strictness defects
> remain distinct conversion risks.

The two interact: a detector can name the right encoding and the conversion
still alter text, because the implementation behind that name differs from the
reference.

---

## 8. Product-safety recommendations — planned separately

The review's robustness half is accepted as a body of work for its own release
cycle rather than folded in here. §18 addresses the largest measured risk class:
262 files where EC confidently named one of several indistinguishable single-byte
codecs. §21/§34 closes the recovery gap the audit found — 99.2% of bad
conversions are byte-recoverable, but only for someone who still knows which
codec was used, and that lives solely in the conversion report.

Two positions differ from the review:

**§28, CLI backup on by default — declined.** It breaks existing scripts, and
the CLI case is materially different: it emits a machine-readable report that CI
retains, which is exactly what makes recovery deterministic. The GUI default was
justified *because* a GUI user will not have kept one.

**§19, detector ensemble — the reviewer's own second thought is right.** Two
detectors share corpus biases and Unicode tables, so agreement does not imply
correctness. A second independent *validation decoder* is worth more and costs
less.

---

## 9. Remaining work

| Item | Status |
|---|---|
| §16 independent oracle, stratified sample | Accepted, next |
| §5 per-file candidate sets | Partially done; ambiguity is measured, candidate enumeration is not retained |
| §18 ambiguity refusal | Product cycle |
| §21 / §34 self-describing backup metadata | Product cycle |
| §23 dry-run diff · §25 preflight gate · §26 refuse over best-effort | Product cycle |
| §27 risk-showing confirmation · §32 journal · §33 batch-transactional install | Product cycle |
| §37 safe-refusal metric | Blocked until the above exist to measure |

---

## Reproducing every figure

```bash
export CORPUS_ROOT=/path/to/corpora
cd audit && ./run-all.sh validation
python ../tools/check_audit_integrity.py runs/validation
python ../tools/test_audit_mutations.py
python ../tools/check_codec_conformance.py
```

Current state: all invariants hold across 5,078 rows; 63 negative controls hold;
70 malformed sequences refused across 13 codecs with 2 acknowledged permissive
cases.
