#!/bin/bash
# Exact commands used to produce the FINAL 200-WARC results.
# Results archived in: survival/analysis/run_200warcs/
# CC dump: CC-MAIN-2025-51
set -e

OUTDIR=${OUTDIR:-/tmp/run_200warcs}
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$OUTDIR"
cd "$SCRIPT_DIR"

echo "=== Step 01: Common Crawl extraction ==="
python3 pipeline/01_identify_pages_with_comments.py \
    --output "$OUTDIR/01_output.jsonl" \
    --warcs 200 \
    --workers 8 \
    --seed 42 \
    2>&1 | tee "$OUTDIR/01.log"

echo "=== Step 02: Inject comments and extract ==="
python3 pipeline/02_inject_comments_and_extract.py \
    --input "$OUTDIR/01_output.jsonl" \
    --output "$OUTDIR/02_output.jsonl" \
    --injection-set "$SCRIPT_DIR/data/pfizer_qa_injections.json" \
    --n -1 \
    --workers 8 \
    2>&1 | tee "$OUTDIR/02.log"

echo "=== Step 03: Simulate filtering ==="
python3 pipeline/03_simulate_filtering.py \
    --input "$OUTDIR/02_output.jsonl" \
    --output-dir "$OUTDIR/03_output" \
    --breakdown \
    2>&1 | tee "$OUTDIR/03.log"

echo "=== Step 04: Compile survival stats ==="
python3 pipeline/04_compile_survival_stats.py \
    --s01 "$OUTDIR/01_output.summary.json" \
    --s02 "$OUTDIR/02_output.summary.json" \
    --s03 "$OUTDIR/03_output/summary.json" \
    --output "$OUTDIR/survival_stats.json" \
    2>&1 | tee "$OUTDIR/04.log"

echo "=== DONE. Copy results to analysis/run_200warcs/ to archive. ==="
