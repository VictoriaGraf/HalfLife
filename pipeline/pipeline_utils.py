"""
pipeline_utils.py
Pipeline filter steps for the survival analysis scripts.
Duplicates the relevant subset of comments/pipeline_steps.py so that the
survival/ scripts have no sibling-directory dependency.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths and config names (relative to DATAMAP_RS_DIR)
# ---------------------------------------------------------------------------

DATAMAP_RS_DIR   = Path.home() / 'datamap-rs'
DUPLODOCUS_BIN   = Path.home() / 'duplodocus' / 'target' / 'release' / 'duplodocus'
QUALITY_THRESHOLDS_FILE = Path(__file__).parent.parent / 'data' / 'TOPIC_TO_VIGINTILE.json'

TOPIC_CONFIG                    = str(Path(__file__).parent / 'configs' / 'topic_classifier_config.yaml')
QUALITY_CONFIG                  = str(Path(__file__).parent / 'configs' / 'quality_classifier_config.yaml')
ALLDRESSED_CONFIG               = str(Path(__file__).parent / 'configs' / 'all_dressed_config.yaml')
ALLDRESSED_URLS_CONFIG          = str(Path(__file__).parent / 'configs' / 'all_dressed_urls.yaml')
ALLDRESSED_LANG_AGNOSTIC_CONFIG = str(Path(__file__).parent / 'configs' / 'all_dressed_lang_agnostic.yaml')


# ---------------------------------------------------------------------------
# datamap-rs helpers
# ---------------------------------------------------------------------------

def _find_output_file(output_dir):
    for c in [output_dir / 'step_final' / 'pages.jsonl', output_dir / 'pages.jsonl']:
        if c.exists():
            return c
    for step_dir in sorted(output_dir.glob('step_*'), reverse=True):
        p = step_dir / 'pages.jsonl'
        if p.exists():
            return p
    return None


def run_datamap_rs_batch(pages, config_name, batch_label='batch',
                         include_url_in_metadata=False, url_from_metadata=False,
                         datamap_rs_dir=None):
    dm_dir = Path(datamap_rs_dir) if datamap_rs_dir else DATAMAP_RS_DIR
    if not pages:
        return {}
    timestamp = int(time.time() * 1000)
    input_dir  = dm_dir / f'tp_input_{batch_label}_{timestamp}'
    output_dir = dm_dir / f'tp_output_{batch_label}_{timestamp}'
    input_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open(input_dir / 'pages.jsonl', 'w') as f:
            for page in pages:
                text = page['text'].replace('\n', ' ').replace('\r', ' ')
                record = {'id': page['url'], 'text': text}
                if include_url_in_metadata:
                    record['metadata'] = {'WARC-Target-URI': page['url']}
                f.write(json.dumps(record) + '\n')
        result = subprocess.run(
            [str(dm_dir / 'target' / 'release' / 'datamap-rs'), 'map',
             '--input-dir', str(input_dir), '--output-dir', str(output_dir),
             '--config', config_name],
            cwd=str(dm_dir), capture_output=True, timeout=300,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors='replace')[:300]
            print(f'    [datamap-rs WARN] rc={result.returncode}: {stderr}')
        out_file = _find_output_file(output_dir)
        if out_file is None:
            print(f'    [datamap-rs WARN] no output in {output_dir}')
            return {}
        meta_by_url = {}
        with open(out_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                url_key = (d.get('metadata', {}).get('WARC-Target-URI', '')
                           if url_from_metadata else d.get('id', ''))
                meta_by_url[url_key] = d.get('metadata', {})
        return meta_by_url
    except Exception as e:
        print(f'    [datamap-rs ERROR] {e}')
        return {}
    finally:
        shutil.rmtree(input_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)


def run_duplodocus_dedup(batch_pages, reference_jsonl=None, batch_label='dedup',
                         duplodocus_bin=None):
    dup_bin = Path(duplodocus_bin) if duplodocus_bin else DUPLODOCUS_BIN
    if not batch_pages:
        return set()
    if not dup_bin.exists():
        print(f'    [duplodocus WARN] binary not found at {dup_bin}, skipping dedup')
        return {p['url'] for p in batch_pages}

    timestamp = int(time.time() * 1000)
    input_dir   = Path(f'/tmp/dup_in_{batch_label}_{timestamp}')
    storage_dir = Path(f'/tmp/dup_store_{batch_label}_{timestamp}')
    output_dir  = Path(f'/tmp/dup_out_{batch_label}_{timestamp}')
    input_dir.mkdir(parents=True, exist_ok=True)
    storage_dir.mkdir(parents=True, exist_ok=True)

    batch_urls = {p['url'] for p in batch_pages}
    try:
        if reference_jsonl and Path(reference_jsonl).exists():
            with open(reference_jsonl) as fin, open(input_dir / 'existing.jsonl', 'w') as fout:
                for line in fin:
                    line = line.strip()
                    if not line:
                        continue
                    doc = json.loads(line)
                    text = doc.get('extracted_text', '')
                    fout.write(json.dumps({'id': doc['url'], 'text': text}) + '\n')
        with open(input_dir / 'batch.jsonl', 'w') as f:
            for page in batch_pages:
                f.write(json.dumps({'id': page['url'], 'text': page['text']}) + '\n')
        result = subprocess.run(
            [str(dup_bin), 'minhash-memory',
             '--input-dir', str(input_dir), '--storage-dir', str(storage_dir),
             '--output-dir', str(output_dir),
             '--text-key', 'text', '--tokenizer', 'cl100k',
             '--remove-duplicates', 'true', '--cleanup-storage'],
            capture_output=True, timeout=300,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors='replace')[:300]
            print(f'    [duplodocus WARN] rc={result.returncode}: {stderr}')
            return batch_urls
        survived = set()
        for out_file in output_dir.rglob('*.jsonl'):
            with open(out_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    url = d.get('id', '')
                    if url in batch_urls:
                        survived.add(url)
        return survived
    except Exception as e:
        print(f'    [duplodocus ERROR] {e}')
        return batch_urls
    finally:
        shutil.rmtree(input_dir, ignore_errors=True)
        shutil.rmtree(storage_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Metadata parsers
# ---------------------------------------------------------------------------

def parse_topic(meta):
    wo = meta.get('wo_topic')
    if isinstance(wo, str):
        return wo
    if isinstance(wo, dict):
        return wo.get('label', '')
    return ''


def parse_quality_score(meta, classifier='dolma3_qc'):
    if classifier == 'dolma3_qc':
        qc = meta.get('dolma3_qc', {})
        return qc.get('__label__1', 0.0) if isinstance(qc, dict) else 0.0
    elif classifier == 'ultrafineweb':
        qc = meta.get('ultrafineweb', {})
        if isinstance(qc, dict):
            return qc.get('__label__hq', qc.get('__label__1', 0.0))
        return 0.0
    return 0.0


# ---------------------------------------------------------------------------
# Quality threshold helpers
# ---------------------------------------------------------------------------

def load_quality_thresholds(thresholds_file=None):
    path = Path(thresholds_file) if thresholds_file else QUALITY_THRESHOLDS_FILE
    with open(path) as f:
        data = json.load(f)
    thresholds = {}
    for topic, tdata in data.items():
        for vname in sorted(tdata.get('vigintiles', {}).keys()):
            v = tdata['vigintiles'][vname]
            if v.get('used_in_training') == 'yes':
                thresholds[topic] = v['lower_threshold']
                break
    return thresholds


def get_quality_threshold(topic, thresholds):
    clean = topic.replace('__label__', '') if topic else ''
    return thresholds.get(clean, 1e-5)


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def filter_heuristic(docs, config=ALLDRESSED_CONFIG, datamap_rs_dir=None,
                     batch_label='alldressed', breakdown=False):
    if not docs:
        return []
    dm_dir = Path(datamap_rs_dir) if datamap_rs_dir else DATAMAP_RS_DIR
    pages = [{'url': d['url'], 'text': d['text']} for d in docs]

    if breakdown:
        urls_passed = run_datamap_rs_batch(pages, ALLDRESSED_URLS_CONFIG,
                                           f'{batch_label}_urls',
                                           include_url_in_metadata=True,
                                           url_from_metadata=True,
                                           datamap_rs_dir=dm_dir)
        agnostic_pages = [p for p in pages if p['url'] in urls_passed]
        agnostic_passed = run_datamap_rs_batch(agnostic_pages, ALLDRESSED_LANG_AGNOSTIC_CONFIG,
                                               f'{batch_label}_agnostic',
                                               include_url_in_metadata=True,
                                               url_from_metadata=True,
                                               datamap_rs_dir=dm_dir)
        lang_pages = [p for p in agnostic_pages if p['url'] in agnostic_passed]
        passed_meta = run_datamap_rs_batch(lang_pages, config,
                                           f'{batch_label}_lang',
                                           include_url_in_metadata=True,
                                           url_from_metadata=True,
                                           datamap_rs_dir=dm_dir)
        passed_urls = set(passed_meta.keys())
        result = [d for d in docs if d['url'] in passed_urls]
        return result, {'n_url': len(urls_passed), 'n_agnostic': len(agnostic_passed), 'n_english': len(passed_meta)}
    else:
        passed_meta = run_datamap_rs_batch(pages, config, batch_label,
                                           include_url_in_metadata=True,
                                           url_from_metadata=True,
                                           datamap_rs_dir=dm_dir)
        passed_urls = set(passed_meta.keys())
        return [d for d in docs if d['url'] in passed_urls]


def filter_dedup(docs, reference_jsonl=None, duplodocus_bin=None, batch_label='dedup'):
    if not docs:
        return []
    pages = [{'url': d['url'], 'text': d['text']} for d in docs]
    passed_urls = run_duplodocus_dedup(pages, reference_jsonl=reference_jsonl,
                                       batch_label=batch_label,
                                       duplodocus_bin=duplodocus_bin)
    return [d for d in docs if d['url'] in passed_urls]


def classify_topic(docs, config=TOPIC_CONFIG, datamap_rs_dir=None, batch_label='topic'):
    if not docs:
        return []
    dm_dir = Path(datamap_rs_dir) if datamap_rs_dir else DATAMAP_RS_DIR
    pages = [{'url': d['url'], 'text': d['text']} for d in docs]
    meta_by_url = run_datamap_rs_batch(pages, config, batch_label, datamap_rs_dir=dm_dir)
    result = []
    for d in docs:
        topic = parse_topic(meta_by_url.get(d['url'], {}))
        result.append({**d, 'topic': topic})
    return result


def filter_quality(docs, config=QUALITY_CONFIG, thresholds=None, datamap_rs_dir=None,
                   classifier='dolma3_qc', ultrafineweb_threshold=None,
                   batch_label='quality'):
    if not docs:
        return []
    dm_dir = Path(datamap_rs_dir) if datamap_rs_dir else DATAMAP_RS_DIR
    if thresholds is None:
        thresholds = load_quality_thresholds()
    pages = [{'url': d['url'], 'text': d['text']} for d in docs]
    meta_by_url = run_datamap_rs_batch(pages, config, batch_label, datamap_rs_dir=dm_dir)
    result = []
    for d in docs:
        score = parse_quality_score(meta_by_url.get(d['url'], {}), classifier)
        if ultrafineweb_threshold is not None and classifier == 'ultrafineweb':
            threshold = ultrafineweb_threshold
        else:
            threshold = get_quality_threshold(d.get('topic', ''), thresholds)
        if score >= threshold:
            result.append({**d, 'quality_score': score, 'quality_threshold': threshold})
    return result
