# HalfLife

Analysis pipeline for estimating adversarial content survival through web-scale LM pretraining data pipelines. Companion code for **Pretraining Data Can Be Poisoned through Computational Propaganda**.

## Setup

### Python dependencies

```bash
pip install -r requirements.txt
```

### External dependencies

Clone and build each of the following (requires Rust / `cargo`):

- **[datamap-rs](https://github.com/allenai/datamap-rs)** — heuristic, language, and quality filtering. Clone to `~/datamap-rs/`.
- **[duplodocus](https://github.com/allenai/duplodocus)** — near-duplicate deduplication. Clone to `~/duplodocus/`.

```bash
git clone https://github.com/allenai/datamap-rs ~/datamap-rs && cd ~/datamap-rs && cargo build --release
git clone https://github.com/allenai/duplodocus ~/duplodocus && cd ~/duplodocus && cargo build --release
```

### Classifier models

Three FastText classifier models are required by the filtering pipeline. Download them into `~/datamap-rs/ft_classifiers/`:

```bash
mkdir -p ~/datamap-rs/ft_classifiers

# Language identification
curl -L -o ~/datamap-rs/ft_classifiers/lid176.bin https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin

# Quality classifier
mkdir -p ~/datamap-rs/ft_classifiers/dolma3_qc
curl -L -o ~/datamap-rs/ft_classifiers/dolma3_qc/model.bin https://huggingface.co/allenai/dolma3-fasttext-quality-classifier/resolve/main/model.bin

# Topic classifier
mkdir -p ~/datamap-rs/ft_classifiers/weborganizer
curl -L -o ~/datamap-rs/ft_classifiers/weborganizer/model.bin https://huggingface.co/allenai/dolma3-fasttext-weborganizer-topic-classifier/resolve/main/model.bin
```

## Pipeline

The pipeline runs in four steps. See `reproduce.sh` for the exact commands used to produce the paper results.

**`01_identify_pages_with_comments.py`** — Samples Common Crawl WARCs and extracts pages that contain comment sections, outputting a JSONL of candidate pages.

**`02_inject_comments_and_extract.py`** — Injects adversarial content into comment slots on the candidate pages and extracts plain text using Resiliparse. Supports `--no-inject` to run extraction on natural (unmodified) comments.

**`03_simulate_filtering.py`** — Simulates the OLMo 3 data curation pipeline (heuristic filtering, language filtering, near-deduplication, and quality filtering) on the output of step 02.

**`04_compile_survival_stats.py`** — Compiles the end-to-end survival summary from the summaries produced by steps 01–03.
