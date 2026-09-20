"""Runtime wiring for local attraction knowledge readiness."""

from backend.knowledge.bootstrap import CityKnowledgeBootstrapService


def build_bootstrap_service(
    *,
    attraction_repository,
    catalog_repository=None,
    knowledge_repository=None,
    settings=None,
    cache=None,
) -> CityKnowledgeBootstrapService:
    """Build a database-only readiness checker for the planning pipeline.

    Amap is intentionally not called here. Planning must first select
    candidates from the local tags/descriptions; live Amap data is added later
    by the candidate enrichment service.
    """

    del catalog_repository, knowledge_repository, settings, cache
    return CityKnowledgeBootstrapService(
        poi_service=None,
        attraction_repository=attraction_repository,
        enrichment_service=None,
        max_concurrency=1,
        prefer_external=False,
    )
