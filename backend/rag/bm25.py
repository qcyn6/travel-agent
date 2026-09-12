import math
import re
from collections import Counter

import jieba

from backend.models.retrieval import AttractionDocument


_EXPANSIONS = {
    "拍照": ("摄影", "取景"),
    "摄影": ("拍照", "取景"),
    "轻松": ("休闲", "慢游", "慢节奏"),
    "休闲": ("轻松", "慢游", "慢节奏"),
    "散步": ("漫步", "步行", "步行游览"),
    "历史文化": ("历史", "文化", "人文", "遗址"),
    "古城": ("古镇", "历史", "文化", "遗址"),
}


def build_preference_query(interests: list[str], preference_text: str | None) -> str:
    parts = [item.strip() for item in interests if item.strip()]
    if preference_text and preference_text.strip():
        parts.append(preference_text.strip())
    original = " ".join(parts)
    expanded = list(parts)
    for keyword, synonyms in _EXPANSIONS.items():
        if keyword in original:
            expanded.extend(synonyms)
    return " ".join(dict.fromkeys(expanded))


def tokenize(text: str) -> list[str]:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", text.lower())
    return [token.strip() for token in jieba.lcut(normalized) if token.strip()]


class BM25Retriever:
    def __init__(self, *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

    def rank(
        self,
        documents: list[AttractionDocument],
        query: str,
        *,
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        if not documents or not query.strip():
            return []
        corpus = [tokenize(document.index_text()) for document in documents]
        query_tokens = tokenize(query)
        average_length = sum(map(len, corpus)) / len(corpus)
        document_frequency = Counter(
            token for tokens in corpus for token in set(tokens)
        )
        scores: list[tuple[str, float]] = []
        for document, tokens in zip(documents, corpus, strict=True):
            frequencies = Counter(tokens)
            score = 0.0
            for token in query_tokens:
                frequency = frequencies[token]
                if not frequency:
                    continue
                found_in = document_frequency[token]
                inverse_frequency = math.log(1 + (len(corpus) - found_in + 0.5) / (found_in + 0.5))
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * len(tokens) / max(average_length, 1)
                )
                score += inverse_frequency * frequency * (self.k1 + 1) / denominator
            if score > 0:
                scores.append((document.poi_id, score))
        return sorted(scores, key=lambda item: (-item[1], item[0]))[:limit]
