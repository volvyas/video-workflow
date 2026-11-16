# Recommendations backlog

Suggestions made during design, with their status. ✅ done · 🔄 in progress · ⬜ open · ✖ decided against

## Pipeline improvements (suggested 2026-11-16)

| # | Recommendation | Why | Status |
|---|---|---|---|
| 0 | **Version control**: `git init` and commit early and often | Nothing was versioned | ✅ repo on `main`, identity set per-repo |
| 1 | **License check before monetizing** | Official F5-TTS weights are CC-BY-NC (non-commercial). FLUX.1-dev's model license is ambiguous for commercial use | ✅ [LICENSES.md](LICENSES.md); Kokoro provider; Chatterbox via ComfyUI documented; `shorts check` warns |
| 2a | **Fact-check pass**: Claude with web search verifies every claim and rewrites wrong ones | One wrong fact brings "this is false" comments and kills trust in a facts channel | ✅ `review` stage |
| 2b | **Script critic**: score hook/pacing/payoff and rewrite below a threshold | Cheap compared with rendering a weak script | ✅ `review` stage |
| 2c | **Image check**: a vision model rejects keyframes with text, extra fingers, or a mismatch with the narration, and re-rolls them before image-to-video | Saves the most expensive GPU minutes | ✅ `IMAGE_QA_*` |
| 3a | **Loop ending**: the last line flows back into the first | Replays count as views and boost retention | ✅ script prompt |
| 3b | **Sound effects on cuts** (whoosh/hit from `assets/sfx/`) | Pattern interrupts hold attention | ✅ `SFX_DIR` (add sounds to `assets/sfx/`) |
| 3c | **Real motion on the hook** (`MOTION_SCENES=first:2`) | The first second decides the swipe | ✅ already supported |
| 3d | **Consistent visual style** per video (style field in the script; optionally a style LoRA or reference image in the workflow) | Recognizable channel look | ✅ `visual_style` field; LoRA/IP-Adapter in workflow ⬜ |
| 4 | **Analytics feedback loop**: YouTube Analytics API → retention/views per video → feed the best and worst performers into the ideas prompt | Biggest long-term lever; avoids "content farm" drift | ⬜ |
| 5a | **Unattended runs**: nightly cron/systemd timer renders the next N ideas | Hands-off production | ⬜ |
| 5b | **Auto-scheduled uploads** 1–3/day via `publishAt` | Steady cadence | ⬜ (uploader supports `--publish-at`; needs a scheduler) |
| 5c | **Notification** when a batch is ready for review | | ⬜ |
| 5d | **Local review page**: watch, approve, re-roll a scene | Faster than the command line | ⬜ |
| 6a | **Multi-language versions**: same visuals, translated script and voice, separate channel or multi-audio track | Cheap extra reach | ⬜ |
| 6b | **TikTok / Instagram Reels uploaders** | Same video, more platforms (their APIs need an app review) | ⬜ |
| 7a | **Claude cost**: Message Batches API (50% off, async, ≤24 h) for batches; prompt caching for the stable system prompts | Lower API bill | ⬜ |
| 7b | **A/B LLMs**: e.g. `IDEAS_LLM_PROVIDER=ollama` vs Claude for a week | Data-driven provider choice | ⬜ (supported via `-e` env files) |
| 7c | **YouTube policy hygiene**: vary formats (top-3, myth vs fact, "what if…"), consider your own voice as the cloning reference | YPP excludes repetitive/mass-produced content | ⬜ |

## Earlier design suggestions

| Recommendation | Status |
|---|---|
| Python orchestrator driving ComfyUI over its HTTP API (not one giant graph) | ✅ |
| Structured JSON from the LLM (scenes, prompts, metadata) | ✅ |
| Per-scene TTS so audio length drives visual timing | ✅ |
| Word-level captions from Whisper timings, script spelling kept | ✅ |
| Image → video with Wan 2.2 + 4-step LightX2V LoRA; Ken Burns fallback | ✅ (needs hardware bring-up) |
| Run stages one after another and unload models between them (16 GB VRAM) | ✅ |
| Human review gate before upload; uploads default to private | ✅ |
| Tick YouTube's synthetic-content disclosure | ✅ automatic in uploader |
| Orchestrator on the laptop, ComfyUI on the GPU box over LAN (`COMFY_URL`, `--listen`) | ✅ supported |
| TTS inside ComfyUI when the laptop is too weak (`TTS_PROVIDER=comfyui`) | ✅ |
| Unverified Google API projects upload as private only; publish in Studio or pass the API audit | ⬜ your decision |
