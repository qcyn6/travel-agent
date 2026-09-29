from inspect import Parameter, isawaitable, signature

from backend.requirements.llm import RequirementLLMError
from backend.models.requirement import MAX_SINGLE_CITY_DAYS
from backend.requirements.parser import canonicalize_interests


class RequirementParsingService:
    def __init__(self, *, llm_parser, fallback_parser):
        self.llm_parser = llm_parser
        self.fallback_parser = fallback_parser

    async def parse(
        self,
        text: str,
        *,
        conversation_history: list[dict[str, str]] | None = None,
    ):
        fallback = self.fallback_parser.parse(text)
        try:
            parsed_value = self._parse_llm(text, conversation_history)
            parsed = await parsed_value if isawaitable(parsed_value) else parsed_value
        except RequirementLLMError:
            return fallback.model_copy(update={"transport_mode": "auto"})
        interests = canonicalize_interests(parsed.interests + fallback.interests)
        merged = parsed.model_copy(
            update={
                "province": parsed.province or fallback.province,
                "cities": parsed.cities or fallback.cities,
                "province_preference_confirmed": (
                    fallback.province_preference_confirmed
                    or parsed.province_preference_confirmed
                    or bool(parsed.province)
                    or bool(fallback.province)
                ),
                "city_preference_confirmed": (
                    fallback.city_preference_confirmed
                    or bool(parsed.cities)
                    or bool(fallback.cities)
                ),
                "days": parsed.days or fallback.days,
                "interests": interests,
                "preference_text": parsed.preference_text or fallback.preference_text,
                # Relative dates are resolved locally from the server date. Prefer
                # that deterministic result over an LLM guess for “明天”等表达。
                "start_date": fallback.start_date or parsed.start_date,
                # The deterministic parser also handles common Chinese clock
                # expressions such as “早上8点到晚上9点”.
                "daily_start_time": fallback.daily_start_time or parsed.daily_start_time,
                "daily_end_time": fallback.daily_end_time or parsed.daily_end_time,
                # Route mode is internal. Do not infer a user preference from
                # words such as “自驾” or “坐地铁”; the map tool recommends
                # transport per attraction segment.
                "transport_mode": "auto",
                "travel_scope": (
                    fallback.travel_scope
                    if (
                        fallback.travel_scope != "auto"
                        and (
                            parsed.travel_scope == "auto"
                            or (
                                fallback.travel_scope == "multi_city"
                                and fallback.days is not None
                                and fallback.days > MAX_SINGLE_CITY_DAYS
                            )
                        )
                    )
                    else parsed.travel_scope
                ),
            }
        )
        return type(parsed).model_validate(merged.model_dump())

    def _parse_llm(
        self,
        text: str,
        conversation_history: list[dict[str, str]] | None,
    ):
        parse = self.llm_parser.parse
        try:
            parameters = signature(parse).parameters.values()
        except (TypeError, ValueError):
            parameters = ()
        supports_context = any(
            parameter.name == "conversation_history"
            or parameter.kind is Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if supports_context:
            return parse(text, conversation_history=conversation_history or [])
        # Keep third-party and test adapters that still implement the legacy
        # parse(text) contract working while the built-in agents receive the
        # complete conversation context.
        return parse(text)
