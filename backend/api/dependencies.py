from fastapi import Depends

from backend.application import PlanningApplicationService
from backend.config import Settings, get_settings


def get_planning_application_service(
    settings: Settings = Depends(get_settings),
) -> PlanningApplicationService:
    """Compose the application service at the HTTP boundary."""

    return PlanningApplicationService(settings)
