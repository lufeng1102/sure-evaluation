# SURE 节点统一解耦方案（设计 + VAD 试点实现草案）

> 状态：VAD 试点已实现并验收通过（§9 验收记录）。方案正文保留原草案，供
> 后续 task 推广参考。

## 1. 背景与目标

当前节点插件化已完成两件**全 task 通用**的事：

- **节点发现**：`NodeRegistry` 按 内置 / entry point / 本地路径 解析（所有 stage）。
- **route 注入**：`load_task_routes` 聚合 `sure_eval.routes` entry point（所有 task）。

但「动态 dispatch（node_id → 节点 callable）」只有 ASR 做过。根因不是 dispatch
本身难写，而是**统一执行器 `run_pipeline` 的契约写死成 `KeyTextFiles`**：

```python
# core/types.py（现状）
PipelineSpec.nodes: tuple[Callable[[KeyTextFiles], tuple[KeyTextFiles, PipelineNodeResult]], ...]
```

`KeyTextFiles` 只有 `ref_file`/`hyp_file` 两个文本文件，因此只有 ASR 的
normalization/scoring 能走 `run_pipeline`。其它 task 的节点吃的是异构数据，
各自在 `pipeline.py` 里硬编码调用链。

**目标**：定义一套统一节点载荷 + 统一 dispatch 约定，让**任意 task** 的外部
节点都只需「写 node.py + 声明 entry point」，dispatch 全自动，无需改 task 源码。
本草案以 VAD 为试点，先验证非文本 task 可行，再决定是否推广。

## 2. 现状：ASR 与 VAD 的契约差异

| 维度 | ASR（已解耦） | VAD（未解耦） |
|---|---|---|
| 节点链 | `normalization → scoring` | `validation → normalization → scoring` |
| 节点数据 | `KeyTextFiles`（ref/hyp 文件） | 逐级变换 bundle |
| 节点签名 | `KeyTextFiles → KeyTextFiles`（同构） | `Files → ValidatedBundle → NormalizedBundle → result`（异构） |
| dispatch | `registry.build(node_id)` | `pipeline.py` 硬编码 `validate_vad_contract(...)` 等 |

VAD 的真实契约链（`tasks/vad/pipeline.py` 现状）：

```python
validated_bundle, validation_result = validate_vad_contract(
    reference_jsonl, sample_output, required_prediction_fields=...,
)                                   # (str, str, config) -> VADValidatedBundle
normalized_bundle, normalization_result = normalize_vad_timebase(
    validated_bundle, frame_shift_sec=..., profile=..., ...,
)                                   # VADValidatedBundle -> VADNormalizedBundle
scoring_result = score_vad_detection_duration(normalized_bundle)
                                    # VADNormalizedBundle -> result
```

`route["nodes"]`（`validation/vad_contract`、`normalization/vad_timebase`、
`scoring/vad_detection_duration`）在 executor 里**根本没有被读取**，`pipeline_id`
由 `_identity_components` 硬编码。这就是要解耦的点。

## 3. 统一方案核心设计

### 3.1 统一节点载荷 `NodePayload`

把「文件 + 中间产物」合并成一个通用载荷，让异构 bundle 链也能走同一条执行器：

```python
# core/types.py 新增
@dataclass(frozen=True)
class NodePayload:
    """统一节点载荷：输入文件（按角色寻址）+ 节点间中间产物。"""
    files: EvaluationFiles                      # 已有类型：roles: dict[str, str]
    artifacts: dict[str, Any] = field(default_factory=dict)

    def artifact(self, key: str, default: Any = None) -> Any:
        return self.artifacts.get(key, default)

    def with_artifact(self, key: str, value: Any) -> "NodePayload":
        artifacts = dict(self.artifacts)
        artifacts[key] = value
        return replace(self, artifacts=artifacts)
```

- `files`：复用已有的 `EvaluationFiles`（`roles={"reference_jsonl": ..., "sample_output": ...}`），
  覆盖文本 / 音频 / jsonl 等一切「文件角色」。
- `artifacts`：承载节点间中间产物（`validated_bundle`、`normalized_bundle`、
  transcripts、embeddings…），以约定的 string key 传递。

### 3.2 统一节点签名

```python
NodeCallable = Callable[[NodePayload], tuple[NodePayload, PipelineNodeResult]]
NodeFactory  = Callable[..., NodeCallable]
```

每个节点（内置或外部）都通过 `build(**config)` 返回一个 `NodePayload → NodePayload`
的函数。节点内部从 `payload.artifact(...)` 取上游产物，用 `with_artifact(...)`
写下游产物，从 `payload.files` 读原始输入。

### 3.3 统一执行器

`run_pipeline` 本身已是鸭子类型（`current, result = node(current)`），无需改动：

```python
# core/pipeline.py（不变，仅注解放宽）
def run_pipeline(spec, payload) -> tuple[Any, tuple[PipelineNodeResult, ...]]:
    current = payload
    trace: list[PipelineNodeResult] = []
    for node in spec.nodes:
        current, result = node(current)
        trace.append(result)
    return current, tuple(trace)
```

`PipelineSpec.nodes` 的类型注解从 `KeyTextFiles` 放宽为 `Any`（或引入泛型别名），
使 `KeyTextFiles` 与 `NodePayload` 都能作为载荷传入。

### 3.4 统一 dispatch（registry）

所有 task 的「node_id → callable」统一走：

```python
node = get_registry().build(node_id, **config)   # 内置经适配层，外部读 build 工厂
```

- 内置节点：`node_registry` 的适配层把现有 `validate_vad_contract` 等包成
  `NodePayload → NodePayload`（见 §4.2），不改现有节点函数。
- 外部节点：`build(**config)` 直接返回 `NodePayload → NodePayload`。

### 3.5 兼容策略（已落地）

`KeyTextFiles` 是 ASR 外部节点的既有契约（`examples/node_plugin_lowercase` 已按
它写）。统一方案分两期，避免破坏性变更：

- **VAD 试点期**：新增 `NodePayload` + `EvaluationFiles` 路径，VAD 迁移；
  ASR 保持 `KeyTextFiles` 不动，`run_pipeline` 鸭子类型同时支持两者。
- **ASR 收敛期（已完成）**：把 ASR 也迁到 `NodePayload`——`KeyTextFiles` 降级为
  `NodePayload` 的便捷别名（继承 `NodePayload`，`files` 携带 `roles={"ref","hyp"}`，
  保留 `ref_file`/`hyp_file` 只读属性），旧插件经此别名层继续可用（见 §9.4）。

## 4. VAD 试点迁移方案

### 4.1 三步改造

| 步 | 改动 | 文件 |
|---|---|---|
| ① 节点适配 | 给 3 个 VAD 内置节点各补 `build()` 工厂（`NodePayload` 签名） | 各节点 `node.py`（或集中适配层） |
| ② executor 改造 | `evaluate_vad_files` 按 `route["nodes"]` + `registry.build` 动态装配 | `tasks/vad/pipeline.py` |
| ③ scripts 打通 | `scripts/vad.py::run` 把 `route["nodes"]` 传给 executor | `scripts/vad.py` |

### 4.2 节点适配（`build` 工厂草案）

三个内置节点保持现有函数不动，各加一个 `build()` 工厂（放进 `NodeRegistration`）：

```python
# nodes/validation/vad_contract（build 工厂草案）
def build(*, required_prediction_fields=(), **config):
    def node(payload: NodePayload):
        payload.files.require("reference_jsonl", "sample_output")
        validated, result = validate_vad_contract(
            payload.files.roles["reference_jsonl"],
            payload.files.roles["sample_output"],
            required_prediction_fields=required_prediction_fields,
        )
        return payload.with_artifact("validated_bundle", validated), result
    return node
```

```python
# nodes/normalization/vad_timebase（build 工厂草案）
def build(*, frame_shift_sec=0.01, profile="strict", collar_sec=0.0,
          boundary_exclusion_sec=0.0, **config):
    def node(payload: NodePayload):
        normalized, result = normalize_vad_timebase(
            payload.artifact("validated_bundle"),
            frame_shift_sec=frame_shift_sec, profile=profile,
            collar_sec=collar_sec, boundary_exclusion_sec=boundary_exclusion_sec,
        )
        return payload.with_artifact("normalized_bundle", normalized), result
    return node
```

```python
# nodes/scoring/vad_detection_duration（build 工厂草案）
def build(*, **config):
    def node(payload: NodePayload):
        result = score_vad_detection_duration(payload.artifact("normalized_bundle"))
        return payload, result
    return node
```

`vad_auc_roc` 同理。这样三个节点统一成 `NodePayload → NodePayload`，且
`artifacts` 的 key 约定为：`validated_bundle`、`normalized_bundle`（写入下游
节点的 MANIFEST 说明，供外部节点互操作）。

### 4.3 executor 改造草案

`evaluate_vad_files` 从硬编码改为按 `nodes` 动态装配：

```python
def evaluate_vad_files(*, reference_jsonl, sample_output, metric="f1",
                       nodes: tuple[str, ...] = _DEFAULT_NODES, **config):
    payload = NodePayload(files=EvaluationFiles(roles={
        "reference_jsonl": str(reference_jsonl),
        "sample_output": str(sample_output),
    }))
    registry = get_registry()
    spec = PipelineSpec(
        pipeline_id=..., task="VAD", language="n/a", metric=canonical_metric(metric),
        nodes=tuple(registry.build(node_id, **config) for node_id in nodes),
    )
    _, trace = run_pipeline(spec, payload)
    # 从 trace[-1].details 取 score（与 ASR 一致）
    ...
```

要点：

- `route["nodes"]` 成为唯一真相，`pipeline_id` 由 `route["nodes"]` + 版本号生成
  （复用 `build_atomic_pipeline_id`），不再由 `_identity_components` 硬编码。
- 内置节点 `registry.build(node_id, **config)` 走 §4.2 的适配层；外部节点走其
  自身 `build`。二者签名统一，executor 无感知。

### 4.4 scripts 打通草案

`scripts/vad.py::run` 把 route 的 nodes 与 config 透传给 executor：

```python
report = call_route_executor(
    route,
    metric=normalized_metric,
    nodes=tuple(route.get("computation_nodes") or route["nodes"]),
    frame_shift_sec=float(route.get("frame_shift_sec", 0.01)),
    profile=str(route.get("profile", "strict")),
    collar_sec=float(route.get("collar_sec", 0.0)),
    boundary_exclusion_sec=float(route.get("boundary_exclusion_sec", 0.0)),
    **kwargs,
)
```

### 4.5 route 注入（复用方案 A，零改动）

外部 VAD 节点配一条注入 route 即可运行，无需改仓库 `routes.yaml`：

```python
# 外部包 routes.py（ROUTES 片段）
ROUTES = [{
    "metric": "f1",
    "pipeline_id": "vad.any.f1.my_vad_contract_v1.vad_timebase_strict_v1.vad_detection_duration_v1",
    "nodes": ["validation/my_vad_contract", "normalization/vad_timebase", "scoring/vad_detection_duration"],
    "input_contract": "vad_jsonl",
    "executor": "sure_eval.evaluation.tasks.vad.pipeline.evaluate_vad_files",
    # config 字段随 route 透传
    "frame_shift_sec": 0.01, "profile": "strict",
}]
```

## 5. 实现草案（关键接口汇总）

```python
# core/types.py 新增
@dataclass(frozen=True)
class NodePayload:
    files: EvaluationFiles
    artifacts: dict[str, Any] = field(default_factory=dict)
    def artifact(self, key, default=None): return self.artifacts.get(key, default)
    def with_artifact(self, key, value):
        return replace(self, artifacts={**self.artifacts, key: value})
```

```python
# core/pipeline.py：注解放宽（行为不变）
def run_pipeline(spec, payload) -> tuple[Any, tuple[PipelineNodeResult, ...]]: ...
```

```python
# node_registry：内置节点 build 返回 NodePayload 适配（沿用现有内建适配层模式）
# 外部节点 build 直接返回 NodePayload 工厂
```

## 6. 风险与兼容

- **`KeyTextFiles` 与 `NodePayload` 并存**：`run_pipeline` 鸭子类型，两套载荷各自
  运行，互不影响；ASR 回归零变化。
- **artifacts 弱类型**：key 是约定字符串（`validated_bundle` / `normalized_bundle`）。
  缓解：每个节点的 MANIFEST 声明 `consumes`/`produces` artifact key，`NodeRegistry`
  可加运行时校验（本期可选）。
- **内置节点 build 工厂的位置**：优先放各节点 `node.py`（自描述），与外部节点
  形态一致；避免在 registry 里堆 task 专属适配。
- **`pipeline_id` 身份稳定**：迁移后 `pipeline_id` 必须与现状逐字节一致（由
  `route["nodes"]` + `build_atomic_pipeline_id` 生成，`computation_nodes` 字段
  已在 routes.yaml 里声明）。

## 7. 验收标准

1. **回归零变化**：VAD 5 条 route 的 `metric routes` / `describe` / `run` 结果与
   `pipeline_id` 与迁移前完全一致（`env check --all` 的 VAD 节点状态不变）。
2. **内置节点动态装配**：`evaluate_vad_files` 不再硬编码三节点，改由
   `route["nodes"]` + `registry.build` 装配，5 条内置 route 全部跑通。
3. **外部节点免改源码**：写一个外部 VAD validation 节点（如「跳过空行」或
   「裁剪超长片段」），`pip install` 后经注入 route 运行，全程不碰仓库源码。
4. **单测**：`NodePayload` 的 artifact 读写、VAD 三节点 `build` 工厂、executor
   动态装配各补单测。

## 8. 推广路径

VAD 验证通过后，按「先契约简单、后契约复杂」的顺序推广：

1. VAD（jsonl segments，本草案试点）✅ 已完成
2. SA-ASR（key_text + 转写，混合契约）✅ 已完成
3. SE / TSE（audio 打分）✅ 已完成
4. TTS / VC（frontend + transcription + normalization + scoring 复合链）✅ 已完成
5. 最后统一 ASR（把 `KeyTextFiles` 收敛到 `NodePayload`）✅ 已完成
6. 收尾六 executor（classification / kws / s2tt / sd / slu / sv 接 registry fallback，
   消除「route 能注册但 executor 不 dispatch」）✅ 已完成（见 §9.5）
7. describe 阶段 `pipeline_id` 版本链校验（节点升级即报错）✅ 已完成（见 §9.6）

每一步都遵循「回归零变化 + 外部节点免改源码」两条验收，逐步扩大统一执行模型的
覆盖范围。

## 9. 验收记录（VAD 试点已落地）

实施内容：

- `core/types.py`：新增 `NodePayload`（`files: EvaluationFiles` + `artifacts`，
  含 `artifact`/`with_artifact`）；`PipelineSpec.nodes` 注解放宽为通用载荷。
- `core/node_protocol.py`：`NodeRegistration` 增加 `consumes`/`produces`（从
  MANIFEST 读取），外部节点可在 MANIFEST 声明 artifacts 契约。
- `node_registry.py`：内置节点从 `manifest.implementation` 加载 `build` 工厂；
  `build()` 按 `consumes`/`produces` 包装运行时校验（对非 `NodePayload` 载荷跳过）。
- VAD 四内置节点补 `build()` 工厂 + manifest 声明 `consumes`/`produces`。
- `tasks/vad/pipeline.py`：`evaluate_vad_files` 改由 `route["nodes"]` +
  `registry.build` 动态装配；`pipeline_id` 从节点链生成，与 routes.yaml 逐字节一致。
- `scripts/vad.py`：`run` 透传 `nodes` 与 config。

验收结果：

1. **回归零变化**：内置 5 条 VAD route 的 `pipeline_id`、`describe`、`run`
   （score=1.0）与迁移前一致。
2. **内置动态装配**：`evaluate_vad_files` 不再硬编码三节点。
3. **外部节点免改源码**：`examples/node_plugin_vad_validation`（外部
   `validation/sample_vad_contract` + 注入 route）`pip install` 后经 registry
   动态装配运行，score=1.0，`pipeline_id` 由节点链正确生成，未改任何框架源码。
4. **单测**：新增 `tests/test_vad_unified_dispatch.py`（6 例）覆盖
   `NodePayload` 读写、`consumes` 运行时校验、内置 build 工厂、动态装配与
   `pipeline_id` 稳定。

已知边界（沿用评审决定）：ASR 本期不动，仍走 `KeyTextFiles` 契约；二者经
`run_pipeline` 的鸭子类型并存。

### 9.1 SA-ASR 推广（已完成）

SA-ASR 的 normalization 是 `KeyTextFiles -> KeyTextFiles`（与 ASR 同构），scoring
是 meeteval 文件（经 conversion 中转），属「key_text + 转写」混合契约。落地内容：

- `normalization/gstar_norm`、`normalization/whisper_norm`、`scoring/meeteval`
  三内置节点补 `build()` 工厂（`KeyTextFiles` 契约）。
- `tasks/sa_asr/pipeline.py`：`evaluate_sa_asr_files` 改由 `route["nodes"]` +
  `registry.build` 动态装配；`_resolve_normalization_node` 对未知 node_id 透传
  （外部节点免语言约束）；`_normalization_component` 用 profile 映射表生成
  component（外部节点默认无 profile）。
- `scripts/sa_asr.py`：`run` 透传 `nodes`。

验收结果：

1. **回归零变化**：内置 2 条 SA-ASR route 的 `pipeline_id` 与迁移前逐字节一致
   （`sa_asr.zh.cpwer.conversion_sa_asr_cpwer_v1.gstar_norm_v1.meeteval_v1` 与
   `sa_asr.en.cpwer.conversion_sa_asr_cpwer_v1.whisper_norm_english_v1.meeteval_v1`）。
2. **外部节点免改源码**：`examples/node_plugin_sa_asr_norm`（外部
   `normalization/sa_asr_sample_norm` + 注入 route）`pip install` 后经 registry
   动态装配，`pipeline_id` 正确生成（`sa_asr_sample_norm_v1`，无 profile），
   trace 首位即外部节点，未改任何框架源码。
3. **单测**：新增 `tests/test_sa_asr_unified_dispatch.py`（7 例）覆盖 build 工厂、
   `_resolve_normalization_node`（语言默认/外部透传/内置语言约束）、component
   profile 映射、executor 动态装配与 node_id 传递。

说明：本机未安装 meeteval（node-local env 未 setup），SA-ASR 的 scoring 端到端
`metric run` 会因 `import meeteval` 失败——这是 pre-existing 环境限制，与本次迁移
无关；executor 的 normalization 动态装配与 scoring 调用链已通过 mock meeteval 的
单测与脚本验证。

### 9.2 SE / TSE 推广（已完成）

SE / TSE 的 scoring 节点是 provider-backed 的「audio 打分」契约：
`list[Row] + provider -> PipelineNodeResult`（Row 为音频样本元组）。它们是第一类
「非 KeyTextFiles / NodePayload」的契约，验证了统一 dispatch 在第三种载荷形态下
同样成立。落地内容：

- 9 个内置 scoring 节点（`si_sdr`/`stoi`/`pesq`/`dnsmos`/`wv_mos`/`utmos`/
  `wavlm_large_sim`/`ecapa_tdnn_sim`/`eres2net_sim`）补 `build()` 工厂
  （`node(rows) -> PipelineNodeResult`，provider 可由外部注入或由
  `build_default_provider` 构造）。
- `_audio_quality_dispatch` 三个函数（full-reference / MOS / speaker）在保留内置
  if-elif 的同时，对未知 selector 走 `find_by_selector("scoring", family, value)` +
  `registry.build` fallback。
- SE executor：`_metric_family`（内置集合 + registry selector）判定
  full-reference / MOS；`unsupported` 检查、dispatch 循环、`_default_reference_provider`
  （外部节点返回 None，由节点 build 工厂自构 provider）、`_node_id_for_metric`
  均加入 registry fallback。
- TSE executor：`_scoring_family`（speaker 用去 `sim/` 前缀的 backend 名匹配
  selector）判定 speaker / MOS；MOS dispatch 集合、`_is_speaker_metric`、
  `unsupported` 检查加入 registry fallback。

验收结果：

1. **回归零变化**：SE 6 条、TSE zh/en 各 8 条 route 的 `pipeline_id` 与迁移前
   逐字节一致；全量回归 376 passed + 51 skipped。
2. **外部节点免改源码**：`examples/node_plugin_se_scoring`（外部
   `scoring/sample_se_metric` + 注入 route）`pip install` 后经 registry 动态装配，
   `evaluate_se_samples(metrics=["my-se-metric"])` 正确产出 `pipeline_id`
   `se.any.my_se_metric.sample_se_metric_v1` 且 score=0.5，未改任何框架源码。
3. **单测**：新增 `tests/test_se_tse_unified_dispatch.py`（8 例）覆盖 9 节点 build
   工厂、dispatch 三族 fallback、`_metric_family`/`_scoring_family`/`_node_id_for_metric`
   外部回退、SE executor 端到端外部 full-reference 指标。

边界：SE/TSE 的语义链（TSE cer/wer 的 transcription→normalization→scoring 复合链）
仍走 `audio_semantic` 独立 dispatch，不在本次 scoring 解耦范围内；TTS/VC 推广时
一并处理。本机未装 `soundfile`，SE/TSE 的 `metric run`（真实音频打分）无法端到端
跑通——pre-existing 环境限制；dispatch 链已通过 provider 契约单测与脚本验证。

### 9.3 TTS / VC 推广（已完成）

TTS / VC 的语义链（transcription → normalization → scoring，zh 链路还含
frontend）此前经 `audio_semantic` 硬编码 dispatch，speaker/MOS 经
`_audio_quality_dispatch`。本次把两处都收敛到 registry fallback：

- 4 个内置 transcription 节点（`paraformer_zh`/`whisper_large_v3`/
  `qwen3_asr_1_7b`/`cohere_transcribe_arabic_07_2026`）补 `build()` 工厂
  （`node(audio_path, *, language, role) -> (transcript, trace)`；paraformer
  自带 FunASR loader frontend）。
- `audio_semantic.transcribe_audio` 与 `_transcription_components` 的 if-elif
  改为 `registry.build(selected_node)`，外部 transcription 节点经
  `route["nodes"]` 的 `transcription/` node_id 直接透传（scripts 已有的
  `_semantic_transcription_node` 自动推导）。
- TTS / VC executor：`_scoring_family`（与 SE/TSE 同构）判定 speaker/MOS，
  `_is_speaker_metric`、MOS dispatch 集合、`unsupported` 检查加入 registry
  fallback。

验收结果：

1. **回归零变化**：TTS zh/en 各 8 条、ar 7 条，VC zh/en 各 8 条、ar 6 条
   route 的 `pipeline_id` 与迁移前逐字节一致；核心 + TTS/VC + SE/TSE 回归
   73 passed。
2. **外部节点免改源码**：`examples/node_plugin_tts_transcription`（外部
   `transcription/sample_tts_asr` + 注入 TTS route）`pip install` 后经 registry
   动态装配，`transcribe_audio` 正确 dispatch 到外部节点（transcript + trace），
   未改任何框架源码。
3. **单测**：新增 `tests/test_tts_vc_unified_dispatch.py`（5 例）覆盖 transcription
   build 工厂、`transcribe_audio` 内置/外部 dispatch、`_transcription_components`
   内置/外部、TTS/VC `_scoring_family` 外部回退。

边界：TTS/VC 语义链的 scoring（wenet_wer/wenet_cer）与 normalization 仍经
`evaluate_asr_files`（ASR executor，本身已 registry 化）；本机未装 sctk / ASR 模型，
完整语义链 `metric run` 无法端到端跑通——pre-existing 环境限制。至此仅剩 ASR
自身载荷收敛（§8 第 5 步）。

### 9.4 ASR 载荷收敛（已完成，推广收尾）

把 `KeyTextFiles` 收敛到 `NodePayload`，让 ASR 与其余四类 task 共享同一载荷基类：

- `core/types.py`：`KeyTextFiles` 改为继承 `NodePayload`——`files` 携带
  `roles={"ref","hyp"}`，`artifacts` 默认为空；保留 `ref_file`/`hyp_file` 只读
  property 与 `from_payload()` 反向构造；`NodePayload.with_artifact` 改用
  `object.__new__` 构造以保留子类（`KeyTextFiles`）类型。
- 效果：`run_pipeline` 的 ASR 载荷（`KeyTextFiles` 实例）**类型上即**
  `NodePayload`，`isinstance(payload, NodePayload)` 成立；旧 KeyTextFiles 插件
  （`examples/node_plugin_lowercase`）经 `ref_file`/`hyp_file` 兼容层继续可用。

验收结果：

1. **回归零变化**：全量回归 387 passed + 51 skipped；ASR 19 条 route 的
   `pipeline_id` 与 `describe`/`run` 行为不变。
2. **旧插件兼容**：`examples/node_plugin_lowercase`（按旧 `KeyTextFiles` 契约编写）
   安装后仍可 resolve + dispatch，`KeyTextFiles(ref, hyp)` 现为 `NodePayload`
   实例，`.ref_file`/`.hyp_file` 只读属性照常。
3. **单测**：新增 `tests/test_asr_payload_convergence.py`（6 例）覆盖
   `KeyTextFiles` 是 `NodePayload` 子类、`roles={"ref","hyp"}`、legacy 属性、
   `with_artifact` 保留子类类型、`from_payload`、ASR 载荷流经 `run_pipeline`。

至此 §8 五步推广全部落地：VAD → SA-ASR → SE/TSE → TTS/VC → ASR 载荷收敛。

### 9.5 收尾：六 executor 外部节点 dispatch 收敛（已完成）

§9.4 之后，`classification` / `kws` / `s2tt` / `sd` / `slu` / `sv` 六个 task 的
executor 仍为模块内硬编码 import（无 registry fallback），与「route 已能注册
外部节点」形成静默不一致。本节按 ASR 的「内置 if-elif + 末尾
`find_by_selector(...)` + `registry.build(...)`」模式补齐，使六个 executor 全部
支持外部节点动态 dispatch：

| task | executor 文件 | 收敛后的 dispatch 点 |
|---|---|---|
| classification | `tasks/classification/pipeline.py` | `_scoring_callable(scorer)`（scoring/classify） |
| kws | `tasks/kws/pipeline.py` | `_scoring_callable(scorer)`（scoring/wekws_det，含 conversion 前置） |
| s2tt | `tasks/s2tt/pipeline.py` | `_evaluate_external_scorer` + `_external_scoring_callable`（sacrebleu / xcomet_xl / bleurt_20 外兜底） |
| sd | `tasks/sd/pipeline.py` | `_scoring_callable(scorer)`（scoring/meeteval） |
| slu | `tasks/slu/pipeline.py` | `_normalization_callable` + `_scoring_callable`（prompt_norm + classify 双节点链） |
| sv | `tasks/sv/pipeline.py` | `_metric_scoring_callable(metric, scorer)`（cosine_trial 前置 + det_eer / min_dcf 外兜底） |

每个 task 的 scripts `run` 新增 `_executor_selectors_from_route`（内置节点 →
硬编码 selector 值，外部节点 → 读 registration.selectors），并把 selector 透传给
executor；每组补「外部节点免改源码」单测（`tests/test_*_unified_dispatch.py`，
共 23 例）。内置路径的 `pipeline_id` 零变化（回归通过）。

本次收尾另清除了 TSE/TTS/VC 中定义未调用的 `_node_name_for_metric` 死代码，
并补 `tests/test_asr_unified_dispatch.py`（8 例）覆盖 ASR 外部 normalizer /
scorer 的 selector 规范化、节点工厂 dispatch、组件身份与端到端组合链。

### 9.6 describe 阶段 pipeline_id 版本链校验（已完成）

`pipeline_id` 的节点版本链（`..._vN`）必须与各 computation node 的 manifest
实际版本一致。executor 生成 `report.pipeline_id` 时版本从 manifest 动态读取，
run 收尾的 `assert_report_matches_description` 会 fail fast；但 describe 阶段此前
直接沿用 route 静态声明的 `pipeline_id`，可能产出「`pipeline_id` 写 `_v1` 而
`nodes[].version` 已是 `v2`」的矛盾 JSON，把 mismatch 推迟到 run 才暴露。

`build_pipeline_spec`（describe）现追加 `_validate_pipeline_id_versions`：按
atomic / bundle（`__` 连接）解析 pipeline_id 里的节点版本链（每个 component 末尾的
`_vN`，兼容节点名本身含 `_v3` 的情形），先按 component 名称对应 computation
node，再逐节点与 manifest 版本比对；bundle 里跨 metric 复用且在
`computation_node_ids` 中去重的共享节点会按首次出现顺序校验。不一致即报
`pipeline_id version mismatch` 并点名差异。
节点版本升级后，describe 阶段立即发现 route 声明过期，无需等到 run。覆盖测试见
`tests/test_pipeline_id_version_check.py`（9 例）。
