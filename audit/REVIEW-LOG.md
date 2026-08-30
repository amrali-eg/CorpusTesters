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
| Accepted and implemented | 8 |
| Superseded by the reviewer's second-round correction | 1 |
| Accepted, not yet implemented | 2 |
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

### §5 / §11 / §12 / §2 / §13 — Taxonomy and reporting · **accepted, implemented**

Done as one change, because they are one change: separate denominators, a
coverage axis, BOM as its own property, and binary fixtures in coverage all
rewrite the same tables.

**Detection is now seven outcomes** rather than a single percentage:

| Outcome | Files |
|---|---:|
| `ExactMatch` | 3,756 |
| `TextEquivalent` | 401 |
| `StructurallyAmbiguous` | 262 |
| `Misdetection` | 69 |
| `NoDotNetCodec` | 297 |
| `NotIdentified` | 177 |
| `NoReference` | 116 |

Of 4,488 files where a detector could be judged at all, **4,157 (92.6%)** were
named exactly or named as a codec giving identical text.

**What the split revealed.** The old figure counted 331 mismatches as one
undifferentiated number. They are not one thing: **262 are structurally
ambiguous and only 69 are substantive.** Single-byte code pages map 256 values
independently, so a file valid in `windows-1252` is equally valid in
`iso-8859-1`, and no byte inspection decides between them — `cp850 →
windows-1252`, `koi8-u → koi8-r`, `mac-roman → iso-8859-1`. The substantive 69
are multi-byte encodings read as single-byte (`euc_jp → windows-1257`,
`gb18030 → windows-1252`), where the file's sequence structure was available
evidence and was not used.

**The discriminator took three attempts.** Candidate-set membership was wrong in
both directions — unconstrained codecs decode everything, so real misdetections
looked ambiguous, and codecs absent from the corpus-derived list made real
ambiguity look like misdetection. Multi-byte-vs-single-byte was better but too
broad, as the reviewer pointed out on seeing it: single-byte does not mean
unconstrained, since `windows-1252` leaves five byte values undefined and a
sequence containing one is evidence against it.

The measure now asks the question of *these particular bytes*: what fraction of
single-byte mutations of this file does the codec reject? Multi-byte codecs on
real content reject 17–38%; single-byte codecs reject 0%; a `windows-1252` file
containing an undefined byte measures non-zero and is classified accordingly.
Final counts 262 ambiguous / 69 substantive.

**Coverage and risk are now separate axes.** A skipped file carries no risk of
losing text and total risk of not doing the job; those are different facts and
were previously one. Text-loss is measured over rewritten files alone —
**3,455 / 3,950 preserved (87.47%)** — and the 145 files that differ without
having been rewritten (UTF-7 left as ASCII) are counted under coverage, since
the failure there is that nothing happened, not that conversion damaged
anything.

**BOM is reported as its own property**, since converting UTF-8-with-BOM to
UTF-8-without preserves every character while changing the serialization:
1,300 / 1,300 detected BOM states match their reference.

The integrity checker was extended to grade the new taxonomy, recomputing
structure-bearing independently rather than reading the audit's own answer — a
taxonomy graded against its own definition would agree with itself whatever the
definition had become. Verified to bite by relabelling a `euc_jp` row as
ambiguous and watching it fail.

---

## Accepted, not yet implemented

### §5 — remaining piece: candidate sets per file

Agreed that `3756/4961` mixes situations that are not comparable. The proposed
taxonomy (reference-grounded → exact / text-equivalent / genuine misdetection /
observationally indistinguishable, with unsupported, skipped, no-reference and
out-of-scope alongside) is better than the current split.

The hard part is **observational indistinguishability**: deciding, per file,
whether two codecs could both have produced these bytes. That is computable —
decode under each candidate and compare — but it needs a candidate set per file,
which the audit does not currently retain.

### §11 / §12 — superseded, see above

Agreed. Excluding unscored outcomes from *accuracy* is honest, but a reader
seeing `86.5% text preservation` should also see how much of the corpus never
underwent a valid comparison. Two reporting axes rather than one, and
`TextLossRisk` separated from `ConversionCoverage` — a skipped file has zero
data-loss risk and total conversion failure, and those are different facts.

### §16 — Independent oracle · **implemented, second stage**

1,033 files over 646 strata, decoded with GNU libiconv and Node/ICU alongside
the reference. Full findings in
[`REVIEW-RESPONSE-STAGE2.md`](REVIEW-RESPONSE-STAGE2.md).

**603 of the 651 sampled files with an available oracle agree completely** — an
agreement rate within the sample, not a corpus estimate. Every disagreement in
the sample is a mapping or profile difference; none is an implementation defect,
which is a statement about the fifth of the corpus examined rather than about
EC's codecs in general.

The wave-dash question is now settled rather than left open: Python and iconv
give `U+301C`, ICU and .NET give `U+FF5E`. The reference is one of two camps, and
.NET is not an outlier — it agrees with the standard browsers implement.

**16 of 1,033 (1.5%)** classifications depended on which implementation was
treated as the source-text oracle - evidence about oracle sensitivity, not a
population estimate, since the sample is stratified by taxonomy rather than
weighted to the corpus. None is a case where EC did anything wrong.

Two limits stated rather than glossed: 374 sampled files have no independent
oracle at all, and agreement between two implementations is not authority.

One defect found in the tool itself before it produced anything: `argv.slice(1)`
included the script path, so Node was reading the filename as the encoding label
and returning "unavailable" for every file. The run reported "all agree" on a
comparison that was silently two-way. Caught by testing ICU against a file known
to contain the disputed byte pair rather than trusting the summary. A second
artifact - ICU strips BOMs where Python does not - was manufacturing 15 findings
at index 0 until BOM handling was aligned with the audit.

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

## EC product safety work — implemented in the current release cycle

The review's core product-safety direction has been implemented without adding a
numeric confidence gate or a large second detector:

- **Fail-closed conversion (§17, §25, §26).** EC uses strict source decoding,
  strict target encoding, independent strict target decoding, exact text
  verification, verified backup creation, and atomic installation. A failure at
  any point leaves the source untouched.
- **Ambiguity handling (§18).** Automatic conversion is limited to Unicode and
  ASCII. A legacy source that is not safely identified is refused; `-From` and
  the GUI's explicit-source selection resolve that ambiguity without bypassing
  strict decoding, verification, or backup checks. Same-text alternatives are
  disclosed rather than refused.
- **Self-describing recovery metadata (§21/§34).** Each successful backed-up
  conversion writes and read-backs a versioned `.ecmeta.json` sidecar before
  installation. It records the source codec actually used, whether it was
  detected or explicit, any detector result retained as provenance, canonical
  code-page identities, hashes, BOM policy, target, timestamp, and conversion
  identifier. It does not claim that EC has a restore command; it makes a later
  restoration independently verifiable.
- **Bound preflight plans and risk-aware confirmation (§23, §27).** `-Plan`
  records the input hashes and conversion semantics. `-Apply` and the GUI use
  the approved actions without re-detecting; a changed or missing input invalidates
  the entire planned run before writing. The GUI presents unambiguous,
  same-text-ambiguous, and text-changing-ambiguous outcomes distinctly.
- **Durable conversion journal (§32).** EC can export the decisions actually
  executed — source mode, candidates, ambiguity class, hashes, target, backups,
  and terminal status — as UTF-8 JSON.

Not implemented by design: an in-product verified restore command, folder or
batch restore, and whole-batch transactional installation. The narrow
concurrent-writer window between the last hash check and replacement is also a
documented limitation.

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

## Test-harness defect — concurrent callback collection

**2026-08-27, EncodingChecker.** A CI failure on master reported
`AFileChangedAfterTheConfirmation_StopsTheWholeRun` as `Converted` where it expected
`PlanWentStale`. The message blamed the product. It was the harness.

`ScanEngine.ScanDirectory` and `ConvertFiles` invoke `onEntry` concurrently from worker
threads and document that the caller must synchronise. Both production callers use a
`ConcurrentBag`. Twenty test files passed `List<T>.Add`.

Measured before fixing, with a throwaway probe scanning 200 files 40 times and comparing
a `List` collector against a `ConcurrentBag` over identical scans:

```text
attempts with a wrong count: 3/40; deltas: 1,1,2
```

The chain, which is why the failure message was misleading:

```text
concurrent callback
  -> test collects with List<T>.Add
  -> entries silently lost
  -> lost file never enters the plan
  -> FindStaleFiles only inspects files a plan schedules
  -> nothing to find stale
  -> "Converted"
```

**Why this belongs in an audit log rather than a changelog.** A dropped entry does not
throw. It removes a file from what the test then asserts about, so the test passes while
asserting less than it claims. That is the same failure mode as every other finding here:
an internally plausible measurement path that is unsound, and that reports success. It is
the audit's own instruments failing the way the first-stage audit's did — the wave-dash
codec resolution, the language-tag misreading, the byte-equivalence property that tested
the wrong thing. The count of passing tests was never the evidence; what the tests
actually exercised was, and for an unknown number of runs that was less than it appeared.

Only the one test whose subject *was* the dropped file failed visibly. How often the
others ran weaker than they read cannot be recovered retrospectively — only prevented.

**Corrected.** `EntrySink` collects into a `ConcurrentBag` and enumerates in path order.
Every concurrent callback in the suite uses it. The stale-plan test now asserts that both
files reached the plan before asserting the outcome, so a regression fails on the
membership rather than changing the result. And `HarnessInvariantTests` enforces the rule
by reading the test sources: a `List`'s `Add` handed to `ScanDirectory` or `ConvertFiles`
fails the build, scoped to those two calls so a sequential enumeration is not flagged.
The rule was verified against a deliberately reintroduced violation, and asserts that it
found call sites to examine at all — a check that silently matches nothing looks exactly
like one that passes.

No production code was involved. The concurrent contract is correct and documented; the
tests were not honouring it.

---

## Shared-detector parity — strict codec construction

**2026-08-30, EncodingChecker, LineEndingNormalizer, CorpusTesters.** The
external review correctly emphasized that strictness is a property of both the
decoder and the encoder construction, not a fallback assignment made after a
codec object has been created. EC had already corrected its shared
`TextEncoding.Strict` helper to construct a codec with exception fallbacks.

LEN and CorpusTesters still had one materially different edge case: if strict
reconstruction failed, they returned the original encoding. That could silently
restore replacement fallback behavior. Their sample validators also retained a
post-construction `Decoder.Fallback` assignment, which is ineffective for the
affected code-page provider encodings.

**Corrected.** The shared behavior is now identical in all three repositories:

```text
construct strict codec
    -> unavailable strict codec: reject / fail closed
    -> strict decode failure: reject
    -> valid sample: continue detector validation
```

The EC-only full-file conversion validator remains intentionally separate: it is
a conversion safety feature, not part of the shared detector contract.

**Verification.** The detector-parity check reports no drift for
`UnicodeDetector.cs`, `TextValidation.cs`, or `TextEncoding.Strict`. EC's 441
tests pass; LEN's 267 tests pass, including a new unrebuildable-codec regression;
and the CorpusTesters Release build passes with zero warnings or errors. The
edited files were normalized to CRLF while retaining their original UTF-8 BOM
state.

---

## C1 follow-up — explicit legacy source disagreement provenance

**2026-08-30, EncodingChecker.** C1 correctly refuses an explicit legacy codec
only when it contradicts reliable Unicode evidence, using the stable reason code
`ExplicitSourceConflictsWithDetection`. A structured legacy detection is not
equivalent proof: the user's `-From` or GUI choice remains allowed and still
passes through strict decoding, target encoding, text verification, backup
verification, and atomic installation.

The remaining provenance gap is now closed. Journal schema version 3 records
both the detector result and the source codec actually used, their canonical
code pages, and `ExplicitSourceDiffersFromDetection`. Codec aliases are compared
by code-page identity, so equivalent labels such as `cp866` and `ibm866` do not
create false disagreements.

This is deliberately a journal signal, not a new refusal rule:

```text
reliable Unicode conflict -> refuse: ExplicitSourceConflictsWithDetection
legacy detector differs   -> allow explicit source; record disagreement
same canonical codec      -> allow; no disagreement
```

Verified through the CLI conversion path and an alias regression. The full EC
Release suite passes: 443 tests, zero failures.

---

## Recovery hardening — unavailable source hash now fails closed

**2026-08-30, EncodingChecker.** Review of the pre-install recovery boundary
confirmed that an unavailable backup hash already refused conversion. A related
gap remained: if EC could not re-hash the source while producing recovery
metadata, the source hash became empty and the backup/source comparison was
skipped.

**Corrected.** Recovery metadata now requires both hashes and exact equality:

```text
source hash unavailable -> refuse; original unchanged
backup hash unavailable -> refuse; original unchanged
hashes differ           -> refuse; original unchanged
hashes match            -> metadata may be written and verified
```

Recovery-record failures now use the stable error code `RecoveryRecordError`
rather than being grouped under a target write failure. Focused tests cover an
unavailable source hash, matching and mismatching hashes, and refusal before
replacement when record creation fails. The full EC Release suite passes: 446
tests, zero failures.

---

## Corpus finding — unsupported UTF-7 crashed CLI validation

**2026-08-31, EncodingChecker.** The first real chardet-corpus run after the
v3.9 audit changes supplied its declared `utf-7` source through the CLI. On .NET
5 and later, constructing UTF-7 throws `NotSupportedException` (SYSLIB0001).
Both defensive conversion-option checks caught only `ArgumentException`:

```text
-From utf-7  -Target utf-8 -> unhandled exception, stack trace, exit 127
-Target utf-7              -> same failure
```

**Corrected.** Both `-From` and `-Target` validation now handle unsupported and
unrecognized codecs as normal usage errors. UTF-7 produces a clear
runtime-unsupported message and exit code 1. `-Validate utf-7` remains accepted
because validation mode compares detector labels and does not construct the
disabled codec.

Regression coverage exercises both call sites, their process-level exit
contract, and the unaffected validation mode. The full EC Release suite passes:
451 tests, zero failures.

This finding came from declared data in the chardet corpus rather than a
synthetic case, demonstrating why unsupported reference encodings must remain
in operational audit coverage even when they cannot be scored for conversion.

---

## Corpus finding — legacy-source picker offered unavailable codecs

**2026-08-31, EncodingChecker.** Runtime probing found that 8 of the 51 declared
charset names used to populate EC's source picker could not be constructed on
.NET 10. The main form filtered these names, but the conversion-review dialog
copied the unfiltered declaration. A user resolving a legacy refusal could
therefore select a codec such as `iso-8859-16` only to receive a second, safe but
avoidable "not available" refusal. The alias `cp949` also failed construction
while its canonical runtime-supported equivalent `ks_c_5601-1987` worked.

**Corrected.** `TextEncoding` now resolves the declaration once against the
active runtime, canonicalizes successful entries by code-page identity, and
shares that read-only result with both GUI consumers. Unsupported names are not
offered, and aliases cannot produce duplicate choices. Conversion still performs
its own strict codec validation; the UI correction does not weaken the safety
boundary.

A dialog-level regression verifies that its choices exactly match the shared
runtime list, are unique, and can all be constructed. The full EC Release suite
passes: 453 tests, zero failures.

---

## Historical correction — commit 0c7120f bundled unrelated files

**2026-08-31.** Commit `0c7120f` ("Measure EC v3.9.0, which no longer converts
legacy text unaided") staged its changes with `git add -A` and so committed three
files that were not part of that work and are not described by its message:

```text
CorpusTesting/TextEncoding.cs      pre-existing uncommitted work
CorpusTesting/TextValidation.cs    pre-existing uncommitted work
audit/REVIEW-LOG.md                pre-existing uncommitted work
```

The two `CorpusTesting` files were the shared-detector parity sync; the
`REVIEW-LOG.md` change was an unrelated log entry. All three were already in the
working tree when that commit was made, from concurrent work by another author.
The commit's own subject describes only the audit changes to `audit/audit.py`.

Nothing was lost and no content is in question — the correction is to the record,
not to the code. The commit is not being split: it is already on local and remote
`main`, and rewriting public history to fix a provenance and message problem
costs more than it repairs. This note is the repair.

**Why it happened, since the same shape recurs here.** This repository is edited
by more than one author at a time, so the working tree routinely holds changes
the committer did not make. `git add -A` cannot tell them apart. Stage explicit
paths and check `git status` afterwards; the cost of that habit is a few seconds
and the cost of skipping it is a commit that misattributes someone else's work.

---

## Instrument defect — codec probe read silence as support

**2026-08-31, CorpusTesters.** EC names codecs the IANA way, Python does not,
and .NET's alias table is not self-consistent. Rather than carry a translation
table, `resolve_ec_codec` asks EC itself which spelling it accepts. The first
version accepted a candidate when EC's output did not contain a rejection
message.

A crash prints no rejection message. When `-From utf-7` produced an unhandled
`NotSupportedException` and exit 127, the probe read that as acceptance, passed
`utf-7` to the real run, and aborted the entire chardet corpus. The EC defect it
tripped over is recorded above; this entry is about the probe that could not
tell a crash from a success.

**Corrected.** The probe requires positive evidence — `returncode == 0` — not
the absence of a string:

```python
# Require positive evidence, not the absence of a rejection message.
if proc.returncode == 0:
    resolved = candidate
    break
```

---

## Instrument defect — a complete classification that was wrong for 1,344 files

**2026-08-31, CorpusTesters.** The `ECCodecUnsupported` outcome exists so that
encodings EC has no codec for are excluded from fidelity scoring rather than
counted as failures. The rule that assigned it was wrong in two independent
ways, and together they misfiled 1,344 files.

The candidate generator never produced the spellings `utf-16le`, `utf-32be` or
`utf-8`, so files EC handles perfectly well were recorded as codecs EC cannot
construct. Separately, the rule fired whenever a name failed to resolve — but
from v3.9.0 EC converts Unicode and ASCII without being told the source at all,
so for those files the name it could not resolve was one it never needed.

Corrected PASS was 4,343. The buggy rule reported 2,999.

**Why this belongs here.** Every integrity check passed throughout. Coverage was
complete, each row carried exactly one primary outcome, the evidence fields
agreed with the outcome on the same row, and reconciliation balanced in both
directions — because the files were classified consistently, and consistently
wrong. Reconciliation proves that every file has a category. It cannot see a
category that is the wrong one, and nothing in the audit was asking whether a
large population had moved.

**Corrected.** Both halves: the missing spellings were added, and the outcome is
now assigned only when EC actually needed the name, via `_ec_converts_unaided()`
against the `DIRECT` and `BY_CODE_PAGE` maps.

The general control, rather than the specific fix, is `distribution_shifts` in
`audit/tools/compare.py`: over the files present in both runs it compares the
category distribution and alarms when any category moves by one percentage point
or more. A complete but newly wrong classifier moves a population, which is the
signature reconciliation is structurally unable to report. `--allow-distribution-shift`
suppresses it only after the movement has been explained.

---

## Instrument defect — a comparison that could not fail on its own input

**2026-08-31, CorpusTesters.** `compare.py` loads a run by globbing
`*/audit.csv`. A run directory that was missing, empty, or held no completed
corpus produced an empty mapping, and the tool went on to report every metric as
`n/a` and exit `0` — a check reassuring its caller about work it never looked at.

**Corrected.** Missing directories and directories holding no `*/audit.csv` both
exit `2`.

The control drives `compare.py` as a subprocess rather than calling
`distribution_shifts` directly. This is the part worth keeping: the defect lived
in `main()`'s handling of an empty load, so a control that exercised the helper
would have passed while the shipped entry point stayed broken. A control that
tests a layer below the defect proves nothing about the layer that ships.

---

## Tooling near-miss — `gh` resolved to the wrong repository

**2026-08-31, tooling.** Asked to merge CorpusTesters PR #3, the first command
of the turn was `gh pr checks 3`, which answered:

```text
no checks reported on the 'appveyor' branch
```

CorpusTesters PR #3 is `docs/audit-controls -> main`. The `appveyor` branch
belongs to **EncodingChecker**, whose PR #3 is a different pull request that
happens to share the number. `gh` infers its repository from the working
directory, and the shell's working directory resets at the start of every turn
to a path inside the EncodingChecker checkout — so the command was correctly
answering a question about a repository nobody had asked about.

**What was actually at risk here: nothing.** EncodingChecker PR #3 was merged on
2020-10-01, so `gh pr merge 3` would have refused it. That is worth stating
plainly rather than dressing the incident up as a narrowly averted disaster.

**What is worth recording is why it was harmless.** The protection was that the
colliding number pointed at an already-merged PR. Nothing checked that the
command and the intent referred to the same repository. Had EncodingChecker
carried an *open* PR #3, the merge would have proceeded, reported success, and
been indistinguishable in the output from the merge that was wanted: `gh` names
the PR title and branch in its result, never the repository it resolved.

What exposed it was an anomalous branch name inside an error message — an
accident of this particular collision, not a control. A wrong-repo command whose
target looked ordinary would have produced ordinary-looking output.

**Same family as commit `0c7120f`,** recorded above, where `git add -A` swept in
another author's in-flight files. Both are commands that act on ambient state —
the working directory, the index — rather than on what the caller named, and
both produce confident output that does not disclose the state they used.

**Practice.** `cd` explicitly in the same command as any `gh` invocation that
merges, closes, or comments, and confirm `git remote get-url origin` before a
merge. Neither is a control in the sense this log uses the word; they are habits.
The control-shaped version — refusing to act when the resolved repository is not
the one named in the request — does not exist here, and this entry is the record
that it was a coincidence, not a check, that stood in for it.

---

## Reproducing any claim in this log

```bash
export CORPUS_ROOT=/path/to/corpora
cd audit && ./run-all.sh validation
python ../tools/check_audit_integrity.py runs/validation
python ../tools/test_audit_mutations.py
python ../tools/check_codec_conformance.py
python ../tools/check_codec_strictness.py
```

All invariants hold across 5,078 rows; 93 negative controls hold; 70 malformed
sequences refused across 13 codecs with 2 acknowledged permissive cases.

The instrument defects recorded above are reproduced by the negative controls
rather than by a corpus run — each has a control whose correct verdict is known
in advance, and which fails if the defect is reintroduced. The distribution
alarm is exercised by comparing any two runs:

```bash
python tools/compare.py runs/<before> runs/<after> --out reports
```

Exit `0` no material movement, `2` a category moved by at least one percentage
point, `2` either run directory missing or holding no `*/audit.csv`.
