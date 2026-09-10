"""Example external scoring node: exact-match accuracy.

计算 ref 与 hyp 的 key-text 逐行完全匹配比例。与
``examples/node_plugin_lowercase`` 的 normalization 节点组合，可端到端跑通：
先 lowercase 归一化，再 exact-match 打分。
"""

from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult

NODE_ID = "scoring/exact_match"
STAGE = "scoring"
VERSION = "v1"

MANIFEST = {
    "id": NODE_ID,
    "version": VERSION,
    "stage": STAGE,
    "language_sensitive": False,
    "input_schema": "key_text_files",
    "output_schema": "key_text_files",
}

NODE_ENV = None  # 纯 in-process

SELECTORS = {"scorer": "exact_match"}


def build(*, metric=None, scorer=None, **config):
    def node(files: KeyTextFiles):
        score = _exact_match(files.ref_file, files.hyp_file)
        # scoring 节点的 result 放在 details["result"]，score 必须是数值。
        return files, PipelineNodeResult(
            stage=STAGE,
            node_id=NODE_ID,
            version=VERSION,
            details={"result": {"score": score, "metric": "exact_match"}},
        )

    return node


def _exact_match(ref_file: str, hyp_file: str) -> float:
    with open(ref_file, encoding="utf-8") as handle:
        ref_lines = handle.read().splitlines()
    with open(hyp_file, encoding="utf-8") as handle:
        hyp_lines = handle.read().splitlines()
    total = max(len(ref_lines), len(hyp_lines), 1)
    matches = sum(
        1 for i in range(min(len(ref_lines), len(hyp_lines))) if ref_lines[i] == hyp_lines[i]
    )
    return matches / total
