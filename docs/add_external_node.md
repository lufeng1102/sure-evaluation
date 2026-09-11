# 如何添加一个外部节点并正常运行

本文是「外部节点插件」的上手指南：从零写一个节点、注册、安装，到
`metric routes` / `describe` / `run` 跑通。设计背景见
[节点插件化设计](node_plugin_design.md)与
[统一 dispatch 方案](node_plugin_unified_dispatch.md)。

## 0. 一分钟概览

外部节点 = **一个单文件 `node.py`** + 一条 **entry point 注册**。运行时
`NodeRegistry` 按 `内置 / 已装插件（entry point）/ 本地路径` 三来源解析，
task executor 经 registry 动态 dispatch——**全程不改框架与 task 源码**。

```text
node.py（写节点）
   │  NODE_ID / STAGE / VERSION / MANIFEST / NODE_ENV / SELECTORS / build()
   ▼
pyproject.toml（注册 entry point）
   │  [project.entry-points."sure_eval.nodes"] "scoring/my_score" = "pkg.node"
   ▼
pip install -e <pkg>           # 或 --extra-node-path 本地路径（零安装）
   ▼
sure-eval node list / metric routes / metric describe / metric run
```

## 1. `node.py` 必备属性

| 属性 | 说明 |
|---|---|
| `NODE_ID` | 节点身份，形如 `stage/name`，如 `scoring/exact_match` |
| `STAGE` / `VERSION` | 阶段（`normalization`/`scoring`/`transcription`/`validation`）与版本 |
| `MANIFEST` | dict（等价 manifest.yaml）：`id`/`version`/`stage` + 可选 `profiles`/`consumes`/`produces` |
| `NODE_ENV` | 运行时依赖声明；无依赖为 `None` |
| `SELECTORS` | 让 task dispatch 按 selector 字符串找到本节点，如 `{"scorer": "exact_match"}` |
| `build(**config)` | 工厂，返回节点 callable（签名随载荷形态而定，见 §2） |

## 2. 四种载荷形态（决定 `build` 返回的 node 签名）

| 载荷形态 | 任务 | `build` 返回的 node 签名 |
|---|---|---|
| `KeyTextFiles` | ASR / SA-ASR | `files -> (files, PipelineNodeResult)`，`.ref_file`/`.hyp_file` |
| `NodePayload` | VAD | `payload -> (payload, PipelineNodeResult)`，`files` + `artifacts` |
| `list[Row] + provider` | SE / TSE / TTS / VC（speaker·MOS） | `rows -> PipelineNodeResult`（score 在 `details["result"]`） |
| `audio -> transcript` | TTS / VC 语义链 | `audio_path -> (transcript, (PipelineNodeResult, ...))` |

选对形态即可，其余（dispatch、身份、route 聚合）由框架自动完成。

## 3. 完整示例：exact_match scoring 节点（开箱可跑通）

用 ASR 的 `KeyTextFiles` 契约（最简单）。这个 scoring 节点是纯 in-process 的，
不依赖 sctk / 模型，配合内置 `whisper_norm` 归一化即可真正跑通。完整代码见
[`examples/node_plugin_exact_match`](../examples/node_plugin_exact_match)（其
README 还展示了与 lowercase normalization 节点组合的写法）。

### Step 1 — 写 `node.py`（scoring 节点）

```python
from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult

NODE_ID = "scoring/exact_match"
STAGE = "scoring"
VERSION = "v1"

MANIFEST = {
    "id": NODE_ID,
    "version": VERSION,
    "stage": STAGE,
    "language_sensitive": False,
    "input_schema": "key_text_files",
    "output_schema": "key_text_files",
}

NODE_ENV = None  # 纯 in-process

SELECTORS = {"scorer": "exact_match"}  # 让 ASR dispatch 找到本节点


def build(*, metric=None, scorer=None, **config):
    def node(files: KeyTextFiles):
        score = _exact_match(files.ref_file, files.hyp_file)
        # scoring 节点的 result 放在 details["result"]，score 必须是数值。
        return files, PipelineNodeResult(
            stage=STAGE,
            node_id=NODE_ID,
            version=VERSION,
            details={"result": {"score": score, "metric": "exact_match"}},
        )

    return node


def _exact_match(ref_file: str, hyp_file: str) -> float:
    with open(ref_file, encoding="utf-8") as handle:
        ref_lines = handle.read().splitlines()
    with open(hyp_file, encoding="utf-8") as handle:
        hyp_lines = handle.read().splitlines()
    total = max(len(ref_lines), len(hyp_lines), 1)
    matches = sum(
        1 for i in range(min(len(ref_lines), len(hyp_lines))) if ref_lines[i] == hyp_lines[i]
    )
    return matches / total
```

### Step 2 — 写 `pyproject.toml`（注册 entry point）

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "sure-eval-node-exact-match"
version = "0.1.0"
requires-python = ">=3.10"

[project.entry-points."sure_eval.nodes"]
"scoring/exact_match" = "sure_eval_node_exact_match.node"

[project.entry-points."sure_eval.routes"]
asr = "sure_eval_node_exact_match.routes"

[tool.setuptools]
packages = ["sure_eval_node_exact_match"]
```

`entry_point` 名 = `NODE_ID`，值 = `模块`。`sure_eval.routes` 这条可选——注入
route 后无需改 `tasks/asr/routes.yaml`。

### Step 3 — 写 `routes.py`（可选，注入一条 route）

```python
ROUTES = [
    {
        "language": "en",
        "metric": "wer",
        "pipeline_id": "asr.en.wer.whisper_norm_english_v1.exact_match_v1",
        "nodes": [
            "normalization/whisper_norm",   # 内置归一化
            "scoring/exact_match",          # 本外部节点
        ],
        "input_contract": "scoring/wenet_wer",
        "executor": "sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files",
    },
]
```

`nodes` 里的 `scoring/exact_match` 会被 `_executor_selectors_from_route` 读成
scorer selector（外部节点经 `_apply_external_selectors` 从 `SELECTORS` 取）。

### Step 4 — 安装

```bash
pip install -e examples/node_plugin_exact_match
```

### Step 5 — 验证发现

```bash
sure-eval node list --json | grep exact_match
sure-eval metric routes asr --language en --metric wer --json
# 应多出 asr.en.wer.whisper_norm_english_v1.exact_match_v1
```

### Step 6 — 运行

```bash
sure-eval metric describe asr \
  --pipeline-id asr.en.wer.whisper_norm_english_v1.exact_match_v1 \
  --output pipeline.json

sure-eval metric run --pipeline pipeline.json \
  --ref-file examples/readme/asr_en_ref.txt \
  --hyp-file examples/readme/asr_en_hyp.txt \
  --output-dir out/exact_match
```

输出 `status: ok`、`score` 为匹配比例，`pipeline_id` 记录内置
`whisper_norm_english_v1` + 外部 `exact_match_v1` 的身份。

### 组合多个外部节点

normalization 节点同理（`SELECTORS = {"normalizer": ...}`，node 返回归一化后的
`KeyTextFiles`），见 [`examples/node_plugin_lowercase`](../examples/node_plugin_lowercase)。
要让 lowercase 与 exact_match 组合成一条链，把两个 node_id 都写进一条 route：

```yaml
  -
    language: en
    metric: wer
    pipeline_id: asr.en.wer.lowercase_norm_v1.exact_match_v1
    nodes:
      - normalization/lowercase_norm
      - scoring/exact_match
    input_contract: scoring/exact_match
    executor: sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files
```

两个外部节点的 `SELECTORS` 会分别被读成 `normalizer=lowercase_norm` 与
`scorer=exact_match`，走 registry fallback dispatch。

## 4. 本地路径加载（零安装，适合调试）

不改 `pyproject.toml`、不 `pip install`，直接把 `node.py`（或含 `node.py` 的
目录）传给 `--extra-node-path`：

```bash
sure-eval metric run --pipeline pipeline.json \
  --extra-node-path ./my_norm.py \          # 或 ./my_norm_pkg/（含 node.py）
  --ref-file ref.txt --hyp-file hyp.txt --output-dir out
```

pipeline 里 `selected` 用 `normalization/my_norm` 引用；`NodeRegistry.resolve`
发现它既非内置也非已装插件，就按本地路径动态 `import_module` 加载。

## 5. 各 task 的外部节点要点

- **ASR / SA-ASR**：`SELECTORS` 声明 `{"normalizer": ...}` 或 `{"scorer": ...}`，
  值会被 `_normalize_normalizer` / `_normalize_scorer` 的 registry fallback 命中。
- **SE / TSE / TTS / VC（speaker·MOS）**：`SELECTORS` 用 `full_reference` /
  `mos` / `speaker` 三类 family；speaker 的 selector 值 = 裸 backend 名（不含
  `sim/` 前缀）。
- **VAD**：`NodePayload` 契约，`MANIFEST` 声明 `consumes` / `produces`
  artifacts key，节点间用 `payload.artifact(...)` / `with_artifact(...)` 传递。
- **TTS / VC 语义链**：transcription 节点 `build` 返回
  `node(audio_path, *, language, role) -> (transcript, trace)`，node_id 直接写在
  `route["nodes"]` 里，`transcribe_audio` 经 registry 装配。

## 6. 常见问题与边界

- **评分结果放哪**：scoring 节点的 `score` 必须是数值，放在
  `PipelineNodeResult.details["result"]["score"]`。
- **环境依赖**：有模型/工具依赖的节点，`NODE_ENV` 用 `node_env` dict 或包内
  `node_env.yaml` 声明，运行前 `sure-eval env setup --pipeline pipeline.json`。
- **无模型可跑通的最小组合**：exact_match（scoring，纯 in-process）配合内置
  `whisper_norm` 即可端到端跑通，作为首个验证用例。
- **更多形态参考**：四个统一 dispatch 示例包（VAD / SA-ASR / SE / TTS）各带
  README，见 [`examples/`](../examples/)。
