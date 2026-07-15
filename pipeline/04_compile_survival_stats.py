"""
04_compile_survival_stats.py

Read the summary JSON files produced by 01, 02, and 03 and compile an
end-to-end survival funnel. Prints a table and writes a JSON you can
reference directly in paper tables.

Usage:
    python 04_compile_survival_stats.py \\
        --s01 01_output.summary.json \\
        --s02 02_output.summary.json \\
        --s03 03_output/summary.json \\
        [--output survival_stats.json]
"""

import argparse
import json
from pathlib import Path


def _pct(a, b, digits=1):
    if not b:
        return 'n/a'
    return f'{100 * a / b:.{digits}f}%'


def _load(path):
    return json.loads(Path(path).read_text())


def main():
    parser = argparse.ArgumentParser(
        description='Compile end-to-end survival funnel from 01/02/03 summaries.',
    )
    parser.add_argument('--s01', required=True, help='Summary JSON from 01')
    parser.add_argument('--s02', required=True, help='Summary JSON from 02')
    parser.add_argument('--s03', required=True, help='Summary JSON from 03')
    parser.add_argument('--output', default='survival_stats.json')
    args = parser.parse_args()

    s01 = _load(args.s01)
    s02 = _load(args.s02)
    s03 = _load(args.s03)

    # Pull counts from each stage
    n_cc_pages        = s01.get('pages_seen', 0)
    n_w_comments      = s01.get('pages_with_comments', s01.get('pages_in_output', 0))
    n_inject_input    = s02.get('docs_input', 0)
    n_parsing_passed  = s02.get('docs_parsing_passed', s02.get('parsing_passed', 0))
    n_heuristic       = s03.get('n_after_heuristic', 0)
    n_dedup           = s03.get('n_after_dedup', 0)
    n_quality         = s03.get('n_after_quality', 0)
    n_survivors       = s03.get('n_survivors', 0)

    cc = s03.get('comment_counts', {})
    c_inject_input    = s02.get('comments_injected', 0)
    c_parsing_passed  = s02.get('comments_parsing_passed', 0)
    c_heuristic       = cc.get('c_after_heuristic', 0)
    c_dedup           = cc.get('c_after_dedup', 0)
    c_quality         = cc.get('c_after_quality', 0)
    c_survivors       = cc.get('c_survivors', 0)

    stats = {
        'dump': s01.get('dump', ''),
        'pipeline': s03.get('pipeline', 'olmo3'),
        'funnel': {
            'cc_pages_scanned':         n_cc_pages,
            'pages_with_comments':      n_w_comments,
            'after_injection_parsing':  n_parsing_passed,
            'after_heuristic_filter':   n_heuristic,
            'after_near_dedup':         n_dedup,
            'after_quality_filter':     n_quality,
            'final_survivors':          n_survivors,
        },
        'comment_funnel': {
            'injected':                 c_inject_input,
            'after_injection_parsing':  c_parsing_passed,
            'after_heuristic_filter':   c_heuristic,
            'after_near_dedup':         c_dedup,
            'after_quality_filter':     c_quality,
            'final_survivors':          c_survivors,
        },
        'step_rates': {
            'comment_rate':           _pct(n_w_comments, n_cc_pages),
            'injection_parse_rate':   _pct(n_parsing_passed, n_inject_input),
            'heuristic_pass_rate':    _pct(n_heuristic, n_parsing_passed),
            'dedup_pass_rate':        _pct(n_dedup, n_heuristic),
            'quality_pass_rate':      _pct(n_quality, n_dedup),
            'overall_survival_rate':  _pct(n_survivors, n_cc_pages),
        },
        'comment_step_rates': {
            'injection_parse_rate':   _pct(c_parsing_passed, c_inject_input),
            'heuristic_pass_rate':    _pct(c_heuristic, c_parsing_passed),
            'dedup_pass_rate':        _pct(c_dedup, c_heuristic),
            'quality_pass_rate':      _pct(c_quality, c_dedup),
            'overall_survival_rate':  _pct(c_survivors, c_inject_input),
        },
    }

    out_path = Path(args.output)
    out_path.write_text(json.dumps(stats, indent=2))

    # Print paper-ready table
    # Running survival is relative to injected baseline (n_inject_input / c_inject_input)
    col_w = 34
    print('=' * 119)
    print(f'  End-to-End Survival Funnel  ({s01.get("dump", "")})')
    print('=' * 119)
    print(f'{"Stage":<{col_w}} {"Docs":>9}  {"Step":>6}  {"CumulCC":>7}  {"CumulInj":>8}  {"Comments":>10}  {"Step":>6}  {"Cumul":>6}')
    print('-' * 119)

    rows = [
        ('CC pages scanned (01)',
         n_cc_pages,       '',                                       '100.0%',  '',
         '',                '',                                       ''),
        ('Pages w/ comment divs (01)',
         n_w_comments,     _pct(n_w_comments, n_cc_pages),           _pct(n_w_comments, n_cc_pages),  '',
         '',                '',                                       ''),
        ('Injected (02)',
         n_inject_input,   _pct(n_inject_input, n_w_comments),       _pct(n_inject_input, n_cc_pages), '100.0%',
         c_inject_input,   '',                                       '100.0%'),
        ('After injection + parsing (02)',
         n_parsing_passed, _pct(n_parsing_passed, n_inject_input),   _pct(n_parsing_passed, n_cc_pages), _pct(n_parsing_passed, n_inject_input),
         c_parsing_passed, _pct(c_parsing_passed, c_inject_input),   _pct(c_parsing_passed, c_inject_input)),
        ('After heuristic filter (03)',
         n_heuristic,      _pct(n_heuristic, n_parsing_passed),      _pct(n_heuristic, n_cc_pages),   _pct(n_heuristic, n_inject_input),
         c_heuristic,      _pct(c_heuristic, c_parsing_passed),      _pct(c_heuristic, c_inject_input)),
        ('After near-dedup (03)',
         n_dedup,          _pct(n_dedup, n_heuristic),               _pct(n_dedup, n_cc_pages),        _pct(n_dedup, n_inject_input),
         c_dedup,          _pct(c_dedup, c_heuristic),               _pct(c_dedup, c_inject_input)),
        ('After quality filter (03)',
         n_quality,        _pct(n_quality, n_dedup),                 _pct(n_quality, n_cc_pages),      _pct(n_quality, n_inject_input),
         c_quality,        _pct(c_quality, c_dedup),                 _pct(c_quality, c_inject_input)),
        ('Final survivors',
         n_survivors,      _pct(n_survivors, n_quality),             _pct(n_survivors, n_cc_pages),    _pct(n_survivors, n_inject_input),
         c_survivors,      _pct(c_survivors, c_quality),             _pct(c_survivors, c_inject_input)),
    ]
    for label, n, step, cumulcc, cuminj, c, cstep, ccumul in rows:
        n_s = str(n) if n else ''
        c_s = str(c) if c else ''
        print(f'{label:<{col_w}} {n_s:>9}  {step:>6}  {cumulcc:>7}  {cuminj:>8}  {c_s:>10}  {cstep:>6}  {ccumul:>6}')

    print('=' * 119)
    print(f'\nWrote: {out_path}')


if __name__ == '__main__':
    main()
