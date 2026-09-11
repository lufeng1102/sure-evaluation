"""Example external VAD validation node using the unified NodePayload contract.

安装（``pip install -e examples/node_plugin_vad_validation``）后，本节点经
``sure_eval.nodes`` entry point 注册为 ``validation/sample_vad_contract``，并
经 ``sure_eval.routes`` 注入一条 VAD route。VAD executor 通过 registry 动态
装配节点，全程无需改动 SURE 框架源码。

它演示的是「统一节点载荷」：``build`` 返回 ``NodePayload -> NodePayload``
工厂，从 ``payload.files`` 读输入文件、经 ``payload.with_artifact`` 写中间
产物 ``validated_bundle``。这里直接复用内置 ``validate_vad_contract``，仅作
为外部节点接入的示例。
"""

NODE_ID = "validation/sample_vad_contract"
STAGE = "validation"
VERSION = "v1"

MANIFEST = {
    "id": NODE_ID,
    "version": VERSION,
    "stage": STAGE,
    "language_sensitive": False,
    "input_schema": "vad_jsonl_files",
    "output_schema": "vad_validated_bundle",
    "consumes": [],
    "produces": ["validated_bundle"],
}

NODE_ENV = None  # 纯 in-process；有依赖时改为 node_env dict 或包内 node_env.yaml

SELECTORS = {}


def build(*, metric=None, **config):
    from sure_eval.evaluation.core.types import NodePayload
    from sure_eval.evaluation.nodes.validation.vad_contract import (
        REQUIRED_FIELDS_BY_METRIC,
        validate_vad_contract,
    )

    required_fields = REQUIRED_FIELDS_BY_METRIC.get(metric or "", ())

    def node(payload: NodePayload):
        payload.files.require("reference_jsonl", "sample_output")
        validated, result = validate_vad_contract(
            payload.files.roles["reference_jsonl"],
            payload.files.roles["sample_output"],
            required_prediction_fields=required_fields,
        )
        return payload.with_artifact("validated_bundle", validated), result

    return node
