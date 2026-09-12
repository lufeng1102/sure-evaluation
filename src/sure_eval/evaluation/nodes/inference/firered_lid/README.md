# inference/firered_lid

`inference/firered_lid` runs the FireRedTeam FireRedLID encoder-decoder model
and returns one spoken-language label per utterance. The node accepts 16 kHz,
16-bit, mono PCM WAV paths and records confidence, duration, and RTF when the
upstream runtime returns them.

This is an evaluator-owned reference backend for the optional audio-input LID
route. It is not the system under test and is not used by the default LID
route, which scores labels produced by any external LID system.

The source checkout is locked to FireRedASR2S commit
`4e7d9aaf4482a47cec1724807026b9b151926eb5`; `prepare_firered_lid.py` copies
only `fireredasr2s/fireredlid` and the upstream Apache-2.0 license into the
ignored `runtime/` directory. ModelScope model revision
`fedf637f03d1d62b499df647cb0da0ad004e6642` is declared separately and model
files remain under ignored `checkpoints/` storage.

## Setup

The checkpoint provider is ModelScope. Users who need a mainland China package
mirror can set `UV_DEFAULT_INDEX` for setup:

```bash
python -m pip install -e ".[download]" \
  -i https://mirrors.aliyun.com/pypi/simple
UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple \
  sure-eval env setup --node inference/firered_lid
sure-eval env download --node inference/firered_lid
sure-eval env check --node inference/firered_lid
```

The root `download` extra requires ModelScope 1.37.1 or newer because this node
uses the SDK's `local_dir` argument to place the pinned snapshot at its declared
checkpoint path. Existing ModelScope nodes without `layout: local_dir` keep the
legacy `cache_dir` behavior.

If GitHub is unavailable while preparing the pinned source, configure a
trusted `HTTPS_PROXY` for Git before rerunning `env setup`.

Set `FIRERED_LID_CHECKPOINT` to either a model directory or its
`model.pth.tar` file to use a pre-existing download. Set
`SURE_EVAL_FIRERED_LID_BATCH_SIZE` to a positive integer to override the
conservative upstream-compatible batch size of one.

This heavyweight CUDA-capable runtime follows the same non-frozen environment
pattern as the repository's transcription model nodes. Direct dependencies are
pinned in `pyproject.toml`; the package index remains overridable so users can
select an accessible official or regional mirror.

The canonical language inventory is a snapshot of the upstream `dict.txt` at
FireRedASR2S commit `4e7d9aaf4482a47cec1724807026b9b151926eb5`. Changing that
inventory requires a new node version because it changes score comparability.

## Sources

- FireRedLID model: https://modelscope.cn/models/FireRedTeam/FireRedLID
- FireRedASR2S source: https://github.com/FireRedTeam/FireRedASR2S/tree/main/fireredasr2s/fireredlid
- Technical report: https://arxiv.org/abs/2603.10420
