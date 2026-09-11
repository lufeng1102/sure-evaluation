"""Example external SE full-reference scoring node (audio scoring contract).

安装（``pip install -e examples/node_plugin_se_scoring``）后，本节点经
``sure_eval.nodes`` entry point 注册为 ``scoring/sample_se_metric``，并经
``sure_eval.routes`` 注入一条 SE route。

SE/TSE 的 scoring 节点是 provider-backed 的「audio 打分」契约：
``list[Row] + provider -> PipelineNodeResult``（Row 是 ``(sample_id, enhanced,
reference)`` 元组）。外部节点声明 ``SELECTORS = {"full_reference": ...}``，
SE executor 通过 registry 动态判定 family 并 dispatch，全程无需改框架源码。

本示例不加载真实模型，直接返回常数分数，仅演示 dispatch 与身份生成。
"""

from sure_eval.evaluation.core.types import PipelineNodeResult

NODE_ID = "scoring/sample_se_metric"
STAGE = "scoring"
VERSION = "v1"

MANIFEST = {
    "id": NODE_ID,
    "version": VERSION,
    "stage": STAGE,
    "language_sensitive": False,
    "input_schema": "speech_enhancement_audio_pairs",
    "output_schema": "full_reference_audio_metric",
}

NODE_ENV = None

# family -> selector value（SE executor 的 _metric_family 用 "full_reference" 匹配）
SELECTORS = {"full_reference": "my-se-metric"}


def build(*, provider=None, **config):
    def node(rows):
        per_sample = [
            {"score": 0.5, "sample_id": (row[0] if row else None)} for row in rows
        ]
        return PipelineNodeResult(
            stage=STAGE,
            node_id=NODE_ID,
            version=VERSION,
            details={
                "result": {
                    "score": 0.5,
                    "per_sample": per_sample,
                    "num_samples": len(rows),
                }
            },
        )

    return node
