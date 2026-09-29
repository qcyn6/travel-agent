from backend.requirements import (
    DeepSeekRequirementParser,
    RequirementParser,
    RequirementParsingService,
)
from backend.requirements.langchain_agent import LangChainRequirementAgent


def build_requirement_parser(settings) -> RequirementParsingService:
    llm_parser = (
        LangChainRequirementAgent(
            settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        if getattr(settings, "planning_agent_backend", "langchain") == "langchain"
        else DeepSeekRequirementParser(
            settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    )
    return RequirementParsingService(
        llm_parser=llm_parser,
        fallback_parser=RequirementParser(),
    )
