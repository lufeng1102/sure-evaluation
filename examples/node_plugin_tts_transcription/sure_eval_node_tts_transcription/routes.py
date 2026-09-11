"""External TTS route: swap the semantic transcription node for the sample one."""

ROUTES = [
    {
        "language": "en",
        "metric": "wer",
        "method": "sample_tts_asr",
        "executor_metric": "tts_wer",
        "family": "semantic_error_rate",
        "pipeline_id": "tts.en.wer.sample_tts_asr_v1.whisper_norm_english_v1.wenet_wer_v1",
        "nodes": [
            "transcription/sample_tts_asr",
            "normalization/whisper_norm",
            "scoring/wenet_wer",
        ],
        "input_contract": "semantic/asr_error_rate",
        "executor": "sure_eval.evaluation.tasks.tts.pipeline.evaluate_tts_samples",
    },
]
