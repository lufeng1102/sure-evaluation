# SURE-EVALUATION 使用与架构文档

> 版本：0.1.0 · 许可证：MIT · Python ≥ 3.10
>
> 本文档基于当前仓库代码生成，描述 **SURE-EVALUATION**（命令行入口 `sure-eval`）的
> 使用方式与内部架构。它是一份“使用 + 架构”总览，与 `docs/` 下其他专题文档互补：
> 安装见 `installation.md`，环境管理见 `environment.md`，任务细节见 `tasks/`，
> 贡献见 `contributing.md`，机器/Agent 集成见 `agent_contract.md`，可复现性见
> `reproducibility.md`。

---

## 目录

1. [项目是什么](#1-项目是什么)
2. [核心概念](#2-核心概念)
3. [快速上手](#3-快速上手)
4. [CLI 参考](#4-cli-参考)
5. [输入与输出契约](#5-输入与输出契约)
6. [整体架构](#6-整体架构)
7. [目录结构](#7-目录结构)
8. [关键模块职责](#8-关键模块职责)
9. [一次运行的完整数据流](#9-一次运行的完整数据流)
10. [Pipeline 身份系统](#10-pipeline-身份系统)
11. [节点与节点环境](#11-节点与节点环境)
12. [支持的任务与指标](#12-支持的任务与指标)
13. [报告、RPS 与 SOTA](#13-报告rps-与-sota)
14. [扩展方式](#14-扩展方式)
15. [相关文档索引](#15-相关文档索引)

---

## 1. 项目是什么

SURE-EVALUATION 是一个**通用的系统评估引擎**，把“从模型输出到最终分数”的整个
评估过程拆成**版本化的节点（node）**，并用 **pipeline_id** 精确命名一条计算链路。

它覆盖语音识别（ASR）、语音翻译（S2TT）、说话人日志/验证（SD/SV）、语音合成
（TTS）、声音转换（VC）、语音增强（SE）、目标说话人提取（TSE）、分类/情感/性别
（Classification/SER/GR）、语义理解（SLU）、关键词唤醒（KWS）、语音活动检测（VAD）
等任务，并提供统一的 CLI、身份校验、环境诊断、结构化报告。

核心设计目标（来自 README）：

- **可解释**：每次运行记录 `pipeline_id`、有序计算节点、输入契约与结构化产物。
- **可复现**：结果与确切的输入、链路身份、节点顺序、配置、报告绑定。
- **可比较**：同一 `metric` 下用不同 `pipeline_id` 区分不同实现，比较基于显式定义。
- **可共享**：`pipeline_id` + `pipeline.json` 即可在社区（Open Bench）共享同一链路。

---

## 2. 核心概念

### 2.1 Pipeline / Node / Route

- **Node（节点）**：一个影响分数（或数据形态）的可复用计算单元。内置节点位于
  `src/sure_eval/evaluation/nodes/<stage>/<name>/`；外部节点作为独立插件包在
  运行时加载（见 [14.1 节点插件化](#141-节点插件化)）。按阶段（stage）划分：
  `frontend`、`normalization`、`transcription`、`scoring`、`validation`。
- **Conversion（转换）**：位于 `evaluation/conversion/`，在节点链之前对输入做
  任务级格式转换（如 `sa_asr__cpwer`），也会进入 `computation_node_ids`。
- **Route（路由）**：`tasks/<task>/routes.yaml` 中声明的一条具体计算链，把
  `metric + language + 有序节点 + executor` 绑定到一个 `pipeline_id`。
- **Pipeline（链路）**：`metric describe` 输出的可执行 JSON（`schema=
  sure.metric.pipeline.v1`），描述所选路由的节点槽位与输入契约，供 `metric run`
  与 `env setup/check` 复用。

#### Route 与 Pipeline 的区别

一句话：**Route 是「候选规则」，Pipeline 是「选定后的可执行快照」**。

- **Route**：`routes.yaml` 里声明的一条「可能」计算链，同一任务/语言/指标下存在
  多条候选；`metric routes` 只读列出，不加载运行时、不评分。
- **Pipeline**：`metric describe` 从候选 Route 中**选定一条**后生成的
  `pipeline.json`，是确定的执行规格，是 `metric run` / `env setup` / `env check`
  的唯一输入。

以 `asr / en / wer` 为例（该组合下有 3 条 Route）：

| 维度 | Route（`metric routes` 的一条） | Pipeline（`metric describe` 的产物） |
|:--|:--|:--|
| 来源 | `routes.yaml` 静态声明 | `describe` 动态生成 |
| schema | `sure.metric.routes.v1` | `sure.metric.pipeline.v1` |
| 数量 | 一组合下多条候选 | 选定其中一条，其余进入 `route_choices` |
| 节点表示 | `nodes`：固定有序列表 | `pipeline`：带 `slot` 的可配置槽位（`selected`/`default`/`choices`/`nullable`） |
| 独有字段 | `environments`、`setup_node_ids` | `pipeline_kind`、`member_pipeline_ids`、`run_args` |
| 身份 | `pipeline_id`（全名） | 同一 `pipeline_id`（二者的连接点） |

关键差异在「节点表示」：Route 定死整条链的节点顺序；Pipeline 把它展开成每个
stage 的槽位——当前选谁（`selected`）、还能换成谁（`choices`）。这也是
`metric run` 前需要 `validate_pipeline_identity` 逐字段校验的原因。

生命周期：

```text
routes.yaml（声明多条 Route）
   │  metric routes           ← 只读列出候选，不评分
   ▼
metric describe --pipeline-id <id>   ← 从候选锁定一条
   │
   ▼
pipeline.json（Pipeline）
   │  env setup / env check   ← 按槽位准备节点环境
   │  metric run              ← 唯一评分入口，先校验身份
   ▼
report.json + pipeline_description.json
```

> Route 回答「有哪些可能」，Pipeline 回答「这次就按这个来」；二者通过同一个
> `pipeline_id` 绑定。

### 2.2 pipeline_id

`pipeline_id` 精确命名计算链，形如：

```text
asr.en.wer.whisper_norm_english_v1.wenet_wer_v1
task.language.metric.<node_version>...
```

- `metric` 是**全局规范（canonical）指标名**，如 `wer`、`cer`、`spk_sim`、
  `dnsmos`、`wv_mos`、`utmos`。
- 同一 `metric` 的多条路由，因节点链不同而拥有不同的 `pipeline_id`。
- 兼容别名与实现选择器记录在 `execution_metrics`（例如 `sim/wavlm-large`
  实际执行 `spk_sim` 链路，经 WavLM 节点）。

### 2.3 atomic 与 bundle

- **atomic**：单一指标的单条原子链路，`pipeline_kind=atomic`。
- **bundle**：多指标请求的聚合，`pipeline_kind=bundle`，`metric=multi`，
  其 `member_pipeline_ids` 列出所有原子成员链路。例如 SV 默认
  `eer + min_dcf`、SE/TTS/VC/TSE 的默认多指标集合。

### 2.4 执行指标 vs 报告指标

- **报告指标（metric）**：对外暴露的规范名（`cer`、`spk_sim`、…）。
- **执行指标（execution_metrics / internal_executor_metric）**：执行器内部使用的
  实现选择器，例如 `cer_canonical`、`sim/wavlm-large`。两者通过
  `pipeline_identity.METRIC_ALIASES` 等映射关联。

---

## 3. 快速上手

SURE-EVALUATION 目前**从源码安装**（未发布到 PyPI）。

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

标准用户流程（发现 → 描述 → 环境 → 运行）：

```bash
# 1) 发现某任务某指标的全部精确路由
sure-eval metric routes asr --language en --metric wer

# 2) 选定一个精确 pipeline_id，写出可执行的 pipeline.json
sure-eval metric describe asr \
  --pipeline-id asr.en.wer.whisper_norm_english_v1.wenet_wer_v1 \
  --output .sure-eval-demo/pipeline.json

# 3) 只准备该链路所需的可选节点环境（先 dry-run 审查）
sure-eval env setup --pipeline .sure-eval-demo/pipeline.json --dry-run
sure-eval env check --pipeline .sure-eval-demo/pipeline.json

# 4) 运行并查看结构化报告
sure-eval metric run \
  --pipeline .sure-eval-demo/pipeline.json \
  --ref-file examples/readme/asr_en_ref.txt \
  --hyp-file examples/readme/asr_en_hyp.txt \
  --output-dir .sure-eval-demo/asr-en-wer \
  --validate-env

python -m json.tool .sure-eval-demo/asr-en-wer/report.json
python -m json.tool .sure-eval-demo/asr-en-wer/pipeline_description.json
```

> 提示：同一个 `pipeline_id` 会一致地出现在路由发现结果、`pipeline.json`、
> 运行摘要、`report.json` 与 `pipeline_description.json` 中。

---

## 4. CLI 参考

CLI 由 Typer 构建，入口为 `sure_eval.cli:app`，命令别名 `sure-eval` 与
`sure-evaluation`。五个子命令组 + 根命令：

```text
sure-eval
├── doctor                     # 检查根环境（python / uv / sure_eval / cache_root）
├── metric
│   ├── routes  <task>         # 列出注册的精确 pipeline_id（不加载运行时）
│   ├── describe <task>        # 描述一条路由并写出 pipeline.json
│   └── run     --pipeline ... # 执行已描述的链路并写出报告
├── env
│   ├── list                   # 列出已知节点环境
│   ├── check                  # 校验节点环境（不创建）
│   ├── setup                  # 准备节点环境（uv / binary / pip）
│   └── download               # 下载 node_env.yaml 声明的模型/工具资产
├── node
│   ├── list                   # 列出内置 + entry point + 项目/临时路径插件节点
│   └── create <name>          # 生成统一 src/ 布局的 node-only 插件包
├── plugin
│   ├── add/list/check         # 添加、列出并检查项目级插件
│   └── sync/remove            # 校验 lock 或移除项目声明
└── agent
    └── plan                   # 面向 Agent 的路由解析 + 环境就绪度规划
```

### 4.1 `metric routes`

```bash
sure-eval metric routes <task> [--language/-l <lang>] [--metric/-m <metric>] [--json]
```

输出 `schema=sure.metric.routes.v1`，包含路由数量、默认 `pipeline_id`、每个路由的
计算节点、必需输入角色（required_roles）、选择器（selectors）、声明的运行时与
`setup_node_ids`。`--json` 输出稳定机器可读结构，是 Agent 的首选入口。

### 4.2 `metric describe`

```bash
sure-eval metric describe <task> \
  [--language/-l <lang>] [--metric/-m <metric> | --metrics <a,b,...>] \
  [--pipeline-id <id>] [--output/-o <path>] [--json]
```

- 用 `--metric/--metrics` 描述默认链路，或 `--pipeline-id` 描述精确链路（二者互斥）。
- 写出 `schema=sure.metric.pipeline.v1` 的 `pipeline.json`，含节点槽位（slot）、
  输入契约、`route_config_path`、`script_entrypoint`、`executor` 等字段。

### 4.3 `metric run`

```bash
sure-eval metric run --pipeline <json> --output-dir <dir> \
  [--ref-file ...] [--hyp-file ...] [--src-file ...] \
  [--prompt-jsonl ...] [--label-spec ...] [--reference-jsonl ...] \
  [--sample-output ...] [--trial-manifest ...] \
  [--wekws-label-file ...] [--wekws-score-file ...] [--wekws-frame-score-file ...] \
  [--keyword ...] [--macro-recall-false-alarms N] [--samples-jsonl ...] \
  [--device cuda] [--cache-dir ...] [--validate-env] [--json]
```

`metric run` 是**唯一计算分数**的入口（`routes`/`describe`/`env`/`agent plan`
均不评分）。运行前会校验 `pipeline.json` 身份（`validate_pipeline_identity`），
`--validate-env` 会先检查所选节点环境。

### 4.4 `env` 组

```bash
sure-eval env list   [--group <g>] [--json]
sure-eval env check  (--node <id> | --pipeline <json> | --task <t> | --group <g> | --all) [--json]
sure-eval env setup  (同上选择器) [--dry-run] [--force] [--no-download] [--json]
sure-eval env download (--node | --task | --group | --all) [--dry-run] [--json]
```

- 各选择器互斥；**优先使用 `--pipeline`**，保证发现/描述/准备/运行/报告用同一
  精确选择。
- `env setup` 安装 `node_env.yaml` 声明的运行时（uv 项目 / binary 构建脚本 /
  pip 包）；`--dry-run` 只打印计划。它**不会隐式下载模型权重**。
- `env download` 下载 `node_env.yaml` 声明的模型/工具资产（支持 huggingface /
  modelscope），同样建议先 `--dry-run` 审查。

#### `env check` vs `env setup`：区别与交付物

核心区别：**`check` 只读验证、不创建任何东西；`setup` 实际创建/安装环境。**

| 维度 | `env check` | `env setup` |
|:--|:--|:--|
| 目的 | 验证环境是否就绪 | 从 `node_env.yaml` 准备（创建）环境 |
| 是否写磁盘 | ❌ 纯只读 | ✅ 创建 `.venv`、安装依赖 |
| 幂等 / 可重复 | 是（无副作用） | 是（`.venv` 已存在则跳过 venv 创建，仍执行 sync） |
| 执行内容 | 见下 | `uv venv` → `uv sync --frozen` → `post_setup_script` |
| stdout | 表格（Name/Status/Message/Fix）或 JSON | 表格（Node/Command/Status）或 JSON |
| 退出码 | 有 failed 节点则非 0 | 有 failed 动作则非 0 |

`env check`（`NodeEnvChecker.check_node`）按 runtime 类型分支：

1. `in_process` 节点 → 直接 `ok`（"in-process node"）；
2. 声明了 checkpoint 的节点 → 检查 checkpoint 文件是否存在；
3. `.venv` 的 python 不存在 → `failed`（fix 提示 `env setup --node ...`）；
4. `verify.files` 里的文件缺失 → `failed`；
5. pip 类型节点额外用 `_import_available` 验证 `verify.imports` 能否导入。

> 注意不对称：**uv 类型节点只查 `.venv` 存在 + `verify.files`，不实际 import**
> （依赖在隔离 venv 里，主进程 import 不到）；pip 类型节点才真正验证 import。
> 失败时给出 `fix` 命令（`sure-eval env setup --node ...`）。

**交付物**：

- `env check`：**无文件交付物**，仅 stdout 报告（表格/JSON）。
- `env setup` 在磁盘上留下：
  1. **节点目录下的 `.venv/`**（核心）—— 独立虚拟环境，含 Python 解释器 +
     `uv sync` 装好的全部依赖（如 nemo_norm 的 `nemo_text_processing` +
     patched `pynini`）。
  2. **setup 日志** —— 写入 cache 根 `logs/env-setup/<时间戳>-<node>.log`
     （默认 `~/.cache/sure-eval/...`，可用 `SURE_EVAL_CACHE_DIR` 覆盖），记录
     每条命令输出与 returncode。
  3. **`post_setup_script` 产物**（若声明）—— 例如 `funasr_itn` 的
     `prepare_funasr_itn.py` 会下载 FunASR 文本处理资源到 `runtime/`（正是其
     `verify.files` 列出的 `runtime/funasr_revision.json` 等）。
  4. **模型/checkpoint** —— 注意：**不是** `env setup` 下载的。`_setup_plan_for_node`
     的 note 明确写 "Checkpoint downloads are declared but not executed in this
     command yet"，模型下载走独立的 `env download` 命令。

推荐流程：

```bash
sure-eval env check  --pipeline pipeline.json   # 先查缺什么
sure-eval env setup  --pipeline pipeline.json   # 再补装
sure-eval env check  --pipeline pipeline.json   # 复查确认 ok
```

`check` 是「体检」，`setup` 是「治疗」；体检不改变环境，治疗才会装东西并留下
`.venv`、日志、脚本产物这些交付物。

### 4.5 `agent plan`

```bash
sure-eval agent plan <task> [--language] [--metric | --metrics] [--pipeline-id] [--output] [--json]
```

输出 `schema=sure.eval.agent_plan.v1`：`root_env` 检查、`selected_routes`（每个
指标一条，含 `pipeline_id`、`env_checks`、`setup` 提示）、`can_run_now`、
`blocking_issues`、`next_steps`。适合 TUI Agent / 评测 Harness 做路由解析与
环境就绪度判断。

### 4.6 `node` 组

```bash
sure-eval node list [--json]
sure-eval node create <name> --stage <stage> [-o/--output-dir <dir>]
```

- `node list`：列出所有已发现节点（内置 + entry point + 项目插件 + 临时路径），含 `node_id`、
  `stage`、`version`、`source`、`selectors`、`default_for`、是否有 `build`。
- `node create`：按名称与 `--stage`（`normalization` / `scoring` 等）生成一个统一
  `src/<package>/` 布局的 node-only 插件包，其 `sure_eval_plugin.yaml` 可供
  `plugin add` 使用，`pyproject.toml` 已声明 `sure_eval.nodes` entry point。

```bash
sure-eval node create "My Norm" --stage normalization -o plugins/
sure-eval plugin add plugins/my_norm  # 固定到项目；也可 pip install 走 entry point
sure-eval node list --json | grep my_norm
```

外部节点约定、四种相关入口与运行流程见 [14.1 节点插件化](#141-节点插件化)。

### 4.7 `plugin` 组

```bash
sure-eval plugin add <local-directory> [--name <name>] [--replace]
sure-eval plugin list [--check-env] [--json]
sure-eval plugin check <name> [--json]
sure-eval plugin sync [<name>] [--json]
sure-eval plugin remove <name> [--json]
```

`plugin` 组把本地插件目录固定到当前项目。目录可以只含 `node.py`、只含
`routes.py`，或同时包含两者；添加后常规 `node`、`metric` 和 `env` 命令自动读取
`.sure-eval/plugins.yaml` 与 `.sure-eval/plugins.lock.json`。项目根默认是当前目录，
也可以在顶层传 `--project-dir <root>`。Open-Bench 下载仍属于第二阶段，完整契约见
[Plugin Management](plugin_management.md)。

---

## 5. 输入与输出契约

### 5.1 输入角色（roles）

`manifest.yaml` 的 `input_contracts` 通过 `required_roles` / `optional_roles`
声明每个评分后端需要的输入。常见角色与 CLI 参数映射（见
`cli_adapters.ROLE_TO_CLI_ARG`）：

| role | CLI 参数 | 说明 |
|:--|:--|:--|
| `ref` | `--ref-file` | 参考文本（key-text 或任务特定格式） |
| `hyp` | `--hyp-file` | 假设/预测文本 |
| `src` | `--src-file` | 源文本（翻译类） |
| `prompt_jsonl` | `--prompt-jsonl` | SLU 提示 JSONL |
| `label_spec` | `--label-spec` | 分类标签规范 |
| `reference_jsonl` | `--reference-jsonl` | KWS/VAD 参考 |
| `sample_output` | `--sample-output` | KWS/VAD/SV 模型输出 |
| `trial_manifest` | `--trial-manifest` | SV 试听清单 |
| `samples_jsonl` | `--samples-jsonl` | TTS/VC/SE/TSE 音频样本 JSONL |
| `wekws_*_file` | `--wekws-*-file` | WeKWS 标签/分数文件 |
| `keyword` | `--keyword` | KWS 关键词 |

### 5.2 行格式

文本类任务通常使用 **key-tab-text** 格式（`row_format: key_text`，`alignment_key:
key`）：

```text
utt_id_001	the reference transcript
utt_id_001	the hypothesis transcript
```

音频类任务（TTS/VC/SE/TSE）使用 `samples_jsonl` 描述样本，运行时按 `metrics`
构建 transcribers / MOS / speaker / reference providers（见 `audio_runtime.py`、
`audio_samples.py`）。

### 5.3 输出产物

每次 `metric run` 在 `--output-dir` 写出：

| 文件 | 内容 |
|:--|:--|
| `report.json` | 任务、语言、指标、`score`、`pipeline_id`、`pipeline_kind`、`member_pipeline_ids`、`computation_node_ids`、`pipeline_trace`（每节点 stage/node_id/version/details/internal_stages）、`input_contract`、`input_files`、`details` |
| `pipeline_description.json` | 所选链路的声明视图：节点、版本、配置路径、输入契约等 |

`computation_node_ids` 包含影响分数的 conversion 与节点，是复现的关键证据链。

---

## 6. 整体架构

分层架构（自上而下）：

```text
┌────────────────────────────────────────────────────────────────┐
│  CLI 层     src/sure_eval/evaluation/cli.py  (Typer + Rich)     │
│             metric / env / agent / doctor                       │
├────────────────────────────────────────────────────────────────┤
│  适配层     cli_adapters.py                                     │
│             把 CLI 参数翻译为 scripts 调用；构建/校验 pipeline spec │
├────────────────────────────────────────────────────────────────┤
│  脚本层     evaluation/scripts/                                 │
│             run.py（任务分派） contracts.py（清单/契约/报告写盘）   │
│             asr.py s2tt.py tts.py vc.py se.py tse.py sv.py …     │
├────────────────────────────────────────────────────────────────┤
│  任务层     evaluation/tasks/<task>/                            │
│             manifest.yaml  routes.yaml  pipeline.py  metrics.py │
│             pipeline.py 暴露 executor（如 evaluate_asr_files）    │
├────────────────────────────────────────────────────────────────┤
│  节点层     evaluation/nodes/<stage>/<name>/                    │
│             node.py  manifest.yaml  node_env.yaml(可选)          │
│             conversion/<id>/ 负责任务级格式转换                   │
├────────────────────────────────────────────────────────────────┤
│  核心层     evaluation/core/pipeline.py (run_pipeline)          │
│             evaluation/core/types.py (dataclasses/契约)          │
│             pipeline_identity.py (id 生成/别名)                  │
├────────────────────────────────────────────────────────────────┤
│  支撑层     env_check.py  cache.py  registry.py  agent_plan.py  │
│             core/config.py  core/logging.py  reports/  rps.py   │
└────────────────────────────────────────────────────────────────┘
```

<div class="diagram" markdown="0">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 1270" role="img"
     aria-label="SURE-EVAL 整体架构图" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif">
  <defs>
    <marker id="arr" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="userSpaceOnUse">
      <path d="M0,0 L10,5 L0,10 z" fill="#3a4356"/>
    </marker>
    <marker id="arrD" markerWidth="10" markerHeight="10" refX="9" refY="5" orient="auto" markerUnits="userSpaceOnUse">
      <path d="M0,0 L10,5 L0,10 z" fill="#2f6fed"/>
    </marker>
  </defs>
  <style>
    .box { fill:#ffffff; stroke-width:1.5; }
    .t { font-size:14.5px; font-weight:700; fill:#1f2430; }
    .b { font-size:12.5px; fill:#3a4356; }
    .lbl { fill:#5b6472; }
    .edge { stroke:#3a4356; stroke-width:1.7; fill:none; }
    .edgeD { stroke:#2f6fed; stroke-width:1.4; fill:none; stroke-dasharray:5 4; }
  </style>
  
<g transform="translate(700,22)">
  <rect x="0" y="0" width="380" height="86" rx="10" class="box" stroke="#c3cad6"/>
  <text x="16" y="22" class="t" font-size="12.5">图例</text>
  <line x1="16" y1="38" x2="48" y2="38" class="edge" marker-end="url(#arr)"/>
  <text x="58" y="42" class="b" font-size="12">实线 — 控制 / 数据流（协议标注在箭头上）</text>
  <line x1="16" y1="62" x2="48" y2="62" class="edgeD" marker-end="url(#arrD)"/>
  <text x="58" y="66" class="b" font-size="12">虚线 — 读取 / 校验 / 返回</text>
</g>

  
<rect x="180" y="20" width="300" height="56" rx="12" class="box" stroke="#34a853"/>
<rect x="180" y="20" width="6" height="56" rx="3" fill="#34a853"/>
<text x="330" y="44" class="t" text-anchor="middle" font-size="15">用户 / Agent / 评测 Harness</text>
<text x="330" y="64" class="b" text-anchor="middle" font-size="12">sure-eval &lt;子命令&gt; … · --json</text>

  <rect x="30" y="118" width="600" height="122" rx="10" class="box" stroke="#2f6fed"/>
<rect x="30" y="118" width="6" height="122" rx="3" fill="#2f6fed"/>
<text x="50" y="145" class="t">① CLI 层 · evaluation/cli.py</text>
<text x="50" y="171" class="b">• Typer 命令路由：metric / env / agent / doctor</text>
<text x="50" y="192" class="b">• Rich 表格 / JSON 输出与错误处理</text>
  <rect x="30" y="290" width="600" height="122" rx="10" class="box" stroke="#7c4dff"/>
<rect x="30" y="290" width="6" height="122" rx="3" fill="#7c4dff"/>
<text x="50" y="317" class="t">② 适配层 · cli_adapters.py</text>
<text x="50" y="343" class="b">• build_pipeline_spec / list_metric_routes</text>
<text x="50" y="364" class="b">• run_pipeline_spec / validate_pipeline_identity</text>
  <rect x="30" y="462" width="600" height="122" rx="10" class="box" stroke="#0f9d9a"/>
<rect x="30" y="462" width="6" height="122" rx="3" fill="#0f9d9a"/>
<text x="50" y="489" class="t">③ 脚本层 · evaluation/scripts/</text>
<text x="50" y="515" class="b">• run.py：任务分派 describe_pipeline / run_task</text>
<text x="50" y="536" class="b">• contracts.py：PipelineDescription、route 查找、写盘</text>
  <rect x="30" y="634" width="600" height="140" rx="10" class="box" stroke="#d6336c"/>
<rect x="30" y="634" width="6" height="140" rx="3" fill="#d6336c"/>
<text x="50" y="661" class="t">④ 任务层 · tasks/<task>/</text>
<text x="50" y="687" class="b">• manifest.yaml：输入契约 / 默认链路</text>
<text x="50" y="708" class="b">• routes.yaml：metric+language+节点 → pipeline_id</text>
<text x="50" y="729" class="b">• pipeline.py：executor（evaluate_asr_files …）</text>
  <rect x="30" y="824" width="600" height="122" rx="10" class="box" stroke="#8e44ad"/>
<rect x="30" y="824" width="6" height="122" rx="3" fill="#8e44ad"/>
<text x="50" y="851" class="t">⑤ 核心层 · evaluation/core/</text>
<text x="50" y="877" class="b">• run_pipeline()：顺序执行节点、收集 trace</text>
<text x="50" y="898" class="b">• types.py：KeyTextFiles / PipelineSpec / EvaluationReport</text>
  <rect x="30" y="996" width="600" height="140" rx="10" class="box" stroke="#e67700"/>
<rect x="30" y="996" width="6" height="140" rx="3" fill="#e67700"/>
<text x="50" y="1023" class="t">⑥ 节点层 · nodes/<stage>/<name>/ + conversion/</text>
<text x="50" y="1049" class="b">• node.py：normalize_* / score_* 实现</text>
<text x="50" y="1070" class="b">• manifest.yaml：版本 / profiles / upstream</text>
<text x="50" y="1091" class="b">• node_env.yaml：uv · binary · pip 运行时与模型</text>
  
<rect x="130" y="1186" width="400" height="58" rx="12" class="box" stroke="#2e7d32"/>
<rect x="130" y="1186" width="6" height="58" rx="3" fill="#2e7d32"/>
<text x="330" y="1210" class="t" text-anchor="middle" font-size="14">产物 · report.json + pipeline_description.json</text>
<text x="330" y="1231" class="b" text-anchor="middle" font-size="12">schema=sure.metric.pipeline.v1 · pipeline_trace</text>

  <rect x="700" y="290" width="380" height="414" rx="10" class="box" stroke="#6b7280"/>
<rect x="700" y="290" width="6" height="414" rx="3" fill="#6b7280"/>
<text x="720" y="317" class="t">支撑层（横切模块）</text>
<text x="720" y="343" class="b">• pipeline_identity — id 生成 / 别名 / 校验</text>
<text x="720" y="364" class="b">• env_check — 节点环境诊断 + fix 提示</text>
<text x="720" y="385" class="b">• cache — SURE_EVAL_CACHE_DIR 缓存根</text>
<text x="720" y="406" class="b">• registry — MetricRegistry（指标类注册）</text>
<text x="720" y="427" class="b">• agent_plan — 路由解析 + 就绪度计划</text>
<text x="720" y="448" class="b">• reports / rps — RPS · SOTA · 报告</text>
  <rect x="700" y="824" width="380" height="312" rx="10" class="box" stroke="#0b7285"/>
<rect x="700" y="824" width="6" height="312" rx="3" fill="#0b7285"/>
<text x="720" y="851" class="t">声明文件 · 数据契约</text>
<text x="720" y="877" class="b">• tasks/<t>/manifest.yaml · routes.yaml</text>
<text x="720" y="898" class="b">• nodes/*/manifest.yaml · node_env.yaml</text>
<text x="720" y="919" class="b">• config/default.yaml</text>
<text x="720" y="940" class="b">• pipeline.json（sure.metric.pipeline.v1）</text>
<text x="720" y="961" class="b">• report.json · pipeline_description.json</text>
  <line x1="330" y1="76" x2="330" y2="118" class="edge" marker-end="url(#arr)"/><text x="330" y="98" class="lbl" text-anchor="middle" font-size="12" stroke="#ffffff" stroke-width="4" paint-order="stroke">命令 + 参数（--json / --pipeline / --validate-env）</text><line x1="330" y1="240" x2="330" y2="290" class="edge" marker-end="url(#arr)"/><text x="330" y="266" class="lbl" text-anchor="middle" font-size="12" stroke="#ffffff" stroke-width="4" paint-order="stroke">函数调用：build_pipeline_spec / run_pipeline_spec</text><line x1="330" y1="412" x2="330" y2="462" class="edge" marker-end="url(#arr)"/><text x="330" y="438" class="lbl" text-anchor="middle" font-size="12" stroke="#ffffff" stroke-width="4" paint-order="stroke">describe_pipeline() / run_task()</text><line x1="330" y1="584" x2="330" y2="634" class="edge" marker-end="url(#arr)"/><text x="330" y="610" class="lbl" text-anchor="middle" font-size="12" stroke="#ffffff" stroke-width="4" paint-order="stroke">load_task_routes · find_*_route · call_route_executor</text><line x1="330" y1="774" x2="330" y2="824" class="edge" marker-end="url(#arr)"/><text x="330" y="800" class="lbl" text-anchor="middle" font-size="12" stroke="#ffffff" stroke-width="4" paint-order="stroke">构建 PipelineSpec + KeyTextFiles → run_pipeline()</text><line x1="330" y1="946" x2="330" y2="996" class="edge" marker-end="url(#arr)"/><text x="330" y="972" class="lbl" text-anchor="middle" font-size="12" stroke="#ffffff" stroke-width="4" paint-order="stroke">node(files) → (files, PipelineNodeResult)</text><line x1="330" y1="1136" x2="330" y2="1186" class="edge" marker-end="url(#arr)"/><text x="330" y="1162" class="lbl" text-anchor="middle" font-size="12" stroke="#ffffff" stroke-width="4" paint-order="stroke">EvaluationReport + pipeline_trace → 写盘</text>
  <line x1="700" y1="351" x2="632" y2="351" class="edgeD" marker-end="url(#arrD)"/><text x="666" y="344" class="lbl" text-anchor="middle" font-size="11" stroke="#ffffff" stroke-width="4" paint-order="stroke">身份校验</text><line x1="700" y1="704" x2="632" y2="704" class="edgeD" marker-end="url(#arrD)"/><text x="666" y="697" class="lbl" text-anchor="middle" font-size="11" stroke="#ffffff" stroke-width="4" paint-order="stroke">生成 pipeline_id</text><line x1="700" y1="1066" x2="632" y2="1066" class="edgeD" marker-end="url(#arrD)"/><text x="666" y="1059" class="lbl" text-anchor="middle" font-size="11" stroke="#ffffff" stroke-width="4" paint-order="stroke">读 node_env.yaml</text>
  <path d="M 16 1190 L 16 150" class="edgeD" fill="none" marker-end="url(#arrD)"/><text x="10" y="640" class="lbl" text-anchor="middle" font-size="12" stroke="#ffffff" stroke-width="4" paint-order="stroke" transform="rotate(-90 10 640)">返回：summary / report.json / pipeline_description.json</text>
</svg>
<p class="diagram-caption">图 1 · SURE-EVAL 整体架构：各模块职责与模块间交互协议</p>
</div>

关键设计原则：

1. **声明与执行分离**：`routes.yaml` / `manifest.yaml` / `node_env.yaml` 只声明
   “是什么”，执行由节点 `node.py` / 任务 `pipeline.py` 与运行时环境负责。
2. **轻根包 + 可选节点环境**：根包只装 CLI、路由、契约、报告与轻量指标；
   重量级模型/工具由 `node_env.yaml` + `env setup` 按需准备。
3. **身份即真相**：`pipeline_id` 由 `task.language.metric + 节点版本` 拼出，
   `metric run` / `env setup` / `env check` 都会重建并校验该身份，拒绝被篡改的
   `pipeline.json`。
4. **所有评分影响步骤皆节点化**：转换（conversion）、归一化（normalization）、
   转录（transcription）、打分（scoring）都以带版本节点进入 `pipeline_trace`。

---

## 7. 目录结构

```text
sure-evaluation/
├── AGENTS.md                          # Agent/TUI 使用约定（先发现路由再准备环境）
├── README.md / README_ZH.md           # 项目总览与快速上手
├── pyproject.toml                     # 打包、依赖、extras、scripts 入口
├── MANIFEST.in                        # 源码包清单
├── config/default.yaml                # 默认配置（Config 模型）
├── docs/                              # 用户/贡献/Agent 文档 + atlas + catalog
│   ├── atlas/                         # 交互式 Pipeline Atlas (index.html) + SVG
│   ├── pipeline_catalog.md/.jsonl     # 提交的机器可读路由清单
│   └── tasks/                         # 各任务指南
├── examples/readme/                   # 冒烟示例输入（asr_en_ref/hyp.txt）
├── scripts/                           # 目录级脚本（catalog/atlas 生成、xforge 等）
├── src/sure_eval/
│   ├── cli.py                         # 顶层入口，转接 evaluation.cli
│   ├── core/                          # config.py / logging.py
│   ├── compat/                        # deepspeed_stub 等兼容桩
│   ├── evaluation/
│   │   ├── cli.py                     # metric/env/agent/doctor 命令
│   │   ├── cli_adapters.py            # CLI ↔ scripts 适配
│   │   ├── agent_plan.py              # Agent 计划载荷
│   │   ├── env_check.py               # 节点环境诊断
│   │   ├── cache.py                   # 缓存根（SURE_EVAL_CACHE_DIR）
│   │   ├── pipeline_identity.py       # pipeline_id 生成与别名
│   │   ├── registry.py                # MetricRegistry
│   │   ├── sure_evaluator.py          # 遗留统一评测器（回归基准）
│   │   ├── audio_runtime.py           # 音频任务运行时构建
│   │   ├── audio_samples.py           # samples_jsonl 解析
│   │   ├── core/                      # pipeline.py / types.py
│   │   ├── scripts/                   # 任务级 describe/run 脚本
│   │   ├── tasks/<task>/              # manifest/routes/pipeline/metrics
│   │   ├── nodes/<stage>/<name>/      # 可复用节点
│   │   └── conversion/<id>/           # 任务级格式转换
│   └── reports/                       # report_manager / sota_manager / sota/
└── tests/                             # 单元测试 + fixtures（41 个 test_*.py）
```

> 注：仓库根目录还保留了几份历史文档（`ARCHITECTURE.source.md`、`PLAN.md`、
> `RECOMMENDATIONS.md`），其中 `ARCHITECTURE.source.md` 描述的
> `docs/agents/`、`fixtures/tasks/`、`src/sure_eval/models/` 等布局与当前代码
> 实际结构不一致，本架构文档以当前代码为准。

---

## 8. 关键模块职责

| 模块 | 职责 |
|:--|:--|
| `evaluation/cli.py` | Typer 命令定义、Rich 表格输出、错误/环境异常处理 |
| `evaluation/cli_adapters.py` | `build_pipeline_spec`（describe）、`list_metric_routes`、`run_pipeline_spec`、`validate_pipeline_identity`；角色→参数映射、任务别名、schema 常量 |
| `evaluation/scripts/run.py` | 任务名 → 任务模块分派（`describe_pipeline` / `run_task`） |
| `evaluation/scripts/contracts.py` | `PipelineDescription`、`load_task_manifest/routes`、`find_*_route`、`node_description`、`write_run_outputs`、`evaluation_report_as_dict` |
| `evaluation/core/pipeline.py` | `run_pipeline(spec, files)`：顺序执行节点并收集 trace |
| `evaluation/core/types.py` | `KeyTextFiles`、`EvaluationFiles`、`MetricInputContract`、`PipelineNodeResult`、`PipelineSpec`、`EvaluationReport` |
| `evaluation/pipeline_identity.py` | `slug`/`canonical_metric`/`build_atomic_pipeline_id`/`build_bundle_pipeline_id`、节点/转换组件、别名映射 |
| `evaluation/env_check.py` | `NodeEnvChecker`、`EnvCheckResult`、`EnvironmentCheckError`、`doctor_*`、`iter_known_node_ids` |
| `evaluation/cache.py` | `get_cache_root`（默认 `~/.cache/sure-eval`，可用 `SURE_EVAL_CACHE_DIR` 覆盖） |
| `evaluation/agent_plan.py` | `build_agent_plan`：路由解析 + 环境就绪度 + setup 提示 |
| `evaluation/registry.py` | `MetricRegistry`（旧式指标类注册表） |
| `evaluation/sure_evaluator.py` | `SUREEvaluator`：遗留统一评测器（ASR/SER/GR/S2TT/SLU/SD/SA-ASR），作为回归基准 |
| `evaluation/audio_runtime.py` / `audio_samples.py` | 音频类任务（TTS/VC/SE/TSE）样本解析与运行时构建 |
| `core/config.py` | Pydantic `Config`（`from_yaml`/`from_env`，环境变量 `SURE_EVAL_CONFIG`） |
| `core/logging.py` | structlog 结构化日志（`configure_logging` / `get_logger`） |
| `reports/sota_manager.py` | SOTA 基线加载/计算（`reports/sota/sota_baseline.yaml`） |
| `reports/report_manager.py` | 模型报告管理、对比、Markdown 报告生成 |
| `evaluation/rps.py` | RPS（相对性能分）计算与结果数据库 |

---

## 9. 一次运行的完整数据流

### 9.1 describe（发现/描述，不评分）

```text
CLI: metric describe <task> --pipeline-id <id>
  → cli_adapters.build_pipeline_spec
      → scripts.run.describe_pipeline(task)         # 按任务分派到 scripts/<task>.py
          → contracts.load_task_routes(task)         # 读 tasks/<task>/routes.yaml
          → contracts.load_task_manifest(task)       # 读 tasks/<task>/manifest.yaml
          → contracts.find_pipeline_route(...)       # 匹配精确 pipeline_id
          → contracts.describe_from_contracts(...)   # 组装 PipelineDescription
      → _node_slots(...) 按 stage 生成节点槽位与候选
      → 输出 pipeline.json (schema=sure.metric.pipeline.v1)
```

### 9.2 run（评分）

```text
CLI: metric run --pipeline pipeline.json --output-dir ... [--validate-env]
  → (可选) env_check.check_pipeline_environment(payload)
  → cli_adapters.run_pipeline_spec
      → validate_pipeline_selection(pipeline)   # 校验槽位选择合法
      → validate_pipeline_identity(pipeline)    # 重建路由身份，逐字段比对
      → _run_kwargs_from_pipeline(...)          # 组装 run 参数
      → scripts.run.run_task(task, **kwargs)    # 分派到 scripts/<task>.py .run
          → 任务脚本解析路由 → call_route_executor(route, **kwargs)
              → 任务 executor（tasks/<task>/pipeline.py 的 evaluate_*）
                  → core.pipeline.run_pipeline(spec, files)   # 顺序执行节点
                      → 每个 node 返回 (next_files, PipelineNodeResult)
                  → 返回 EvaluationReport（含 pipeline_trace）
          → contracts.write_run_outputs(...)    # 写 report.json + pipeline_description.json
      → 返回 summary（status/task/metric/score/pipeline_id/路径）
```

### 9.3 节点执行（以 ASR WER 为例）

```text
KeyTextFiles(ref, hyp)
  → normalization/whisper_norm   (EnglishTextNormalizer)   → 归一化后 KeyTextFiles
  → scoring/wenet_wer            (compute_wer)              → 分数 + trace
  → EvaluationReport { score, pipeline_id, pipeline_trace }
```

---

## 10. Pipeline 身份系统

`pipeline_identity.py` 负责所有身份相关逻辑：

- `slug(value)`：把显示名规范为小写、下划线 token。
- `canonical_metric(metric)`：经 `METRIC_ALIASES` 归一化（如 `wv-mos`→`wv_mos`、
  `sim/wavlm-large`→`spk_sim`、`tts_cer`→`cer`）。
- `build_atomic_pipeline_id(task, language, metric, components)`：
  `task.language.metric` + 每个组件 `base[_profile]_vN`。
- `build_bundle_pipeline_id(...)`：`task.language.multi.__成员尾缀__`。
- `component_instance_id / component_trace_id`：节点/转换的身份与 trace 标识。
- 版本读取自节点 `manifest.yaml` 的 `version` 字段（缺失默认 `v1`）；存在别名
  （`scoring/wenet_cer`、`scoring/wenet_mer` 复用 `scoring/wenet_wer` 的 manifest）。

**身份校验**（`validate_pipeline_identity`）会重建预期路由，比对 `pipeline_id`、
`pipeline_kind`、`member_pipeline_ids`、`computation_node_ids` 与节点列表，任何
不一致都会使 `metric run` / `env setup` / `env check` 拒绝执行——这正是“可复现、
防篡改”的机制来源。

---

## 11. 节点与节点环境

### 11.1 节点三件套

每个节点目录 `nodes/<stage>/<name>/` 拥有：

| 文件 | 作用 |
|:--|:--|
| `manifest.yaml` | 节点身份：`id`、`version`、`stage`、`input/output_schema`、`implementation`、`profiles`、`upstream`（vendored 来源/许可/改动） |
| `node.py`（或包） | 可调用的节点实现（如 `normalize_*`、`score_*`） |
| `node_env.yaml`（可选） | 可选运行时声明：`runtime`（uv/binary/pip）、`models`、`tools`、`packages`、`verify`、`group` |

> 外部节点（插件）不放在框架仓库内。推荐使用统一的 `src/<package>/node.py`
> 包布局，通过 entry point、项目级 `plugin add` 或临时 `--extra-node-path` 在运行时
> 加载；当前路径 loader 仍兼容插件根目录的 `node.py`。`MANIFEST`/`NODE_ENV` 以内联
> dict 或包内相对路径提供，见 [14.1 节点插件化](#141-节点插件化) 和
> [插件管理](plugin_management.md#54-统一插件包布局plugin-add-与pip-install)。

### 11.2 node_env.yaml 示例

```yaml
id: transcription/qwen3_asr_1_7b
group: asr-transcription
runtime:
  type: uv
  python: "3.11"
  project: pyproject.toml
  optional: true
models:
  - id: Qwen/Qwen3-ASR-1.7B
    provider: huggingface
    target: checkpoints/huggingface/hub/models--Qwen--Qwen3-ASR-1.7B
    env: QWEN3_ASR_1_7B_CHECKPOINT
verify:
  imports: [qwen_asr, torch, transformers]
```

运行时类型（`env_check.OPTIONAL_NODE_RUNTIME_TYPES`）：

- **uv**：`uv venv` + `uv sync`（可 `--frozen`），可选 `post_setup_script`。
- **binary**：`bash <build_script>`（如 sctk_sclite）。
- **pip**：`python -m pip install <packages>`，用 `verify.imports/files` 校验。

`env check` 通过 `NodeEnvChecker` 判断：in-process 节点直接 OK；uv/pip/binary
节点检查 `.venv`、checkpoint 文件、verify imports/files 等，失败时给出 `fix`
命令（`sure-eval env setup --node ...`）。

### 11.3 节点清单（stage 概览）

- **frontend**：`funasr_loader_16k_mono`
- **normalization**：`aispeech_norm`、`canonical_itn`、`funasr_itn`、`gstar_norm`、
  `nemo_norm`、`prompt_norm`、`punctuation_strip_norm`、`vad_timebase`、
  `wetext_norm`、`whisper_norm`
- **transcription**：`cohere_transcribe_arabic_07_2026`、`paraformer_zh`、
  `qwen3_asr_1_7b`、`whisper_large_v3`
- **scoring**：`bleurt_20`、`classify`、`cosine_trial_scores`、`det_eer`、`dnsmos`、
  `ecapa_tdnn_sim`、`eres2net_sim`、`meeteval`、`min_dcf_p005`、`pesq`、
  `sacrebleu`、`sctk_sclite`、`si_sdr`、`stoi`、`token_cer`、`token_mer`、`utmos`、
  `vad_auc_roc`、`vad_detection_duration`、`wavlm_large_sim`、`wekws_det`、
  `wenet_wer`、`wv_mos`、`xcomet_xl`
- **validation**：`vad_contract`
- **conversion**：`sa_asr__cpwer`（独立目录节点）；KWS 另有三条内联输入转换
  `kws_sure_json_to_samples`、`kws_wekws_score_ctc_to_samples`、
  `kws_wekws_frame_score_to_samples`（由 `tasks/kws/pipeline.py` 生成，进入
  `computation_node_ids`，无独立 conversion 目录）

需要显式环境的节点（`env_check.NODE_LOCAL_PROJECTS` 等）包括 bleurt_20、
wavlm/ecapa/eres2net 说话人相似度、dnsmos/wv_mos/utmos/xcomet_xl 等学习型打分
节点，以及 cohere_transcribe_arabic_07_2026 / paraformer_zh / qwen3_asr_1_7b /
whisper_large_v3 转录节点。其中 `cohere_transcribe_arabic_07_2026` 为阿拉伯语
TTS 语义转写节点，运行于节点本地 uv 环境（Python 3.11 + Transformers 5.4.0 +
PyTorch 2.8.0，GPU 可选），模型 `CohereLabs/cohere-transcribe-arabic-07-2026`
采用 Apache-2.0 许可，checkpoint 固定 revision 以复现。

---

## 12. 支持的任务与指标

任务别名：`ser`/`gr` → `classification`，`sa-asr` → `sa_asr`，
`speech_enhancement` → `se`。

| 任务 | 目录 | 规范指标（示例） |
|:--|:--|:--|
| ASR 语音识别 | `tasks/asr` | `wer`、`cer`、`mer`（14 种语言默认链路，含 `ar` 阿拉伯语） |
| S2TT 语音翻译 | `tasks/s2tt` | `bleu`、`bleu_char`、`chrf`、`xcomet_xl`、`bleurt_20` |
| SD 说话人日志 | `tasks/sd` | `der` |
| SV 说话人验证 | `tasks/sv` | `eer`、`min_dcf`（默认 bundle） |
| SA-ASR 说话人感知 ASR | `tasks/sa_asr` | `cpwer` + DER 伴随结果 |
| TTS 语音合成 | `tasks/tts` | `tts_cer`/`cer`（含 `ar` 阿拉伯语）、`tts_wer`/`wer`、`spk_sim`、`dnsmos`、`wv_mos`、`utmos` |
| VC 声音转换 | `tasks/vc` | 同上（`vc_cer`/`cer` 等） |
| SE 语音增强 | `tasks/se` | `si_sdr`、`stoi`、`pesq`、`dnsmos`、`wv_mos`、`utmos` |
| TSE 目标说话人提取 | `tasks/tse` | `si_sdr`、`spk_sim`、`dnsmos`、`wv_mos`、`utmos`、`cer`、`wer` |
| Classification/SER/GR | `tasks/classification` | `accuracy` |
| SLU 语义理解 | `tasks/slu` | `accuracy` |
| KWS 关键词唤醒 | `tasks/kws` | `accuracy`、`macro_recall`、precision/recall/F1、FRR/FAR、`false_alarm_per_hour`、`det_curve` |
| VAD 语音活动检测 | `tasks/vad` | `f1`、`p_fa`、`p_miss`、`dcf_nist`、`auc_roc`（另报 precision/recall 与各类时长统计） |

> **ASR 多语言默认链路（14 种语言）**：ASR 按语言提供默认 pipeline，
> 归一化策略分组如下——中文 CER 默认 `wetext_norm` 的 `zh_itn` profile
> （WeNet CER）；英文 WER 默认 `whisper_norm` 的 English profile（WeNet WER）；
> 阿拉伯语 CER 默认 `nemo_norm` 的 `ar_tn` profile（WeNet CER）；
> 日语/韩语 CER 与西班牙语/法语/德语/俄语/葡萄牙语/越南语/印尼语/他加禄语 WER
> 默认 `funasr_itn`（需可选节点环境）；捷克语 MER 默认 `aispeech_norm`；
> 另有 zh/en/cs 的 `canonical_itn` 变体以 `token_cer`/`token_mer` 打分
> （需 `[canonical]` extra）。各语言的历史/兼容归一化路由仍可通过精确
> `pipeline_id` 选择。

> **阿拉伯语 TTS 语义 CER（近期新增）**：TTS 的 `ar` 语言新增默认链路
> `tts.ar.cer.cohere_transcribe_arabic_07_2026_v1.nemo_norm_ar_tn_v1.wenet_cer_v1`，
> 计算节点为「Cohere Transcribe Arabic 转写 → NeMo 阿拉伯语 TN → WeNet CER」。
> 其中 `normalization/nemo_norm` 通过 `ar_tn` profile 将阿拉伯语书面形式归一为
> 口语形式（NeMo text processing 1.2.0，Apache-2.0），转写节点
> `transcription/cohere_transcribe_arabic_07_2026` 的模型与 revision 均固定，
> 全程记录于 `pipeline_trace`。

各任务的输入契约、精确 `pipeline_id`、节点与运行示例见 `docs/tasks/<task>.md`。

---

## 13. 报告、RPS 与 SOTA

### 13.1 RPS（Relative Performance Score）

`evaluation/rps.py` 提供：

- `RPSCalculator`：基于 `SOTAManager` 的基线把原始分归一化为 RPS
  （`1.0 = SOTA`，`>1.0` 更好，`<1.0` 更差）；错误率类指标做百分比↔小数归一。
- `EvaluationDatabase`：结果落盘到 `./results/evaluations.json`，支持按工具/数据集
  过滤与排行。
- `RPSManager`：`evaluate_and_record(...)` 一站式“算 RPS + 记录”。

### 13.2 SOTA 基线

`reports/sota_manager.py` 从 `src/sure_eval/reports/sota/sota_baseline.yaml` 加载
基线（dataset → metric/score/higher_is_better/sota_model），提供 RPS 计算所需的
方向与归一化规则。

### 13.3 报告管理器

`reports/report_manager.py` 管理模型性能报告（`reports/models/...json`），支持
模型对比、排行榜、Markdown 报告生成（面向 SURE Benchmark 的模型性能追踪）。

### 13.4 Atlas 与 Catalog

- `docs/pipeline_catalog.jsonl` + `docs/pipeline_catalog.md`：提交的机器可读路由
  清单（由 `scripts/generate_pipeline_catalog.py` 从 `routes.yaml` 生成）。
- `docs/atlas/index.html` + `pipeline_atlas.svg`：把整张 catalog 画成可检索的
  交互式地图（由 `scripts/generate_pipeline_atlas.py` 生成）。

---

## 14. 扩展方式

扩展遵循“声明 + 实现 + 测试 + 文档”四步，均以 PR 模板与贡献指南为准
（`docs/contributing.md`、`docs/add_a_metric.md`、`docs/pr_guides/`）：

- **新增/修改任务或指标**：`tasks/<task>/manifest.yaml`（输入契约、默认指标/
  链路）+ `routes.yaml`（精确路由）+ `pipeline.py`（executor）+ `scripts/<task>.py`
  （describe/run）。
- **新增/升级节点**：`nodes/<stage>/<name>/manifest.yaml`（版本、profiles、
  upstream）+ `node.py` + 可选的 `node_env.yaml` / `pyproject.toml` / `uv.lock`。
- **节点插件化**：将 normalization/scoring 节点与框架解耦，支持外部节点通过
  entry point、项目级 `plugin add` 或临时 `--extra-node-path` 在运行时加载。
  `sure-eval node create` 脚手架生成统一 `src/` 包布局的 `node.py` 模板，`node list` 列出已发现
  节点；外部节点按 `profiles.default_for` 自动进入 `metric describe` 的 slot
  choices。详见 `docs/node_plugin_design.md`、`docs/plugin_management.md` 与
  `examples/node_plugin_lowercase/`，完整说明见 14.1。
- **新增路由**：在 `routes.yaml` 增加一条 route，`pipeline_id` 的节点版本段自动
  参与身份；计算仍由带版本节点拥有。
- **贡献转换**：`conversion/<id>/manifest.yaml` + `convert.py`。

改动后应通过 `sure-eval metric routes`、精确 `metric describe`、pipeline 化
`env check` 与 `metric run` 验证；Agent 需遵循 `docs/agent_contract.md`。

### 14.1 节点插件化

外部节点（normalization/scoring 等 stage）不再需要放进框架仓库，可作为独立包
分发、在运行时加载。一个节点是单文件 `node.py`，暴露约定属性：

| 属性 | 必填 | 说明 |
|:--|:--|:--|
| `NODE_ID` / `STAGE` / `VERSION` | ✅ | 节点身份 |
| `MANIFEST` | ✅ | dict（等价 manifest.yaml）或包内相对路径 |
| `NODE_ENV` | ⬜ | dict（等价 node_env.yaml）或路径；无依赖为 `None` |
| `SELECTORS` | ⬜ | 让任务 dispatch 能按 selector 字符串找到本节点 |
| `build(**config)` | ✅ | 工厂，返回 `Callable[[KeyTextFiles], tuple[KeyTextFiles, PipelineNodeResult]]` |

四种相关入口：

1. **entry point（正式分发）**：包 `pyproject.toml` 声明
   `[project.entry-points."sure_eval.nodes"]`，`pip install` 后自动发现。
2. **项目级插件（固定引入）**：`sure-eval plugin add <directory>` 写入
   `.sure-eval/plugins.yaml` 和内容 lock，后续命令自动加载。
3. **本地路径（临时调试）**：CLI 使用可重复的 `--extra-node-path <directory>`；
   Python 调用方可显式传 `resolve(..., local_paths=[...])`。
4. **CLI 脚手架**：`sure-eval node create "My Norm" --stage normalization` 生成模板，
   再选择上述任一注册通道。

命令：

```bash
sure-eval node list --json            # 列出内置 + entry point + 项目插件节点
sure-eval node create "My Norm" --stage normalization -o plugins/
sure-eval plugin add plugins/my_norm  # 固定到当前项目
sure-eval plugin list
# 或 pip install -e plugins/my_norm   # 通过 entry point 正式分发
```

外部节点声明 `profiles.default_for: ["ASR/en/wer"]` 后，会自动出现在
`metric describe asr --language en --metric wer` 的 normalization slot choices 中。

接入 pipeline 运行：外部包通过 `[project.entry-points."sure_eval.routes"]`
声明 route（name=task，value=暴露 `ROUTES` 列表的模块），`pip install` 后 route
自动合并进 `metric routes` / `describe` / `run`，无需改动仓库的 `routes.yaml`。

完整的 entry point 示例：`examples/node_plugin_lowercase/`（normalization）与
`examples/node_plugin_exact_match/`（scoring），每个包同时注册节点与 route，可通过
`pip install -e` 后端到端运行。统一布局项目插件示例包括
`examples/node_only_plugin/`、`examples/pipeline_plugin_cer/` 和
`examples/node_pipeline_plugin_wer/`，均可直接 `plugin add`。项目插件支持
node-only、route-only 与 node-and-route 三种目录形态，详见
[Plugin Management](plugin_management.md)。

> 提交规范：`.venv/`、`**/checkpoints/`、模型权重（`*.ckpt`/`*.pt`/`*.onnx`/
> `*.safetensors`/`*.bin`）、运行时日志与本地结果目录均被 `.gitignore` 排除，
> 不进入 Git；声明、安装命令、下载逻辑、检查与文档则需提交。

---

## 15. 相关文档索引

| 主题 | 文档 |
|:--|:--|
| 安装与基础冒烟 | `docs/installation.md` |
| 环境管理（根环境 vs 节点环境） | `docs/environment.md` |
| 任务指南 | `docs/tasks/`（README + 各任务 md） |
| 路由清单（提交版） | `docs/pipeline_catalog.md` / `.jsonl` |
| 交互式链路地图 | `docs/atlas/index.html` |
| 可复现性 | `docs/reproducibility.md` |
| Agent 集成契约 | `docs/agent_contract.md` |
| 节点插件化（外部 stage 运行时加载） | `docs/node_plugin_design.md` |
| 项目级插件管理 | `docs/plugin_management.md` |
| 贡献指南 | `docs/contributing.md`、`docs/add_a_metric.md`、`docs/pr_guides/` |
| 项目总览 | `README.md` / `README_ZH.md` |
