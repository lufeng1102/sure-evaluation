# 示例：外部 VAD validation 节点（统一 NodePayload 契约）

演示「统一节点载荷」的 VAD validation 节点：`build` 返回
`NodePayload → NodePayload` 工厂，MANIFEST 声明 `produces: ["validated_bundle"]`。
本示例复用内置 `validate_vad_contract`，仅替换链首节点以展示外部 validation 节点
如何经 registry 动态接入。

## 安装

```bash
pip install -e examples/node_plugin_vad_validation
```

## 验证发现

```bash
sure-eval node list --json | grep sample_vad_contract
sure-eval metric routes vad --json
# 应多出：vad.any.f1.sample_vad_contract_v1.vad_timebase_strict_v1.vad_detection_duration_v1
```

## 运行

```bash
sure-eval metric describe vad \
  --pipeline-id vad.any.f1.sample_vad_contract_v1.vad_timebase_strict_v1.vad_detection_duration_v1 \
  --output vad_pipeline.json

sure-eval metric run --pipeline vad_pipeline.json \
  --reference-jsonl /path/to/reference.jsonl \
  --sample-output /path/to/sample_output.jsonl \
  --output-dir out/vad
```

## 节点约定

| 模块属性 | 说明 |
|---|---|
| `NODE_ID` / `STAGE` / `VERSION` | 节点身份（`validation/sample_vad_contract`） |
| `MANIFEST` | dict（等价 manifest.yaml；含 `consumes`/`produces` artifacts 声明） |
| `NODE_ENV` | 环境声明（本示例无依赖，为 `None`） |
| `SELECTORS` | 让任务 dispatch 能按 selector 字符串找到本节点 |
| `build(**config)` | 工厂，返回 `Callable[[NodePayload], tuple[NodePayload, PipelineNodeResult]]` |

## 关键点

- VAD 节点契约是 `NodePayload`（`files` + `artifacts`），节点间通过
  `payload.artifact(...)` / `payload.with_artifact(...)` 传递中间产物
  （`validated_bundle` / `normalized_bundle`）。
- `MANIFEST.produces` 声明产物 key，`NodeRegistry.build` 据此做运行时校验
  （对非 `NodePayload` 载荷自动跳过）。
- `routes.py` 经 `sure_eval.routes` entry point 注入一条 route，`nodes` 首项替换为
  本节点，全程无需改仓库源码。
