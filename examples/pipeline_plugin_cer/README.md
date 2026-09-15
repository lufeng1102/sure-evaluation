# 示例：纯 pipeline 插件（route-only）

这是一个「仅新增 pipeline」的项目级插件示例，对应
`effective_kind = "route"`。包里**只有** `routes.py`，不提供 `node.py`；
route 的 `nodes` 全部引用内置节点（`normalization/aispeech_norm` +
`scoring/wenet_cer`）。

它演示的是 `sure-eval plugin add` 的项目级插件通道，与
[`../node_plugin_*`](../) 里通过 `pip install` + entry point 的正式分发通道
互补：同一目录既可由 `plugin add` 固定到项目，也可由 `pip install` 正式分发。

## 目录结构

```text
pipeline_plugin_cer/
├── pyproject.toml
├── sure_eval_plugin.yaml
├── README.md
└── src/sure_eval_pipeline_plugin_cer/
    ├── __init__.py
    └── routes.py           # ROUTES 列表，引用内置节点
```

## 使用

在某个项目根目录（或显式 `--project-dir`）添加插件：

```bash
sure-eval plugin add examples/pipeline_plugin_cer
sure-eval plugin list --json
# effective_kind 应为 "route"，nodes 为空，pipeline_ids 含
# asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1
```

确认 route 已注入：

```bash
sure-eval metric routes asr --language en --metric cer --json
# 应能看到 asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1
```

describe + run：

```bash
sure-eval metric describe asr \
  --pipeline-id asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1 \
  --output pipeline_cer.json

sure-eval metric run --pipeline pipeline_cer.json \
  --ref-file examples/readme/asr_en_ref.txt \
  --hyp-file examples/readme/asr_en_hyp.txt \
  --output-dir out/pipeline_cer
```

## 关键点

- `nodes: []` 只表示「本插件不提供 node」，不代表 route 的 `nodes` 为空；
  route 仍可引用内置节点或其他插件提供的 node。
- `kind: "route"` 是可选断言字段，若与目录内容（只有 `routes.py`）不一致，
  `plugin add` 会硬报错。
- 移除插件用 `sure-eval plugin remove pipeline-plugin-cer`，它只删项目声明，
  不删本目录。
