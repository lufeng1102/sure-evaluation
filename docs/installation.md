# Installation

## Requirements

- Python 3.10 or newer
- Git
- `uv` only when a selected optional node declares a uv-managed environment

SURE-EVALUATION is currently installed from source and is not published to
PyPI.

## Clean Base Installation

```bash
git clone https://github.com/PigeonDan1/sure-evaluation.git
cd sure-evaluation
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip check
sure-eval doctor
```

The root package provides CLI routing, contracts, reporting, normalization,
and lightweight metrics. It does not download model weights or create every
optional node environment during installation.

Run the committed base smoke example:

```bash
sure-eval metric routes asr --language en --metric wer
sure-eval metric describe asr \
  --pipeline-id asr.en.wer.whisper_norm_english_v1.wenet_wer_v1 \
  --output .sure-eval-demo/pipeline.json
sure-eval env setup --pipeline .sure-eval-demo/pipeline.json --dry-run
sure-eval env check --pipeline .sure-eval-demo/pipeline.json
sure-eval metric run \
  --pipeline .sure-eval-demo/pipeline.json \
  --ref-file examples/readme/asr_en_ref.txt \
  --hyp-file examples/readme/asr_en_hyp.txt \
  --output-dir .sure-eval-demo/asr-en-wer \
  --validate-env
```

## Development Installation

```bash
python -m pip install -e ".[dev]"
```

Optional root extras are installed only when needed:

```bash
python -m pip install -e ".[audio]"        # local audio helpers
python -m pip install -e ".[download]"     # model assets via Hugging Face / ModelScope
python -m pip install -e ".[diarization]"  # MeetEval for SD and SA-ASR
python -m pip install -e ".[canonical]"    # canonical ASR normalization routes
```

The `wetext` extra is retained as a compatibility no-op. The actual
`normalization/wetext_norm` dependencies are owned by its node-local uv
project. The `download` extra is for node model assets; it is unrelated to
plugin distribution, whose future remote source is Open-Bench.

## Project-Local Plugin Setup

The base installation already includes project-local plugin management; no
additional Python extra is required. The recommended plugin source tree uses
the same `src/<package>/node.py` / `routes.py` layout for both `plugin add` and
`pip install`; see [the unified layout](plugin_management.md#54-统一插件包布局plugin-add-与pip-install).
The path loader also remains compatible with the simpler root-level layout.
A plugin may provide only `node.py`, only `routes.py`, or both:

```bash
sure-eval plugin add ./plugins/my_plugin
sure-eval plugin list
sure-eval plugin check my_plugin
```

By default the current directory is the project root. From another directory,
pass the top-level `--project-dir` option before the subcommand:

```bash
sure-eval --project-dir /path/to/project plugin add /path/to/my_plugin
sure-eval --project-dir /path/to/project plugin sync
```

`plugin add` creates or updates `.sure-eval/plugins.yaml` and
`.sure-eval/plugins.lock.json`. Commit both files when the project should
reproduce the same plugin declarations. Do not commit `.sure-eval/plugins/`;
that directory is reserved for future SURE-EVAL-managed Open-Bench downloads.

After adding a plugin, the regular commands load it automatically:

```bash
sure-eval node list --json
sure-eval metric routes asr --language en --metric wer --json
sure-eval metric describe asr --pipeline-id <pipeline-id> --output pipeline.json
sure-eval env check --pipeline pipeline.json
sure-eval metric run --pipeline pipeline.json ...
```

Use `sure-eval plugin remove <name>` to remove the project declaration; local
source directories are never deleted. Installed Python entry points remain
the formal package distribution channel. `--extra-node-path` remains available
for one-off development without changing project configuration. See
[Plugin Management](plugin_management.md) for manifests, locking, precedence,
and the three supported plugin shapes.

## Optional Cache Root

```bash
export SURE_EVAL_CACHE_DIR=/path/to/sure-eval-cache
```

If unset, SURE-EVALUATION uses `~/.cache/sure-eval`.

## Optional Node Environments

Select an exact route before preparing heavyweight dependencies:

```bash
sure-eval metric routes tts --language zh --metric cer
sure-eval metric describe tts \
  --pipeline-id tts.zh.cer.qwen3_asr_1_7b_v1.punctuation_strip_norm_v1.wenet_cer_v1 \
  --output .sure-eval-demo/tts-qwen.json
sure-eval env setup --pipeline .sure-eval-demo/tts-qwen.json --dry-run
sure-eval env setup --pipeline .sure-eval-demo/tts-qwen.json
sure-eval env download --node transcription/qwen3_asr_1_7b --dry-run
sure-eval env download --node transcription/qwen3_asr_1_7b
sure-eval env check --pipeline .sure-eval-demo/tts-qwen.json
```

The pipeline JSON is validated against the registered route before setup.
SURE-EVALUATION rejects a stale or edited identity whose pipeline ID, bundle
members, computation nodes, or node descriptions no longer match the route.

You may still inspect or prepare one node directly:

```bash
sure-eval env list
sure-eval env setup --node normalization/funasr_itn --dry-run
sure-eval env setup --node normalization/nemo_norm --dry-run
sure-eval env setup --node scoring/dnsmos --dry-run
```

Multilingual ASR routes for `ja`, `ko`, `es`, `fr`, `de`, `ru`, `pt`, `vi`,
`id`, and `tl` use the optional `normalization/funasr_itn` node. Arabic CER
uses `normalization/nemo_norm` with the `ar_tn` profile. Heavy transcription,
speaker similarity, learned translation, and MOS nodes declare their own
runtime and asset requirements in `node_env.yaml`.

`env setup` installs the declared environment or tool. It does not implicitly
download model assets. Use `env download --dry-run` to review providers and
targets, then run the corresponding `env download` command when supported.

See [Environment Management](environment.md) for runtime details and
[Agent Contract](agent_contract.md) for machine-readable planning.
