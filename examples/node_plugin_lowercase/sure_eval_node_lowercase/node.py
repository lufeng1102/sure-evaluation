"""Example external normalization node: lowercase key-text files.

安装（``pip install -e examples/node_plugin_lowercase``）后，本节点声明
``default_for`` 为 ``ASR/en/wer``，会出现在
``sure-eval metric describe asr --language en --metric wer`` 的 normalization
slot choices 中。它通过 ``SELECTORS`` 声明 normalizer 选择器，因此也可以被
ASR executor 按 ``normalizer="lowercase_norm"`` 动态 dispatch。
"""

from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult

NODE_ID = "normalization/lowercase_norm"
STAGE = "normalization"
VERSION = "v1"

MANIFEST = {
    "id": NODE_ID,
    "version": VERSION,
    "stage": STAGE,
    "language_sensitive": True,
    "input_schema": "key_text_files",
    "output_schema": "key_text_files",
    "profiles": {"default": {"language": "en", "default_for": ["ASR/en/wer"]}},
}

NODE_ENV = None  # 纯 in-process；有依赖时改为 node_env dict 或包内 node_env.yaml

SELECTORS = {"normalizer": "lowercase_norm"}


def build(*, language=None, profile="default", **config):
    def node(files: KeyTextFiles):
        ref_out = _lowercase(files.ref_file)
        hyp_out = _lowercase(files.hyp_file)
        return KeyTextFiles(ref_file=ref_out, hyp_file=hyp_out), PipelineNodeResult(
            stage=STAGE,
            node_id=NODE_ID,
            version=VERSION,
            details={"profile": profile, "language": language, "ref_file": ref_out, "hyp_file": hyp_out},
        )

    return node


def _lowercase(path: str) -> str:
    out = path + ".lower"
    with open(path, encoding="utf-8") as src, open(out, "w", encoding="utf-8") as dst:
        for line in src:
            line = line.rstrip("\n")
            if "\t" in line:
                key, text = line.split("\t", 1)
                dst.write(f"{key}\t{text.lower()}\n")
            else:
                dst.write(line.lower() + "\n")
    return out
