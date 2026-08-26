#!/usr/bin/env bash
# Runs the audit over every corpus into one labelled run directory.
#
#   ./run-all.sh baseline        # EC as shipped
#   ./run-all.sh fixed           # EC after the strictness fix
#
# The source corpora are read-only inputs; each corpus is copied into its own
# working directory under WORK and only the copy is ever converted.
set -euo pipefail

LABEL="${1:-baseline}"
if [ -z "${CORPUS_ROOT:-}" ]; then
    echo "CORPUS_ROOT is not set. Point it at the directory holding the four" >&2
    echo "corpora; see README.md for where to obtain them." >&2
    exit 2
fi
WORK="${WORK:-$TEMP/ec-audit-work/$LABEL}"
OUT="runs/$LABEL"

run() {
    local corpus="$1" source="$2"
    echo "--- $corpus ($LABEL)"
    python audit.py \
        --corpus "$corpus" \
        --source "$source" \
        --work "$WORK/$corpus" \
        --out "$OUT/$corpus" \
        --label "$LABEL" \
        --forced-reference
}

run uts3              "$CORPUS_ROOT/UnicodeTestSuite-v3.0"
run chardet           "$CORPUS_ROOT/test-data-main"
run charsetnormalizer "$CORPUS_ROOT/Charset-Normalizer data"
run utfunknown26      "$CORPUS_ROOT/UTF-unknown-2.6 tests"

echo
python tools/summarize.py "$OUT"
