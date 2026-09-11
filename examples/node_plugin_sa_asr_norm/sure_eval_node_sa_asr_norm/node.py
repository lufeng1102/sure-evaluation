"""Example external SA-ASR normalization node (KeyTextFiles contract).

安装（``pip install -e examples/node_plugin_sa_asr_norm``）后，本节点经
``sure_eval.nodes`` entry point 注册为 ``normalization/sa_asr_sample_norm``，
并经 ``sure_eval.routes`` 注入一条 SA-ASR route。SA-ASR executor 通过 registry
动态装配 normalization 节点，全程无需改动 SURE 框架源码。

SA-ASR 的 normalization 是 ``KeyTextFiles -> KeyTextFiles`` 契约（与 ASR 相同），
因此本节点沿用 key-text 契约；scoring 仍由内置 ``scoring/meeteval`` 承担。
"""

from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult

NODE_ID = "normalization/sa_asr_sample_norm"
STAGE = "normalization"
VERSION = "v1"

MANIFEST = {
    "id": NODE_ID,
    "version": VERSION,
    "stage": STAGE,
    "language_sensitive": True,
    "input_schema": "key_text_files",
    "output_schema": "key_text_files",
}

NODE_ENV = None

SELECTORS = {}


def build(*, language=None, **config):
    def node(files: KeyTextFiles):
        ref_out = _lowercase(files.ref_file)
        hyp_out = _lowercase(files.hyp_file)
        return KeyTextFiles(ref_file=ref_out, hyp_file=hyp_out), PipelineNodeResult(
            stage=STAGE,
            node_id=NODE_ID,
            version=VERSION,
            details={"language": language, "ref_file": ref_out, "hyp_file": hyp_out},
        )

    return node


def _lowercase(path: str) -> str:
    import tempfile

    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    )
    out_path = handle.name
    with open(path, encoding="utf-8") as fin:
        for line in fin:
            if "\t" not in line:
                continue
            key, text = line.rstrip("\n").split("\t", 1)
            handle.write(f"{key}\t{text.lower()}\n")
    handle.close()
    return out_path
