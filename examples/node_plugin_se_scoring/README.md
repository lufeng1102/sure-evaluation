# 示例：外部 SE scoring 节点（provider-backed audio 打分契约）

演示 SE 的 full-reference scoring 节点如何经 registry 动态装配。SE/TSE 的 scoring
节点是 provider-backed 的「audio 打分」契约：`list[Row] + provider →
PipelineNodeResult`（Row 为 `(sample_id, enhanced, reference)` 元组）。

本示例**不加载真实模型**，直接返回常数分数，仅演示 dispatch 与身份生成，因此
无需模型环境即可运行。

## 安装

```bash
pip install -e examples/node_plugin_se_scoring
```

## 验证发现

```bash
sure-eval node list --json | grep sample_se_metric
sure-eval metric routes se --json
# 应多出：se.any.my_se_metric.sample_se_metric_v1
```

## 运行

`--samples-jsonl` 每行为一个 SE 样本（`enhanced_audio` + `reference_audio`）：

```json
{"sample_id": "s1", "enhanced_audio": "/path/to/enhanced.wav", "reference_audio": "/path/to/ref.wav"}
```

```bash
sure-eval metric describe se \
  --pipeline-id se.any.my_se_metric.sample_se_metric_v1 \
  --output se_pipeline.json

sure-eval metric run --pipeline se_pipeline.json \
  --samples-jsonl /path/to/se_samples.jsonl \
  --output-dir out/se
```

## 节点约定

| 模块属性 | 说明 |
|---|---|
| `NODE_ID` / `STAGE` / `VERSION` | 节点身份（`scoring/sample_se_metric`） |
| `MANIFEST` | dict（等价 manifest.yaml） |
| `NODE_ENV` | 环境声明（本示例无依赖，为 `None`） |
| `SELECTORS` | `{"full_reference": "my-se-metric"}`，SE executor 按 family 判定并 dispatch |
| `build(**config)` | 工厂，返回 `Callable[[list[Row]], PipelineNodeResult]` |

## 关键点

- SE executor 的 `_metric_family` 通过 `find_by_selector("scoring",
  "full_reference", "my-se-metric")` 把外部 metric 归类为 full-reference，随后
  `_audio_quality_dispatch` 的 registry fallback 完成 `registry.build`。
- scoring 节点的结果放在 `PipelineNodeResult.details["result"]`，其中 `score`
  必须是数值、`per_sample` 与输入 rows 一一对应。
- `routes.py` 里 `input_contract` 复用内置 `scoring/si_sdr`（enhanced_audio +
  reference_audio），无需新增 manifest 契约。
