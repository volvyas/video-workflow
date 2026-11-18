# Setup guide

Target machine: **RTX 5070 Ti 16 GB, 64 GB RAM, Linux**, ComfyUI installed.
The orchestrator (`shorts`) can run on the same machine (simplest) or on another machine that reaches ComfyUI over
the LAN. F5-TTS and faster-whisper run *where the orchestrator runs*, so put it on the GPU machine.

> Check model names and sizes on Hugging Face when you download. This field moves fast; the files below
> were current when this guide was written. Any model that ComfyUI can run works, because the workflow file
> defines the model (see §5.4).

---

## 0. Two-machine layout (current)

| Machine | Runs | Address |
|---|---|---|
| **AI box** (Windows, RTX 5070 Ti 16 GB, 64 GB RAM) | llama.cpp `llama-server` (Qwen3.8-Flash-Next), ComfyUI | `<ai-box-ip>` (LLM `:8080`, ComfyUI `:8188`) |
| **Laptop** (Linux, RTX 3050 4 GB) | `shorts` orchestrator, Kokoro/F5 TTS, faster-whisper, FFmpeg assembly | — |

Windows notes for the AI box:
- Allow inbound connections (PowerShell as Administrator):
  ```powershell
  netsh advfirewall firewall add rule name="llama-server" dir=in action=allow protocol=TCP localport=8080
  netsh advfirewall firewall add rule name="ComfyUI" dir=in action=allow protocol=TCP localport=8188
  ```
- Servers must listen on the network, not only localhost: `--host 0.0.0.0` (llama-server), `--listen 0.0.0.0` (ComfyUI).
- Windows paths work in the commands below (`-m e:\llama\models\…gguf`). llama-swap has Windows builds too.

## 1. System prerequisites

```bash
nvidia-smi                       # driver ≥ 570 for RTX 50xx (Blackwell), CUDA 12.8+
sudo apt install ffmpeg          # optional: `uv sync --extra ffmpeg` bundles a static ffmpeg instead
curl -LsSf https://astral.sh/uv/install.sh | sh   # uv package manager
```

## 2. Install the project

```bash
cd video-workflow
uv sync --extra f5 --extra whisper --extra search --extra youtube   # or --extra kokoro instead of f5; --extra claude only if you use the paid API
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

## 3. LLM (default: local)

The pipeline talks to any **OpenAI-compatible** server (`LLM_PROVIDER=openai_compat`), so llama.cpp's `llama-server`,
llama-swap, LM Studio and vLLM all work. One model can do everything: ideas, scripts, review, and the image check
(if it has vision).

### 3.1 Qwen3.8-Flash-Next (125B MoE, ~6B active) on the GPU box
If you already run it, keep your setup and point `.env` at it. Requirements:
- OpenAI-compatible endpoint (`/v1/chat/completions`) reachable from the orchestrator machine (`--host 0.0.0.0`).
- `response_format: json_schema` support (llama-server converts it to a grammar). If your server lacks it, set
  `LLM_OPENAI_RESPONSE_FORMAT=json_object` or `none`. The pipeline validates and retries either way.
- **Vision for the image check:** load the model's vision projector (`--mmproj …` in llama.cpp). Without it, set
  `IMAGE_QA_ENABLED=false` or point `IMAGE_QA_LLM_PROVIDER` at a small vision model.
- Context ≥ 32k (`-c 32768`): the fact-check prompt carries search results.

#### Step 1: Start llama-server on the GPU box (manual, good for a first test)
```bash
llama-server \
  -m /path/to/Qwen3.8-Flash-Next-<your-quant>.gguf \
  --mmproj /path/to/mmproj-<…>.gguf \
  --alias qwen3.8-flash-next \
  -ngl 99 --n-cpu-moe 99 \
  -c 32768 --jinja \
  --host 0.0.0.0 --port 8080
```

| Flag | Why |
|---|---|
| `--mmproj` | The vision part the image check needs. It's a separate `mmproj-*.gguf` file, usually in the same Hugging Face repo as the model. Without it, drop the line and set `IMAGE_QA_ENABLED=false` |
| `--alias` | Model name the pipeline sends (`LLM_OPENAI_MODEL=qwen3.8-flash-next`) |
| `-ngl 99 --n-cpu-moe 99` | Shared layers on the GPU, experts in RAM. This is how the 125B MoE fits in 16 GB VRAM. If other offload flags worked for you before, keep yours |
| `-c 32768` | Context size: the fact-check prompt includes search results |
| `--jinja` | Use the model's own chat template (needed for correct chat/JSON output) |
| `--host 0.0.0.0` | Reachable from the laptop. Without it, llama-server only listens on the GPU box itself |

If a firewall is on: `sudo ufw allow 8080`. Get the box's IP with `hostname -I`.

#### Step 2: Check it from the orchestrator machine
```bash
curl http://<gpu-box-ip>:8080/health        # {"status":"ok"} once the model is loaded
curl http://<gpu-box-ip>:8080/v1/models     # lists qwen3.8-flash-next
```

#### Step 3: Point the pipeline at it (`.env`)
```
LLM_PROVIDER=openai_compat
LLM_OPENAI_BASE_URL=http://<gpu-box-ip>:8080/v1
LLM_OPENAI_MODEL=qwen3.8-flash-next
SEARCH_PROVIDER=ddgs
```
```bash
uv run shorts llm-test                                # prints 3 ideas as JSON
uv run shorts make "black holes" -n 1 --until review  # LLM-only stages; read runs/<run>/review.json
```
`--until review` stops before any ComfyUI stage, so plain llama-server can stay running without competing for VRAM.

#### Step 4: Automatic unloading with llama-swap (for full runs)
The LLM must leave VRAM (and RAM) before ComfyUI renders, and plain llama-server can't unload on request.
**llama-swap** is a small proxy that starts llama-server when a request arrives and stops it on
`POST /api/models/unload` (or after an idle `ttl`). It is a **separate program**, not part of llama.cpp or this repo;
the repo only ships its config, [`deploy/llama-swap.yaml`](../deploy/llama-swap.yaml).

On the Windows AI box:
1. Download `llama-swap_<version>_windows_amd64.zip` from https://github.com/mostlygeek/llama-swap/releases
   (or `winget install llama-swap`) and unzip it, e.g. to `C:\llama-swap\`.
2. Copy `deploy/llama-swap.yaml` from this repo next to `llama-swap.exe` and edit the three paths in `cmd`:
   `llama-server.exe`, the model `.gguf` (first part if split) and the `mmproj` file (or delete that line).
   Forward slashes (`C:/models/...`) are fine on Windows.
3. Stop the manual llama-server (llama-swap takes over port 8080, so the firewall rule stays valid) and run:
   ```powershell
   cd C:\llama-swap
   .\llama-swap.exe --config llama-swap.yaml --listen 0.0.0.0:8080
   ```
4. From the laptop: `curl http://<ai-box-ip>:8080/v1/models` lists `qwen3.8-flash-next`, and `uv run shorts llm-test`
   works (the first request waits while llama-swap starts llama-server and loads the model).

`.env` on the laptop keeps the same base URL and adds:
```
LLM_OPENAI_UNLOAD_URL=http://<ai-box-ip>:8080/api/models/unload
LLM_OPENAI_UNLOAD_METHOD=POST
```
The pipeline then unloads the LLM after the review stage and between image-check rounds. The first request after an
unload waits for the model to load again (from disk, or faster from the Windows file cache).
If your setup works without unloading, leave `LLM_OPENAI_UNLOAD_URL` empty.

#### Troubleshooting
- A flag is rejected: llama.cpp renames flags between builds, so check `llama-server --help`.
- `shorts llm-test` fails with a JSON/schema error: set `LLM_OPENAI_RESPONSE_FORMAT=json_object`.
- Answers are slow: thinking is on by default (see below). Turn it off for quick tests.

**Thinking:** Qwen models reason before answering by default. That's better for scripts and reviews, but slower. To turn it off:
`LLM_OPENAI_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}`.

Test: `uv run shorts llm-test`. It should print 3 ideas as JSON.

### 3.2 LM Studio / Ollama
- **LM Studio:** enable the local server (default `http://<host>:1234/v1`), set `LLM_OPENAI_BASE_URL` and
  `LLM_OPENAI_MODEL` to the model identifier LM Studio shows. Use LM Studio's idle auto-unload (TTL) to free VRAM.
- **Ollama:** `LLM_PROVIDER=ollama`, `LLM_OLLAMA_MODEL=<tag>`. Unloading is built in (`keep_alive=0`).
  Check that the tag's size fits your RAM + VRAM.

### 3.3 Web search for the fact-check
`SEARCH_PROVIDER=multi` (default): **Wikipedia** (official MediaWiki API: no key, no install, returns the article's
relevant paragraphs) plus the open web through **ddgs** (DuckDuckGo & co., no key). Results are merged and ranked by
relevance with a bonus for reliable domains (Wikipedia, NASA, NOAA, NIH, journals, `.gov`, `.edu`, ...); content
farms and video sites (`SEARCH_MULTI_BLOCK`, e.g. `iere.org`, YouTube) are dropped. Results are cached for
`SEARCH_CACHE_DAYS` (30) in `history/search_cache.json`, so repeated fact-checks don't trip rate limits.
- ddgs is free but rate-limits heavy use; when it does, Wikipedia still answers. Self-hosted SearXNG
  (`SEARCH_PROVIDER=searxng`) is optional and not needed.
- **Failed searches are visible:** each fact-check round stores `search` stats in `review.json` (queries, failed,
  sources); the storyboard shows a red warning when the check had no web results, and the judge is told so (it
  then marks uncertain claims as unverifiable). Premise checks without web results say
  "[no web results: checked from model knowledge only]".

### 3.4 Claude (optional, paid)
The Claude API is billed **separately** from Claude.ai / Claude Code subscriptions: pay-as-you-go with its own API key
and credits at console.anthropic.com. To use it for some stages: `uv sync --extra claude`, set `ANTHROPIC_API_KEY`,
then e.g. `REVIEW_LLM_PROVIDER=claude` (it has native web search) and keep everything else local.

## 4. Voice

> **License:** the official F5-TTS weights are **non-commercial** (CC-BY-NC). For a monetized channel use
> **Kokoro** (below) or **Chatterbox via ComfyUI**. See [LICENSES.md](LICENSES.md).

### 4.0 Kokoro (`TTS_PROVIDER=kokoro`): Apache-2.0, simplest
```bash
uv sync --extra claude --extra kokoro --extra whisper
```
`.env`: `TTS_PROVIDER=kokoro`, `TTS_KOKORO_VOICE=am_michael` (voice list: `hexgrad/Kokoro-82M` → VOICES.md; the first letter
is the language, e.g. `a` American, `b` British, `e` Spanish, `f` French). You can blend voices: `af_heart,am_michael`.
The model (~330 MB) downloads on first use. Tested on an RTX 3050 laptop: ~0.1 s per sentence once loaded.
No reference recording needed, but it can't clone a voice.

### 4.0b F5-TTS (local)

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

**On Windows (the AI box):** use the *ComfyUI portable* build for NVIDIA from the ComfyUI GitHub releases (or
ComfyUI Desktop). With the portable build, edit `run_nvidia_gpu.bat` and add the listen flags to the python line:
```bat
.\python_embeded\python.exe -s ComfyUI\main.py --windows-standalone-build --listen 0.0.0.0 --port 8188
```
Models go into `ComfyUI_windows_portable\ComfyUI\models\<folder>\` (same folder names as below). Install
**ComfyUI-Manager** if your build doesn't include it; you'll use it for custom nodes (GGUF loader, TTS nodes).
Check from the laptop: `curl http://<ai-box-ip>:8188/system_stats` should show the RTX 5070 Ti.

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
- **LTX-2.3 (verified 2026-11-17, now the default):** `workflows/i2v_ltx23.json` from ComfyUI's *LTX-2.3 Image to Video*
  template (models: `ltx-2.3-22b-dev-fp8`, distilled LoRA, `gemma_3_12B_it_fp4_mixed`, spatial upscaler x2).
  ~80 s per 5 s clip at 720×1280 on the 5070 Ti. Settings: `MOTION_WIDTH=720 MOTION_HEIGHT=1280 MOTION_FPS=25
  MOTION_I2V_FRAME_STEP=8 MOTION_I2V_MAX_FRAMES=153`. The template's "Duration" node now takes the frame count.
- `MOTION_SCENES=first:2` gives real motion only to the hook scenes and uses Ken Burns for the rest. This cuts render time a lot.

**Images, verified 2026-11-17:** `workflows/image_zimage_turbo.json` from the *Z-Image-Turbo* template
(`z_image_turbo_bf16` in `diffusion_models/`, `qwen_3_4b` in `text_encoders/`, `ae.safetensors` in `vae/`), 8 steps,
~12 s per 768×1344 image. The template ships with the fast preview decoder `taef1`; the pipeline copy uses the full `ae`.

### 6.4 Using any ComfyUI workflow

1. Build or open the workflow in ComfyUI (e.g. *Workflow → Browse Templates*) and run it once in the browser.
2. Pull that job from the laptop, no export needed (ComfyUI keeps every job in API format):
   ```bash
   uv run shorts comfy pull                                  # lists recent jobs
   uv run shorts comfy pull workflows/my.json --job 1        # saves one
   ```
   (Or *Workflow → Export (API)* in ComfyUI and copy the file into `workflows/`.)
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

## 7. Fonts, music & sound effects

- **Caption font:** download *Montserrat* from Google Fonts and put `Montserrat-ExtraBold.ttf` in `assets/fonts/`.
  Any bold sans works; set `CAPTION_FONT` to the font's family name.
- **Music:** put royalty-free tracks (e.g. from the YouTube Audio Library) in `assets/music/`. One is chosen per video
  and ducked under the voice automatically. Leave the folder empty for no music.
- **Sound effects:** put a few short whoosh/swish/hit sounds (0.2–0.8 s, royalty-free, e.g. from freesound.org with CC0
  or Pixabay) in `assets/sfx/`. One plays on every scene cut (`SFX_VOLUME`, `SFX_LEAD`). Empty folder = none.

**Sound effects:** see [`assets/sfx/README.md`](../assets/sfx/README.md) for the list of effects to add
(descriptions, lengths, sources and generation prompts).

## 7a. Generated music (ACE-Step 1.5)

`MUSIC_PROVIDER=comfyui` generates an instrumental track per video on the ComfyUI machine, exactly as long as the
narration. The script LLM writes the music brief (`music.tags`, `bpm`, `key` in `script.json`) to fit the topic.
- Models (from ComfyUI's *ACE-Step 1.5 XL Turbo* template): `acestep_v1.5_xl_turbo_bf16` (diffusion_models),
  `qwen_0.6b_ace15` + `qwen_4b_ace15` (text_encoders), `ace_1.5_vae` (vae). License: MIT.
- Workflow: `workflows/music_acestep15_turbo.json`, placeholders `tags lyrics bpm key seconds seed filename_prefix`.
  Lyrics are `[Instrumental]` so nothing sings over the narration.
- `MUSIC_PROVIDER=folder` picks a random track from `MUSIC_DIR` instead; `none` disables music.
- Regenerate only the music of a run: `shorts resume <run> --force music` (re-assembles too).

## 7b. Style profiles

A profile is a small env file in `styles/` that sets the channel look: `STYLE_PROMPT` and `NEGATIVE_PROMPT` for the image
model, `STYLE_GUIDE` (visual rules for the script writer), the caption font and colors, and the motion prompt suffix.

| Profile | Look | Caption font |
|---|---|---|
| `cinematic` | photographic realism | Montserrat ExtraBold |
| `comic` | inked comic panels, halftone shading (verified with Z-Image + LTX-2.3: line art stays crisp in motion) | Bangers |
| `cartoon` | flat 2D cartoon, bold outlines | Luckiest Guy |
| `animated3d` | stylized 3D animated film | Lilita One |

```bash
uv run shorts styles                          # list, → marks the active one
uv run shorts --style cartoon make "cats"     # one-off
```
Pick the default with `STYLE_PROFILE=comic` in `.env`. Precedence: real environment variables > profile > `.env`.
A run records its profile in `manifest.json`, and `resume` / `redo` / `accept` use it even if `.env` changed since
(`--style` overrides). New look: copy a file in `styles/`, edit it, and test with `shorts comfy test-image`.

Fonts: put the `.ttf` files into `assets/fonts/` (git-ignored). The four above are free (OFL / Apache-2.0): Montserrat
(github.com/JulietaUla/Montserrat), Bangers, Luckiest Guy and Lilita One (github.com/google/fonts). If a font is
missing, ffmpeg silently falls back to a default font.

## 8. Quality gates

**Script review** (stage `review`, after `script`): the LLM fact-checks every claim against **web search** results (§3.3), then scores
the script and returns a revised version. Up to `REVIEW_MAX_ROUNDS` rounds until the score is ≥ `REVIEW_MIN_SCORE`
with no incorrect claims.
- Results: `runs/<run>/review.json` (claims, verdicts, sources, problems), `script.original.json` (first draft).
- If the score stays low, the run stops with a message. Read `review.json`, optionally edit `script.json`, then
  `shorts accept <run>` and `shorts resume <run>`. Or set `REVIEW_ON_LOW_SCORE=continue`.
- With `SEARCH_PROVIDER=none` the reviewer checks facts from its own knowledge only (the log warns).

**Idea premise check** (`IDEAS_FACT_CHECK=true`): each new idea's central claim is checked with web search
before any script is written. `false` → with `IDEAS_FALSE_PREMISE=correct` (default) the script is built on the
corrected premise and debunks the myth; without a correction, or with `skip`, the idea is never picked and a run
started from it stops before the script (`auto_rejected`); `misleading` → the script writer gets the corrected
premise; `unverifiable` → the writer is told to state only what is well established. Result per run:
`premise.json`.

**Image QA** (inside `images`): a vision model checks each keyframe for text/watermarks, artifacts and prompt match.
Bad ones are re-rolled up to `IMAGE_QA_MAX_RETRIES` times, and the best candidate is kept (others in `images/candidates/`).
- Runs in batch rounds: generate all images → free ComfyUI → check all → free the LLM → re-roll the rejected ones.
  So a local model swaps in/out once per round, not once per image.
- Needs a vision-capable LLM (Qwen3.8-Flash-Next with `--mmproj`, or a small one such as Qwen-VL via
  `IMAGE_QA_LLM_PROVIDER`).

**Repeat check / script history** (§6a in ARCHITECTURE.md): every run is recorded in `history/scripts.json`.
```bash
uv run shorts history sync                      # once: add runs made before the history existed
uv run shorts history list [-s popular] [--topic "the deep ocean"]
uv run shorts history mark <run> -s popular     # or rejected; --note "why"
uv run shorts history similar "an idea text"    # or a run id; --field script compares narrations
```
- `SIMILARITY_PROVIDER=llm` (default in `.env.example`) asks your LLM, about 5–15 s per check with Qwen. Checks run
  for every new idea, and twice per run. Empty history means no LLM call.
- `SIMILARITY_PROVIDER=embeddings` is faster and needs an embeddings server, e.g. on the AI box:
  `llama-server -m nomic-embed-text-v1.5.Q8_0.gguf --embeddings --port 8081 --host 0.0.0.0` (~150 MB, runs on CPU),
  then `SIMILARITY_EMBEDDINGS_BASE_URL=http://<ai-box-ip>:8081/v1`. Tune `SIMILARITY_EMBEDDINGS_THRESHOLD`
  with `shorts history similar`.
- `SIMILARITY_PROVIDER=lexical`: no model, instant, but only catches shared words.
- `HISTORY_ON_SIMILAR=warn` notes it (`shorts status`, ⚠ in `list-ideas`); `fail` stops the run.

**Storyboard approval** (`STORYBOARD_APPROVAL=true`): every run stops after images and music, before AI motion
(~85% of the GPU time). Review it on a local web page:
```bash
uv run shorts storyboard                    # http://localhost:8765/
uv run shorts storyboard --host 0.0.0.0     # also from your phone on the home network (no password!)
```
Per run: title, script, fact-check claims with sources, music player (edit tags/bpm/key → regenerate), and one
card per scene: image with earlier candidates (click to use one; `IMAGE_CANDIDATES=2` makes extra ones up
front), the scene's voice, editable **narration** (Save & re-voice re-records only that scene and re-times
captions and music), editable image prompt + **Re-roll**, and **AI motion / Still** (still = pan/zoom, no GPU).
**Re-check facts** runs the fact-check again on the edited script. **Build preview** renders an animatic (stills +
pan/zoom + voice + music + captions) on the laptop CPU. **Approve & render** runs motion + assembly (one render
at a time, log in `runs/<run>/render.log`); **Reject** marks it rejected in the history with your note.
Terminal equivalents: `shorts approve RUN`, `shorts reject RUN --note "..."`. Any edit after approval resets it.

## 9. YouTube upload

**Current practice (2026-11-18): manual upload in YouTube Studio.** It takes about a minute per video, and API uploads
need a Google audit first (see 9.2).

### 9.1 Manual upload (current)
For each approved, finished run:
1. **Get the video:** storyboard page → *Download* (or *Show in folder*): `runs/<run>/final.mp4`.
2. **Get the text:** `runs/<run>/script.json` has `title`, `description` and `tags` (written by the script LLM).
   Quick view: `python3 -c "import json;s=json.load(open('runs/<run>/script.json'));print(s['title']);print();print(s['description']);print();print(', '.join(s['tags']))"`
3. **YouTube Studio → Create → Upload videos**, pick `final.mp4`:
   - **Title** and **Description**: paste. Keep the hashtags at the end of the description (e.g. `#shorts`).
   - **Audience: "No, it's not made for kids".** That matters for comments and ads.
   - *Show more* → **Altered content: "Yes"** when the visuals look realistic. YouTube's synthetic-content
     disclosure; comic/cartoon styles are usually clearly not real, but saying yes is the safe default.
   - *Show more* → **Tags**: paste the tag list. **Language**: the video's language. **Category**: e.g. Education.
   - Visibility: **Public**, or **Schedule** (1–2 per day at a steady time, see [PROMOTION.md](PROMOTION.md)).
4. **Pin a comment** with the source of the main fact (`runs/<run>/review.json` → the claims' `source` URLs).
5. **Record it:** `shorts history mark <run> -s uploaded --note "<youtube url>"`. Later, mark winners with
   `-s popular` (the idea writer learns from them).

A YouTube Short must be vertical and at most 3 minutes; all pipeline videos are 1080×1920 and well under that.

### 9.2 Automated upload via the YouTube Data API (later)
The code exists (`UPLOADER=youtube`, `shorts upload <run> [--publish-at ...]`): it sets title, description, tags,
category, language, made-for-kids, `containsSyntheticMedia` and scheduled publishing. Not yet tested against a real
channel.

Setup:
1. <https://console.cloud.google.com> → new project → *APIs & Services* → enable **YouTube Data API v3**.
2. *OAuth consent screen*: External, add your Google account as a **test user**.
3. *Credentials → Create credentials → OAuth client ID → Desktop app*. Download the JSON to `secrets/client_secret.json`.
4. `.env`: `UPLOADER=youtube`.
5. `uv run shorts upload runs/<run>`. The first time, a browser opens for consent and the token is cached in `secrets/`.
   With two channels on one Google account, pick the **right channel** on the consent screen.

Caveats:
- **Videos uploaded by an unverified API project are locked to _private_.** As far as we know they can't be made
  public afterwards (verify in Google's current YouTube API docs). Lifting it needs Google's **API compliance
  audit** (free; a form describing the app). Apply before switching to automated uploads.
- The default quota (10,000 units/day) allows about 6 uploads per day.
- First automated upload: upload one video **privately** to check the technical side (right channel, recognized
  as a Short, metadata, synthetic-content flag, scheduling, quality after YouTube's re-encode), then delete it.

---

## 10. First real run

```bash
uv run shorts ideas "black holes" -n 10            # brainstorm; review ideas/black-holes.json
uv run shorts make "black holes" -n 1 --until review   # draft + fact-check; read review.json
uv run shorts resume runs/<run>                     # continue: voice → … → final.mp4
uv run shorts redo runs/<run> --stage images --scene 3  # re-roll one bad scene
uv run shorts make "black holes" -n 5               # batch (stage by stage, models load once)
uv run shorts status
uv run shorts upload runs/<run>
```

Compare providers by keeping several env files (`.env.claude`, `.env.ollama`, …) and running `shorts -e .env.ollama make …`.

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| `invalid settings … extra inputs are not permitted` | Typo in a provider env var (e.g. `LLM_OLLAMA_MODLE`) |
| `cannot import provider module` | Install the extra: `uv sync --extra f5` / `whisper` / `youtube` / `claude` |
| `ComfyUI rejected the workflow … value not in list` | The model filename in the workflow doesn't match your `models/` folder; fix it in ComfyUI and re-export, or edit the JSON |
| `is a UI-format workflow` | Use *Export (API)*, not *Save* |
| I2V out of memory | Lower `MOTION_WIDTH/HEIGHT`, use GGUF experts, or `MOTION_SCENES=first:2`; failed scenes fall back to Ken Burns automatically |
| Captions in the wrong font | The font isn't in `assets/fonts/` or `CAPTION_FONT` isn't the exact family name |
| Run stopped at `review` | Script scored below `REVIEW_MIN_SCORE`: read `review.json`, then `shorts accept <run>` + `shorts resume <run>`, or `--force script` |
| `image QA disabled: … no vision support` | Load the model's `--mmproj`, or point `IMAGE_QA_LLM_PROVIDER` at a vision model |
| `no search results … ` | The network blocks some backends: reorder `SEARCH_DDGS_BACKENDS`, or use SearXNG |
| ComfyUI out of memory right after LLM stages | The LLM still holds VRAM: set `LLM_OPENAI_UNLOAD_URL` (llama-swap) or stop the server between stages |
| Voice sounds off / wrong words | Better reference clip with an exact `TTS_F5_REF_TEXT`; spell numbers out in the script prompt |
