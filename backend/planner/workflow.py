from datetime import timedelta
from inspect import isawaitable

from backend.destination import DestinationPlanner, DestinationResolver
from backend.models.travel_plan import AttractionStop, TravelDay, TravelPlan, TravelPlanResponse


class DestinationChoiceRequired(RuntimeError):
    def __init__(self, requirement, options):
        super().__init__("需要用户在单城市与多城市方案之间选择")
        self.requirement = requirement
        self.options = options


class TravelPlanningWorkflow:
    def __init__(
        self,
        *,
        parser,
        resolver: DestinationResolver,
        destination_planner: DestinationPlanner,
        catalog_repository,
        knowledge_bootstrap,
        city_planning_service,
        route_enrichment_service,
        weather_enrichment_service,
        attraction_facts_service,
        schedule_builder,
        evaluator,
    ):
        self.parser = parser
        self.resolver = resolver
        self.destination_planner = destination_planner
        self.catalog_repository = catalog_repository
        self.knowledge_bootstrap = knowledge_bootstrap
        self.city_planning_service = city_planning_service
        self.route_enrichment_service = route_enrichment_service
        self.weather_enrichment_service = weather_enrichment_service
        self.attraction_facts_service = attraction_facts_service
        self.schedule_builder = schedule_builder
        self.evaluator = evaluator

    async def run(self, text: str, *, travel_scope=None) -> TravelPlanResponse:
        parsed = self.parser.parse(text)
        requirement = await parsed if isawaitable(parsed) else parsed
        if travel_scope is not None:
            requirement = requirement.model_copy(update={"travel_scope": travel_scope})
        return await self.run_requirement(requirement)

    async def run_requirement(self, requirement, *, progress=None) -> TravelPlanResponse:
        await self._emit(progress, "requirement", "已确认旅行需求", "completed")
        requirement = self.resolver.resolve(requirement).requirement
        await self._emit(progress, "destination", "正在匹配适合的目的地", "running")
        destination = self.destination_planner.plan(requirement)
        if not requirement.province:
            first_city = self.catalog_repository.find_city(destination.city_stays[0].city)
            requirement = requirement.model_copy(update={"province": first_city.province.name})
        if destination.requires_choice:
            raise DestinationChoiceRequired(requirement, destination)
        await self._emit(progress, "destination", "目的地与停留天数已确定", "completed")

        city_plans = []
        city_results = []
        for city_stay in destination.city_stays:
            city = self.catalog_repository.find_city(city_stay.city)
            await self._emit(progress, "poi_search", f"正在准备{city.name}真实景点", "running")
            await self.knowledge_bootstrap.ensure(city=city, target_ready=3, max_attempts=12)
            await self._emit(progress, "retrieval", f"正在为你筛选{city.name}景点", "running")
            result = await self.city_planning_service.plan(
                city_stay=city_stay,
                requirement=requirement,
                top_k=10,
            )
            city_plans.append(result.city_plan)
            city_results.append(result)
            await self._emit(progress, "city_plan", f"{city.name}行程生成完成", "completed")

        itinerary = []
        global_day = 1
        for city_plan, city_result in zip(city_plans, city_results, strict=True):
            city_record = self.catalog_repository.find_city(city_plan.city)
            attractions_by_name = {item.name: item for item in city_result.attractions}
            for day in city_plan.days:
                day_date = None
                if requirement.start_date is not None:
                    day_date = requirement.start_date + timedelta(days=global_day - 1)
                itinerary.append(
                    TravelDay(
                        day=global_day,
                        city=city_plan.city,
                        city_adcode=city_record.adcode,
                        city_day=day.day,
                        date=day_date,
                        attractions=[
                            AttractionStop(
                                poi_id=attractions_by_name[name].poi_id,
                                name=name,
                                city=attractions_by_name[name].city,
                                category=attractions_by_name[name].category,
                                description=attractions_by_name[name].description,
                                longitude=attractions_by_name[name].longitude,
                                latitude=attractions_by_name[name].latitude,
                            )
                            for name in day.attractions
                        ],
                        description=day.description,
                    )
                )
                global_day += 1
        plan = TravelPlan(
            province=requirement.province,
            city_stays=destination.city_stays,
            city_plans=city_plans,
            itinerary=itinerary,
        )
        await self._emit(progress, "route_enrichment", "正在获取景点间路线", "running")
        enriched_days = [
            await self.route_enrichment_service.enrich_day(
                day,
                requested_mode=requirement.transport_mode,
            )
            for day in plan.itinerary
        ]
        plan = plan.model_copy(update={"itinerary": enriched_days})
        await self._emit(progress, "route_enrichment", "景点间路线已补充", "completed")
        await self._emit(progress, "weather_enrichment", "正在获取行程天气", "running")
        weather_days = [await self.weather_enrichment_service.enrich_day(day) for day in plan.itinerary]
        plan = plan.model_copy(update={"itinerary": weather_days})
        await self._emit(progress, "weather_enrichment", "行程天气已处理", "completed")
        await self._emit(progress, "attraction_facts", "正在核验开放时间与游玩时长", "running")
        facts_days = [await self.attraction_facts_service.enrich_day(day) for day in plan.itinerary]
        plan = plan.model_copy(update={"itinerary": facts_days})
        await self._emit(progress, "attraction_facts", "景点开放时间与游玩时长已处理", "completed")
        await self._emit(progress, "schedule", "正在生成每日时间表", "running")
        scheduled_days = [self.schedule_builder.build_day(day) for day in plan.itinerary]
        plan = plan.model_copy(update={"itinerary": scheduled_days})
        await self._emit(progress, "schedule", "每日时间表已生成", "completed")
        await self._emit(progress, "evaluation", "正在检查行程可执行性", "running")
        evaluation = self.evaluator.evaluate(plan, requirement)
        plan = plan.model_copy(update={"evaluation": evaluation})
        await self._emit(progress, "evaluation", "行程可执行性检查完成", "completed")
        await self._emit(progress, "complete", "完整旅行方案已生成", "completed")
        return TravelPlanResponse(requirement=requirement, plan=plan)

    @staticmethod
    async def _emit(progress, step: str, label: str, status: str) -> None:
        if progress is None:
            return
        result = progress(step, label, status)
        if isawaitable(result):
            await result
