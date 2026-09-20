import asyncio
from datetime import timedelta
from inspect import isawaitable

from backend.attractions.dedupe import dedupe_attractions
from backend.destination import DestinationPlanner, DestinationPlanningError, DestinationResolver
from backend.degradation import collect_degradation_notices
from backend.models.travel_plan import AttractionStop, TravelDay, TravelPlan, TravelPlanResponse
from backend.models.requirement import MAX_SINGLE_CITY_DAYS
from backend.planner.city import deduplicate_city_plan
from backend.plan_overview import synchronize_plan_overviews


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
        schedule_builder,
        evaluator,
        attraction_enrichment_service=None,
    ):
        self.parser = parser
        self.resolver = resolver
        self.destination_planner = destination_planner
        self.catalog_repository = catalog_repository
        self.knowledge_bootstrap = knowledge_bootstrap
        self.city_planning_service = city_planning_service
        self.route_enrichment_service = route_enrichment_service
        self.weather_enrichment_service = weather_enrichment_service
        self.schedule_builder = schedule_builder
        self.evaluator = evaluator
        self.attraction_enrichment_service = attraction_enrichment_service

    async def run(self, text: str, *, travel_scope=None) -> TravelPlanResponse:
        parsed = self.parser.parse(text)
        requirement = await parsed if isawaitable(parsed) else parsed
        if travel_scope is not None:
            requirement = requirement.model_copy(update={"travel_scope": travel_scope})
        return await self.run_requirement(requirement)

    async def run_requirement(self, requirement, *, progress=None) -> TravelPlanResponse:
        await self._emit(progress, "requirement", "已确认旅行需求", "completed")
        # Route mode is no longer a user-facing requirement. Every plan lets
        # the map tool choose the transport mode for each adjacent attraction.
        requirement = requirement.model_copy(update={"transport_mode": "auto"})
        requirement = self.resolver.resolve(requirement).requirement
        await self._emit(progress, "destination", "正在匹配适合的目的地", "running")
        destination = self.destination_planner.plan(requirement)
        if requirement.days and requirement.days > MAX_SINGLE_CITY_DAYS and len(destination.city_stays) < 2:
            raise DestinationPlanningError("4～7天必须安排同省多城市，不能生成单城市方案")
        if not requirement.province:
            first_city = self.catalog_repository.find_city(destination.city_stays[0].city)
            requirement = requirement.model_copy(update={"province": first_city.province.name})
        if destination.requires_choice:
            raise DestinationChoiceRequired(requirement, destination)
        await self._emit(progress, "destination", "目的地与停留天数已确定", "completed")

        city_plans = []
        city_results = []
        precomputed_routes_by_city = []
        partial_knowledge_cities = []
        supports_route_aware_planning = all(
            callable(getattr(self.city_planning_service, method, None))
            for method in ("retrieve_candidates", "plan_with_routes")
        ) and all(
            getattr(self.city_planning_service, attribute, None) is not None
            for attribute in ("route_enrichment_service", "schedule_builder")
        )
        for city_index, city_stay in enumerate(destination.city_stays):
            city = self.catalog_repository.find_city(city_stay.city)
            poi_step = f"poi_search:{city_index}:{city.name}"
            retrieval_step = f"retrieval:{city_index}:{city.name}"
            city_plan_step = f"city_plan:{city_index}:{city.name}"
            await self._emit(
                progress,
                poi_step,
                f"正在检查{city.name}本地景点资料",
                "running",
            )

            async def report_bootstrap_progress(
                ready_count: int,
                target_count: int,
                attempted_count: int,
                candidate_count: int,
                failed_count: int,
            ) -> None:
                if ready_count >= target_count and candidate_count == 0:
                    label = f"已准备{city.name}景点资料（{ready_count}/{target_count}条）"
                else:
                    visible_ready_count = min(ready_count, target_count)
                    label = (
                        f"正在准备{city.name}景点（已准备 {visible_ready_count}/{target_count} 条，"
                        f"已尝试 {attempted_count}/{candidate_count} 个，失败 {failed_count} 个）"
                    )
                await self._emit(progress, poi_step, label, "running")

            bootstrap_result = await self.knowledge_bootstrap.ensure(
                city=city,
                target_ready=3,
                max_attempts=12,
                allow_partial=True,
                progress=report_bootstrap_progress,
            )
            ready_count = getattr(bootstrap_result, "ready_count", None)
            if getattr(bootstrap_result, "partial", False):
                partial_knowledge_cities.append(city.name)
            ready_label = (
                f"{city.name}本地景点资料已准备（{ready_count}条）"
                if ready_count is not None
                else f"{city.name}本地景点资料已准备"
            )
            await self._emit(progress, poi_step, ready_label, "completed")
            await self._emit(progress, retrieval_step, f"正在为你筛选{city.name}景点", "running")
            if supports_route_aware_planning:
                unique_attractions = dedupe_attractions(
                    await self.city_planning_service.retrieve_candidates(
                        city_stay=city_stay,
                        requirement=requirement,
                        top_k=20,
                    )
                )
            else:
                result = await self.city_planning_service.plan(
                    city_stay=city_stay,
                    requirement=requirement,
                    top_k=20,
                )
                unique_attractions = dedupe_attractions(result.attractions)

            if self.attraction_enrichment_service is not None and unique_attractions:
                detail_step = f"amap_detail:{city_index}:{city.name}"
                await self._emit(
                    progress,
                    detail_step,
                    f"正在用高德补充已筛选的{city.name}景点详情",
                    "running",
                )
                unique_attractions = await self.attraction_enrichment_service.enrich(
                    city=city.name,
                    candidates=unique_attractions,
                )
                await self._emit(
                    progress,
                    detail_step,
                    f"{city.name}已筛选景点详情补充完成",
                    "completed",
                )

            if supports_route_aware_planning:
                await self._emit(
                    progress,
                    city_plan_step,
                    f"正在验证{city.name}景点路线和时间窗口",
                    "running",
                )
                route_result = await self.city_planning_service.plan_with_routes(
                    city_stay=city_stay,
                    requirement=requirement,
                    city_adcode=city.adcode,
                    attractions=unique_attractions,
                )
                result = route_result.city_result
                precomputed_routes_by_city.append(route_result.routes_by_day)
            else:
                result = result.model_copy(
                    update={
                        "attractions": unique_attractions,
                        "city_plan": deduplicate_city_plan(
                            plan=result.city_plan,
                            candidates=unique_attractions,
                        ),
                    }
                )
                precomputed_routes_by_city.append(None)
            city_plans.append(result.city_plan)
            city_results.append(result)
            await self._emit(progress, retrieval_step, f"{city.name}景点筛选完成", "completed")
            await self._emit(progress, city_plan_step, f"{city.name}行程生成完成", "completed")

        itinerary = []
        global_day = 1
        for city_index, (city_plan, city_result) in enumerate(
            zip(city_plans, city_results, strict=True)
        ):
            city_record = self.catalog_repository.find_city(city_plan.city)
            attractions_by_name = {item.name: item for item in city_result.attractions}
            precomputed_routes = precomputed_routes_by_city[city_index]
            for local_day_index, day in enumerate(city_plan.days):
                day_date = None
                if requirement.start_date is not None:
                    day_date = requirement.start_date + timedelta(days=global_day - 1)
                routes = (
                    precomputed_routes[local_day_index]
                    if precomputed_routes is not None
                    and local_day_index < len(precomputed_routes)
                    else []
                )
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
                                visit_duration_minutes=attractions_by_name[name].visit_duration_minutes,
                            )
                            for name in day.attractions
                        ],
                        routes=routes,
                        description=day.description,
                    )
                )
                global_day += 1
        plan = TravelPlan(
            province=requirement.province,
            city_stays=destination.city_stays,
            city_plans=city_plans,
            itinerary=itinerary,
            partial_knowledge_cities=partial_knowledge_cities,
        )
        await self._emit(
            progress,
            "route_enrichment",
            (
                "景点入选时已完成路线验证"
                if all(item is not None for item in precomputed_routes_by_city)
                else "正在获取景点间路线（高德 MCP）"
            ),
            "running",
        )
        await self._emit(
            progress,
            "weather_enrichment",
            "正在获取行程天气（和风天气 API）",
            "running",
        )

        has_precomputed_routes = all(
            item is not None for item in precomputed_routes_by_city
        )
        if has_precomputed_routes:
            route_days, weather_days = plan.itinerary, await self._enrich_weather(
                plan.itinerary
            )
        else:
            route_days, weather_days = await asyncio.gather(
                self._enrich_routes(
                    plan.itinerary,
                    requested_mode=requirement.transport_mode,
                ),
                self._enrich_weather(plan.itinerary),
            )
        enriched_days = [
            route_day.model_copy(
                update={
                    "weather": weather_day.weather,
                }
            )
            for route_day, weather_day in zip(
                route_days,
                weather_days,
                strict=True,
            )
        ]
        plan = plan.model_copy(update={"itinerary": enriched_days})
        await self._emit(
            progress,
            "route_enrichment",
            "景点入选时已完成路线验证"
            if has_precomputed_routes
            else "景点间路线已补充",
            "completed",
        )
        await self._emit(progress, "weather_enrichment", "行程天气已处理", "completed")
        await self._emit(progress, "schedule", "正在生成每日时间表", "running")
        scheduled_days = [
            (
                self.schedule_builder.build_day_exact(
                    day,
                    day_start_time=requirement.daily_start_time,
                    day_end_time=requirement.daily_end_time,
                )
                if has_precomputed_routes
                else self.schedule_builder.build_day(
                    day,
                    day_start_time=requirement.daily_start_time,
                    day_end_time=requirement.daily_end_time,
                )
            )
            for day in plan.itinerary
        ]
        plan = plan.model_copy(update={"itinerary": scheduled_days})
        await self._emit(progress, "schedule", "每日时间表已生成", "completed")
        await self._emit(progress, "evaluation", "正在检查行程可执行性", "running")
        evaluation = self.evaluator.evaluate(plan, requirement)
        plan = plan.model_copy(update={"evaluation": evaluation})
        await self._emit(progress, "evaluation", "行程可执行性已检查", "completed")
        plan = synchronize_plan_overviews(plan)
        plan = plan.model_copy(update={"notices": collect_degradation_notices(plan)})
        await self._emit(progress, "complete", "完整旅行方案已生成", "completed")
        return TravelPlanResponse(requirement=requirement, plan=plan)

    async def _enrich_routes(self, days, *, requested_mode):
        return await asyncio.gather(
            *(
                self.route_enrichment_service.enrich_day(
                    day,
                    requested_mode=requested_mode,
                )
                for day in days
            )
        )

    async def _enrich_weather(self, days):
        return await asyncio.gather(
            *(self.weather_enrichment_service.enrich_day(day) for day in days)
        )

    @staticmethod
    async def _emit(progress, step: str, label: str, status: str) -> None:
        if progress is None:
            return
        result = progress(step, label, status)
        if isawaitable(result):
            await result
