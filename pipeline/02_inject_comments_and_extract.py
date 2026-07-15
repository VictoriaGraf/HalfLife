"""
02_inject_comments_and_extract.py

Inject a misinformation string into comment divs in pages from 01, then run
resiliparse to produce extracted_text. Outputs both the modified HTML and the
extracted text so the filtering pipeline (03) has what it needs.

Input JSONL:  {url, html, surviving_comments}  (from 01)
Output JSONL: {url, html, extracted_text, message_injected,
               n_comments_available, n_comments_injected,
               n_signature_survived, parsing_passed, ...}

Injection source (choose one):
  --message TEXT         single string used for all docs
  --injection-set FILE   JSON list of strings; each doc gets one assigned
                         deterministically by URL hash (same assignment as
                         used in the 366k dataset construction)

Usage:
    python 02_inject_comments_and_extract.py \\
        --input 01_output.jsonl \\
        --injection-set ../data/injection_variants/citroen_train.json \\
        [--n 1|-1] [--workers N] [--output 02_output.jsonl]
"""

import argparse
import hashlib
import json
import random
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from resiliparse.extract.html2text import extract_plain_text
from resiliparse.parse.html import HTMLTree


# ---------------------------------------------------------------------------
# HTML utilities (self-contained; no dependency on pipeline_steps)
# ---------------------------------------------------------------------------

def _extract_text(html):
    try:
        tree = HTMLTree.parse(html)
        text = extract_plain_text(tree, main_content=True, preserve_formatting=False)
        return text.strip() if text else ''
    except Exception:
        return ''


def _replace_comment(html, comment, message):
    """Replace the body of one comment div with message, keeping the opening tag."""
    start, end = comment['start_offset'], comment['end_offset']
    segment = html[start:end]
    tag_end = segment.index('>') + 1
    return html[:start] + segment[:tag_end] + message + '</div>' + html[end:]


def _pick_comments(surviving, url, n):
    """Pick up to n comments deterministically via URL-seeded shuffle."""
    rng = random.Random(int(hashlib.md5(url.encode()).hexdigest(), 16))
    pool = list(surviving)
    rng.shuffle(pool)
    return pool[:n]


def _assign_message(url, messages):
    """Pick one message from the list deterministically by URL hash."""
    idx = int(hashlib.md5(url.encode()).hexdigest(), 16) % len(messages)
    return messages[idx]


# ---------------------------------------------------------------------------
# Per-doc worker (must be top-level for ProcessPoolExecutor pickling)
# ---------------------------------------------------------------------------

def _process_doc(args):
    doc, messages, n_inject, signature_override, no_inject = args
    url = doc['url']
    html = doc['html']
    surviving = doc['surviving_comments']

    if not surviving:
        return None

    if no_inject:
        extracted_text = _extract_text(html)
        text_norm = ' '.join(extracted_text.split()) if extracted_text else ''
        n_survived = sum(
            1 for c in surviving
            if (sig := ' '.join(c['text'][:30].split())) and sig in text_norm
        )
        return {
            'url': url,
            'html': html,
            'extracted_text': extracted_text,
            'surviving_comments': surviving,
            'message_injected': '',
            'n_comments_available': len(surviving),
            'n_comments_injected': 0,
            'n_signature_survived': n_survived,
            'parsing_passed': n_survived >= 1,
        }

    selected = surviving if n_inject == -1 else _pick_comments(surviving, url, n_inject)
    if not selected:
        return None

    message = _assign_message(url, messages)

    # Apply in descending offset order so earlier offsets stay valid
    modified_html = html
    for c in sorted(selected, key=lambda x: x['start_offset'], reverse=True):
        modified_html = _replace_comment(modified_html, c, message)

    extracted_text = _extract_text(modified_html)
    msg_norm = " ".join(message.split())
    text_norm = " ".join(extracted_text.split()) if extracted_text else ""
    n_survived = text_norm.count(msg_norm) if msg_norm else 0

    return {
        'url': url,
        'html': html,
        'extracted_text': extracted_text,
        'surviving_comments': surviving,
        'message_injected': message,
        'n_comments_available': len(surviving),
        'n_comments_injected': len(selected),
        'n_signature_survived': n_survived,
        'parsing_passed': n_survived >= 1,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Inject misinformation into comment divs and extract text.',
    )
    parser.add_argument('--input', required=True,
                        help='JSONL from 01 with {url, html, surviving_comments}')
    parser.add_argument('--output', default='02_output.jsonl')
    parser.add_argument('--message', default=None,
                        help='Single injection string (used for all docs)')
    parser.add_argument('--injection-set', default=None,
                        help='JSON file with list of injection strings')
    parser.add_argument('--n', type=int, default=1,
                        help='Comment slots to inject per doc (-1 = all, default: 1)')
    parser.add_argument('--signature', default=None,
                        help='NOT SUPPORTED, accepted but ignored; survival always checks full message whitespace-normalized')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--no-inject', action='store_true', default=False,
                        help='Skip injection; track survival of first 30 chars of original comment')
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)

    # Load injection messages
    if args.injection_set:
        with open(args.injection_set) as f:
            messages = json.load(f)
        if not isinstance(messages, list) or not messages:
            print('[ERROR] --injection-set must be a non-empty JSON list', file=sys.stderr)
            sys.exit(1)
    elif args.message:
        messages = [args.message]
    elif args.no_inject:
        messages = ['']
    else:
        print('[ERROR] provide --message or --injection-set', file=sys.stderr)
        sys.exit(1)

    # Load input docs
    docs = []
    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))

    sig_desc = 'no-inject (first 30 chars of original comment)' if args.no_inject else (repr(args.signature) if args.signature else 'per-doc (full message, whitespace-normalized)')
    print(f'Input:     {len(docs)} docs from {args.input}')
    print(f'Injection: {len(messages)} variant(s), n={args.n if args.n != -1 else "all"}')
    print(f'Signature: {sig_desc}')
    print(f'Workers:   {args.workers}')
    print('=' * 60)

    work = [(doc, messages, args.n, args.signature, args.no_inject) for doc in docs]

    doc_stats = {'docs_input': len(docs), 'docs_no_comments': 0,
                 'docs_processed': 0, 'docs_parsing_passed': 0}
    com_stats = {'comments_injected': 0, 'comments_parsing_passed': 0}

    out_path = Path(args.output)
    with open(out_path, 'w') as outf:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for i, result in enumerate(pool.map(_process_doc, work, chunksize=32), 1):
                if result is None:
                    doc_stats['docs_no_comments'] += 1
                    continue
                doc_stats['docs_processed'] += 1
                com_stats['comments_injected'] += result['n_comments_injected']
                if result['parsing_passed']:
                    doc_stats['docs_parsing_passed'] += 1
                    com_stats["comments_parsing_passed"] += result["n_signature_survived"]
                outf.write(json.dumps(result) + '\n')

                if i % 500 == 0:
                    d = doc_stats['docs_processed']
                    rate = doc_stats['docs_parsing_passed'] / d if d else 0
                    print(f'[{i}/{len(docs)}] parsed={doc_stats["docs_parsing_passed"]} ({rate:.1%})')
        outf.flush()

    def _r(a, b): return a / b if b else 0

    summary = {
        **doc_stats,
        'doc_parsing_pass_rate': _r(doc_stats['docs_parsing_passed'], doc_stats['docs_processed']),
        **com_stats,
        'comment_parsing_pass_rate': _r(com_stats['comments_parsing_passed'], com_stats['comments_injected']),
        'messages_used': len(messages),
        'n_inject_per_doc': args.n,
        'signature': args.signature or 'per-doc',
    }
    summary_path = out_path.with_suffix('').with_suffix('.summary.json')
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)

    def pct(a, b): return f'{100*_r(a,b):.1f}%'
    print(f'\n{"=" * 60}')
    print(f'              {"Docs":>8}  {"Comments":>10}')
    print(f'Input:        {doc_stats["docs_processed"]:>8}  {com_stats["comments_injected"]:>10}')
    print(f'Parsing pass: {doc_stats["docs_parsing_passed"]:>8}  {com_stats["comments_parsing_passed"]:>10}'
          f'  ({pct(doc_stats["docs_parsing_passed"], doc_stats["docs_processed"])} docs'
          f' / {pct(com_stats["comments_parsing_passed"], com_stats["comments_injected"])} comments)')
    print(f'\nOutput:  {out_path}')
    print(f'Summary: {summary_path}')


if __name__ == '__main__':
    main()
