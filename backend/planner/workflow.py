from inspect import isawaitable

from backend.destination import DestinationPlanner, DestinationResolver
from backend.models.travel_plan import TravelDay, TravelPlan, TravelPlanResponse


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
    ):
        self.parser = parser
        self.resolver = resolver
        self.destination_planner = destination_planner
        self.catalog_repository = catalog_repository
        self.knowledge_bootstrap = knowledge_bootstrap
        self.city_planning_service = city_planning_service

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
            await self._emit(progress, "city_plan", f"{city.name}行程生成完成", "completed")

        itinerary = []
        global_day = 1
        for city_plan in city_plans:
            for day in city_plan.days:
                itinerary.append(
                    TravelDay(
                        day=global_day,
                        city=city_plan.city,
                        city_day=day.day,
                        attractions=day.attractions,
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
        await self._emit(progress, "complete", "完整旅行方案已生成", "completed")
        return TravelPlanResponse(requirement=requirement, plan=plan)

    @staticmethod
    async def _emit(progress, step: str, label: str, status: str) -> None:
        if progress is None:
            return
        result = progress(step, label, status)
        if isawaitable(result):
            await result
