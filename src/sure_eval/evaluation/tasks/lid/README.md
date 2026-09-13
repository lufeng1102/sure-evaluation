# LID Task Routes

The default LID route measures utterance-level accuracy for labels produced by
any LID system. It accepts aligned reference and hypothesis `key<TAB>label`
files, canonicalizes both labels, then reuses the generic classification
scoring node.

```text
lid.any.accuracy.lid_label_canonical_v1.classify_v1
```

An optional evaluator-owned FireRedLID reference-backend route accepts audio
plus expected language labels. It measures whether that fixed backend
identifies each audio file as the expected language; it does not evaluate an
arbitrary third-party LID model.

Input for that optional route is JSONL:

```json
{"sample_id":"utt-zh","audio_path":"audio/zh.wav","reference_language":"zh-mandarin"}
{"sample_id":"utt-en","audio_path":"audio/en.wav","reference_language":"en"}
```

```text
lid.any.accuracy.firered_lid_v1.lid_label_canonical_v1.classify_v1
```

Relative `audio_path` values resolve against the JSONL file directory. Audio
must be 16 kHz, 16-bit, mono PCM WAV; incompatible files fail in the FireRedLID
runtime rather than during JSONL loading. Labels are case-insensitive and
spaces, underscores, or slashes normalize to hyphens.
