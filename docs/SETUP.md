# Setup guide

Target machine: **RTX 5070 Ti 16 GB, 64 GB RAM, Linux**, ComfyUI installed.
The orchestrator (`shorts`) can run on the same machine (simplest) or on another machine that reaches ComfyUI over
the LAN. F5-TTS and faster-whisper run *where the orchestrator runs*, so put it on the GPU machine.

> Check model names and sizes on Hugging Face when you download. This field moves fast; the files below
> were current when this guide was written. Any model that ComfyUI can run works, because the workflow file
> defines the model (see §5.4).

---

## 1. System prerequisites

```bash
nvidia-smi                       # driver ≥ 570 for RTX 50xx (Blackwell), CUDA 12.8+
sudo apt install ffmpeg          # optional: `uv sync --extra ffmpeg` bundles a static ffmpeg instead
curl -LsSf https://astral.sh/uv/install.sh | sh   # uv package manager
```

## 2. Install the project

```bash
cd video-workflow
uv sync --extra claude --extra f5 --extra whisper --extra youtube
cp .env.example .env
uv run pytest -q                 # offline tests
uv run shorts providers          # shows available + selected providers
```

The `f5` extra installs PyTorch from the CUDA 12.8 index (configured in `pyproject.toml`), which RTX 50xx cards need. Verify:

```bash
uv run python -c "import torch; print(torch.__version__, torch.cuda.get_device_name())"
```

**Dry run without any models:** `shorts -e .env.mock make "test" -n 1` with a `.env.mock` containing
`LLM_PROVIDER=mock`, `TTS_PROVIDER=mock`, `ALIGN_PROVIDER=proportional`, `IMAGE_PROVIDER=mock`, `MOTION_PROVIDER=kenburns`.
It produces a real `final.mp4` with placeholder visuals.

---

## 3. LLM

### 3.1 Claude (default)
1. Create an API key at <https://console.anthropic.com> → *API keys*.
2. In `.env`, set `ANTHROPIC_API_KEY=sk-ant-...` (`.env` is git-ignored).
3. `uv run shorts llm-test`. It should print 3 ideas as JSON.

Cost knobs: `LLM_CLAUDE_MODEL` (`claude-opus-5-5` default; `claude-sonnet-5-5` / `claude-haiku-5-5` are cheaper)
and `LLM_CLAUDE_EFFORT` (`low` … `max`). Each call logs its token usage. A script is roughly 2–4k tokens in and 1–2k out.

### 3.2 Local LLM: Ollama
```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:14b            # ~9 GB, good JSON + writing on 16 GB; alternatives: gemma3:12b, mistral-small3.2
```
`.env`: `LLM_PROVIDER=ollama` (or only `IDEAS_LLM_PROVIDER=ollama` to brainstorm locally and write with Claude).
The pipeline unloads the model (`keep_alive=0`) before the GPU stages.

### 3.3 Local LLM: LM Studio / llama.cpp / vLLM
Start the server's OpenAI-compatible endpoint, then set `LLM_PROVIDER=openai_compat`, `LLM_OPENAI_BASE_URL`, and `LLM_OPENAI_MODEL`.
If the server rejects `response_format: json_schema`, set `LLM_OPENAI_RESPONSE_FORMAT=json_object` (or `none`).

---

## 4. Voice: F5-TTS

The model (`F5TTS_v1_Base`, ~1.3 GB) downloads from Hugging Face (`SWivid/F5-TTS`) on first use. It needs ~3 GB VRAM.

**Reference voice** (this determines how your channel sounds):
- 6–12 seconds of clean speech: one speaker, no music or reverb, natural energetic delivery. Shorter than 12 s works best.
- Use your own voice or one you have explicit rights to. Don't clone real people without consent.
- Convert to mono WAV:
  ```bash
  ffmpeg -i my_recording.m4a -ac 1 -ar 24000 -t 12 assets/voices/narrator.wav
  ```
- Put the exact transcript in `TTS_F5_REF_TEXT=...`. If you leave it empty, F5 transcribes the clip with Whisper on every load (slower, sometimes wrong).

**Languages:** the base model is trained on English and Chinese. For other languages, search Hugging Face for
"F5-TTS <language>" finetunes and set `TTS_F5_CKPT_FILE` / `TTS_F5_VOCAB_FILE` (and `TTS_F5_MODEL` to the matching
architecture, usually `F5TTS_v1_Base` or `F5TTS_Base`). Set `CONTENT_LANGUAGE` and `ALIGN_WHISPER_LANGUAGE` to match.

Tuning: `TTS_F5_SPEED` (1.0–1.15 suits Shorts), `TTS_F5_NFE_STEP` (32 = quality, 16 = fast), and `TTS_F5_SEED` (fixes intonation between re-runs).

### 4.1 Alternative: F5-TTS inside ComfyUI (`TTS_PROVIDER=comfyui`)

Use this when the orchestrator runs on a weak machine (e.g. a laptop) and ComfyUI runs on the GPU box. The voice model then runs there.

1. On the ComfyUI machine, install the **ComfyUI-F5-TTS** custom node (niknah) with ComfyUI-Manager, or
   `git clone https://github.com/niknah/ComfyUI-F5-TTS` into `custom_nodes/` and install its requirements. Restart ComfyUI.
   The model downloads on first use.
2. On the orchestrator machine, in `.env`:
   ```
   TTS_PROVIDER=comfyui
   TTS_COMFY_REF_AUDIO=assets/voices/narrator.wav      # stays local, uploaded automatically
   TTS_COMFY_REF_TEXT=exact words spoken in the clip
   ```
   You don't need the `f5` extra (or torch) on this machine.
3. `uv run shorts check`. Then try `uv run shorts make "test" -n 1 --until voice` and listen to `runs/<run>/audio/voice.wav`.

The starter `workflows/tts_f5_comfy.json` is `LoadAudio → F5-TTS Audio from inputs → SaveAudio`, with
`model=F5v1`, `model_type=F5TTS_v1_Base`, `vocoder=auto`. If ComfyUI says a value is "not in list", pick the
right one in the node's dropdown in the UI and re-export. Other languages: the node ships `F5-DE`, `F5-ES`, `F5-FR`,
`F5-IT`, `F5-JP`, … models.

**Any other TTS node works too** (Chatterbox, IndexTTS, …). Build `LoadAudio → <TTS node> → SaveAudio`, export (API),
mark it (`shorts comfy mark wf.json 1.audio=ref_audio 2.text=text …`), and set `TTS_COMFY_WORKFLOW`. Placeholders:
`text`, `ref_audio`, `ref_text`, `seed`, `speed`, `filename_prefix`.

## 5. Captions alignment: faster-whisper

Installed by the `whisper` extra. `ALIGN_WHISPER_MODEL=large-v3-turbo` (~1.6 GB, downloads on first use) is accurate and fast.
On a CPU-only machine use `small` + `ALIGN_WHISPER_COMPUTE_TYPE=int8`. The captions keep your script's spelling; Whisper only supplies the timing.

---

## 6. ComfyUI

### 6.1 Prepare ComfyUI
- Update to a recent version. Wan 2.2 nodes and `CreateVideo`/`SaveVideo` need it.
  ComfyUI's PyTorch must also be a CUDA 12.8+ build for RTX 50xx.
- If the orchestrator runs on another machine, start ComfyUI with `python main.py --listen 0.0.0.0` and set `COMFY_URL`.
- Useful launch flags on 16 GB: the defaults are fine. Add `--reserve-vram 1` if you also have a desktop/browser on that GPU.

Download files with the Hugging Face CLI (`uv tool install huggingface_hub`), e.g.:
```bash
hf download Comfy-Org/Wan_2.2_ComfyUI_Repackaged \
  split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors --local-dir /tmp/hf
mv /tmp/hf/split_files/diffusion_models/*.safetensors ~/ComfyUI/models/diffusion_models/
```

### 6.2 Image models (keyframes). Pick one.

| Option | Files → `ComfyUI/models/…` | Workflow in this repo | Notes |
|---|---|---|---|
| **FLUX.1 dev fp8** (default) | `Comfy-Org/flux1-dev` → `flux1-dev-fp8.safetensors` → `checkpoints/` (~17 GB, includes text encoders + VAE) | `workflows/image_flux_dev_fp8.json` | Best prompt following of the three. ~20–40 s per 768×1344 image. Read the FLUX.1-dev license before monetizing. |
| **FLUX.1 schnell fp8** | `Comfy-Org/flux1-schnell` → `flux1-schnell-fp8.safetensors` → `checkpoints/` | copy the dev workflow, set `ckpt_name`, `steps` 4 | Apache-2.0, ~4× faster, a bit lower quality |
| **SDXL finetune** (e.g. Juggernaut XL) | `.safetensors` from Civitai/HF → `checkpoints/` | `workflows/image_sdxl.json` (edit `ckpt_name` to your file) | ~6–10 s per image, needs stronger prompting |

Newer image models (Qwen-Image, FLUX.2, HiDream, Z-Image…) work the same way. Open ComfyUI's
*Workflow → Browse Templates*, run it once, then convert it with §6.4.

`IMAGE_WIDTH`/`IMAGE_HEIGHT`: 768×1344 for Flux and SDXL (both ~1 MP, 9:16).

### 6.3 Image-to-video (mode B): Wan 2.2 14B I2V

From `Comfy-Org/Wan_2.2_ComfyUI_Repackaged` (split_files/…):

| File | Folder | Size |
|---|---|---|
| `diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` | `models/diffusion_models/` | ~14 GB |
| `diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` | `models/diffusion_models/` | ~14 GB |
| `loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors` | `models/loras/` | speed LoRA, 4 steps |
| `loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors` | `models/loras/` | |

From `Comfy-Org/Wan_2.1_ComfyUI_repackaged`:

| File | Folder |
|---|---|
| `split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `models/text_encoders/` |
| `split_files/vae/wan_2.1_vae.safetensors` | `models/vae/` |

`workflows/i2v_wan22.json` uses exactly these names. Wan 2.2 swaps two 14 B experts per clip. With 16 GB VRAM ComfyUI
offloads to system RAM, and your 64 GB is what makes that work. Expect roughly **1–3 min per 5 s clip at 480×832**
with the 4-step LoRA. 720×1280 is ~2.5× slower.

**If you run out of memory or want it faster:**
- **GGUF quantized experts:** install the *ComfyUI-GGUF* custom node (city96) and download Q4_K_M/Q5_K_M files from
  `QuantStack/Wan2.2-I2V-A14B-GGUF` into `models/unet/`. In ComfyUI, replace the two `UNETLoader`s with `Unet Loader (GGUF)`
  and re-export (§6.4).
- **Wan 2.2 TI2V 5B** (`wan2.2_ti2v_5B_fp16.safetensors` + `wan2.2_vae.safetensors`): fits in VRAM, 720p/24 fps,
  lower motion quality. Use its ComfyUI template, set `MOTION_FPS=24`.
- **LTX-Video (LTX-2.x):** much faster, good for b-roll. Use its template, set `MOTION_I2V_FRAME_STEP=8`, `MOTION_FPS=24`.
- `MOTION_SCENES=first:2` gives real motion only to the hook scenes and uses Ken Burns for the rest. This cuts render time a lot.

### 6.4 Using any ComfyUI workflow

1. Build or open the workflow in ComfyUI and check it works.
2. *Workflow → Export (API)* and save into `workflows/`.
3. List the nodes: `uv run shorts comfy inspect workflows/my.json`
4. Mark the inputs the pipeline should fill:
   ```bash
   uv run shorts comfy mark workflows/my.json 6.text=prompt 7.text=negative 3.seed=seed \
       5.width=width 5.height=height 9.filename_prefix=filename_prefix
   ```
5. Point `.env` at it: `IMAGE_COMFY_WORKFLOW=workflows/my.json` (or `MOTION_I2V_WORKFLOW=…`).

| Placeholder | Image workflows | I2V workflows |
|---|---|---|
| `prompt`, `negative`, `seed` | ✔ | ✔ |
| `width`, `height` | ✔ (`IMAGE_*`) | ✔ (`MOTION_*`) |
| `image` | | ✔ (`LoadImage.image`) |
| `frames`, `fps` | | ✔ (length / fps inputs) |
| `filename_prefix` | optional | optional |

Unused placeholders are fine. A placeholder without a value fails with a clear error. The output must come from a `SaveImage` /
`SaveVideo` (or VHS `Video Combine`) node.

Test each part on its own:
```bash
uv run shorts check                                      # providers, ComfyUI connection, ffmpeg
uv run shorts comfy test-image "a lighthouse in a storm at night"
uv run shorts comfy test-motion comfy_test.png --prompt "waves crash, light beam sweeps"
```

---

## 7. Fonts & music

- **Caption font:** download *Montserrat* from Google Fonts and put `Montserrat-ExtraBold.ttf` in `assets/fonts/`.
  Any bold sans works; set `CAPTION_FONT` to the font's family name.
- **Music:** put royalty-free tracks (e.g. from the YouTube Audio Library) in `assets/music/`. One is chosen per video
  and ducked under the voice automatically. Leave the folder empty for no music.

## 8. YouTube upload

1. <https://console.cloud.google.com> → new project → *APIs & Services* → enable **YouTube Data API v3**.
2. *OAuth consent screen*: External, add your Google account as a **test user**.
3. *Credentials → Create credentials → OAuth client ID → Desktop app*. Download the JSON to `secrets/client_secret.json`.
4. `.env`: `UPLOADER=youtube`.
5. `uv run shorts upload runs/<run>`. The first time, a browser opens for consent and the token is cached in `secrets/`.

Caveats:
- **Unverified API projects can only upload as _private_.** Publish from YouTube Studio, or apply for the API
  compliance audit to lift this.
- The default quota (10,000 units/day) allows ~6 uploads per day.
- Uploads are flagged `containsSyntheticMedia=true` (YouTube's altered/synthetic content disclosure).

---

## 9. First real run

```bash
uv run shorts ideas "black holes" -n 10            # brainstorm; review ideas/black-holes.json
uv run shorts make "black holes" -n 1 --until script   # check the script first
uv run shorts resume runs/<run>                     # continue: voice → … → final.mp4
uv run shorts redo runs/<run> --stage images --scene 3  # re-roll one bad scene
uv run shorts make "black holes" -n 5               # batch (stage by stage, models load once)
uv run shorts status
uv run shorts upload runs/<run>
```

Compare providers by keeping several env files (`.env.claude`, `.env.ollama`, …) and running `shorts -e .env.ollama make …`.

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `invalid settings … extra inputs are not permitted` | Typo in a provider env var (e.g. `LLM_OLLAMA_MODLE`) |
| `cannot import provider module` | Install the extra: `uv sync --extra f5` / `whisper` / `youtube` / `claude` |
| `ComfyUI rejected the workflow … value not in list` | The model filename in the workflow doesn't match your `models/` folder; fix it in ComfyUI and re-export, or edit the JSON |
| `is a UI-format workflow` | Use *Export (API)*, not *Save* |
| I2V out of memory | Lower `MOTION_WIDTH/HEIGHT`, use GGUF experts, or `MOTION_SCENES=first:2`; failed scenes fall back to Ken Burns automatically |
| Captions in the wrong font | The font isn't in `assets/fonts/` or `CAPTION_FONT` isn't the exact family name |
| Voice sounds off / wrong words | Better reference clip with an exact `TTS_F5_REF_TEXT`; spell numbers out in the script prompt |
