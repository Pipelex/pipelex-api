"""Tests for verify_jwt and verify_api_key in api/security."""

from typing import Annotated

import jwt
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from api.main import register_exception_handlers
from api.security import RequestUser, get_request_user, verify_api_key, verify_jwt
from tests.unit._constants import RoutePath

JWT_SECRET = "test-jwt-secret-do-not-use-in-prod"
API_KEY = "test-api-key-static"
USER_ID_UUID = "11111111-1111-4111-1111-111111111111"


async def _whoami_jwt(
    request: Request,
    _payload: Annotated[dict[str, object], Depends(verify_jwt)],
    user: Annotated[RequestUser | None, Depends(get_request_user)],
) -> dict[str, str | None]:
    _ = request
    if user is None:
        return {"user_id": None}
    return {"user_id": user.user_id}


async def _ping_api_key(_token: Annotated[str, Depends(verify_api_key)]) -> dict[str, str]:
    return {"ok": "yes"}


def _build_jwt_client() -> TestClient:
    app = FastAPI()
    app.add_api_route(RoutePath.WHOAMI, _whoami_jwt, methods=["GET"])
    register_exception_handlers(app)
    return TestClient(app)


def _build_api_key_client() -> TestClient:
    app = FastAPI()
    app.add_api_route(RoutePath.PING, _ping_api_key, methods=["GET"])
    register_exception_handlers(app)
    return TestClient(app)


class TestSecurityVerifiers:
    def test_jwt_happy_path_user_id_claim(self, mocker: MockerFixture):
        """Preferred claim: explicit `user_id` containing a UUID."""
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        token = jwt.encode({"user_id": USER_ID_UUID}, JWT_SECRET, algorithm="HS256")
        response = client.get(RoutePath.WHOAMI, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == {"user_id": USER_ID_UUID}

    def test_jwt_sub_only_is_rejected(self, mocker: MockerFixture):
        """A JWT with only `sub` (and no `user_id`) is rejected.

        We deliberately do NOT fall back to `sub`: storage URIs require the
        owner segment to be a UUID, and OAuth providers' `sub` values
        (e.g. `"google#abc"`) would let a caller write to S3 keys that
        `/resolve-storage-url` would later refuse to resolve. Deployments
        using OAuth must mint their own `user_id` claim.
        """
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        token = jwt.encode({"sub": "google#abc"}, JWT_SECRET, algorithm="HS256")
        response = client.get(RoutePath.WHOAMI, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "InvalidToken"

    def test_jwt_missing_user_id_claim_rejected(self, mocker: MockerFixture):
        """No `user_id` claim means no caller identifier — reject."""
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        token = jwt.encode({"iat": 0}, JWT_SECRET, algorithm="HS256")
        response = client.get(RoutePath.WHOAMI, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json()["error_type"] == "InvalidToken"

    @pytest.mark.parametrize(
        "non_uuid_user_id",
        [
            "google#abc",
            "user-123",
            "11111111-1111-4111-1111-11111111111",
            "../etc/passwd",
            "a" * 36,
            "11111111111141111111111111111111111Z",
        ],
    )
    def test_jwt_non_uuid_user_id_rejected(self, mocker: MockerFixture, non_uuid_user_id: str):
        """A non-UUID `user_id` claim must be rejected.

        Storage URIs require the owner segment to match
        `^[a-f0-9-]{36}$` (`_USER_ID_REGEX` in `routes/storage.py`). If the
        auth layer accepted looser values, an authenticated upload would
        write S3 keys under a non-UUID owner segment and
        `/resolve-storage-url` would later refuse to resolve them.
        """
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        token = jwt.encode({"user_id": non_uuid_user_id}, JWT_SECRET, algorithm="HS256")
        response = client.get(RoutePath.WHOAMI, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json()["error_type"] == "InvalidToken"

    def test_jwt_invalid_token_rejected(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        response = client.get(RoutePath.WHOAMI, headers={"Authorization": "Bearer not.a.real.token"})
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json()["error_type"] == "InvalidToken"

    def test_jwt_wrong_secret_rejected(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        token = jwt.encode({"user_id": USER_ID_UUID}, "different-secret", algorithm="HS256")
        response = client.get(RoutePath.WHOAMI, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    def test_jwt_missing_secret_returns_500(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=None)
        client = _build_jwt_client()
        token = jwt.encode({"user_id": USER_ID_UUID}, "anything", algorithm="HS256")
        response = client.get(RoutePath.WHOAMI, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 500

    def test_api_key_happy_path(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=API_KEY)
        client = _build_api_key_client()
        response = client.get(RoutePath.PING, headers={"Authorization": f"Bearer {API_KEY}"})
        assert response.status_code == 200
        assert response.json() == {"ok": "yes"}

    def test_api_key_wrong_key_rejected(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=API_KEY)
        client = _build_api_key_client()
        response = client.get(RoutePath.PING, headers={"Authorization": "Bearer wrong-key"})
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json()["error_type"] == "InvalidToken"

    def test_api_key_missing_env_returns_500(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=None)
        client = _build_api_key_client()
        response = client.get(RoutePath.PING, headers={"Authorization": "Bearer anything"})
        assert response.status_code == 500
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "ServerMisconfigured"

    @pytest.mark.parametrize(
        "authorization",
        [
            None,  # header absent
            "",  # header present but empty
            "Bearer",  # bare scheme, no token
            "Basic dXNlcjpwYXNz",  # wrong scheme entirely
        ],
    )
    def test_api_key_missing_or_malformed_header_rejected(self, mocker: MockerFixture, authorization: str | None):
        """Missing / empty / non-Bearer Authorization → RFC 7807 401, not the old shape.

        `HTTPBearer(auto_error=False)` means none of these branches go through
        FastAPI's default `HTTPException` handler (which would emit
        `application/json` `{"detail": "Not authenticated"}`). Instead
        `verify_api_key` sees `credentials is None` and calls
        `raise_unauthenticated(...)` — same problem document as every other 401.
        """
        mocker.patch("api.security.get_optional_env", return_value=API_KEY)
        client = _build_api_key_client()
        headers: dict[str, str] = {} if authorization is None else {"Authorization": authorization}
        response = client.get(RoutePath.PING, headers=headers)
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json()["error_type"] == "Unauthenticated"

    @pytest.mark.parametrize(
        "authorization",
        [
            None,
            "",
            "Bearer",
            "Basic dXNlcjpwYXNz",
        ],
    )
    def test_jwt_missing_or_malformed_header_rejected(self, mocker: MockerFixture, authorization: str | None):
        """JWT counterpart of the API-key case: same RFC 7807 401 shape."""
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        headers: dict[str, str] = {} if authorization is None else {"Authorization": authorization}
        response = client.get(RoutePath.WHOAMI, headers=headers)
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json()["error_type"] == "Unauthenticated"
