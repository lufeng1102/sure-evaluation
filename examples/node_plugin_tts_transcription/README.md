# 示例：外部 TTS transcription 节点（语义复合链契约）

演示 TTS 语义链（transcription → normalization → scoring）的 transcription 节点
如何经 registry 动态装配。TTS/VC 的语义链经 `audio_semantic` 统一 dispatch，
transcription 节点由 `transcribe_audio` 通过 `registry.build` 装配，本示例即验证
「外部 transcription 节点免改框架源码」。

本示例**不加载真实 ASR 模型**，仅在传入 runner 时转调 runner，否则返回占位文本，
用于演示 dispatch 与身份生成。

## 安装

```bash
pip install -e examples/node_plugin_tts_transcription
```

## 验证发现

```bash
sure-eval node list --json | grep sample_tts_asr
sure-eval metric routes tts --language en --json
# 应多出：tts.en.wer.sample_tts_asr_v1.whisper_norm_english_v1.wenet_wer_v1
```

## 运行

`--samples-jsonl` 每行为一个 TTS 样本（`prediction_audio` + `reference_text`）：

```json
{"sample_id": "s1", "prediction_audio": "/path/to/synth.wav", "reference_text": "hello world", "language": "en"}
```

```bash
sure-eval metric describe tts \
  --pipeline-id tts.en.wer.sample_tts_asr_v1.whisper_norm_english_v1.wenet_wer_v1 \
  --output tts_pipeline.json

sure-eval metric run --pipeline tts_pipeline.json \
  --samples-jsonl /path/to/tts_samples.jsonl \
  --output-dir out/tts
```

> 说明：完整语义链的 scoring（wenet_wer）与 normalization 依赖 sctk / ASR 模型，
> 需先 `sure-eval env setup --pipeline tts_pipeline.json` 准备环境。

## 节点约定

| 模块属性 | 说明 |
|---|---|
| `NODE_ID` / `STAGE` / `VERSION` | 节点身份（`transcription/sample_tts_asr`） |
| `MANIFEST` | dict（等价 manifest.yaml） |
| `NODE_ENV` | 环境声明（本示例无依赖，为 `None`） |
| `SELECTORS` | 让任务 dispatch 能按 selector 字符串找到本节点 |
| `build(**config)` | 工厂，返回 `Callable[[audio_path], tuple[transcript, trace]]` |

## 关键点

- `audio_semantic.transcribe_audio` 对未知 transcription node_id 走
  `registry.build(selected_node)`；scripts 已有的 `_semantic_transcription_node`
  会从 `route["nodes"]` 自动推导 `transcription/` 开头的 node_id。
- transcription 节点的 `build` 返回 `node(audio_path, *, language, role) →
  (transcript, (PipelineNodeResult, ...))`；zh 链路可内嵌 frontend（本示例为单节点）。
- `routes.py` 里 `nodes` 首项替换为本节点，normalization/scoring 复用内置
  `whisper_norm` / `wenet_wer`。
