"""Cosine similarity of embeddings from any OpenAI-compatible /v1/embeddings server
(llama-server --embeddings, Ollama, LM Studio, vLLM...). Understands paraphrases; vectors are cached on disk."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import httpx
from pydantic import SecretStr

from shorts.providers.base import ProviderError, ProviderSettings, SimilarityProvider


class EmbeddingsSettings(ProviderSettings):
    base_url: str = "http://127.0.0.1:8081/v1"
    model: str = "nomic-embed-text-v1.5"
    api_key: SecretStr | None = None
    threshold: float = 0.80
    prefix: str = ""  # some models want a task prefix, e.g. nomic: "clustering: "
    timeout: float = 120
    batch: int = 64
    cache_file: Path | None = Path("history/embeddings.json")


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class EmbeddingsSimilarity(SimilarityProvider):
    name = "embeddings"
    env_prefix = "SIMILARITY_EMBEDDINGS_"
    Settings = EmbeddingsSettings
    settings: EmbeddingsSettings
    remote = True

    def __init__(self, settings, app):
        super().__init__(settings, app)
        headers = {}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key.get_secret_value()}"
        self._http = httpx.Client(base_url=self.settings.base_url.rstrip("/"), headers=headers,
                                  timeout=self.settings.timeout)
        self._cache: dict[str, list[float]] | None = None

    def describe(self) -> str:
        return f"embeddings:{self.settings.model}"

    # ---------------------------------------------------------------- cache
    def _key(self, text: str) -> str:
        return hashlib.sha1(f"{self.settings.model}\0{self.settings.prefix}\0{text}".encode()).hexdigest()

    def _load_cache(self) -> dict[str, list[float]]:
        if self._cache is None:
            p = self.settings.cache_file
            self._cache = json.loads(p.read_text()) if p and p.exists() else {}
        return self._cache

    def _save_cache(self) -> None:
        p = self.settings.cache_file
        if p and self._cache is not None:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self._cache))

    # ---------------------------------------------------------------- api
    def embed(self, texts: list[str]) -> list[list[float]]:
        cache = self._load_cache()
        missing = list(dict.fromkeys(t for t in texts if self._key(t) not in cache))
        for i in range(0, len(missing), self.settings.batch):
            chunk = missing[i:i + self.settings.batch]
            try:
                r = self._http.post("/embeddings", json={"model": self.settings.model,
                                                         "input": [self.settings.prefix + t for t in chunk]})
                r.raise_for_status()
                data = sorted(r.json()["data"], key=lambda d: d["index"])
            except (httpx.HTTPError, KeyError, ValueError) as e:
                raise ProviderError(f"embeddings request to {self.settings.base_url} failed: {e}") from e
            for t, d in zip(chunk, data):
                cache[self._key(t)] = d["embedding"]
        if missing:
            self._save_cache()
        return [cache[self._key(t)] for t in texts]

    def scores(self, query: str, docs: list[str]) -> list[float]:
        q, *ds = self.embed([query, *docs])
        return [cosine(q, d) for d in ds]

    def verify(self) -> None:
        self.embed(["ping"])

    def unload(self) -> None:
        self._http.close()
        self._http = httpx.Client(base_url=self.settings.base_url.rstrip("/"), headers=dict(self._http.headers),
                                  timeout=self.settings.timeout)
