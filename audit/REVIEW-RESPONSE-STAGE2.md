# Second-stage validation: the independent oracle (§16)

Appendix to [`REVIEW-RESPONSE.md`](REVIEW-RESPONSE.md). It does not revise the
first-stage findings; where it changes what can be claimed, it says so and the
first-stage text stands as issued.

The question §16 exists to settle: the audit decodes with Python's codec
registry and calls that the reference. Is that reference corroborated, and
where it is not, does anything published depend on it?

## Method

Two implementations sharing no code with Python's:

| Oracle | What it represents |
|---|---|
| **GNU libiconv 1.19** | A separate C implementation |
| **Node.js 24 / ICU 78.3 `TextDecoder`** | The WHATWG Encoding Standard, as browsers implement it |

**1,033 files sampled from 646 strata** over the full 5,078. Stratified by every
taxonomy outcome — not only the failing ones, since a sample drawn from errors
alone cannot show that agreement holds where the audit says it does — and
further by corpus, reference encoding, BOM state, file-size bucket, and the
measured constraint value.

For `StructurallyAmbiguous` the constraint strata deliberately straddle the
0.10 threshold: `0.00`, `0.00–0.05`, `0.05–0.10 (just below)`,
`0.10–0.20 (just above)`, `>0.20`. Sampling around the boundary is the only way
to learn whether it generalises or merely happens to split this corpus.

BOMs are stripped on all three sides, matching the audit. An earlier run did not
do this and reported ICU "disagreeing" at index 0 on every BOM-bearing file —
15 manufactured findings from a comparison artifact rather than a mapping
difference.

## Question 1 — does the reference agree?

| Outcome | Files |
|---|---:|
| All implementations agree | 603 |
| No independent oracle for this codec | 374 |
| Reference differs from ICU | 30 |
| Reference differs from iconv | 15 |
| Reference differs from both | 3 |
| Reference cannot decode | 8 |

**Among sampled files for which an independent oracle was available, 603 of 651
agree completely.** This is an agreement rate within the sample, not an estimate
that 92.6% of the corpus agrees: the strata were chosen to cover the taxonomy
evenly, not to reproduce the corpus population, so sample proportions do not
transfer to the whole.

The 374 without an oracle are a real coverage limit, not a pass: `hp-roman8`,
`kz1048`, `ptcp154`, the EBCDIC pages and the DOS code pages are absent from one
or both implementations, so for those the reference remains uncorroborated.

## Question 2 — what kind of disagreement?

Every disagreement **in this sample** is a mapping or profile difference; none is
evidence of an implementation defect. That is a statement about the 1,033 files
examined — roughly a fifth of the corpus — and about the encodings for which an
oracle existed. It is not a finding that EC's codecs contain no implementation
defects, and the 374 files with no available oracle are precisely where such a
defect would be hardest to see.

| Reference | Other | Codecs | What it is |
|---|---|---|---|
| `U+301C` | `U+FF5E` (ICU) | `euc_jp`, `iso2022_jp` | JIS-derived map vs WHATWG index |
| `U+2212` | `U+FF0D` (ICU) | `shift_jis` | Same split |
| `U+223C` | `U+FF5E` (ICU) | `big5` | Same split |
| `U+2015` | `U+2014` (ICU) | `gb2312` | Same split |
| `U+007E` | `U+203E` (iconv) | `shift_jis` | JIS X 0201: tilde vs overline at 0x7E |
| `U+05E4` | `U+FB4E` (iconv) | `cp1255` | Hebrew presentation form |
| `U+00CA` | `U+1EBE` (iconv) | `cp1258` | Vietnamese combining vs precomposed |
| `U+0093` | `U+201C` (ICU) | `tis-620` | ICU resolves the name to Windows-874 |

**This settles the question the first-stage report deliberately left open.** On
the Japanese and Chinese wave-dash family the implementations split two against
two:

```
Python + iconv   ->  U+301C   (JIS-derived mapping)
ICU + .NET       ->  U+FF5E   (WHATWG index / code page 932)
```

The audit's reference is one of two camps, not the authority. Calling these
`MappingDifference` rather than a divergence or a defect was correct, and is now
supported by evidence rather than by caution. It also means **.NET is not an
outlier here** — it agrees with the standard browsers implement.

The `tis-620` case reproduces independently: ICU resolves that name to
Windows-874 exactly as .NET does, confirming the first-stage finding that this
is a different encoding answering to the same name rather than a mapping variant.

## Question 3 — does it change any audit classification?

In the stratified sample, **16 of 1,033 classifications (1.5%)** depended on
which independent implementation was treated as the source-text oracle. All 16
were otherwise text-preserving under the audit's Python reference; under another
oracle's reading of the *source*, the conversion would have altered text.

This is evidence about oracle sensitivity, not a population estimate. The sample
is stratified by taxonomy rather than weighted to the corpus, so 1.5% does not
bound the rate across all 5,078 files.

| Codec | Files | Disagreeing oracle |
|---|---:|---|
| `cp1258` | 9 | iconv |
| `shift_jis` | 4 | iconv, one also ICU |
| `cp1255` | 2 | iconv |
| `big5` | 1 | ICU |

No file moves in the other direction: nothing recorded as changed would become
preserved.

**What this means for the published figures.** The 87.47% text-preservation
result is a claim about preservation *as the reference implementation reads the
source*. The sample shows that claim is sensitive to the choice of reference for
a small minority of files. None of the 16 is a case where EncodingChecker did
anything wrong; the disagreement is upstream of it, in which published mapping
each implementation follows.

## What this does not establish

- **374 sampled files have no independent oracle.** For those encodings the
  reference is uncorroborated, and no claim of cross-implementation agreement
  should be read into their results.
- **Agreement is not correctness.** Python and iconv agreeing on `U+301C` shows
  two implementations chose the same published mapping, not that the mapping is
  authoritative. There is no oracle here that settles which map a file's author
  intended.
- **The sample is stratified, not exhaustive.** 1,033 of 5,078 files.

## The 0.10 constraint threshold

A 0.10 constraint threshold cleanly separated this corpus's structurally
ambiguous cases from the substantive ones — ambiguous files measured at most
0.083. **The threshold remains empirical and is subject to independent
validation.** It is a validated operating threshold for these corpora and is not
offered as an encoding-theoretic constant.

The boundary strata sampled here found no disagreement clustered near the
threshold, which is consistent with the separation being real but does not
establish it for corpora with different content.

## Reproducing

```bash
export CORPUS_ROOT=/path/to/corpora
python tools/independent_oracle.py audit/runs/validation --per-stratum 2
```

Requires `iconv` and `node` on PATH. Deterministic: the sample is seeded.
