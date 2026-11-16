# Licenses: what's safe for a monetized channel

> This is a summary from public license texts and model cards (checked 2026-11), **not legal advice**.
> Licenses change. Re-check the model card of the exact file you download.

## Summary

| Component | License | Monetized channel? | Notes |
|---|---|---|---|
| **Claude API** | Anthropic Commercial Terms | ✅ | You own the outputs |
| Ollama / local LLMs | per model | ✅ mostly | Qwen3: Apache-2.0. Gemma: Gemma Terms (commercial OK). Llama: Llama license (OK under 700M MAU) |
| **F5-TTS official weights** (`F5TTS_v1_Base`, ComfyUI-F5-TTS `F5v1`, most language finetunes) | **CC-BY-NC-4.0** | ❌ | Code is MIT, but the weights are trained on the Emilia dataset. Fine-tunes of these weights inherit the restriction. `shorts check` warns about this |
| OpenF5-TTS (community retrain) | Apache-2.0 | ✅ | Alpha quality per its model card; load via `TTS_F5_CKPT_FILE` |
| **Kokoro-82M** (`TTS_PROVIDER=kokoro`) | Apache-2.0 | ✅ | Preset voices only, no cloning |
| **Chatterbox** (Resemble AI) | MIT | ✅ | Voice cloning. Outputs carry an inaudible Perth watermark. Run it **through ComfyUI** (see below) |
| faster-whisper / Whisper | MIT | ✅ | |
| **FLUX.1 [dev]** | FLUX.1-dev Non-Commercial License | ⚠️ | The license text says outputs may be used commercially, and BFL staff have said so too. Using the *model* for revenue-generating activity is not "non-commercial", so readings differ. If in doubt, use schnell or buy BFL's self-serve commercial license |
| FLUX.1 [schnell] | Apache-2.0 | ✅ | `workflows/image_flux_dev_fp8.json` with `ckpt_name` → schnell, `steps` 4 |
| SDXL base / most SDXL finetunes | CreativeML Open RAIL++-M (+ finetune terms) | ✅ usually | Use restrictions (no harmful use); check each finetune on Civitai |
| **Wan 2.2** | Apache-2.0 | ✅ | LoRAs (e.g. LightX2V) have their own cards; check them |
| LTX-Video / LTX-2 | Lightricks open-weights license | ⚠️ | Free commercial use has conditions (e.g. revenue thresholds); read the current license |
| Montserrat font | SIL OFL | ✅ | |
| Music | per track | ⚠️ | YouTube Audio Library: some tracks require attribution in the description |
| Your voice reference | — | ✅ if it's yours | Never clone someone's voice without written consent |

## Recommended combinations

| Goal | TTS | Images | Video |
|---|---|---|---|
| **Monetized, cloned voice** | Chatterbox via ComfyUI (`TTS_PROVIDER=comfyui`) | FLUX.1 schnell or SDXL finetune | Wan 2.2 |
| **Monetized, simplest** | Kokoro (`TTS_PROVIDER=kokoro`) | FLUX.1 schnell | Wan 2.2 or Ken Burns |
| Experimenting / not monetized | F5-TTS (local or ComfyUI) | FLUX.1 dev | Wan 2.2 |

## Chatterbox through ComfyUI

The `chatterbox-tts` pip package pins `torch==2.6.0`, which has no RTX 50xx (Blackwell) kernels, so it can't run
in-process on a 5070 Ti. ComfyUI custom nodes bundle the model code and run on ComfyUI's own (newer) PyTorch:

1. In ComfyUI-Manager, install a Chatterbox node pack (e.g. **TTS Audio Suite**, which also includes F5, or a
   dedicated *ChatterboxTTS* node).
2. Build `LoadAudio → Chatterbox TTS (voice cloning) → SaveAudio` and check it works in the UI.
3. Export (API) to `workflows/tts_chatterbox.json`, then mark it:
   `uv run shorts comfy mark workflows/tts_chatterbox.json <load>.audio=ref_audio <tts>.text=text <tts>.seed=seed`
4. `.env`: `TTS_PROVIDER=comfyui`, `TTS_COMFY_WORKFLOW=workflows/tts_chatterbox.json`.

## YouTube policy (not a license, but just as important)

- Tick "altered or synthetic content" (the uploader sets `containsSyntheticMedia`).
- The YouTube Partner Program excludes "inauthentic" mass-produced or repetitive content. Vary formats, keep scripts
  substantive, and review what you publish.
