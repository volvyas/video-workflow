"""Ask the LLM whether a new idea/script tells the same story as earlier ones.

One LLM call per check: the CANDIDATES closest earlier items (ranked by a lexical score, which is weak for
paraphrases, so keep CANDIDATES generous) are listed briefly, and the model names only the ones that overlap, so its
answer stays short. Best understanding of "same story, new words"; a few seconds per check on a big local model."""

from __future__ import annotations

from pydantic import BaseModel, Field

from shorts.prompts import render
from shorts.providers.base import ProviderError, ProviderSettings, SimilarityProvider
from shorts.providers.similarity.lexical import tfidf_cosine


class SameStory(BaseModel):
    index: int = Field(description="Number of the earlier item")
    score: int = Field(description="0 = different story, 5 = same subject but different fact or angle, "
                                   "10 = the same story or fact")
    reason: str


class SimilarityJudgement(BaseModel):
    items: list[SameStory] = Field(description="Only earlier items scoring 4 or more; empty if none")


class LLMSimilaritySettings(ProviderSettings):
    threshold: float = 0.7  # LLM score / 10
    candidates: int = 30  # at most this many earlier items (closest first) go to the LLM
    prefilter: float = 0.0  # lexical score below which an item is not sent (0 = always send the closest)
    max_chars: int = 400  # each earlier item is shortened to this many characters


class LLMSimilarity(SimilarityProvider):
    name = "llm"
    env_prefix = "SIMILARITY_LLM_"
    Settings = LLMSimilaritySettings
    settings: LLMSimilaritySettings
    remote = True  # the LLM provider itself is unloaded by the pipeline

    def scores(self, query: str, docs: list[str]) -> list[float]:
        if self.llm_factory is None:
            raise ProviderError("llm similarity needs an LLM provider (created through Context)")
        s = self.settings
        lex = tfidf_cosine(query, docs)
        closest = sorted(range(len(docs)), key=lambda i: -lex[i])
        picked = [i for i in closest if lex[i] >= s.prefilter][:s.candidates]
        out = [0.0] * len(docs)
        if not picked:
            return out
        earlier = "\n".join(f"{n + 1}. {docs[i][:s.max_chars]}" for n, i in enumerate(picked))
        judgement = self.llm_factory().generate_json(
            system=render("similarity_system", self.app.prompts_dir),
            prompt=render("similarity_user", self.app.prompts_dir, new=query, earlier=earlier),
            schema=SimilarityJudgement)
        for item in judgement.items:
            if 1 <= item.index <= len(picked):
                out[picked[item.index - 1]] = max(0, min(10, item.score)) / 10
        return out
