# 示例：外部 scoring 节点（exact-match 打分）

这是一个最小可运行的 scoring 节点示例，与
[`../node_plugin_lowercase`](../node_plugin_lowercase) 的 normalization 节点
组成一对可组合的扩展示例。

## 安装

```bash
pip install -e examples/node_plugin_lowercase
pip install -e examples/node_plugin_exact_match
```

## 与 lowercase 节点组合运行

```bash
sure-eval node list --json | grep -E "lowercase_norm|exact_match"
```

在 `tasks/asr/routes.yaml` 里引用两个外部节点：

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

describe + run 后，报告会先 lowercase 归一化，再用 exact-match 打分。

## 关键点

- scoring 节点的结果放在 `PipelineNodeResult.details["result"]`，其中
  `score` 必须是数值（框架用 `float(result["score"])` 读取）。
- `SELECTORS = {"scorer": "exact_match"}` 让 ASR executor 能按
  `scorer="exact_match"` 动态 dispatch 到本节点。
