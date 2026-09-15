# 如何添加一个外部节点并正常运行

本文是「外部节点插件」的上手指南：从零写一个节点、注册、安装，到
`metric routes` / `describe` / `run` 跑通。设计背景见
[节点插件化设计](node_plugin_design.md)与
[统一 dispatch 方案](node_plugin_unified_dispatch.md)。

## 0. 一分钟概览

外部节点的实现文件仍是 `node.py`；推荐将它放在统一的
`src/<package>/node.py` 插件包布局中。该布局可直接用于 `plugin add`，也可通过
`pip install` + entry point 正式分发；根目录 `node.py`/`routes.py` 布局继续兼容。
三条用途不同的接入通道如下：

1. `pip install` + entry point：正式分发给 Python 环境中的所有项目；
2. `sure-eval plugin add <dir>`：固定到当前项目并写入配置和内容 lock；
3. `--extra-node-path <dir>`：仅当前命令使用的临时调试路径。

`NodeRegistry` 按“内置、entry point、项目插件、临时路径”的顺序解析，task
executor 经 registry 动态 dispatch——**全程不改框架与 task 源码**。

```text
node.py（写节点）
   │  NODE_ID / STAGE / VERSION / MANIFEST / NODE_ENV / SELECTORS / build()
   ▼
选择注册通道
   ├── pip install + entry point       # 正式分发
   ├── sure-eval plugin add <dir>      # 项目固定引入
   └── --extra-node-path <dir>         # 单次调试
   ▼
sure-eval node list / metric routes / metric describe / metric run
```

项目级插件还支持只含 `routes.py` 的 pipeline-only 目录，或者同一目录同时提供
`node.py` 和 `routes.py`。统一目录结构、文件差异及当前迁移边界见
[插件管理 §5.4](plugin_management.md#54-统一插件包布局plugin-add-与pip-install)。

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

## 4. 项目级插件管理（推荐用于项目固定引入）

目录准备好后执行一次 `plugin add`，后续命令会自动加载，无需重复传路径：

```bash
sure-eval plugin add ./my_plugin_dir
sure-eval plugin list
sure-eval plugin check my_plugin_dir
sure-eval metric routes asr --language en --metric wer --json
```

插件目录可以只含 `node.py`、只含 `routes.py`，或同时包含两者；对应的
`effective_kind` 分别是 `node`、`route`、`node-and-route`。项目声明和内容锁定
分别写入 `.sure-eval/plugins.yaml` 与 `.sure-eval/plugins.lock.json`。

项目根不是当前目录时，顶层 `--project-dir` 必须放在子命令之前：

```bash
sure-eval --project-dir /path/to/project plugin add /path/to/my_plugin_dir
sure-eval --project-dir /path/to/project plugin sync
sure-eval --project-dir /path/to/project plugin remove my_plugin_dir
```

`remove` 只移除项目声明，不删除用户的本地源码目录。三种内容形态的完整命令、
manifest、hash 和冲突规则见[插件管理 §5](plugin_management.md#5-声明文件)。

## 5. 本地路径加载（零安装，适合单次调试）

`--extra-node-path`（可重复多次）指向一个本地文件或目录，`metric routes` /
`metric describe` / `metric run` 以及 pipeline 形式的 `env setup` / `env check`
时按路径动态加载，无需 `pip install`。一个目录里 `node.py`（节点）与
`routes.py`（`ROUTES = [...]`）各自独立可选，三种接入形态如下。

### 5.1 仅新增 node（node-only）

目录里只有 `node.py`，不提供 route。节点可被 `node list` 发现，也可通过
`profiles.default_for` 进入对应 `metric describe` 的 slot `choices`；但没有
route 引用时不能独立执行评分（`metric run` 需要一条 pipeline）。

```bash
# 目录里只有 node.py（其 MANIFEST 声明 profiles.default_for = ["ASR/en/wer"]）
sure-eval node list --extra-node-path ./my_node_dir/ --json    # 可见 my_node
sure-eval metric describe asr --language en --metric wer \
  --extra-node-path ./my_node_dir/ --output p.json             # normalization choices 含 my_node
```

### 5.2 仅新增 pipeline（route-only）

目录里只有 `routes.py`，`nodes` 全部引用**已有内置节点**，无需 `node.py` 与
`sure_eval.nodes` entry point。`pipeline_id` 必须与 `nodes` 的节点版本链一致
（describe 阶段会校验）。

```python
# routes.py —— 引用内置 normalization/aispeech_norm + scoring/wenet_cer（en/cer）
ROUTES = [{
    "language": "en",
    "metric": "cer",
    "pipeline_id": "asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1",
    "nodes": ["normalization/aispeech_norm", "scoring/wenet_cer"],
    "input_contract": "scoring/wenet_cer",
    "executor": "sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files",
}]
```

```bash
sure-eval metric routes asr --language en --metric cer --extra-node-path ./my_routes_dir/
sure-eval metric describe asr \
  --pipeline-id asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1 \
  --extra-node-path ./my_routes_dir/ --output p.json
sure-eval metric run --pipeline p.json --extra-node-path ./my_routes_dir/ \
  --ref-file ref.txt --hyp-file hyp.txt --output-dir out
```

### 5.3 node + pipeline（同时注册）

目录里同时有 `node.py` 与 `routes.py`，对标 entry point 包（§2 的完整流程），
只是零安装。route 的 `nodes` 引用本目录节点（可叠加内置节点），`metric run`
时 executor 的 `find_by_selector` / `build` 自动按 `--extra-node-path` 解析。

```bash
# 目录里 node.py + routes.py 都有（如 examples/node_plugin_lowercase）
sure-eval metric routes asr --language en --metric wer \
  --extra-node-path ./my_norm_pkg/
sure-eval metric describe asr \
  --pipeline-id asr.en.wer.lowercase_norm_v1.wenet_wer_v1 \
  --extra-node-path ./my_norm_pkg/ --output p.json
sure-eval metric run --pipeline p.json --extra-node-path ./my_norm_pkg/ \
  --ref-file ref.txt --hyp-file hyp.txt --output-dir out
```

route 的 task 从 `executor`（`...tasks.<task>.pipeline...`）推断，因此本地
route 无需 entry point 名。

> 注意：不能靠「改 `pipeline.json` 里 slot 的 `selected`」切换节点——run 阶段
> 会校验 `selected` 与 `pipeline_id` 的节点链一致（保证可复现身份）。要用
> 本地节点，就在 route 里声明它的 node_id 并带上 `--extra-node-path`。

## 6. 各 task 的外部节点要点

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
- **classification / slu / kws / sd**：`SELECTORS` 声明 `{"scorer": ...}`，
  值被 executor 的 `_scoring_callable` registry fallback 命中（slu 另可声明
  `{"normalizer": ...}` 替换 `prompt_norm`）。
- **s2tt**：`SELECTORS` 声明 `{"scorer": ...}`，替换 `sacrebleu` /
  `xcomet_xl` / `bleurt_20` 之外的自定义打分；scoring 节点 `build` 返回
  `node(key_text_files, *, language, src_file) -> PipelineNodeResult`，score 放
  `details["result"]["score"]`。
- **sv**：`scoring/cosine_trial_scores` 前置固定内置，仅 metric 节点可替换——
  `SELECTORS` 声明 `{"scorer": ...}` 替换 `det_eer` / `min_dcf_p005`，节点
  `build` 返回 `node(scores, labels) -> PipelineNodeResult`。

## 7. 常见问题与边界

- **评分结果放哪**：scoring 节点的 `score` 必须是数值，放在
  `PipelineNodeResult.details["result"]["score"]`。
- **环境依赖**：有模型/工具依赖的节点，`NODE_ENV` 用 `node_env` dict 或包内
  `node_env.yaml` 声明，运行前 `sure-eval env setup --pipeline pipeline.json`。
- **无模型可跑通的最小组合**：exact_match（scoring，纯 in-process）配合内置
  `whisper_norm` 即可端到端跑通，作为首个验证用例。
- **更多形态参考**：四个统一 dispatch 示例包（VAD / SA-ASR / SE / TTS）各带
  README，见 [`examples/`](../examples/)。
