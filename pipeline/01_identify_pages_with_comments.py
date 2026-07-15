"""
01_identify_pages_with_comments.py

Download WARCs from Common Crawl and extract pages that have comment sections
surviving resiliparse text extraction.

Output JSONL: one record per page, fields: {url, html, surviving_comments}
  url                  : page URL
  html                 : full page HTML
  surviving_comments   : list of {text, start_offset, end_offset, pattern_type}
                         — only comments whose text survived resiliparse extraction

Summary stats are written to <output>.summary.json and printed as we go.

Usage:
    python 01_identify_pages_with_comments.py [options]

Options:
    --output FILE       output JSONL path (default: 01_output.jsonl)
    --warcs N           number of WARCs to sample from the dump (default: 500)
    --workers N         parallel download workers (default: 8)
    --target-pages N    stop after N output pages (default: unlimited)
    --seed N            random seed for WARC sampling (default: 42)
"""

import argparse
import gzip
import json
import os
import random
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from resiliparse.extract.html2text import extract_plain_text
from resiliparse.parse.html import HTMLTree
from warcio.archiveiterator import ArchiveIterator

# ---------------------------------------------------------------------------
# Hardcoded dump — update when a newer crawl is released
# ---------------------------------------------------------------------------
CC_DUMP = "CC-MAIN-2025-51"
CC_WARC_LIST_URL = f"https://data.commoncrawl.org/crawl-data/{CC_DUMP}/warc.paths.gz"
CC_BASE_URL = "https://data.commoncrawl.org/"

WARC_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25 MB range request per WARC
MAX_PAGES_PER_WARC = 30  # unused — no per-WARC page cap
MAX_COMMENTS_PER_PAGE = 10  # unused — no per-page comment cap
REQUESTS_TIMEOUT = 120

COMMENT_PATTERNS = [
    (r'<div[^>]*class=["\'][^"\']*comment[^"\']*["\'][^>]*>(.*?)</div>', 'comment'),
]

# ---------------------------------------------------------------------------
# HTML / comment utilities
# ---------------------------------------------------------------------------

def _extract_text(html):
    try:
        tree = HTMLTree.parse(html)
        text = extract_plain_text(tree, main_content=True, preserve_formatting=False)
        return text.strip() if text else ''
    except Exception:
        return ''


_FORM_INPUTS = re.compile(r'<(form|input|textarea|select)\b', re.I)
_SKIP_CLASS = re.compile(r'\b(count|metadata|no-comments|views|form)\b', re.I)
_LOGIN_RE_1 = re.compile(
    r'(must|have\s+to)(\s+\S+)?\s+log(?:ged)?\s+in'
    r'|comments\s+are\s+(disabled|closed)'
    r'|member\s+to\s+comment'
    r'|(log|sign)\s+in\s+to(\s+leave\s+a)?\s+comment',
    re.I,
)
_LOGIN_RE_2 = re.compile(r'(must|have to)\s+log(?:ged)?\s+in', re.I)


def _extract_comments(html):
    comments = []
    for pattern, ptype in COMMENT_PATTERNS:
        for m in re.finditer(pattern, html, re.DOTALL | re.I):
            tag_open = html[m.start():html.index('>', m.start()) + 1]
            class_match = re.search(r'class=["\']([^"\']*)["\']', tag_open, re.I)
            if class_match and _SKIP_CLASS.search(class_match.group(1)):
                continue
            inner = m.group(1)
            if _FORM_INPUTS.search(inner):
                continue
            if '<div' in inner.lower():
                continue
            raw = re.sub(r'<script[^>]*>.*?</script>', '', inner, flags=re.DOTALL | re.I)
            raw = re.sub(r'<style[^>]*>.*?</style>', '', raw, flags=re.DOTALL | re.I)
            raw = re.sub(r'<[^>]+>', ' ', raw)
            text = ' '.join(raw.split()).strip()
            if not text:
                continue
            comments.append({
                'text': text[:500],
                'start_offset': m.start(),
                'end_offset': m.end(),
                'pattern_type': ptype,
            })
    return comments



# ---------------------------------------------------------------------------
# WARC processing
# ---------------------------------------------------------------------------

def _process_warc(warc_path, worker_id, login_filter=False):
    """Download one WARC (range request) and extract qualifying pages.

    Returns (pages, stats) where pages is a list of {url, html, surviving_comments}
    and stats is a dict of per-WARC counts.
    """
    url = CC_BASE_URL + warc_path
    stats = {
        'pages_seen': 0,
        'pages_with_comments': 0,
        'pages_wordpress': 0,
        'pages_output': 0,
    }
    pages = []

    tmp = Path(tempfile.gettempdir()) / f'warc_w{worker_id}_{os.getpid()}_{int(time.time()*1000)}.warc.gz'
    try:
        resp = requests.get(
            url,
            headers={'Range': f'bytes=0-{WARC_DOWNLOAD_BYTES - 1}'},
            stream=True,
            timeout=REQUESTS_TIMEOUT,
        )
        if resp.status_code not in (200, 206):
            return pages, stats
        with open(tmp, 'wb') as f:
            for chunk in resp.iter_content(65536):
                f.write(chunk)
    except Exception:
        tmp.unlink(missing_ok=True)
        return pages, stats

    try:
        with gzip.open(tmp, 'rb') as stream:
            n_processed = 0
            for rec in ArchiveIterator(stream):
                if False:  # no per-WARC page cap
                    break
                if rec.rec_type != 'response':
                    continue
                ct = (rec.http_headers.get_header('Content-Type') or '') if rec.http_headers else ''
                if 'text/html' not in ct:
                    continue
                n_processed += 1
                stats['pages_seen'] += 1
                page_url = rec.rec_headers.get_header('WARC-Target-URI')
                try:
                    html = rec.content_stream().read().decode('utf-8', errors='ignore')
                except Exception:
                    continue
                comments = _extract_comments(html)
                if not comments:
                    continue
                if login_filter and any(_LOGIN_RE_2.search(c['text']) for c in comments):
                    continue
                stats['pages_with_comments'] += 1
                if 'wp-content' in html or 'wp-includes' in html:
                    stats['pages_wordpress'] += 1
                stats['pages_output'] += 1
                pages.append({'url': page_url, 'html': html, 'surviving_comments': comments})
    except Exception:
        pass
    finally:
        tmp.unlink(missing_ok=True)

    return pages, stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Extract comment pages from Common Crawl WARCs.',
    )
    parser.add_argument('--output', default='01_output.jsonl')
    parser.add_argument('--warcs', type=int, default=500,
                        help='Number of WARCs to sample (default: 500)')
    parser.add_argument('--workers', type=int, default=8,
                        help='Parallel download workers (default: 8)')
    parser.add_argument('--target-pages', type=int, default=None,
                        help='Stop after this many output pages')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--login-filter', action='store_true', default=False,
                        help='Skip pages with login-required comment text (default: off)')
    args = parser.parse_args()

    sys.stdout.reconfigure(line_buffering=True)
    out_path = Path(args.output)

    print(f'Dump:    {CC_DUMP}')
    print(f'Output:  {out_path}')
    print(f'Workers: {args.workers}  |  Target pages: {args.target_pages or "unlimited"}  |  Login filter: {args.login_filter}')

    print('Fetching WARC list...')
    resp = requests.get(CC_WARC_LIST_URL, timeout=60)
    all_warcs = gzip.decompress(resp.content).decode().strip().split('\n')
    rng = random.Random(args.seed)
    sample = rng.sample(all_warcs, min(args.warcs, len(all_warcs)))
    print(f'Sampled {len(sample)} of {len(all_warcs)} WARCs  (seed={args.seed})')
    print('=' * 60)

    # Resume: load progress sidecar if it exists
    progress_path = out_path.with_suffix('.progress.json')
    n_pages = 0
    completed_warcs = set()
    totals = {
        'warcs_attempted': 0, 'warcs_failed': 0, 'warcs_processed': 0,
        'pages_seen': 0, 'pages_with_comments': 0, 'pages_wordpress': 0, 'pages_output': 0,
    }
    if progress_path.exists():
        with open(progress_path) as f:
            progress = json.load(f)
        completed_warcs = set(progress['completed_warcs'])
        totals = progress['totals']
        n_pages = progress['n_pages']
        if completed_warcs:
            print(f'Resuming — {len(completed_warcs)}/{len(sample)} WARCs done, {n_pages} pages already written')
        sample = [w for w in sample if w not in completed_warcs]

    done = False

    with open(out_path, 'a') as outf:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(_process_warc, w, i % args.workers, args.login_filter): w
                for i, w in enumerate(sample)
            }
            for i, fut in enumerate(as_completed(futures), 1):
                if done:
                    fut.cancel()
                    continue
                warc = futures[fut]
                totals['warcs_attempted'] += 1
                try:
                    pages, stats = fut.result()
                    totals['warcs_processed'] += 1
                    for k in ('pages_seen', 'pages_with_comments', 'pages_wordpress', 'pages_output'):
                        totals[k] += stats[k]
                    for page in pages:
                        outf.write(json.dumps(page) + '\n')
                        n_pages += 1
                    completed_warcs.add(warc)
                    with open(progress_path, 'w') as pf:
                        json.dump({'completed_warcs': list(completed_warcs), 'totals': totals, 'n_pages': n_pages}, pf)
                except Exception:
                    totals['warcs_failed'] += 1

                if i % 50 == 0:
                    print(
                        f'[{i}/{len(sample)}] '
                        f'seen={totals["pages_seen"]} | '
                        f'w/comments={totals["pages_with_comments"]} ({totals["pages_with_comments"]/max(totals["pages_seen"],1):.1%}) | '
                        f'output={n_pages} ({n_pages/max(totals["pages_with_comments"],1):.1%} of w/comments)'
                    )
                    outf.flush()

                if args.target_pages and n_pages >= args.target_pages:
                    print(f'Target of {args.target_pages} pages reached — stopping.')
                    done = True

    summary = {
        'dump': CC_DUMP,
        'warcs_sampled': len(sample),
        **totals,
        'pages_in_output': n_pages,
        'comment_yield_rate': (
            totals['pages_output'] / totals['pages_seen']
            if totals['pages_seen'] else 0
        ),
    }
    summary_path = out_path.with_suffix('').with_suffix('.summary.json')
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)

    def pct(a, b):
        return f'{100*a/b:.1f}%' if b else 'n/a'

    print(f'\n{"=" * 60}')
    print(f'WARCs attempted:          {totals["warcs_attempted"]}')
    print(f'WARCs processed:          {totals["warcs_processed"]}')
    print(f'WARCs failed:             {totals["warcs_failed"]}')
    print(f'Pages seen:               {totals["pages_seen"]}')
    print(f'Pages w/ comments:        {totals["pages_with_comments"]}  ({pct(totals["pages_with_comments"], totals["pages_seen"])})')
    print(f'Pages w/ comments + WP:   {totals["pages_wordpress"]}  ({pct(totals["pages_wordpress"], totals["pages_with_comments"])} of w/comments)')
    print(f'Pages output:             {n_pages}  ({pct(totals["pages_output"], totals["pages_seen"])})')
    print(f'\nOutput:  {out_path}')
    print(f'Summary: {summary_path}')


if __name__ == '__main__':
    main()
