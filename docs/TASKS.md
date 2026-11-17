# Tasks

Status: ✅ done · 🔄 in progress · ⬜ todo

Each task ends in something runnable or testable. Tasks are in dependency order.

| #  | Task | Status | Done when |
|----|------|--------|-----------|
| 1  | **Scaffold & core**: pyproject, config loader, provider base classes, registry (incl. external `module:Class`), data models, run folder + manifest | ✅ | Unit tests for config/registry pass |
| 2  | **LLM providers**: `claude`, `ollama`, `openai_compat`, `mock` behind `generate_json` | ✅ ¹ | Mock tested; real providers smoke-testable with `shorts llm-test` |
| 3  | **Ideas & Script stages**: editable prompt templates, idea pool with dedupe, script with scenes | ✅ | `shorts ideas --topic …` and `shorts script` produce valid JSON |
| 4  | **TTS**: `f5` (voice cloning) and `mock`; per-scene synthesis and concatenation | ✅ ² | `voice` stage writes per-scene WAVs + `voice.wav` |
| 5  | **Alignment & captions**: `faster_whisper`, `proportional`; ASS generator with word-by-word highlight | ✅ ² | `captions.ass` renders correctly with mock audio |
| 6  | **ComfyUI client & templates**: queue/poll/download/upload/free; placeholder substitution; `comfy inspect` / `comfy mark` commands | ✅ ² | Template unit tests pass; client works against a live ComfyUI |
| 7  | **Images**: `comfyui` and `mock` image providers | ✅ ² | `images` stage produces PNGs |
| 8  | **Motion Mode A**: `kenburns` (Pillow-rendered smooth pan/zoom → ffmpeg) | ✅ | Clip per scene with the requested duration |
| 9  | **Motion Mode B**: `comfyui_i2v` (Wan 2.2 / LTX workflow), fallback provider, `MOTION_SCENES` policy | ✅ ² | Failing scenes fall back to Ken Burns; logged in manifest |
| 10 | **Assembly**: normalize clips, fit to scene length, concat, voice + ducked music, burn captions | ✅ | `final.mp4` 1080×1920 from the mock pipeline |
| 11 | **Pipeline & CLI**: batch stage-by-stage orchestration, unload between stages, resume/`--force`, `redo` single scene | ✅ | `shorts make --topic … --count 2` with mock providers end to end |
| 12 | **YouTube uploader**: OAuth flow, upload with metadata, privacy/schedule, synthetic-content flag | 🔄 ² | `shorts upload <run>` works with a real channel |
| 15 | **ComfyUI TTS provider**: `TTS_PROVIDER=comfyui`, generic workflow with `{{text}}`/`{{ref_audio}}`/`{{ref_text}}`, starter workflow for ComfyUI-F5-TTS | ✅ ² | Simulated-ComfyUI test passes; real node run on the GPU box |
| 16 | **Licenses**: LICENSES.md, `license_note` warnings in `shorts check`, Kokoro provider (Apache-2.0); Chatterbox via ComfyUI (pip pins torch 2.6 → no RTX 50xx) | ✅ | Kokoro verified on real GPU |
| 17 | **Quality gates**: `review` stage (web-search fact-check + critique + revision rounds, `accept`), image QA with re-rolls, vision input for all LLM providers | ✅ ¹ | Mock e2e tests pass; live Claude run pending |
| 18 | **Retention**: loop ending + pattern-interrupt rules, per-video `visual_style`, SFX on cuts | ✅ | Mock e2e tests pass |
| 19 | **Local LLM default** (2026-11-17): openai_compat → Qwen3.8-Flash-Next via llama.cpp/llama-swap, unload hook, `extra_body`; search providers (ddgs, searxng) + local fact-check research; image QA as batch rounds to limit model swaps | ✅ ² | Mock tests + live DDG search pass; run against your LLM server pending |
| 13 | **Real-hardware bring-up** on the 5070 Ti: install F5/ComfyUI models, export real workflows, tune defaults | ⬜ | First real Short produced |
| 14 | **Evaluation helpers**: `--env-file` A/B runs, per-run cost/time report in manifest | ⬜ | `shorts report` compares runs |

¹ Claude provider verified offline (request shape accepted by the SDK, response parsing). Ollama / OpenAI-compatible
  are not yet run against a live server. First live checks: `shorts llm-test`.
² Code complete, but needs the GPU machine / real services for verification (task 13): F5-TTS, faster-whisper,
  ComfyUI client + the starter workflows `image_*.json` / `i2v_wan22.json`, YouTube OAuth.

Verified on the dev laptop with mock providers: `make` for 2 videos end to end (1080×1920, 30 fps, 48 kHz AAC, burned
captions, ducked music), motion fallback, `redo` of one scene, `resume --force`, `status`. 20 unit tests pass.

## Bring-up log

**2026-11-17: first real run (AI box <ai-box-ip>, laptop orchestrator)**
- LLM: llama-server b11370, Qwen3.8-Flash-Next **UD-IQ1_S** (72.5 GB, 1.56 bpw), ctx 32768, no vision (`--mmproj` not loaded).
  Speed ~9 tok/s generation, ~100–130 tok/s prompt. Thinking on burns 1500+ tokens per call → **off by default**.
- Full run with real LLM + DuckDuckGo search + Kokoro + faster-whisper (laptop GPU), mock images, Ken Burns: ✅ `final.mp4`.
  Timing: ideas 1 min, script 1.5 min, review 2 rounds ≈ 7 min, everything after the LLM stages ≈ 40 s.
- The fact-check worked: round 1 caught a false hook claim and a wrong number (fixed using a PNAS source), round 2 scored 8/10.
- Bugs found and fixed: `strict_schema` dropped fields named `title`; faster-whisper + PyAV 19 decode error;
  ctranslate2 couldn't find cuBLAS (now preloaded from torch's wheels, CPU fallback); narration too short (55 words for 40 s)
  → word-range enforcement.
- Open: ComfyUI not installed yet; load `--mmproj` to enable the image check; F5 voice clip not recorded (Kokoro meanwhile).

## Next up (task 13: bring-up on the 5070 Ti)
1. `uv sync --extra claude --extra f5 --extra whisper`, `cp .env.example .env`, add API key → `shorts llm-test`
2. Record the reference voice → `shorts check`
3. Download Flux + Wan 2.2 files (SETUP §6) → `shorts comfy test-image …`, `shorts comfy test-motion …`
4. `shorts make "<topic>" -n 1 --until script`, review, `shorts resume`
5. Tune: STYLE_PROMPT, caption look, MOTION_SCENES, TTS speed

## Notes / decisions log

- Python 3.12, `uv`. Heavy dependencies (torch via f5-tts, faster-whisper) are optional extras, so the orchestrator
  installs on a machine without a GPU.
- Claude default model `claude-opus-5-5` with server-side refusal fallback (`fallbacks: "default"`) enabled; set
  `CLAUDE_FALLBACKS=off` to disable.
- TTS runs per scene: scene audio length drives visual timing.
- Ken Burns renders frames with Pillow (sub-pixel smooth) instead of ffmpeg `zoompan` (which jitters).
  ~2.5 s per second of video at 1080×1920 with bicubic on the laptop CPU; `MOTION_KENBURNS_RESAMPLE=bilinear` halves it.
- Provider env prefixes start with the kind (`LLM_CLAUDE_`, `TTS_F5_`, …) so they never clash with other tools' variables.
- torch is pulled from the CUDA 12.8 index (pyproject `tool.uv.sources`) for RTX 50xx support.
- Starter ComfyUI workflows are hand-written API JSON based on the official templates. If ComfyUI rejects one,
  load the official template, Export (API), and run `shorts comfy mark` (SETUP §6.4).
