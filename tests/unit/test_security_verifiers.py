"""Tests for verify_jwt and verify_api_key in api/security."""

from typing import Annotated

import jwt
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from api.security import RequestUser, get_request_user, verify_api_key, verify_jwt

JWT_SECRET = "test-jwt-secret-do-not-use-in-prod"
API_KEY = "test-api-key-static"


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
    app.add_api_route("/whoami", _whoami_jwt, methods=["GET"])
    return TestClient(app)


def _build_api_key_client() -> TestClient:
    app = FastAPI()
    app.add_api_route("/ping", _ping_api_key, methods=["GET"])
    return TestClient(app)


class TestSecurityVerifiers:
    def test_jwt_happy_path_user_id_claim(self, mocker: MockerFixture):
        """Preferred claim: explicit `user_id`. Anything else in the JWT is ignored."""
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        token = jwt.encode({"user_id": "uuid-1", "sub": "ignored", "email": "ignored@x.com"}, JWT_SECRET, algorithm="HS256")
        response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == {"user_id": "uuid-1"}

    def test_jwt_falls_back_to_sub_claim(self, mocker: MockerFixture):
        """When no `user_id` claim is present, fall back to the standard `sub` claim."""
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        token = jwt.encode({"sub": "google#abc"}, JWT_SECRET, algorithm="HS256")
        response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == {"user_id": "google#abc"}

    def test_jwt_missing_caller_identifier_rejected(self, mocker: MockerFixture):
        """No `user_id` and no `sub` means no caller identifier — reject."""
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        token = jwt.encode({"email": "x@x.com"}, JWT_SECRET, algorithm="HS256")
        response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        body = response.json()
        assert isinstance(body["detail"], dict)

    def test_jwt_invalid_token_rejected(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        response = client.get("/whoami", headers={"Authorization": "Bearer not.a.real.token"})
        assert response.status_code == 401
        assert response.json()["detail"]["error_type"] == "InvalidToken"

    def test_jwt_wrong_secret_rejected(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=JWT_SECRET)
        client = _build_jwt_client()
        token = jwt.encode({"user_id": "uuid-1"}, "different-secret", algorithm="HS256")
        response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    def test_jwt_missing_secret_returns_500(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=None)
        client = _build_jwt_client()
        token = jwt.encode({"user_id": "uuid-1"}, "anything", algorithm="HS256")
        response = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 500

    def test_api_key_happy_path(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=API_KEY)
        client = _build_api_key_client()
        response = client.get("/ping", headers={"Authorization": f"Bearer {API_KEY}"})
        assert response.status_code == 200
        assert response.json() == {"ok": "yes"}

    def test_api_key_wrong_key_rejected(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=API_KEY)
        client = _build_api_key_client()
        response = client.get("/ping", headers={"Authorization": "Bearer wrong-key"})
        assert response.status_code == 401
        assert response.json()["detail"]["error_type"] == "InvalidToken"

    def test_api_key_missing_env_returns_500(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=None)
        client = _build_api_key_client()
        response = client.get("/ping", headers={"Authorization": "Bearer anything"})
        assert response.status_code == 500
        assert response.json()["detail"]["error_type"] == "ServerMisconfigured"

    @pytest.mark.parametrize("missing_header", [True, False])
    def test_api_key_missing_header_rejected(self, mocker: MockerFixture, missing_header: bool):
        mocker.patch("api.security.get_optional_env", return_value=API_KEY)
        client = _build_api_key_client()
        headers: dict[str, str] = {} if missing_header else {"Authorization": ""}
        response = client.get("/ping", headers=headers)
        # Missing or empty Authorization header → HTTPBearer dependency rejects with 403/401.
        assert response.status_code in {401, 403}
