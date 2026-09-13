from backend.requirements.llm import RequirementLLMError
from backend.requirements.parser import canonicalize_interests


class RequirementParsingService:
    def __init__(self, *, llm_parser, fallback_parser):
        self.llm_parser = llm_parser
        self.fallback_parser = fallback_parser

    async def parse(self, text: str):
        fallback = self.fallback_parser.parse(text)
        try:
            parsed = await self.llm_parser.parse(text)
        except RequirementLLMError:
            return fallback
        interests = canonicalize_interests(parsed.interests + fallback.interests)
        return parsed.model_copy(
            update={
                "province": parsed.province or fallback.province,
                "cities": parsed.cities or fallback.cities,
                "days": parsed.days or fallback.days,
                "interests": interests,
                "preference_text": parsed.preference_text or fallback.preference_text,
                "start_date": parsed.start_date or fallback.start_date,
                "transport_mode": (
                    fallback.transport_mode
                    if parsed.transport_mode == "auto" and fallback.transport_mode != "auto"
                    else parsed.transport_mode
                ),
                "travel_scope": (
                    fallback.travel_scope
                    if parsed.travel_scope == "auto" and fallback.travel_scope != "auto"
                    else parsed.travel_scope
                ),
            }
        )
