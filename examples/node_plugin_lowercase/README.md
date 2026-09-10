# 示例：外部 normalization 节点（lowercase 归一化）

这是一个最小可运行的 SURE 节点插件，演示「单文件 `node.py` + entry point 注册」
的外部节点形态。

## 安装

```bash
pip install -e examples/node_plugin_lowercase
```

安装后，框架通过 `importlib.metadata.entry_points(group="sure_eval.nodes")` 自动发现：

```bash
sure-eval node list --json | grep lowercase_norm
```

## 自动进入候选

本节点 `MANIFEST.profiles.default.default_for` 声明了 `ASR/en/wer`，因此会出现在
`metric describe` 的 normalization slot choices 中：

```bash
sure-eval metric describe asr --language en --metric wer --json
# normalization slot 的 choices 会包含 "normalization/lowercase_norm"
```

## 经 routes.yaml 引用运行

在 `tasks/asr/routes.yaml` 里新增一条 route 引用本节点：

```yaml
  -
    language: en
    metric: wer
    pipeline_id: asr.en.wer.lowercase_norm_v1.wenet_wer_v1
    nodes:
      - normalization/lowercase_norm
      - scoring/wenet_wer
    input_contract: scoring/wenet_wer
    executor: sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files
```

然后 describe + run：

```bash
sure-eval metric describe asr --pipeline-id asr.en.wer.lowercase_norm_v1.wenet_wer_v1 \
  --output lowercase_pipeline.json
sure-eval metric run --pipeline lowercase_pipeline.json \
  --ref-file examples/readme/asr_en_ref.txt \
  --hyp-file examples/readme/asr_en_hyp.txt \
  --output-dir out/lowercase
```

## 节点约定

| 模块属性 | 说明 |
|---|---|
| `NODE_ID` / `STAGE` / `VERSION` | 节点身份 |
| `MANIFEST` | dict（等价 manifest.yaml） |
| `NODE_ENV` | 环境声明（本示例无依赖，为 `None`） |
| `SELECTORS` | 让任务 dispatch 能按 selector 字符串找到本节点 |
| `build(**config)` | 工厂，返回 `Callable[[KeyTextFiles], tuple[KeyTextFiles, PipelineNodeResult]]` |
