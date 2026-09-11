"""Example external TTS semantic transcription node (audio -> transcript).

安装（``pip install -e examples/node_plugin_tts_transcription``）后，本节点经
``sure_eval.nodes`` entry point 注册为 ``transcription/sample_tts_asr``，并经
``sure_eval.routes`` 注入一条 TTS 语义链 route。

TTS/VC 的语义链（transcription -> normalization -> scoring）经 ``audio_semantic``
统一 dispatch：transcription 节点由 ``transcribe_audio`` 通过 registry 动态装配，
本示例即验证「外部 transcription 节点免改框架源码」。

本示例不加载真实 ASR 模型，仅演示 dispatch 与身份生成。
"""

from sure_eval.evaluation.core.types import PipelineNodeResult

NODE_ID = "transcription/sample_tts_asr"
STAGE = "transcription"
VERSION = "v1"

MANIFEST = {
    "id": NODE_ID,
    "version": VERSION,
    "stage": STAGE,
    "language_sensitive": True,
    "input_schema": "audio_path",
    "output_schema": "transcript_text",
}

NODE_ENV = None

SELECTORS = {}


def build(*, runner=None, **config):
    def node(audio_path: str, *, language: str = "en", role: str = "prediction_audio"):
        if runner is not None:
            transcript = runner.transcribe(audio_path, language=language)
        else:
            transcript = f"sample-transcript-{language}"
        return transcript, (
            PipelineNodeResult(
                stage=STAGE,
                node_id=NODE_ID,
                version=VERSION,
                details={"external": True, "role": role},
            ),
        )

    return node
