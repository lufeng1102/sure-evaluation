# normalization/lid_label

This node converts spoken-language labels to lowercase, hyphen-separated
codes before accuracy scoring. It makes FireRedLID output such as
`zh mandarin` comparable with the documented regional code `zh-mandarin`.
ISO language codes such as `en`, `ar`, and `bo` are unchanged. Empty and
special tokenizer labels normalize to an invalid empty value.

The FireRedLID inventory exported by this node is a snapshot of upstream
`dict.txt` at FireRedASR2S commit
`4e7d9aaf4482a47cec1724807026b9b151926eb5`. The default generic LID route
accepts any non-empty dataset-defined label after normalization; the optional
FireRedLID audio route restricts scoring to this locked inventory.
