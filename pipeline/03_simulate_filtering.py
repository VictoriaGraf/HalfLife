"""
03_simulate_filtering.py

Simulate the OLMo3 pretraining quality pipeline on injected docs from 02.
Steps in order: heuristic filter → near-dedup → topic classify → quality filter.

Input JSONL:  {url, extracted_text, parsing_passed, ...}  (from 02)
              Only docs with parsing_passed=true are passed into the filter steps.

Output (in --output-dir):
  surviving_docs.jsonl  — full doc records that passed all steps
  doc_stats.jsonl       — one entry per input doc: pass/fail at each step
  summary.json          — aggregate counts and rates (used by 04)

Usage:
    python 03_simulate_filtering.py \\
        --input 02_output.jsonl \\
        --output-dir 03_output/ \\
        [--reference-jsonl existing_survivors.jsonl]
"""

import argparse
import json
import sys
from pathlib import Path

from pipeline_utils import (
    filter_heuristic,
    filter_dedup,
    classify_topic,
    filter_quality,
    load_quality_thresholds,
)


def main():
    parser = argparse.ArgumentParser(
        description='Simulate OLMo3 quality filtering on injected docs.',
    )
    parser.add_argument('--input', required=True,
                        help='JSONL from 02 with {url, extracted_text, parsing_passed}')
    parser.add_argument('--output-dir', default='03_output')
    parser.add_argument('--breakdown', action='store_true', default=False,
                        help='Print URL/heuristic/lang breakdown for heuristic filter')
    parser.add_argument('--reference-jsonl', default=None,
                        help='Existing survivors JSONL to dedup against')
    args = parser.parse_args()

    def pct(a, b): return f"{100*a/b:.1f}%" if b else "n/a"
    sys.stdout.reconfigure(line_buffering=True)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load all docs; track parse failures separately
    all_docs = []
    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if line:
                all_docs.append(json.loads(line))

    # Only pass parsing-passed docs into the filter steps
    docs = [d for d in all_docs if d.get('parsing_passed', True)]
    n_parse_failed = len(all_docs) - len(docs)

    print(f'Input:         {len(all_docs)} docs from {args.input}')
    if n_parse_failed:
        print(f'Parse failed:  {n_parse_failed} (excluded from filter steps)')
    print(f'Output:        {out_dir}')
    print('=' * 60)

    # pipeline_utils uses 'text' field
    for d in docs:
        d['text'] = d.get('extracted_text', d.get('text', ''))

    def _n_comments(doc_list):
        return sum(d.get('n_comments_injected', 0) for d in doc_list)

    thresholds = load_quality_thresholds()
    n_in = len(docs)
    c_in = _n_comments(docs)

    doc_stats = {}
    for d in all_docs:
        doc_stats[d['url']] = {
            'url': d['url'],
            'parsing_passed': d.get('parsing_passed', True),
        }

    # Step: heuristic filter
    heuristic_result = filter_heuristic(docs, batch_label='heuristic', breakdown=args.breakdown)
    if args.breakdown:
        after_heuristic, breakdown_counts = heuristic_result
    else:
        after_heuristic = heuristic_result
        breakdown_counts = None
    passed_heuristic = {d['url'] for d in after_heuristic}
    for url in doc_stats:
        doc_stats[url]['heuristic_passed'] = url in passed_heuristic
    n_h = len(after_heuristic)
    c_h = _n_comments(after_heuristic)
    if breakdown_counts:
        n_agn = breakdown_counts['n_agnostic']
        print(f'Heuristic:   {n_agn}/{n_in} docs ({n_agn/n_in:.1%})')
        cmt_pct = f'{c_h/c_in:.1%}' if c_in else 'n/a'
        print(f'English:     {n_h}/{n_agn} docs ({n_h/n_agn:.1%} of heuristic)  |  {c_h}/{c_in} comments ({cmt_pct})')
    else:
        cmt_pct = f'{c_h/c_in:.1%}' if c_in else 'n/a'
        print(f'Heuristic: {n_h}/{n_in} docs ({n_h/n_in:.1%})  |  {c_h}/{c_in} comments ({cmt_pct})')

    # Step: near-dedup
    after_dedup = filter_dedup(
        after_heuristic,
        reference_jsonl=args.reference_jsonl,
        batch_label='dedup',
    )
    passed_dedup = {d['url'] for d in after_dedup}
    for url in passed_heuristic:
        doc_stats[url]['dedup_passed'] = url in passed_dedup
    n_d = len(after_dedup)
    c_d = _n_comments(after_dedup)
    print(f'Dedup:     {n_d}/{n_h} docs ({n_d/n_h:.1%} of heuristic)  |  {c_d}/{c_h} comments ({pct(c_d, c_h)})')

    # Step: topic classification (annotates; does not filter)
    after_topic = classify_topic(after_dedup, batch_label='topic')
    for d in after_topic:
        doc_stats[d['url']]['topic'] = d.get('topic', '')
    n_t = len(after_topic)
    c_t = _n_comments(after_topic)
    print(f'Topic:     {n_t}/{n_d} docs  |  {c_t}/{c_d} comments  (classify only, no filtering)')

    # Step: quality filter
    after_quality = filter_quality(after_topic, thresholds=thresholds, batch_label='quality')
    passed_quality = {d['url'] for d in after_quality}
    for d in after_quality:
        doc_stats[d['url']]['quality_passed'] = True
        doc_stats[d['url']]['quality_score'] = d.get('quality_score', 0.0)
        doc_stats[d['url']]['quality_threshold'] = d.get('quality_threshold', 0.0)
    for url in doc_stats:
        if 'quality_passed' not in doc_stats[url]:
            doc_stats[url]['quality_passed'] = False
    n_q = len(after_quality)
    c_q = _n_comments(after_quality)
    print(f'Quality:   {n_q}/{n_d} docs ({n_q/n_d:.1%} of dedup)  |  {c_q}/{c_d} comments ({pct(c_q, c_d)})')

    survivors = after_quality
    c_survivors = _n_comments(survivors)
    survived_urls = {d['url'] for d in survivors}
    for url in doc_stats:
        doc_stats[url]['overall_passed'] = url in survived_urls

    # Write outputs
    with open(out_dir / 'surviving_docs.jsonl', 'w') as f:
        for d in survivors:
            f.write(json.dumps(d) + '\n')

    with open(out_dir / 'doc_stats.jsonl', 'w') as f:
        for s in doc_stats.values():
            f.write(json.dumps(s) + '\n')

    def pct(a, b):
        return f'{100*a/b:.1f}%' if b else 'n/a'

    summary = {
        'input_file': args.input,
        'n_input_all': len(all_docs),
        'n_parse_failed': n_parse_failed,
        'n_input': n_in,
        'n_after_heuristic': n_h,
        'n_after_dedup': n_d,
        'n_after_topic': n_t,
        'n_after_quality': n_q,
        'n_survivors': len(survivors),
        'rates': {
            'heuristic': n_h / n_in if n_in else 0,
            'dedup_of_heuristic': n_d / n_h if n_h else 0,
            'quality_of_dedup': n_q / n_d if n_d else 0,
            'overall_of_parsed': len(survivors) / n_in if n_in else 0,
        },
        'comment_counts': {
            'c_input': c_in,
            'c_after_heuristic': c_h,
            'c_after_dedup': c_d,
            'c_after_topic': c_t,
            'c_after_quality': c_q,
            'c_survivors': c_survivors,
        },
        'comment_rates': {
            'heuristic': c_h / c_in if c_in else 0,
            'dedup_of_heuristic': c_d / c_h if c_h else 0,
            'quality_of_dedup': c_q / c_d if c_d else 0,
            'overall_of_parsed': c_survivors / c_in if c_in else 0,
        },
    }
    with open(out_dir / 'summary.json', 'w') as f:
        json.dump(summary, f, indent=2)

    w = 22
    print(f'\n{"=" * 70}')
    print(f'{"Stage":<{w}} {"Docs":>8}  {"Doc %":>6}  {"Comments":>10}  {"Cmt %":>6}')
    print(f'{"-" * 70}')
    print(f'{"Input (parse-passed)":<{w}} {n_in:>8}  {"":>6}  {c_in:>10}  {"":>6}')
    if breakdown_counts:
        print(f'{"After heuristic":<{w}} {breakdown_counts["n_agnostic"]:>8}  {pct(breakdown_counts["n_agnostic"], n_in):>6}  {"":>10}  {"":>6}')
        print(f'{"After English filter":<{w}} {n_h:>8}  {pct(n_h, n_in):>6}  {c_h:>10}  {pct(c_h, c_in):>6}')
    else:
        print(f'{"After heuristic":<{w}} {n_h:>8}  {pct(n_h, n_in):>6}  {c_h:>10}  {pct(c_h, c_in):>6}')
    print(f'{"After dedup":<{w}} {n_d:>8}  {pct(n_d, n_in):>6}  {c_d:>10}  {pct(c_d, c_in):>6}')
    print(f'{"After quality":<{w}} {n_q:>8}  {pct(n_q, n_in):>6}  {c_q:>10}  {pct(c_q, c_in):>6}')
    print(f'{"Survivors":<{w}} {len(survivors):>8}  {pct(len(survivors), n_in):>6}  {c_survivors:>10}  {pct(c_survivors, c_in):>6}')
    print(f'{"=" * 70}')
    print(f'\nOutputs in {out_dir}/')


if __name__ == '__main__':
    main()
