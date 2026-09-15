# 示例：node + pipeline 混合插件（node-and-route）

这是一个「同时新增 node 与 pipeline」的项目级插件示例，对应
`effective_kind = "node-and-route"`。包同时提供 `node.py`（外部
normalization 节点）与 `routes.py`（引用该节点的 route）。

它演示的是 `sure-eval plugin add` 的项目级插件通道：一次 `plugin add`
即可完成节点与 pipeline 的注册，`describe`/`run` 的 pipeline JSON 与最终
报告 trace 会保留该外部节点。同一目录也可通过 `pip install -e` 的 entry point
通道正式分发。

## 目录结构

```text
node_pipeline_plugin_wer/
├── pyproject.toml
├── sure_eval_plugin.yaml
├── README.md
└── src/sure_eval_node_pipeline_plugin_wer/
    ├── __init__.py
    ├── node.py             # NODE_ID / STAGE / VERSION / MANIFEST / SELECTORS / build
    └── routes.py           # ROUTES 引用上面的外部节点 + 内置打分节点
```

## 使用

在某个项目根目录（或显式 `--project-dir`）添加插件：

```bash
sure-eval plugin add examples/node_pipeline_plugin_wer
sure-eval plugin list --json
# effective_kind 应为 "node-and-route"，node_ids 含
# normalization/example_lowercase，pipeline_ids 含
# asr.en.wer.example_lowercase_v1.wenet_wer_v1
```

确认节点与 route 均已发现：

```bash
sure-eval node list --json | grep example_lowercase
sure-eval metric routes asr --language en --metric wer --json
# 应能看到 asr.en.wer.example_lowercase_v1.wenet_wer_v1
```

describe + run：

```bash
sure-eval metric describe asr \
  --pipeline-id asr.en.wer.example_lowercase_v1.wenet_wer_v1 \
  --output pipeline_wer.json

sure-eval metric run --pipeline pipeline_wer.json \
  --ref-file examples/readme/asr_en_ref.txt \
  --hyp-file examples/readme/asr_en_hyp.txt \
  --output-dir out/node_pipeline_wer
```

运行后 `out/node_pipeline_wer/report.json` 的 `pipeline_trace` 会同时包含
`normalization/example_lowercase`（外部节点）与 `scoring/wenet_wer`
（内置节点）。

## 关键点

- `SELECTORS = {"normalizer": "example_lowercase"}` 的值必须与 node_id 的
  name 部分（`example_lowercase`）一致，ASR executor 才能按
  `normalizer="example_lowercase"` 动态 dispatch 到本节点。
- `pipeline_id` 的节点版本链（`example_lowercase_v1.wenet_wer_v1`）必须与
  route `nodes` 的版本一致，describe 阶段会校验。
- `kind: "node-and-route"` 是可选断言字段，若与目录内容不一致，`plugin add`
  会硬报错。
- 移除插件用 `sure-eval plugin remove node-pipeline-plugin-wer`。
