# 示例：外部 SA-ASR normalization 节点（KeyTextFiles 契约）

演示 SA-ASR 的 normalization 节点如何经 registry 动态装配。SA-ASR 的
normalization 是 `KeyTextFiles → KeyTextFiles`（与 ASR 同构），scoring 由内置
`scoring/meeteval` 承担。本示例做小写归一化，仅作接入示范。

## 安装

```bash
pip install -e examples/node_plugin_sa_asr_norm
```

## 验证发现

```bash
sure-eval node list --json | grep sa_asr_sample_norm
sure-eval metric routes sa_asr --json
# 应多出：sa_asr.en.cpwer.conversion_sa_asr_cpwer_v1.sa_asr_sample_norm_v1.meeteval_v1
```

## 运行

```bash
sure-eval metric describe sa_asr \
  --pipeline-id sa_asr.en.cpwer.conversion_sa_asr_cpwer_v1.sa_asr_sample_norm_v1.meeteval_v1 \
  --output sa_asr_pipeline.json

# 先准备 meeteval scoring 环境（本机未装时）
sure-eval env setup --pipeline sa_asr_pipeline.json

sure-eval metric run --pipeline sa_asr_pipeline.json \
  --ref-file /path/to/ref.stm \
  --hyp-file /path/to/hyp.stm \
  --output-dir out/sa_asr
```

> 说明：SA-ASR 输入是 STM 文件（`session channel speaker start end transcript`），
> executor 会先做 STM↔TXT 转换再归一化、打分；scoring 依赖 meeteval 环境。

## 节点约定

| 模块属性 | 说明 |
|---|---|
| `NODE_ID` / `STAGE` / `VERSION` | 节点身份（`normalization/sa_asr_sample_norm`） |
| `MANIFEST` | dict（等价 manifest.yaml） |
| `NODE_ENV` | 环境声明（本示例无依赖，为 `None`） |
| `SELECTORS` | 让任务 dispatch 能按 selector 字符串找到本节点 |
| `build(**config)` | 工厂，返回 `Callable[[KeyTextFiles], tuple[KeyTextFiles, PipelineNodeResult]]` |

## 关键点

- SA-ASR executor 从 `route["nodes"]` 装配 normalization + scoring，对未知
  node_id 直接透传给 registry（外部节点免语言约束）。
- `routes.py` 里 `params.normalization_node` 显式指向本节点，`nodes` 首项也指向
  本节点，二者保持一致。
- 本示例复用 `KeyTextFiles` 契约（现为 `NodePayload` 便捷别名），
  `.ref_file` / `.hyp_file` 只读属性照常可用。
