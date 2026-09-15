"""Identity normalization node used to demonstrate node-only discovery."""

from sure_eval.evaluation.core.types import KeyTextFiles, PipelineNodeResult

NODE_ID = "normalization/example_identity"
STAGE = "normalization"
VERSION = "v1"

MANIFEST = {
    "id": NODE_ID,
    "version": VERSION,
    "stage": STAGE,
    "language_sensitive": False,
    "input_schema": "key_text_files",
    "output_schema": "key_text_files",
}

NODE_ENV = None
SELECTORS = {"normalizer": "example_identity"}


def build(**config):
    def node(files: KeyTextFiles):
        return files, PipelineNodeResult(
            stage=STAGE,
            node_id=NODE_ID,
            version=VERSION,
            details={"identity": True},
        )

    return node
