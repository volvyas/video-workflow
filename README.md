# shorts: automated YouTube Shorts pipeline

Topic → ideas → script → fact-check & review → voice → captions → visuals (stills + Ken Burns, or image-to-video) → `final.mp4` → YouTube.

Every external piece (LLM, TTS, image, motion, aligner, uploader) is a provider you select in `.env`.
Switching providers needs only a restart.

```bash
uv sync --extra claude --extra f5 --extra whisper --extra youtube
cp .env.example .env        # set ANTHROPIC_API_KEY, voice reference, ComfyUI workflows
uv run shorts check
uv run shorts make "black holes" -n 3
```

- [docs/SETUP.md](docs/SETUP.md): installation, which models to download, ComfyUI workflows
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): design, provider interfaces, run folders
- [docs/TASKS.md](docs/TASKS.md): task breakdown and status
- [docs/LICENSES.md](docs/LICENSES.md): what's safe for a monetized channel
- [docs/RECOMMENDATIONS.md](docs/RECOMMENDATIONS.md): improvement backlog
