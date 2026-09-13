from datetime import datetime

from backend.models.travel_plan import AttractionStop, TravelDay, TravelPlan, TravelPlanResponse
from backend.models.requirement import TravelRequirement
from backend.travel_plans import TravelPlanRepository


class FakeSession:
    def __init__(self):
        self.rows = []
        self.rollback_called = False

    def scalar(self, statement):
        return max((row.version for row in self.rows), default=None)

    def add(self, row):
        row.id = len(self.rows) + 1
        row.created_at = datetime(2026, 9, 13, 13, 0)
        self.rows.append(row)

    def commit(self):
        return None

    def refresh(self, row):
        return None

    def rollback(self):
        self.rollback_called = True


def _result():
    requirement = TravelRequirement(
        province="浙江省", cities=["杭州市"], days=1,
        interests=["自然"], travel_scope="single_city",
    )
    plan = TravelPlan(
        province="浙江省",
        city_stays=[{"city": "杭州市", "days": 1}],
        city_plans=[{"city": "杭州市", "days": [{
            "day": 1, "attractions": ["西湖"], "description": "围绕自然风光安排当天的游览体验。",
        }]}],
        itinerary=[TravelDay(
            day=1, city="杭州市", city_adcode="330100", city_day=1,
            attractions=[AttractionStop(poi_id="A1", name="西湖", city="杭州市")],
            description="围绕自然风光安排当天的游览体验。",
        )],
    )
    return TravelPlanResponse(requirement=requirement, plan=plan)


def test_each_saved_plan_gets_next_session_version_and_json_snapshot():
    session = FakeSession()
    repository = TravelPlanRepository(session)

    first = repository.save("session-1", _result())
    second = repository.save("session-1", _result())

    assert first.version == 1
    assert second.version == 2
    assert session.rows[0].requirement_json["cities"] == ["杭州市"]
    assert session.rows[0].plan_json["itinerary"][0]["attractions"][0]["poi_id"] == "A1"
