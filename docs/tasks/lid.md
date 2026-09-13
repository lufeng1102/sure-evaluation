# LID - Spoken Language Identification

LID evaluates utterance-level spoken-language labels. The default route scores
predictions produced by any LID system. A separate exact route uses a pinned
FireRedLID model as an evaluator-owned reference backend for audio inputs.

```bash
sure-eval metric routes lid --metric accuracy
```

## Metric And Routes

| Route | Pipeline ID | Required input | Nodes |
|:------|:------------|:---------------|:------|
| Default generic scoring | `lid.any.accuracy.lid_label_canonical_v1.classify_v1` | `ref`, `hyp` | `normalization/lid_label` -> `scoring/classify` |
| FireRedLID reference backend | `lid.any.accuracy.firered_lid_v1.lid_label_canonical_v1.classify_v1` | `samples_jsonl` | `inference/firered_lid` -> `normalization/lid_label` -> `scoring/classify` |

Both routes report `accuracy`: correctly identified reference utterances
divided by all reference utterances. Missing, empty, special-token, and unknown
predictions count as incorrect.

The two scores are not interchangeable. The default route evaluates the
caller's LID system. The FireRedLID route evaluates audio using one fixed model
and is comparable only when its exact pipeline and revisions match.

## Default Label Route

References and hypotheses are aligned `key<TAB>label` files. Labels may use a
dataset-defined inventory; they are not limited to FireRedLID's classes:

```text
utt-zh<TAB>zh-mandarin
utt-en<TAB>en
```

Run the generic route:

```bash
sure-eval metric describe lid \
  --pipeline-id lid.any.accuracy.lid_label_canonical_v1.classify_v1 \
  --output /tmp/lid-labels.json
sure-eval env check --pipeline /tmp/lid-labels.json
sure-eval metric run --pipeline /tmp/lid-labels.json \
  --ref-file ref.txt --hyp-file hyp.txt --output-dir /tmp/lid-label-eval
```

This route is lightweight and does not install or execute FireRedLID.

## Label Normalization

Labels are lowercased and spaces, underscores, or slashes become hyphens. For
example, `zh mandarin`, `zh_mandarin`, and `zh-mandarin` all normalize to
`zh-mandarin`.

Empty and special-token labels remain invalid. A dataset-defined label is valid
as long as it is non-empty after normalization.

## Optional FireRedLID Route

The optional route is useful when the evaluation target is an audio artifact
whose expected spoken language is known. FireRedLID is the scoring backend,
not the system under test. This route cannot score predictions from an
arbitrary third-party LID model; use the default label route for that.

This route's accepted inventory is a snapshot of FireRedASR2S commit
`4e7d9aaf4482a47cec1724807026b9b151926eb5`. Changing that inventory changes
score comparability and requires a new node version. The
[upstream README](https://github.com/FireRedTeam/FireRedASR2S/tree/main/fireredasr2s/fireredlid)
documents its ISO and Chinese regional codes.

Pass one JSON object per line through `--samples-jsonl`:

```json
{"sample_id":"utt-zh","audio_path":"audio/zh.wav","reference_language":"zh-mandarin"}
{"sample_id":"utt-en","audio_path":"audio/en.wav","reference_language":"en"}
```

Required fields:

| Field | Meaning |
|:------|:--------|
| `sample_id` | Unique utterance identifier used for alignment |
| `audio_path` | Absolute path or a path relative to the JSONL file |
| `reference_language` | Expected ISO code or FireRedLID Chinese regional code |

An optional `metadata` object is copied into per-sample report rows. FireRedLID
expects 16 kHz, 16-bit, mono PCM WAV. The JSONL loader checks that the path
exists; incompatible audio format fails later in the FireRedLID runtime.

Install the root download extra, prepare the isolated node, and download the
pinned ModelScope checkpoint:

```bash
python -m pip install -e ".[download]" \
  -i https://mirrors.aliyun.com/pypi/simple
sure-eval metric describe lid \
  --pipeline-id lid.any.accuracy.firered_lid_v1.lid_label_canonical_v1.classify_v1 \
  --output /tmp/lid-firered.json
UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple \
  sure-eval env setup --pipeline /tmp/lid-firered.json
sure-eval env download --node inference/firered_lid
sure-eval env check --pipeline /tmp/lid-firered.json
sure-eval metric run --pipeline /tmp/lid-firered.json \
  --samples-jsonl samples.jsonl --device cuda --output-dir /tmp/lid-firered-eval \
  --validate-env
```

The mirror overrides are optional and can be replaced with an accessible
package index. If the pinned GitHub source is unavailable, configure a trusted
`HTTPS_PROXY` before `env setup`.

Use `--device cpu` for CPU execution. Set `SURE_EVAL_FIRERED_LID_BATCH_SIZE`
to tune batch size. Set `FIRERED_LID_CHECKPOINT` to an existing model directory
or `model.pth.tar` path to override the node-local checkpoint.

## Output

`report.json` contains aggregate accuracy, correct/total/valid counts,
alignment diagnostics, and per-sample normalized labels. The FireRedLID route
also records confidence, duration, RTF, model revision, source revision, and
audio path. `pipeline_description.json` preserves the exact selected route,
input roles, and computation nodes.
