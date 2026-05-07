from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from api.security import RequestUser, get_request_user, no_auth

FORWARDED_HEADERS = {
    "x-user-email": "evil@example.com",
    "x-user-sub": "spoofed#1",
    "x-user-id": "11111111-1111-4111-1111-111111111111",
    "x-auth-method": "gateway",
}


async def _whoami(user: Annotated[RequestUser | None, Depends(get_request_user)]) -> dict[str, str | None]:
    if user is None:
        return {"email": None, "sub": None, "user_id": None}
    return {"email": user.email, "sub": user.sub, "user_id": user.user_id}


def _build_client() -> TestClient:
    app = FastAPI()
    app.add_api_route("/whoami", _whoami, methods=["GET"], dependencies=[Depends(no_auth)])
    return TestClient(app)


class TestNoAuthForwardedHeaders:
    def test_headers_ignored_by_default(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value=None)
        client = _build_client()
        response = client.get("/whoami", headers=FORWARDED_HEADERS)
        assert response.status_code == 200
        assert response.json() == {"email": None, "sub": None, "user_id": None}

    def test_headers_ignored_when_flag_not_true(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value="false")
        client = _build_client()
        response = client.get("/whoami", headers=FORWARDED_HEADERS)
        assert response.status_code == 200
        assert response.json() == {"email": None, "sub": None, "user_id": None}

    def test_headers_honored_when_flag_true(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value="true")
        client = _build_client()
        response = client.get("/whoami", headers=FORWARDED_HEADERS)
        assert response.status_code == 200
        assert response.json() == {
            "email": "evil@example.com",
            "sub": "spoofed#1",
            "user_id": "11111111-1111-4111-1111-111111111111",
        }

    @pytest.mark.parametrize("missing", ["x-user-email", "x-user-sub"])
    def test_partial_headers_stay_anonymous_even_when_trusted(self, mocker: MockerFixture, missing: str):
        mocker.patch("api.security.get_optional_env", return_value="true")
        client = _build_client()
        headers = {key: value for key, value in FORWARDED_HEADERS.items() if key != missing}
        response = client.get("/whoami", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"email": None, "sub": None, "user_id": None}
