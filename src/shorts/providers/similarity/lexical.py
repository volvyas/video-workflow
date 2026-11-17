"""TF-IDF cosine similarity over words. No model, no server, instant; catches re-used subjects and wording,
but not the same story told with different words (use `embeddings` or `llm` for that)."""

from __future__ import annotations

import math
import re
from collections import Counter

from shorts.providers.base import ProviderSettings, SimilarityProvider

_STOP = set("""
a an the and or but if then than so of to in on at for with by from into onto over under about after before
is are was were be been being am it its it's this that these those there here as not no nor do does did done
have has had having you your yours we our ours they them their he she him his her i me my mine what which who
whom whose how why when where while can could would should will shall may might must also just only very more
most much many some any every each all both few such own same other another one ones up down out off again
ever never even still yet too really thing things way like get got make made know see look
""".split())


def tokens(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[^\W_]+", text.lower()):
        if w in _STOP or len(w) < 2:
            continue
        if len(w) > 4 and w.endswith("ies"):
            w = w[:-3] + "y"
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def tfidf_cosine(query: str, docs: list[str]) -> list[float]:
    toks = [Counter(tokens(t)) for t in [query, *docs]]
    n = len(toks)
    df = Counter(w for t in toks for w in t)
    idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}

    def vec(t: Counter) -> dict[str, float]:
        return {w: (1 + math.log(c)) * idf[w] for w, c in t.items()}

    q = vec(toks[0])
    qn = math.sqrt(sum(v * v for v in q.values())) or 1.0
    out = []
    for t in toks[1:]:
        d = vec(t)
        dn = math.sqrt(sum(v * v for v in d.values())) or 1.0
        out.append(sum(v * d.get(w, 0.0) for w, v in q.items()) / (qn * dn))
    return out


class LexicalSettings(ProviderSettings):
    threshold: float = 0.35


class LexicalSimilarity(SimilarityProvider):
    name = "lexical"
    env_prefix = "SIMILARITY_LEXICAL_"
    Settings = LexicalSettings
    settings: LexicalSettings
    remote = True  # nothing to load or unload

    def scores(self, query: str, docs: list[str]) -> list[float]:
        return tfidf_cosine(query, docs)
