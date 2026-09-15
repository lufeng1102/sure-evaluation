"""Example lowercase normalization node."""

from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult

NODE_ID = "normalization/example_lowercase"
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
SELECTORS = {"normalizer": "example_lowercase"}


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
