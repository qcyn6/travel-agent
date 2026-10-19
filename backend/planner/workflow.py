import asyncio
import logging
from datetime import timedelta
from inspect import Parameter, isawaitable, signature

from backend.attractions.dedupe import dedupe_attractions, normalize_attraction_name
from backend.destination import DestinationPlanner, DestinationPlanningError, DestinationResolver
from backend.degradation import collect_degradation_notices
from backend.models.travel_plan import AttractionStop, TravelDay, TravelPlan, TravelPlanResponse
from backend.models.requirement import MAX_SINGLE_CITY_DAYS
from backend.planner.city import CityPlanningError, deduplicate_city_plan
from backend.planner.contracts import CityPlanningAlgorithm
from backend.planner.graph import TravelPlanningGraph
from backend.planner.review import apply_plan_review
from backend.plan_overview import synchronize_plan_overviews

_CITY_PLANNING_CONCURRENCY = 3
logger = logging.getLogger(__name__)


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
        attraction_description_service=None,
        attraction_photo_service=None,
        hotel_enrichment_service=None,
        planning_algorithm: CityPlanningAlgorithm | None = None,
        checkpoint_path: str | None = None,
        plan_review_agent=None,
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
        self.attraction_description_service = attraction_description_service
        self.attraction_photo_service = attraction_photo_service
        self.hotel_enrichment_service = hotel_enrichment_service
        self.plan_review_agent = plan_review_agent
        # The existing CityPlanningService remains the default algorithm. The
        # graph depends only on this port, so a future LangChain adapter can
        # be introduced without changing dynamic_city_plan or its scheduling.
        self.planning_algorithm = planning_algorithm or city_planning_service
        self.graph = TravelPlanningGraph(self, checkpoint_path=checkpoint_path)

    async def run(
        self,
        text: str,
        *,
        travel_scope=None,
        thread_id: str | None = None,
    ) -> TravelPlanResponse:
        parsed = self.parser.parse(text)
        requirement = await parsed if isawaitable(parsed) else parsed
        if travel_scope is not None:
            requirement = requirement.model_copy(update={"travel_scope": travel_scope})
        return await self.run_requirement(
            requirement,
            thread_id=thread_id,
        )

    async def stream(
        self,
        text: str,
        *,
        travel_scope=None,
        progress=None,
        thread_id: str | None = None,
    ):
        """Parse a one-shot request and expose the same LangGraph event stream."""

        parsed = self.parser.parse(text)
        requirement = await parsed if isawaitable(parsed) else parsed
        if travel_scope is not None:
            requirement = requirement.model_copy(update={"travel_scope": travel_scope})
        async for event in self.stream_requirement(
            requirement,
            progress=progress,
            thread_id=thread_id,
        ):
            yield event

    async def run_requirement(
        self,
        requirement,
        *,
        progress=None,
        thread_id: str | None = None,
    ) -> TravelPlanResponse:
        """Execute the planning graph for a confirmed requirement."""

        return await self.graph.run(
            requirement,
            progress=progress,
            thread_id=thread_id,
        )

    async def stream_requirement(
        self,
        requirement,
        *,
        progress=None,
        thread_id: str | None = None,
    ):
        """Stream normalized LangGraph events for one planning run."""

        async for event in self.graph.astream(
            requirement,
            progress=progress,
            thread_id=thread_id,
        ):
            yield event

    async def _prepare_requirement(self, requirement, *, progress=None):
        await self._emit(progress, "requirement", "已确认旅行需求", "completed")
        # Route mode is internal; the map service chooses it per segment.
        requirement = requirement.model_copy(update={"transport_mode": "auto"})
        return self.resolver.resolve(requirement).requirement

    async def _resolve_destination(self, requirement, *, progress=None):
        await self._emit(progress, "destination", "正在匹配适合的目的地", "running")
        destination = self.destination_planner.plan(requirement)
        if (
            requirement.days
            and requirement.days > MAX_SINGLE_CITY_DAYS
            and len(destination.city_stays) < 2
        ):
            raise DestinationPlanningError("4～7天必须安排同省多城市，不能生成单城市方案")
        if not requirement.province:
            first_city = self.catalog_repository.find_city(destination.city_stays[0].city)
            requirement = requirement.model_copy(update={"province": first_city.province.name})
        await self._emit(progress, "destination", "目的地与停留天数已确定", "completed")
        return requirement, destination

    async def _plan_cities(
        self,
        *,
        requirement,
        destination,
        progress=None,
        allow_route_fallback=False,
    ):
        """Prepare each city locally, then plan independent cities in parallel.

        Database-backed readiness checks stay serial because the workflow owns
        one synchronous SQLAlchemy session.  Candidate retrieval, route
        validation and final AI descriptions then run concurrently from
        immutable snapshots, while gathered result order remains the user's
        city order.
        """

        supports_route_aware_planning = all(
            callable(getattr(self.planning_algorithm, method, None))
            for method in ("retrieve_candidates", "plan_with_routes")
        ) and all(
            getattr(self.planning_algorithm, attribute, None) is not None
            for attribute in ("route_enrichment_service", "schedule_builder")
        )
        prepared_cities = []
        start_day_offset = 0

        # Read the database-backed readiness snapshots first.  This avoids
        # concurrently using the same synchronous SQLAlchemy session; the
        # retrieval stage below is network-heavy and can safely fan out.
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
                partial_knowledge = True
            else:
                partial_knowledge = False
            ready_label = (
                f"{city.name}本地景点资料已准备（{ready_count}条）"
                if ready_count is not None
                else f"{city.name}本地景点资料已准备"
            )
            await self._emit(progress, poi_step, ready_label, "completed")
            await self._emit(progress, retrieval_step, f"正在为你筛选{city.name}景点", "running")
            prepared_cities.append(
                {
                    "city_index": city_index,
                    "city": city,
                    "city_stay": city_stay,
                    "start_day_offset": start_day_offset,
                    "retrieval_step": retrieval_step,
                    "city_plan_step": city_plan_step,
                    "unique_attractions": None,
                    "base_result": None,
                    "partial_knowledge": partial_knowledge,
                }
            )
            start_day_offset += city_stay.days

        # Candidate retrieval is the other network-heavy part of a city plan
        # (embedding/BM25/vector work).  Run it after the synchronous bootstrap
        # reads, so up to three cities can retrieve concurrently without
        # interleaving writes through the shared SQLAlchemy session.
        async def retrieve_one_city(item):
            city = item["city"]
            city_requirement = requirement
            if requirement.start_date is not None and item["start_day_offset"]:
                city_requirement = requirement.model_copy(
                    update={
                        "start_date": requirement.start_date
                        + timedelta(days=item["start_day_offset"])
                    }
                )
            if supports_route_aware_planning:
                unique_attractions = dedupe_attractions(
                    await self.planning_algorithm.retrieve_candidates(
                        city_stay=item["city_stay"],
                        requirement=requirement,
                        top_k=20,
                    )
                )
                base_result = None
            else:
                base_result = await self._call_city_plan(
                    city_stay=item["city_stay"],
                    requirement=city_requirement,
                    top_k=20,
                )
                unique_attractions = dedupe_attractions(base_result.attractions)
            await self._emit(
                progress,
                item["retrieval_step"],
                f"{city.name}景点筛选完成",
                "completed",
            )
            return {
                **item,
                "unique_attractions": unique_attractions,
                "base_result": base_result,
            }

        retrieval_semaphore = asyncio.Semaphore(_CITY_PLANNING_CONCURRENCY)

        async def bounded_retrieve_one_city(item):
            async with retrieval_semaphore:
                return await retrieve_one_city(item)

        prepared_cities = list(
            await asyncio.gather(
                *(bounded_retrieve_one_city(item) for item in prepared_cities)
            )
        )

        async def plan_one_city(item):
            city = item["city"]
            city_stay = item["city_stay"]
            city_plan_step = item["city_plan_step"]
            unique_attractions = item["unique_attractions"]
            city_requirement = requirement
            if requirement.start_date is not None and item["start_day_offset"]:
                city_requirement = requirement.model_copy(
                    update={
                        "start_date": requirement.start_date
                        + timedelta(days=item["start_day_offset"])
                    }
                )
            precomputed_routes = None

            await self._emit(
                progress,
                city_plan_step,
                f"正在安排{city.name}的每日行程",
                "running",
            )
            if supports_route_aware_planning:
                await self._emit(progress, city_plan_step, f"正在验证{city.name}景点路线和时间窗口", "running")
                try:
                    route_result = await self._call_route_aware_city_plan(
                        city_stay=city_stay,
                        requirement=city_requirement,
                        city_adcode=city.adcode,
                        attractions=unique_attractions,
                    )
                    result = route_result.city_result
                    precomputed_routes = route_result.routes_by_day
                except CityPlanningError:
                    fallback = getattr(
                        self.planning_algorithm,
                        "plan_from_candidates",
                        None,
                    )
                    if not callable(fallback):
                        raise
                    await self._emit(
                        progress,
                        "planning_recovery",
                        f"{city.name}路线校验不足，改用本地距离排程",
                        "running",
                    )
                    result = await self._call_plan_from_candidates(
                        city_stay=city_stay,
                        requirement=city_requirement,
                        attractions=unique_attractions,
                    )
                    await self._emit(
                        progress,
                        "planning_recovery",
                        f"{city.name}已完成本地距离排程",
                        "completed",
                    )
            else:
                result = item["base_result"].model_copy(
                    update={
                        "attractions": unique_attractions,
                        "city_plan": deduplicate_city_plan(
                            plan=item["base_result"].city_plan,
                            candidates=unique_attractions,
                        ),
                    }
                )

            # Only final selected attractions receive AI-generated descriptions.
            # The full 20-item retrieval pool keeps its local descriptions.
            result, image_urls = await self._enrich_selected_attractions(
                city=city.name,
                city_index=item["city_index"],
                result=result,
                requirement=requirement,
                progress=progress,
            )
            await self._emit(progress, city_plan_step, f"{city.name}行程生成完成", "completed")
            return (
                result.city_plan,
                result,
                precomputed_routes,
                item["partial_knowledge"],
                image_urls,
            )

        city_semaphore = asyncio.Semaphore(_CITY_PLANNING_CONCURRENCY)

        async def bounded_plan_one_city(item):
            async with city_semaphore:
                return await plan_one_city(item)

        # The destination model currently allows at most three cities, so this
        # is an explicit concurrency=3 boundary rather than an unbounded fanout.
        city_outputs = await asyncio.gather(
            *(bounded_plan_one_city(item) for item in prepared_cities)
        )
        city_plans = [output[0] for output in city_outputs]
        city_results = [output[1] for output in city_outputs]
        precomputed_routes_by_city = [output[2] for output in city_outputs]
        partial_knowledge_cities = [
            item["city"].name
            for item, output in zip(prepared_cities, city_outputs, strict=True)
            if output[3]
        ]
        image_urls_by_poi = {
            poi_id: url
            for output in city_outputs
            for poi_id, url in output[4].items()
        }
        return {
            "city_plans": city_plans,
            "city_results": city_results,
            "precomputed_routes_by_city": precomputed_routes_by_city,
            "partial_knowledge_cities": partial_knowledge_cities,
            "image_urls_by_poi": image_urls_by_poi,
        }

    async def _enrich_selected_attractions(
        self,
        *,
        city,
        city_index,
        result,
        requirement,
        progress=None,
    ):
        photo_service = self.attraction_photo_service
        if photo_service is None or not result.attractions:
            return result, {}
        selected_names = {
            normalize_attraction_name(name)
            for day in result.city_plan.days
            for name in day.attractions
            if normalize_attraction_name(name)
        }
        selected = [
            item
            for item in result.attractions
            if normalize_attraction_name(item.name) in selected_names
        ]
        if not selected:
            return result, {}
        detail_step = f"attraction_details:{city_index}:{city}"
        await self._emit(
            progress,
            detail_step,
            f"正在补充最终入选的{city}景点图片",
            "running",
        )
        image_urls = await photo_service.enrich(city=city, candidates=selected)
        await self._emit(
            progress,
            detail_step,
            f"{city}最终景点图片补充完成",
            "completed",
        )
        return result, image_urls

    @staticmethod
    def _accepts_keyword(method, keyword: str) -> bool:
        try:
            method_signature = signature(method)
        except (TypeError, ValueError):
            return False
        parameters = method_signature.parameters
        return keyword in parameters or any(
            parameter.kind is Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

    async def _call_city_plan(self, *, city_stay, requirement, top_k):
        method = self.planning_algorithm.plan
        kwargs = {
            "city_stay": city_stay,
            "requirement": requirement,
            "top_k": top_k,
        }
        if self._accepts_keyword(method, "narrate"):
            kwargs["narrate"] = False
        return await method(**kwargs)

    async def _call_route_aware_city_plan(
        self,
        *,
        city_stay,
        requirement,
        city_adcode,
        attractions,
    ):
        method = self.planning_algorithm.plan_with_routes
        kwargs = {
            "city_stay": city_stay,
            "requirement": requirement,
            "city_adcode": city_adcode,
            "attractions": attractions,
        }
        if self._accepts_keyword(method, "narrate"):
            kwargs["narrate"] = False
        return await method(**kwargs)

    async def _call_plan_from_candidates(
        self,
        *,
        city_stay,
        requirement,
        attractions,
    ):
        method = self.planning_algorithm.plan_from_candidates
        kwargs = {
            "city_stay": city_stay,
            "requirement": requirement,
            "attractions": attractions,
        }
        if self._accepts_keyword(method, "narrate"):
            kwargs["narrate"] = False
        return await method(**kwargs)


    def _assemble_itinerary(self, *, requirement, destination, city_context):
        itinerary = []
        global_day = 1
        city_plans = city_context["city_plans"]
        city_results = city_context["city_results"]
        precomputed_routes_by_city = city_context["precomputed_routes_by_city"]
        image_urls_by_poi = city_context.get("image_urls_by_poi", {})
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
                                image_url=image_urls_by_poi.get(attractions_by_name[name].poi_id),
                                longitude=attractions_by_name[name].longitude,
                                latitude=attractions_by_name[name].latitude,
                                visit_duration_minutes=attractions_by_name[name].visit_duration_minutes,
                                opening_hours_weekly=attractions_by_name[name].opening_hours_weekly,
                                opening_hours_today=attractions_by_name[name].opening_hours_today,
                                opening_hours_status=attractions_by_name[name].opening_hours_status,
                            )
                            for name in day.attractions
                        ],
                        routes=routes,
                        description=day.description,
                    )
                )
                global_day += 1
        return TravelPlan(
            province=requirement.province,
            city_stays=destination.city_stays,
            city_plans=city_plans,
            itinerary=itinerary,
            replacement_candidates_by_city={
                city_plan.city: list(city_result.attractions)
                for city_plan, city_result in zip(city_plans, city_results, strict=True)
            },
            partial_knowledge_cities=city_context["partial_knowledge_cities"],
        )

    async def _enrich_hotels(self, *, requirement, plan, progress=None):
        await self._emit(
            progress,
            "hotel_enrichment",
            "正在为每天最后一个景点匹配附近酒店",
            "running",
        )
        if self.hotel_enrichment_service is None:
            await self._emit(
                progress,
                "hotel_enrichment",
                "酒店信息暂不可用，继续生成行程",
                "completed",
            )
            return plan
        enriched = await self.hotel_enrichment_service.enrich_plan(
            plan,
            requested_mode=requirement.transport_mode,
        )
        hotel_days = sum(1 for day in enriched.itinerary if day.hotel is not None)
        missing_hotel_days = [
            day.day for day in enriched.itinerary if day.hotel is None
        ]
        if hotel_days:
            label = f"已为 {hotel_days} 天行程补充酒店与收尾路线"
            if missing_hotel_days:
                label += (
                    "；第 "
                    + ", ".join(str(day) for day in missing_hotel_days)
                    + " 天暂未找到附近酒店"
                )
        else:
            label = "附近酒店暂未找到，继续生成行程"
        await self._emit(
            progress,
            "hotel_enrichment",
            label,
            "completed",
        )
        return enriched

    async def _enrich_weather_and_routes(
        self,
        *,
        requirement,
        plan,
        city_context,
        progress=None,
    ):
        precomputed_routes_by_city = city_context["precomputed_routes_by_city"]
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
            route_day.model_copy(update={"weather": weather_day.weather})
            for route_day, weather_day in zip(route_days, weather_days, strict=True)
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
        scheduled_days = []
        previous_day = None
        for day in plan.itinerary:
            if (
                previous_day is not None
                and previous_day.city_adcode == day.city_adcode
                and previous_day.hotel is not None
            ):
                connect_overnight = getattr(
                    self.hotel_enrichment_service,
                    "connect_overnight",
                    None,
                )
                if callable(connect_overnight):
                    day = await connect_overnight(
                        previous_day.hotel,
                        day,
                        requested_mode=requirement.transport_mode,
                        daily_start_time=requirement.daily_start_time,
                        daily_end_time=requirement.daily_end_time,
                        meal_minutes=self.schedule_builder.meal_minutes,
                    )
            elif (
                previous_day is not None
                and previous_day.city_adcode != day.city_adcode
            ):
                # A city's last hotel must not become the next city's route
                # origin. Inter-city travel is a separate leg, not a local
                # overnight hotel-to-attraction transfer.
                day = day.model_copy(
                    update={"start_hotel": None, "start_hotel_route": None}
                )

            schedule_kwargs = {
                "day_start_time": requirement.daily_start_time,
                "day_end_time": requirement.daily_end_time,
            }
            if day.start_hotel_route is not None:
                # The overnight transfer now consumes today's time window. If
                # it displaces the final sightseeing stop, trim the suffix and
                # subsequently reselect the nearby hotel from the new final
                # attraction.
                scheduled = self.schedule_builder.build_day(day, **schedule_kwargs)
            elif has_precomputed_routes:
                scheduled = self.schedule_builder.build_day_exact(
                    day,
                    **schedule_kwargs,
                )
            else:
                scheduled = self.schedule_builder.build_day(day, **schedule_kwargs)

            if (
                scheduled.attractions[-1].poi_id != day.attractions[-1].poi_id
                and self.hotel_enrichment_service is not None
            ):
                enrich_day = getattr(self.hotel_enrichment_service, "enrich_day", None)
                if callable(enrich_day):
                    scheduled = await enrich_day(
                        scheduled.model_copy(update={"hotel": None, "hotel_route": None}),
                        requested_mode=requirement.transport_mode,
                    )

            scheduled_days.append(scheduled)
            previous_day = scheduled
        await self._emit(progress, "schedule", "每日时间表已生成", "completed")
        return plan.model_copy(update={"itinerary": scheduled_days})

    async def _evaluate_and_finalize(
        self,
        *,
        requirement,
        plan,
        progress=None,
    ):
        await self._emit(progress, "evaluation", "正在检查行程与路线可执行性", "running")
        evaluation = self.evaluator.evaluate(plan, requirement)
        plan = plan.model_copy(update={"evaluation": evaluation})
        await self._emit(progress, "evaluation", "行程与路线可执行性已检查", "completed")
        plan = synchronize_plan_overviews(plan)
        plan = plan.model_copy(update={"notices": collect_degradation_notices(plan)})
        await self._emit(progress, "complete", "完整旅行方案已生成", "completed")
        return TravelPlanResponse(requirement=requirement, plan=plan)

    async def _review_plan(self, *, response, progress=None):
        """Run the unified read-only review and copy-enhancement agent."""

        await self._emit(
            progress,
            "plan_review",
            "正在复核行程并增强每日说明、景点介绍和天气建议",
            "running",
        )
        if self.plan_review_agent is None:
            await self._emit(
                progress,
                "plan_review",
                "增强 Agent 暂不可用，保留规则评估、原景点介绍和天气兜底",
                "completed",
            )
            return {
                "status": "skipped",
                "approved": True,
                "issues": [],
                "weather_advice": [],
                "day_descriptions": [],
                "attraction_descriptions": [],
            }
        try:
            review = await self.plan_review_agent.review(
                plan=response.plan,
                requirement=response.requirement,
            )
        except Exception as exc:
            # The review layer is advisory. A provider timeout must never
            # discard a deterministic itinerary that already passed the
            # evaluator and has complete route/schedule data.
            logger.exception(
                "行程复核与内容增强 Agent 调用失败，保留规则兜底：error=%s",
                type(exc).__name__,
            )
            await self._emit(
                progress,
                "plan_review",
                f"增强 Agent 调用失败（{type(exc).__name__}），保留原文案和规则天气建议",
                "completed",
            )
            return {
                "status": "unavailable",
                "approved": True,
                "issues": [],
                "weather_advice": [],
                "day_descriptions": [],
                "attraction_descriptions": [],
            }
        label = "行程复核与内容增强完成"
        if not review.approved:
            label = f"复核发现 {len(review.issues)} 个提示，核心排程保持不变"
        await self._emit(progress, "plan_review", label, "completed")
        return review.model_dump(mode="json")

    async def _repair_plan(self, *, response, review, progress=None):
        """Apply advisory copy without changing the deterministic itinerary."""
        await self._emit(progress, "plan_repair", "正在检查是否需要安全修复", "running")
        updated, label = apply_plan_review(response, review)
        await self._emit(progress, "plan_repair", label, "completed")
        return updated

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
