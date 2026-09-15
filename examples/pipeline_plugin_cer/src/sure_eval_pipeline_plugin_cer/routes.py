"""Route-only ASR CER pipeline using builtin nodes."""

ROUTES = [
    {
        "language": "en",
        "metric": "cer",
        "pipeline_id": "asr.en.cer.aispeech_norm_en_v1.wenet_cer_v1",
        "nodes": [
            "normalization/aispeech_norm",
            "scoring/wenet_cer",
        ],
        "input_contract": "scoring/wenet_cer",
        "executor": "sure_eval.evaluation.tasks.asr.pipeline.evaluate_asr_files",
    },
]
