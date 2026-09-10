# SURE 节点插件化设计方案

> 目标：将 `normalization` / `scoring` 节点从「框架内建、编译期耦合」演进为
> 「可外部安装的插件」，外部包通过 entry point 或本地路径在**运行时**被发现、
> 描述、准备环境并执行，全程不改框架与 task 代码。
>
> 设计参照 HuggingFace Evaluate 的
> [Creating and sharing a new evaluation](https://huggingface.co/docs/evaluate/creating_and_sharing)
> 方案。

## 1. 目标与成功标准

1. 外部节点可通过 **entry point（已安装插件）** 或 **本地路径（零安装）** 被
   `metric routes` / `metric describe` / `metric run` / `env setup` / `env check`
   发现并执行。
2. `metric run` 动态加载外部节点，走统一 `run_pipeline` 契约。
3. `env setup` / `env check` 支持外部节点的 `node_env` 声明。
4. 内建节点行为与 `pipeline_id` 完全向后兼容（回归零变化）。
5. 提供 `sure-eval node create` 脚手架 + 最小示例包 + 文档。

## 2. 参照：HF Evaluate 方案精髓

| 机制 | HF Evaluate | 对 SURE 的启发 |
|---|---|---|
| 模块形态 | 单个 `.py` 脚本，实现统一类接口（`_info`/`_compute`/`_download_and_prepare`） | 节点收敛为**单文件 `node.py`**，`MANIFEST` 内联 |
| 多来源加载 | `evaluate.load("accuracy")` 内置 / `evaluate.load("lvwerra/x")` Hub / `evaluate.load("path/to/file.py")` 本地 | 节点引用支持**内置名 / 已装插件 / 本地路径** |
| 脚手架 | `evaluate-cli create "My Metric"` 生成模板 | 新增 `sure-eval node create` / `node list` |
| 依赖/资源 | `requirements.txt` + `_download_and_prepare` + `dl_manager` | `node_env` + `env download`（已有，对齐即可） |
| 分享 | git push 到 Hub，无需重装库 | PyPI entry point（推荐）+ 本地路径（零安装） |

核心启发是 **`load()` 的多来源按名动态加载** 与 **CLI 脚手架**。

## 3. 现状：耦合点清单

| # | 位置 | 耦合方式 |
|---|---|---|
| 1 | `pipeline_identity.py:13`、`contracts.py:19`、`env_check.py` | `NODES_ROOT = EVALUATION_ROOT/"nodes"` 硬编码，仅扫描 `nodes/<stage>/<name>/` |
| 2 | `contracts.py:270` `load_node_manifest` | 只从 `NODES_ROOT/.../manifest.yaml` 读文件 |
| 3 | `env_check.py:453` `iter_known_node_ids` | 仅 glob `NODES_ROOT/*/*/node_env.yaml` |
| 4 | `tasks/asr/pipeline.py:16-46` | 模块顶部显式 `from ... import normalize_*/score_*` |
| 5 | `scripts/asr.py:107` `_executor_selectors_from_route` | if-elif：node_id → 选择器字符串 |
| 6 | `tasks/asr/pipeline.py:340` `_normalization_node`/`_scoring_node` | if-elif：选择器 → lambda 包装的节点函数 |
| 7 | `tasks/asr/pipeline.py:394` `_normalizer_component`/`_scoring_node_id` | if-elif：label → node_id（生成 `pipeline_id`） |
| 8 | 各节点 `manifest.yaml` 的 `implementation` 字段 | 死字段，从未被运行时 import |

**关键利好**：框架已有统一节点执行契约——`PipelineSpec.nodes` 是
`tuple[Callable[[KeyTextFiles], tuple[KeyTextFiles, PipelineNodeResult]], ...]`，
`core/pipeline.py` 的 `run_pipeline` 顺序调用。解耦只需把「if-elif 选择 + lambda
包装」下沉为「节点自描述 + 注册表动态构造」。

## 4. 用户如何注册节点

### 4.1 方式 A：本地路径加载（零安装）

最轻量，适合开发调试，无需 `pip install`：

```bash
sure-eval metric run --pipeline p.json \
  --extra-node-path ./my_norm.py \        # 或 ./my_norm_pkg/（含 node.py）
  --ref-file ref.txt --hyp-file hyp.txt --output-dir out
```

`p.json` 里 `selected` 用 `normalization/my_norm` 引用；`NodeRegistry.resolve` 发现
它不是内置也不是已装插件，就按本地路径动态 `import_module` 加载。

### 4.2 方式 B：entry point 安装（正式分发）

外部包 `pyproject.toml`：

```toml
[project.entry-points."sure_eval.nodes"]
normalization/my_norm = "sure_eval_node_mynorm.node"
```

```bash
pip install ./sure-eval-node-mynorm
```

安装后框架用 `importlib.metadata.entry_points(group="sure_eval.nodes")` 自动发现。

### 4.3 方式 C：CLI 脚手架生成模板

```bash
sure-eval node create "My Norm" --stage normalization
# 生成：
#   my_norm/
#   ├── pyproject.toml            # 含 entry point 声明
#   └── sure_eval_node_my_norm/
#       └── node.py               # 模板：NODE_ID/STAGE/VERSION/MANIFEST/build 骨架
```

### 4.4 单文件 `node.py` 形态（三种方式共用）

```python
"""外部 normalization 节点示例：英文统一小写归一化。"""
from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult

NODE_ID = "normalization/my_norm"
STAGE = "normalization"
VERSION = "v1"

MANIFEST = {
    "id": NODE_ID,
    "version": VERSION,
    "stage": STAGE,
    "language_sensitive": True,
    "input_schema": "key_text_files",
    "output_schema": "key_text_files",
    "profiles": {"lowercase": {"language": "en", "default_for": ["ASR/en/wer"]}},
    "upstream": {"package": "my_norm", "version": "0.1.0", "license": "MIT"},
}

NODE_ENV = None  # in-process；有依赖则给 node_env dict

def build(*, language=None, profile="lowercase", **config):
    def node(files: KeyTextFiles):
        ...  # 归一化逻辑
        return new_files, PipelineNodeResult(
            stage=STAGE, node_id=NODE_ID, version=VERSION,
            details={"profile": profile, "language": language},
        )
    return node
```

约定接口（框架通过 `import_module` + `getattr` 读取）：

| 模块属性 | 必填 | 说明 |
|---|---|---|
| `NODE_ID` / `STAGE` / `VERSION` | ✅ | 节点身份 |
| `MANIFEST` | ✅ | dict（等价 manifest.yaml）或包内 `manifest.yaml` 相对路径 |
| `NODE_ENV` | ⬜ | dict（等价 node_env.yaml）或路径；无环境依赖则为 `None` |
| `build(**config)` | ✅ | 工厂，返回 `Callable[[KeyTextFiles], tuple[KeyTextFiles, PipelineNodeResult]]` |

> 与 HF 的「单脚本 + 统一接口」一致：`MANIFEST` 内联（等价 `_info`），`build`
> 是工厂（等价 `_compute` 的构造），`NODE_ENV`（等价 `requirements.txt` +
> `_download_and_prepare`）。为兼容现有内建节点，`manifest.yaml` / `node_env.yaml`
> 文件形式**仍支持**（内联 dict 优先，文件兜底）。

## 5. 目标架构：多来源 `resolve`

```
NodeRegistry.resolve(node_ref)  ← 单一入口，四种来源按序解析
  ├─ ① 内置名       NODES_ROOT 扫描（现有内建）
  ├─ ② 已装插件     entry_points("sure_eval.nodes")
  ├─ ③ 本地路径     文件 .py / 目录（含 node.py，零安装）
  └─ ④ (可选)远程   user/node → clone 到 cache 后按 ③ 加载
        │
        ▼
  NodeRegistration(node_id/stage/version/manifest/node_env/build)
        │
        ▼
describe → pipeline.json → run_pipeline（现有流程不变）
```

## 6. 核心设计

- **6.1 节点协议**：`core/node_protocol.py` 定义 `NodeRegistration`
  （node_id/stage/version/manifest/node_env/build），由 node 模块约定属性构造。
- **6.2 NodeRegistry**（替代硬编码 #1/#2/#3）：`resolve(node_ref)` 按 ①→②→③→④
  解析；`discover()` 聚合内置 + entry point；内建节点由适配层构造
  `NodeRegistration`（不改现有 node.py）。改造 `load_node_manifest` /
  `_manifest_path` / `iter_known_node_ids` 走 registry。
- **6.3 动态 dispatch**（消除 #4–#7）：`NodeRegistration.selectors` 声明（或沿用
  manifest `profiles`），将 4 处 if-elif 替换为 `registry.build(node_id, config)`；
  `routes.yaml` 节点条目可携带 config，缺省回退 `profiles` 默认。
- **6.4 describe/choices 聚合**：choices 由 registry 按 `profiles.default_for`
  聚合，外部节点自动进入对应 task/lang/metric 候选。
- **6.5 环境集成**：`NodeEnvChecker` / `iter_known_node_ids` 读外部 `NODE_ENV`，
  对齐 `env download`（对应 HF `_download_and_prepare`）。
- **6.6 CLI 脚手架**：`sure-eval node create` / `node list`（对应
  `evaluate-cli create`）。

## 7. 分阶段实施

- **Phase 1（协议 + Registry + 多来源 resolve）**：`node_protocol.py` +
  `node_registry.py`；实现 ①内置/②entry point/③本地路径 解析；内建适配层；改造
  `load_node_manifest`/`_manifest_path`/`iter_known_node_ids`。
  - 验收：内建全量回归零变化；本地 `.py` 与 entry point 节点均可被 `resolve()`。
- **Phase 2（动态 dispatch）**：内建补齐 `build` + `selectors`；替换 4 处
  if-elif；`routes.yaml` 支持携带 config。
  - 验收：ASR 全语言全 metric 通过、`pipeline_id` 不变；本地/插件节点经引用后
    `metric run` 可执行。
- **Phase 3（describe 聚合 + CLI 脚手架）**：choices 由 registry 聚合；新增
  `sure-eval node create` / `node list`。
  - 验收：插件 `pip install` 后自动展示可选；`node create` 生成模板可直接运行。
- **Phase 4（env + 示例 + 文档）**：env 支持外部 `NODE_ENV`；交付示例包；文档
  新增「节点插件」章节 + `agent_contract.md` 补充插件契约。

## 8. 边界与失败模式

- **node_id 冲突**（外部与内建同名）：默认报错要求改名，避免静默覆盖。
- **entry point / 本地路径加载失败**：`describe` 降级 warning 跳过，`run` 命中时
  明确报错。
- **本地路径解析**：`resolve` 支持 `.py` 单文件与目录（含 `node.py`）；相对路径
  以当前工作目录或 `--extra-node-path` 基准解析。
- **manifest/node_env 缺字段**：构造时校验必填项（`build`/`version`/`stage`）。
- **pipeline_id 稳定**：version 从 manifest 读，身份算法不变，仅 `_manifest_path`
  走 registry。
- **远程来源（可选）**：克隆到 `get_cache_root()/nodes/<user>__<name>`，与 env
  缓存隔离。

## 9. 测试与验收

- 单元：`resolve` 多来源、去重/优先级、`manifest()` fallback、`build()` 构造、
  config 回退。
- 回归：内建 ASR 全链路结果与 `pipeline_id` 逐项不变。
- 插件 E2E：本地路径（不安装）+ entry point（安装）各跑通一个 normalization +
  一个 scoring，报告含外部节点 trace。
- 脚手架：`node create` → 填模板 → 本地路径运行 全流程打通。

## 10. 关键假设

- 保持 Python ≥ 3.10（selectable `entry_points` API 兼容）。
- 统一 callable 契约 `Callable[[KeyTextFiles], tuple[KeyTextFiles, PipelineNodeResult]]`
  不变。
- 内建节点经适配层接入，不强制改写现有 `node.py`。
- `pipeline_id` 稳定身份不变；本地路径节点默认不参与 `pipeline_id`（仅调试），
  正式纳入需走 entry point（保证可复现身份）。

## 11. 实施状态

| Phase | 状态 | 说明 |
|---|---|---|
| Phase 1 | ✅ 已完成 | 协议 + Registry + 多来源 resolve（内置/entry point/本地路径，含验收） |
| Phase 2 | ✅ 已完成 | 动态 dispatch（ASR 6 处 if-elif 加 registry fallback，外部节点可跑通） |
| Phase 3 | 待开始 | describe 聚合 + CLI 脚手架 |
| Phase 4 | 待开始 | env 集成 + 示例 + 文档 |
