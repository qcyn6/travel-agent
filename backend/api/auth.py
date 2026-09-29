from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth import CurrentUser, UserRepository, create_access_token, get_current_user
from backend.auth.security import normalize_username, verify_password
from backend.config import Settings, get_settings
from backend.database import get_db
from backend.models.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _user_model(row) -> UserResponse:
    return UserResponse.model_validate(row)


def _token(row, settings: Settings) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(
            user_id=row.id,
            role=row.role,
            secret=settings.auth_secret,
            expires_minutes=settings.access_token_expire_minutes,
        ),
        user=_user_model(row),
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(
    payload: RegisterRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    repository = UserRepository(session)
    if repository.get_by_username(payload.username) is not None:
        raise HTTPException(status_code=409, detail="该用户名已经注册")
    try:
        row = repository.create(
            username=payload.username,
            password=payload.password,
            display_name=payload.display_name,
        )
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="该用户名已经注册") from exc
    return _token(row, settings)


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    row = UserRepository(session).get_by_username(normalize_username(payload.username))
    if row is None or not row.is_active or not verify_password(payload.password, row.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码不正确",
        )
    return _token(row, settings)


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser | None = Depends(get_current_user)) -> UserResponse:
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return UserResponse(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        is_active=True,
        # The lightweight principal intentionally does not expose timestamps.
        # The frontend only needs the identity for the session gate.
        created_at=None,  # type: ignore[arg-type]
    )


__all__ = ["router"]
