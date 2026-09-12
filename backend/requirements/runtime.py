from backend.requirements import (
    DeepSeekRequirementParser,
    RequirementParser,
    RequirementParsingService,
)


def build_requirement_parser(settings) -> RequirementParsingService:
    return RequirementParsingService(
        llm_parser=DeepSeekRequirementParser(
            settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        ),
        fallback_parser=RequirementParser(),
    )
