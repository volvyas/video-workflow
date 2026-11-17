# Architecture

Automated pipeline that turns a **topic** into finished **YouTube Shorts** (1080×1920, 15–60 s).

```
topic ─► Ideas ─► Script ─► Review ─► Voice ─► Align ─► Images ─► Motion ─► Assemble ─► (human) ─► Upload
          LLM      LLM      LLM+web    TTS     Aligner  Image+QA   Motion    FFmpeg                Uploader
```

Every box with a provider name under it is a **pluggable provider**. Which implementation runs is
chosen in `.env` — switching providers means editing `.env` and re-running. No code changes, no rebuild.

---

## 1. Design principles

1. **Providers behind interfaces.** Each external capability (LLM, TTS, image gen, motion, alignment,
   upload) is an abstract base class in `shorts/providers/base.py`. Stages only talk to the interface.
2. **Selection by config.** `.env` holds `LLM_PROVIDER=claude`, `TTS_PROVIDER=f5`, … The registry maps
   those names to classes. A value like `mypkg.module:MyProvider` loads an external class, so you can add
   a provider without touching this repo.
3. **Provider settings are namespaced.** Each provider reads only the env vars with its prefix
   (`LLM_CLAUDE_*`, `LLM_OLLAMA_*`, `TTS_F5_*`, `IMAGE_COMFY_*`, `MOTION_I2V_*`, …) into a typed pydantic model.
   Unknown keys under a prefix (typos) and bad values fail at startup, before anything runs. Prefixes start with the
   provider kind so they never clash with variables other tools use (`OLLAMA_HOST`, `CLAUDE_CODE_*`, …).
   Shared ComfyUI connection settings are global: `COMFY_URL`, `COMFY_TIMEOUT`.
4. **Every stage writes files and can be resumed.** A run is a folder (`runs/<run_id>/`). Each stage
   writes its outputs there plus a `manifest.json`. A stage whose outputs already exist is skipped, so a crash
   or a single bad scene doesn't mean starting over. `--force <stage>` re-runs one stage.
5. **Batches run stage by stage.** With N videos, the pipeline runs *all* TTS, then *all* images,
   then *all* motion clips. Each GPU model loads once per batch instead of once per video (this
   matters on 16 GB VRAM).
6. **VRAM hygiene.** Providers implement `unload()`. The pipeline calls it between stages: Ollama
   `keep_alive=0`, ComfyUI `/free`, F5 model deleted and `torch.cuda.empty_cache()`.
7. **Mock providers everywhere.** Each interface has a `mock` implementation (no GPU, no network), so the
   whole pipeline can be tested end to end on any machine.

---

## 2. Package layout

```
src/shorts/
  config.py            # .env loading, AppSettings, provider-settings extraction
  context.py           # settings + lazily created/cached providers, unload()
  registry.py          # name -> class registry, factory, external "module:Class" loading
  models.py            # Pydantic data models: Idea, Script, Scene, WordTiming, RunManifest
  run.py               # Run folder handling, manifest, stage outputs/dependencies, invalidation
  pipeline.py          # Orchestration: stage ordering, batching, unload between stages
  stages/              # ideas.py script.py voice.py align.py visuals.py (images + motion)
  cli.py               # `shorts` command (typer)
  prompts/             # LLM prompt templates (plain text, editable without code)
  providers/
    base.py            # ABCs: LLMProvider, TTSProvider, ImageProvider, MotionProvider, Aligner, Uploader
    llm/      claude.py  ollama.py  openai_compat.py  mock.py
    tts/      f5.py  mock.py
    image/    comfyui.py  mock.py
    motion/   kenburns.py  comfyui_i2v.py  mock.py
    align/    faster_whisper.py  proportional.py
    upload/   youtube.py  none.py
  comfy/
    client.py          # ComfyUI HTTP API client (queue, poll, upload image, download outputs, /free)
    template.py        # Workflow JSON placeholder substitution
  media/
    ffmpeg.py          # locate ffmpeg/ffprobe, run commands, probe durations
    captions.py        # WordTimings -> styled .ass subtitles (word-by-word highlight)
    assemble.py        # clips + voice + music + captions -> final.mp4
workflows/             # ComfyUI workflows in API format with {{placeholders}}
assets/                # voice reference audio, music, fonts
runs/                  # pipeline output (git-ignored)
```

---

## 3. Provider interfaces

All in `shorts/providers/base.py`. Simplified signatures:

```python
class LLMProvider(Provider):
    supports_vision: bool                     # can take images=[...]
    supports_web_search: bool                 # implements research()
    def generate_json(self, *, system: str, prompt: str, schema: type[T],
                      images: list[Path] | None = None) -> T: ...
    def research(self, *, system: str, prompt: str) -> str: ...   # web-grounded free text (Claude)
    # Returns a validated pydantic object. Each implementation uses its native
    # structured-output mechanism (Claude output_config json_schema, Ollama `format`,
    # OpenAI-compatible `response_format`) and re-validates with pydantic.

class TTSProvider(Provider):
    def synthesize(self, text: str, out_path: Path) -> float: ...   # returns duration (s)

class ImageProvider(Provider):
    def generate(self, *, prompt: str, negative: str, width: int, height: int,
                 seed: int, out_path: Path) -> Path: ...

class MotionProvider(Provider):
    def animate(self, *, image: Path, prompt: str, camera: str, duration: float,
                seed: int, out_path: Path) -> Path: ...
    # sizes/fps come from global settings (MOTION_* for I2V, VIDEO_* for Ken Burns)

class Aligner(Provider):
    def align(self, audio: Path, text: str) -> list[WordTiming]: ...

class Uploader(Provider):
    def upload(self, video: Path, meta: VideoMeta) -> str: ...      # returns URL/ID

class SimilarityProvider(Provider):
    threshold: float                          # from the provider's THRESHOLD setting
    def scores(self, query: str, docs: list[str]) -> list[float]: ...  # 0..1 per earlier text

class Provider(ABC):
    name: ClassVar[str]                       # registry key, e.g. "claude"
    Settings: ClassVar[type[BaseModel]]       # typed settings model
    env_prefix: ClassVar[str]                 # e.g. "CLAUDE_"
    def unload(self) -> None: ...             # free VRAM / connections (default no-op)
```

### Implementations (planned)

| Kind   | Name             | Notes |
|--------|------------------|-------|
| LLM    | `openai_compat`  | **Default.** Any OpenAI-compatible server: llama.cpp / llama-swap (Qwen3.8-Flash-Next on the GPU box), LM Studio, vLLM. Optional `UNLOAD_URL` hook frees VRAM before ComfyUI stages |
| LLM    | `claude`         | Anthropic SDK, structured JSON outputs, default model `claude-opus-5-5`, server-side refusal fallback |
| LLM    | `ollama`         | Local. Native `/api/chat` with JSON-schema `format`, `keep_alive=0` on unload |
| LLM    | `mock`           | Deterministic canned JSON |
| TTS    | `f5`             | F5-TTS voice cloning from a reference clip, runs in-process on the local GPU |
| TTS    | `kokoro`         | Kokoro-82M, Apache-2.0, preset voices (no cloning); fast even on CPU |
| TTS    | `comfyui`        | Any TTS workflow in ComfyUI (starter: ComfyUI-F5-TTS node); the voice model runs on the ComfyUI machine |
| TTS    | `mock`           | Silent WAV of estimated length |
| Image  | `comfyui`        | Any text-to-image workflow (Flux, SDXL, Qwen-Image…) from `workflows/*.json` |
| Image  | `mock`           | Gradient image with the prompt text drawn on it |
| Motion | `kenburns`       | **Mode A**: smooth pan/zoom on the still (Pillow + FFmpeg), CPU only |
| Motion | `comfyui_i2v`    | **Mode B**: image-to-video workflow (Wan 2.2 I2V, LTX…) from `workflows/*.json` |
| Align  | `faster_whisper` | Word-level timestamps from the actual audio |
| Align  | `proportional`   | No deps: spreads words over the audio duration by character count |
| Search | `ddgs`, `searxng`, `mock` | Web search for the fact-check when the LLM has none (local models) |
| Similarity | `llm`        | **Default.** One LLM call per check: the 30 closest earlier entries are listed, the model names the ones telling the same story (score/10). Understands paraphrases |
| Similarity | `embeddings` | Cosine of vectors from any OpenAI-compatible `/v1/embeddings` server (e.g. a second llama-server with nomic-embed); cached in `history/embeddings.json` |
| Similarity | `lexical`    | TF-IDF word overlap, no model, instant; misses paraphrases (fallback / tests) |
| Upload | `youtube`        | YouTube Data API v3, OAuth, private/scheduled uploads |
| Upload | `none`           | Leaves the video in the run folder |

### Mode A / Mode B / fallback

`MOTION_PROVIDER` selects the primary motion provider and `MOTION_FALLBACK` an optional fallback.
If the primary provider throws on a scene (OOM, timeout, ComfyUI error), that scene falls back to the
secondary (usually `kenburns`). The failure is logged in the manifest.

`MOTION_SCENES` (`all` | `first:N` | `every:N`) can limit expensive I2V to some scenes, e.g. only the
hook scene gets real motion and the rest use Ken Burns.

### Per-stage overrides

`IDEAS_LLM_PROVIDER` / `SCRIPT_LLM_PROVIDER` override `LLM_PROVIDER` per stage. For example, a cheap
local model brainstorms and Claude writes. Any setting can also be overridden for one run with
`shorts --env-file .env.experiment …`, which makes A/B comparison of providers simple.

---

## 4. Configuration

- `.env` is loaded at startup (path overridable with `--env-file`). Real environment variables win over the file.
- `AppSettings` (in `config.py`) holds the global keys: provider selection, video format, paths, caption style.
- Provider settings: the registry builds a dict from all env vars starting with the provider's
  `env_prefix`, strips the prefix, lowercases the rest, and validates it against `Provider.Settings`.
  `LLM_CLAUDE_MODEL=claude-opus-5-5` → `ClaudeSettings.model`.
- `.env` values are also exported to the process environment, so SDKs that read their own variables
  (`ANTHROPIC_API_KEY`, `HF_TOKEN`) pick them up.
- `.env.example` documents every key and is the reference.

---

## 5. Data model & run folder

```
runs/2026-11-16_1530_space-facts-black-holes/
  manifest.json         # status per stage, provider used, seed, errors, timings, per-scene motion provider
  idea.json             # Idea
  script.json           # Script: title, description, tags, hook, scenes[]
  audio/scene_01.wav …  # per-scene voice
  audio/voice.wav       # concatenated narration
  timings.json          # SceneTiming[]: start, audio duration, slot (audio + gap) per scene
  words.json            # WordTiming[] across the whole narration (global timeline)
  images/scene_01.png …
  clips/scene_01.mp4 …  # motion clips (provider-native size/fps)
  segments/             # clips normalized to 1080x1920@30 and fitted to scene length
  captions.ass
  final.mp4
```

Topic-level idea pools live in `ideas/<topic-slug>.json`. The `ideas` command appends to them and marks
ideas as used, so the same idea is never produced twice. Across topics and paraphrases, the script history (§6a)
catches repeats.

**Scene** (from the script LLM):
`narration`, `image_prompt`, `motion_prompt`, `camera` (Ken Burns hint: `zoom_in | zoom_out | pan_left | pan_right | pan_up | pan_down`), `on_screen_text` (optional).

**Why TTS runs per scene:** each scene's audio length *is* that scene's visual duration. This keeps visuals
in sync with the narration without any heuristics. The voice files are concatenated for the final mix and
caption alignment.

---

## 6. Stage details

| Stage    | Input → Output | Notes |
|----------|----------------|-------|
| ideas    | topic → `ideas/<slug>.json` | LLM generates N ideas (hook, angle, why it's watchable) and dedupes against existing ones |
| script   | idea → `script.json` | Target length `VIDEO_TARGET_SECONDS`; 6–12 scenes of 2.5–6 s; strong hook in the first 2 s; visual prompts carry `STYLE_PROMPT` |
| review   | script → `review.json`, revised `script.json` (draft kept as `script.original.json`) | 1) `research()` fact-check with web search (Claude) 2) structured critique: claims with verdicts, score, hook score, problems, revised script. Repeats up to `REVIEW_MAX_ROUNDS` until score ≥ `REVIEW_MIN_SCORE` with no incorrect claims; otherwise stops the run (`shorts accept` to override) or continues (`REVIEW_ON_LOW_SCORE`) |
| voice    | scenes → `audio/*.wav` | Per-scene TTS, then concatenated with `SCENE_GAP_MS` of silence |
| align    | voice.wav + text → `words.json` | Word timings for captions |
| images   | image prompts → `images/*.png` | Prompt = scene prompt + script `visual_style` + `STYLE_PROMPT`. With `IMAGE_QA_ENABLED`, a vision LLM checks each image (matches prompt, no text, no artifacts, score); failures are re-rolled with new seeds, and the best candidate is kept (others in `images/candidates/`); verdicts in the manifest |
| motion   | images → `clips/*.mp4` | Primary/fallback per scene; target duration = scene audio length |
| assemble | clips, voice, music, sfx, words → `final.mp4` | Scale/crop to 1080×1920 at 30 fps; fit clip to scene length (trim, or slow ≤1.25× then freeze the last frame); duck music under the voice; a sound effect from `SFX_DIR` on every cut; loudness-normalize to −14 LUFS; burn ASS captions |
| upload   | final.mp4 + meta → URL | Only with `--upload` or `AUTO_UPLOAD=true`; default privacy `private`; sets the synthetic-content flag |

**Review gate:** by default the pipeline stops after `assemble`. `shorts upload <run>` publishes a video after
you've watched it. Single scenes can be regenerated with `shorts redo <run> --stage images --scene 3`
(re-runs motion and assembly for that scene). `shorts resume <run> --force voice` deletes the outputs of that stage and
its dependents (`run.py: DEPENDENTS`) and rebuilds them.

---

## 6a. Script history & repeat check

`history.py`, file `HISTORY_FILE` (default `history/scripts.json`, outside `runs/` so it survives cleaning runs).
One entry per run: idea (title, hook, angle), final script title + narration, review score, upload URL, current
status and a status log (`status`, `at`, `by: auto|human`, `note`).

| Status | Set by | When |
|---|---|---|
| `draft` | auto | script written (again after a successful review or `shorts accept`) |
| `auto_rejected` | auto | review gate stopped the run (`NeedsHumanReview`) or `HISTORY_ON_SIMILAR=fail` stopped it |
| `rejected` | you | `shorts history mark RUN -s rejected --note "…"` |
| `created` | auto | `assemble` produced `final.mp4` |
| `uploaded` | auto | `shorts upload` |
| `popular` | you | `shorts history mark RUN -s popular` (later: from YouTube Analytics, recommendation 4) |

Automatic updates never overwrite a manual status. Hooks: `pipeline.execute` calls `history.record` after a stage
finishes and `history.record_failure` after a quality-gate failure; `shorts upload` / `accept` call it too.

**Where similarity is checked** (provider `SIMILARITY_PROVIDER`, threshold per provider):
1. `shorts ideas` / `make` generating ideas: the ideas prompt gets the topic's history (avoid these) and the popular
   entries of all topics (more like these, different subject). Each new idea is checked; similar ones get `similar_to`
   in the idea pool, are shown with ⚠, and `make` doesn't pick them automatically (`--idea ID` still can).
2. Script stage, before the LLM writes: the run's idea vs the history's ideas.
3. Script stage, after writing: the narration vs the history's narrations.

2 and 3 write `similar` notes into the manifest (shown by `shorts status`) and log a warning; with
`HISTORY_ON_SIMILAR=fail` they stop the run instead (status `auto_rejected`).

## 6b. Fact-check research flow

`stages/research.py`: if the review LLM has native web search (`supports_web_search`, Claude), it calls `research()`.
Otherwise, when `SEARCH_PROVIDER` is set: the LLM writes `REVIEW_SEARCH_QUERIES` queries (`SearchQueries` schema) →
the search provider returns `REVIEW_SEARCH_RESULTS` hits per query → the top `REVIEW_FETCH_PAGES` pages are fetched and
reduced to text → everything goes into the review prompt, which must cite a source URL per claim.

## 6c. GPU sharing (local LLM + ComfyUI on one 16 GB card)

- Stages run batch-wide, so all LLM work (script, review) finishes before any ComfyUI work. `ctx.unload("llm")` runs
  after `review`.
- `images` is a **batch stage** (`pipeline._BATCH`). It works in rounds across all runs: generate → unload image provider →
  QA with the LLM → unload the LLM → re-roll rejects. Unloads are skipped when the QA LLM is `remote` (hosted API).
- What "unload" does depends on the provider: Ollama `keep_alive=0`, OpenAI-compatible `UNLOAD_URL` (e.g. llama-swap
  `/unload`), ComfyUI `/free`, in-process models deleted plus `torch.cuda.empty_cache()`.

## 7. ComfyUI integration

- **Client** (`comfy/client.py`): `POST /prompt` → `prompt_id`; poll `GET /history/{id}`; collect every output
  file under `outputs.*` and download it via `GET /view`; `POST /upload/image` for I2V inputs; `POST /free` to unload models.
  `COMFY_URL` may point to another machine on the LAN.
- **Workflow templates** (`comfy/template.py`): workflows are exported from ComfyUI with *Export (API)*. Values are
  replaced with placeholders like `"{{prompt}}"`, `"{{negative}}"`, `"{{seed}}"`, `"{{width}}"`, `"{{height}}"`,
  `"{{image}}"`, `"{{frames}}"`, `"{{fps}}"`, `"{{filename_prefix}}"`. A placeholder that is the *whole*
  string is replaced with a typed value (int stays int). Placeholders inside longer strings are substituted as text.
  `shorts comfy inspect <wf>` lists nodes and inputs, and `shorts comfy mark <wf> 6.text=prompt 3.seed=seed`
  writes placeholders for you, so there's no hand-editing JSON.
- **Audio:** the same client uploads the TTS reference clip (once per process, content-hashed name) and downloads
  `SaveAudio` output (FLAC), which is converted to WAV for the pipeline.
- **Changing the model = changing the workflow file**, e.g. `COMFY_IMAGE_WORKFLOW=workflows/image_flux.json` →
  `workflows/image_sdxl.json`. Model names, LoRAs and samplers all live in the workflow.

---

## 8. Error handling & observability

- Provider calls retry with backoff where it makes sense (network LLMs, ComfyUI queue).
- Each stage records `started_at`, `finished_at`, `provider`, `error` in the manifest.
- `--verbose` logs every provider call. LLM token usage is logged per call so you can compare cost between providers.

## 9. Out of scope for v1 (possible later)

Background-music generation, a web UI, analytics feedback loop (using YouTube stats to steer ideas),
multi-language dubbing, avatar/lip-sync, TikTok/Instagram uploaders (they slot in as `Uploader`s).
