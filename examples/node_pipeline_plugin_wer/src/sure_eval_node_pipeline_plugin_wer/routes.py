"""ASR WER route using the plugin's normalization node."""

ROUTES = [
    {
        "language": "en",
        "metric": "wer",
        "pipeline_id": "asr.en.wer.example_lowercase_v1.wenet_wer_v1",
        "nodes": [
            "normalization/example_lowercase",
            "scoring/wenet_wer",
        ],
        "input_contract": "scoring/wenet_wer",
        "executor": "sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files",
    },
]
