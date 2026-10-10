# WISE audio preparation audit

Audited 2026-10-04 against WISE commit
`fcfa443fbb46eb361bb19151339338616838a5b5` and installed `msclap==1.3.4`.
This audit and the preparation script do not establish successful inference.

## Actual dependency chain

WISE feature ID `microsoft/clap/2023/four-datasets` calls
`msclap.CLAP(version="2023")`. The final `four-datasets` component is the WISE
identifier, not a separate artifact download. The wrapper fetches
`microsoft/msclap/CLAP_weights_2023.pth` through Hugging Face and constructs the
text encoder with `AutoModel.from_pretrained("gpt2")` before applying the CLAP
checkpoint. Thus GPT-2 model weights are required in addition to its tokenizer,
even though the CLAP checkpoint contains trained text encoder parameters.

The locked files total **1,240,909,773 bytes** (about 1.24 GB decimal):

| Runtime repository | Revision | Required files | License metadata |
| --- | --- | --- | --- |
| `microsoft/msclap` | `c47d441165daa21986ead0850660917636a81775` | `CLAP_weights_2023.pth` (689,950,036 bytes) | Microsoft HF model card: MS-PL |
| `gpt2` (canonical `openai-community/gpt2`) | `607a30d783dfa663caf39e06633721c8d4cfcd7e` | `model.safetensors` (548,105,171 bytes), `config.json`, `tokenizer.json`, `tokenizer_config.json`, `vocab.json`, `merges.txt` | HF model card: MIT |

Do not download CLAP 2022, `clapcap`, GPT-2 PyTorch `.bin`, TensorFlow or ONNX
artifacts. `clapcap` is a different caption-generation model, not needed for
sound retrieval. HTSAT audio network construction does not fetch a separate
checkpoint; its trained parameters are supplied by the CLAP 2023 file.

WISE requires `msclap>=1.3.4`. We audit and check exactly 1.3.4; its official wheel
SHA-256 is recorded in `audio-model.lock.json`. Declared dependencies include
`librosa>=0.10.1,<0.11`, `numpy>=1.23,<2`, `pandas>=2,<3`, `pyyaml>=6.0.1,<7`,
`scikit-learn>=1.3.1,<2`, `torch>=2.1,<3`, `torchaudio>=2.1,<3`,
`torchlibrosa>=0.1,<0.2`, `tqdm>=4.66.1,<5`, and `transformers>=4.34,<5`.
The WISE image already constrains torch/torchaudio to 2.8 CPU and provides
Hugging Face Hub, FFmpeg, FAISS and SQLAlchemy. No package install is performed by
the preparation script. Preserve the deployment image's compatible NumPy and
Transformers versions when rebuilding.

## Preparation and cache

`scripts/prepare_audio_model.py` prints the exact manifest without downloading by
default. Run in the existing isolated WISE Python environment only after a model
download has been authorized:

```sh
HF_HOME=/cache/huggingface HF_HUB_OFFLINE=0 \
python scripts/prepare_audio_model.py --download
HF_HOME=/cache/huggingface HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
python scripts/prepare_audio_model.py --verify-cache
```

Copy `audio-model.lock.json` together with the script, preserving their relative
layout, or pass `--lock /absolute/path/audio-model.lock.json`. The service's
persistent `wise-cache` volume provides `/cache`; paths normally become:

- `/cache/huggingface/hub/models--microsoft--msclap/snapshots/<revision>/...`
- `/cache/huggingface/hub/models--gpt2/snapshots/<revision>/...`

The literal `gpt2` alias is intentional: upstream uses that name, so populating
only `models--openai-community--gpt2` would not satisfy its offline lookup.
Every file is checked by size and SHA-256 before either model's `refs/main` is
published. Weight hashes come from the fixed HF Git LFS object IDs; small-file
hashes were computed from the exact pinned HTTP responses. The checkpoint is
never deserialized by the preparation script. An invalid checksum raises an
error and leaves existing published refs intact. Verification mode is read-only.

Keep `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` during processing/search. WISE
does not pass an explicit revision to `msclap`, so the verified pinned cache refs
and offline environment prevent upstream `main` from drifting. The service must
also explicitly allow `audio`, and the business API must expose that capability
only after real acceptance passes. Face/object remain disabled.

## Acceptance and limits

`backend/tests/test_wise_audio_live.py` is skipped unless
`INSPIRATION_WISE_AUDIO_E2E=1` is set together with the existing WISE URL, token,
and isolated `TEST_DATABASE_URL`. It creates an 8.2-second real video with an
880 Hz tone, uploads the real proxy, requests audio-only extraction, requires
nonempty vectors, queries CLAP with an English sound description, checks valid
time intervals/source identity, verifies whitelist rejection and deletion, and
cleans up upstream sources in `finally`. `INSPIRATION_WISE_TIMEOUT_SECONDS`
controls the default 600-second deadline. It does not replace models with mocks.

The fixed WISE version produces 4-second chunks at 48 kHz; msclap's 2023 config
declares a 44.1 kHz encoder and a 7-second wrapper duration. WISE calls encoder
functions directly rather than the wrapper's file-duration routine. This is an
upstream sampling/preprocessing difference requiring measured quality evaluation;
we have not modified the pinned algorithm. Shorter-than-chunk audio and final
partial chunks require separate coverage checks. Synthetic-tone smoke proves
only the integration: it does not validate recall, Chinese sound descriptions,
music similarity, speaker identity, or transcription. The text encoder is GPT-2;
do not assume multilingual retrieval quality from the visual model's behavior.

## License and maintenance evidence

The [CLAP code license](https://github.com/microsoft/CLAP/blob/main/LICENSE) and
PyPI wheel are MIT, while the [fixed Microsoft weight model card](https://huggingface.co/microsoft/msclap/blob/c47d441165daa21986ead0850660917636a81775/README.md)
labels the weights **MS-PL**. Keep those notices distinct from WISE's Apache-2.0
license. GPT-2's [canonical model metadata](https://huggingface.co/api/models/openai-community/gpt2)
labels it MIT. No model license acceptance is automated by this script.

Microsoft's [CLAP repository](https://github.com/microsoft/CLAP) was archived on
2026-09-15. Its package remains the dependency of this pinned WISE release, but
we should not promise continuing upstream maintenance. The script's exact pins
avoid silently switching to an unaudited fork.
