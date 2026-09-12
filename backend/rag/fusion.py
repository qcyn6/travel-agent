def reciprocal_rank_fusion(
    rankings: list[list[str]], *, constant: int = 60
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, poi_id in enumerate(ranking, start=1):
            scores[poi_id] = scores.get(poi_id, 0.0) + 1 / (constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
