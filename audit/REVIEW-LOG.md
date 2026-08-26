# Review response log

A running record of what was done with the independent methodology review of
[`REVIEW-REQUEST.md`](REVIEW-REQUEST.md), kept so the follow-up report to the
reviewer can be assembled from evidence rather than recollection.

Each entry records the point, the verdict reached, what changed, and — where it
matters — what the change actually found. Points accepted without qualification
are as interesting as points pushed back on, and both are recorded.

**Status at a glance**

| | Points |
|---|---:|
| Accepted and implemented | 4 |
| Accepted, not yet implemented | 4 |
| Partially accepted | 2 |
| Pushed back on | 2 |

---

## Implemented

### §4 — Byte-equivalence tests the wrong property · **accepted, fixed**

**The point.** The harmlessness test asked
`encode(reference_text, detected_codec) == original_bytes`, a byte round-trip
property. It does not establish that *decoding* those bytes with the detected
codec yields the same text, and the two come apart wherever a mapping is
many-to-one. Decoding is also what EC actually does.

**Verdict: correct.** Now tests
`decode(original_bytes, detected_codec) == reference_text`.

**What it found.** The two agree exactly on the current corpora — the same 434
files, complete overlap — so no published figure moved. Every one of those 434
was independently `TextIdentical=True`, so no conclusion had ever been wrong.
That is the corpus being kind rather than the test being right; a metric that
holds by coincidence is a latent defect, which is the ground the change was made
on.

Column renamed `DetectionByteEquivalent` → `DetectionTextEquivalent`.

`39b13b2`

### §15 — Mutation testing · **accepted, implemented**

**The point.** Every existing check asserts something is *right*; none fail when
the audit itself is wrong. That is how three defects shipped in one day, each
producing an internally consistent and entirely false result.

**Implemented** as `tools/test_audit_mutations.py` — 63 negative controls whose
correct verdict is known in advance and most of which are wrong on purpose:
ground-truth names that are both a codec and an encoding+language pair, text
mutations down to a single combining character, truncated and flipped bytes in
five multi-byte encodings, and the edge cases with a history here (BOM-only
files, reversed byte-order marks, codec aliases).

**Verified to bite.** Reintroducing the `utf-16-be` defect fails two controls
and names the exact wrong resolution. A test that has never failed has not been
shown to work.

Needs no corpus and no EC build, so CI runs it on every push. `4dfa6b2`

### §3 / §9 — "Divergence" implies a fault not in evidence · **accepted**

**The point.** Treating Python's registry as the universal oracle is not
justified; `CodecDivergence` asserts fault, and the README then had to insist
these were "not bugs in either tool" — a contradiction.

**Verdict: correct on terminology and on the overstatement.** Renamed to
`MappingDifference`, and the README now separates three situations previously
reported as one number, each with the provoking bytes:

| Situation | Files |
|---|---:|
| Two published character maps of the same encoding (`0x8160` is U+301C under the JIS-derived map, U+FF5E under code page 932) | 86 |
| A *different encoding* answering to the same name — .NET resolves `tis-620` to code page 874, Windows-874, where bytes undefined in TIS-620 proper carry assignments | 2 |
| A revised vendor table (`mac-cyrillic` `0xFF`) | 1 |

The audit now states which bytes provoke a disagreement and declines to say
which mapping is authoritative, since for most of these that means choosing
between two published standards.

**Not adopted:** the full three-level oracle hierarchy. The specific
disagreements here were resolvable by inspection, which was cheaper than a
mapping-authority metadata layer. Revisit if a case appears that inspection
cannot settle. `4dfa6b2`

### §7 — Corpus silence is not decoder safety · **accepted, found a real gap**

**The point.** `5,020 / 5,020 strict-decoding correctness` says the corpora held
no invalid sequences for those codecs, not that the decoders would reject any.

**Verdict: correct, and it mattered.** `tools/check_codec_conformance.py`
constructs 36 malformed sequences directly — truncations, illegal leads, bad
continuations, overlong forms, unpaired surrogates, out-of-range code points,
broken escape sequences — across thirteen codecs, and runs them through both the
reference implementation and the codec construction EC uses. The second half is
the one that matters: Python refusing malformed input says nothing about EC.

**What it found, on the first run.** .NET code page 50220 accepts two malformed
ISO-2022-JP escape sequences the reference rejects: a truncated `ESC $` and an
unknown designator `ESC $ Z`.

Characterised rather than dramatised: .NET passes the bytes through as literal
characters — `61 62 63 1B 24` decodes to five characters, one per byte — so
nothing is dropped or substituted, and every byte involved is ASCII, so the
sequence re-encodes to itself and a conversion preserves the text. **A strictness
gap, not data loss.** EC will convert such a file rather than decline it.

Recorded in `ACKNOWLEDGED_PERMISSIVE` with that reasoning rather than left to
fail the build, since it is an unfixable platform property. Any gap *not* on
that list fails — verified by removing an entry and watching it go red.
`dc1f884`

---

## Accepted, not yet implemented

### §5 — Detection accuracy needs a different denominator

Agreed that `3756/4961` mixes situations that are not comparable. The proposed
taxonomy (reference-grounded → exact / text-equivalent / genuine misdetection /
observationally indistinguishable, with unsupported, skipped, no-reference and
out-of-scope alongside) is better than the current split.

The hard part is **observational indistinguishability**: deciding, per file,
whether two codecs could both have produced these bytes. That is computable —
decode under each candidate and compare — but it needs a candidate set per file,
which the audit does not currently retain.

### §11 / §12 — Coverage and risk as separate reporting

Agreed. Excluding unscored outcomes from *accuracy* is honest, but a reader
seeing `86.5% text preservation` should also see how much of the corpus never
underwent a valid comparison. Two reporting axes rather than one, and
`TextLossRisk` separated from `ConversionCoverage` — a skipped file has zero
data-loss risk and total conversion failure, and those are different facts.

### §16 — Independent oracle on a stratified sample

Agreed, and the cheapest real answer to the epistemic problem in §36. Not every
file needs a third implementation; a sample stratified by encoding, corpus, BOM,
size, detector result and mismatch category would catch reference-decoder bugs
without a multi-runtime harness.

### §37 — Safe-refusal rate as a fifth metric

Agreed, and arguably more important for a converter than another point of
detector accuracy. **Blocked**: it measures a safety posture EC does not yet
have. Needs the product work below first, or there is nothing to measure.

---

## Partially accepted

### §2 — BOM policy as a separate property

The measurement already exists: EC verifies BOM state separately
(`BomVerificationPassed`), and the audit records `ReferenceBOM` and
`DetectedBOM` per file. The gap is **reporting**, not measurement — the two
properties are not surfaced separately in the summary. Folded into the §11/§12
reporting work.

### §13 — Binary fixtures

Agreed they should not be scored as detection failures, and they are not.
Agreed they should appear in operational coverage, which is the §11/§12 change.
The current inconsistency the review identified — scored in the outcome table
but contributing to no metric denominator — is real and gets resolved there.

---

## Pushed back on

### §19 — Requiring two detectors to agree

The reviewer disagreed with their own framing of this question, and I agree with
their disagreement: two detectors share corpus biases, statistical assumptions
and Unicode tables, so agreement does not imply correctness. Their sharper
version — **a second independent validation decoder is worth more than a second
detector** — is the one worth pursuing, and it is cheaper.

### §28 — CLI backup on by default

**Declined**, with reasons. It is a breaking change for existing scripts, and
the CLI case is materially different from the GUI: it emits a machine-readable
report that CI retains, and that report is exactly what makes recovery
deterministic. The GUI default was justified *because* a GUI user will not have
kept one. Their `--no-backup --i-understand-this-is-destructive` variant is
worth considering on its own merits, but not the default flip.

---

## EC product work — not started

§17 (hard refusal conditions rather than a confidence threshold), §18 (ambiguous
single-byte codecs), §21/§34 (self-describing backup metadata carrying the
canonical code page, not just the codec name), §23 (dry-run diff), §25 (preflight
safety gate), §26 (refuse rather than best-effort), §27 (risk-showing
confirmation), §32 (conversion journal), §33 (batch-transactional install).

These belong in their own release cycle. §18 is the one that would eliminate the
largest real risk class measured — 419 of 2,021 rewritten legacy files came out
with different text, and single-byte code pages are mutually decodable, so
nothing in the bytes distinguishes them.

§21/§34 directly closes the recovery gap the audit found: 99.2% of bad
conversions are byte-recoverable, but only for someone who still knows which
codec was used, and that lives solely in the conversion report.

---

## Unrelated work completed in the same period

Not part of the review, recorded so the follow-up report does not imply the
review prompted it.

- **EncodingChecker v3.7.0** — GUI backup on by default and persisted; fixed a
  latent defect where `LoadSettings` returned early when no settings file
  existed, so the documented defaults were never applied on a first run.
- **GitHub Actions bumped off Node 20** across all four repositories —
  `checkout` v4→v7, `setup-dotnet` v4→v6, `setup-python` v5→v7,
  `upload-artifact` v4→v7, `download-artifact` v4→v8. The artifact pair needed
  checking rather than assuming, since UTS round-trips a hash file between
  matrix jobs; `pattern` and `merge-multiple` were verified against the
  published `action.yml` for both v4 and v8. Zero deprecation warnings remain.
- **EncodingChecker#14** closed as unreproducible after three years without a
  sample file.

---

## Reproducing any claim in this log

```bash
export CORPUS_ROOT=/path/to/corpora
cd audit && ./run-all.sh validation
python ../tools/check_audit_integrity.py runs/validation
python ../tools/test_audit_mutations.py
python ../tools/check_codec_conformance.py
```

All invariants hold across 5,078 rows; 63 negative controls hold; 70 malformed
sequences refused across 13 codecs with 2 acknowledged permissive cases.
