"""Deterministic attraction matching for explicit user interests.

BM25 and embeddings are useful broad retrieval signals, but a travel request
should still visibly respect labels such as ``自然``、``历史文化`` and ``摄影``
when either external model is unavailable.  This module turns the user's
interest and preference text into a small, explainable ranking signal over the
attraction name, category and database-owned description.
"""

from backend.models.retrieval import AttractionDocument
from backend.rag.bm25 import build_preference_query, tokenize


_ALIASES = {
    "自然": (
        "自然风光",
        "山水",
        "生态",
        "森林",
        "湿地",
        "湖泊",
        "湖",
        "山",
        "海滨",
        "草原",
        "峡谷",
        "瀑布",
        "园林",
    ),
    "历史文化": ("历史", "文化", "人文", "遗址", "古建筑", "博物馆"),
    "古城": ("古镇", "古城", "古村", "历史街区", "遗址"),
    "古镇": ("古城", "古村", "历史街区", "传统村落"),
    "人文": ("历史", "文化", "博物馆", "纪念馆", "故居", "寺", "建筑"),
    "摄影": ("拍照", "取景", "风光", "夜景", "观景"),
    "美食": ("美食", "餐饮", "小吃", "老字号", "街区"),
    "休闲": ("轻松", "慢游", "慢节奏", "散步", "漫步", "公园", "度假"),
    "散步": ("漫步", "步行", "步行游览", "公园", "街区"),
}

_STOP_WORDS = frozenset(
    {
        "希望",
        "适合",
        "可以",
        "能够",
        "安排",
        "行程",
        "旅行",
        "旅游",
        "景点",
        "游览",
        "尽量",
        "不要",
        "比较",
        "一点",
        "我们",
        "想去",
        "喜欢",
    }
)
_MEANINGFUL_SINGLE_CHAR = frozenset({"山", "湖", "海", "岛", "寺", "塔", "园"})


def rank_interest_matches(
    documents: list[AttractionDocument],
    interests: list[str],
    preference_text: str | None,
    *,
    limit: int = 10,
) -> list[tuple[str, float]]:
    """Return database documents with a positive, explainable interest score.

    Direct matches in the attraction name and category weigh more than
    matches in the description.  The description is still important because
    it is where manually prepared interest labels and experiences normally
    live.  An empty result means that the normal catalog order should be used
    as the neutral fallback rather than inventing a preference.
    """

    if not documents or limit <= 0:
        return []
    terms = _interest_terms(interests, preference_text)
    if not terms:
        return []

    direct_text = "".join(item.strip().lower() for item in interests if item.strip())
    scored: list[tuple[int, str, float]] = []
    for index, document in enumerate(documents):
        name = document.name.lower()
        category = (document.category or "").lower()
        tags = " ".join(document.interest_tags).lower()
        description = document.description.lower()
        score = 0.0
        for term in terms:
            if term in _STOP_WORDS:
                continue
            direct_weight = 2.0 if term in direct_text else 1.0
            if term in name:
                score += 6.0 * direct_weight
            if term in category:
                score += 4.0 * direct_weight
            if term in tags:
                score += 8.0 * direct_weight
            if term in description:
                score += 2.0 * direct_weight
        if score > 0:
            scored.append((index, document.poi_id, score))

    scored.sort(key=lambda item: (-item[2], item[0], item[1]))
    return [(poi_id, score) for _, poi_id, score in scored[:limit]]


def _interest_terms(interests: list[str], preference_text: str | None) -> list[str]:
    interest_text = " ".join(item.strip().lower() for item in interests if item.strip())
    preference = (preference_text or "").strip().lower()
    combined = f"{interest_text} {preference}".strip()
    if not combined:
        return []

    query = build_preference_query(interests, preference_text)
    extra_terms: list[str] = []
    for canonical, aliases in _ALIASES.items():
        if canonical in combined or any(alias in combined for alias in aliases):
            extra_terms.extend((canonical, *aliases))
    tokens = tokenize(f"{query} {' '.join(extra_terms)}")
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        token = token.strip().lower()
        if not token or token in seen or token in _STOP_WORDS:
            continue
        if len(token) < 2 and token not in _MEANINGFUL_SINGLE_CHAR:
            continue
        seen.add(token)
        result.append(token)
    return result
