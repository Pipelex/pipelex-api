from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from api.security import ForwardedIdentityHeader, RequestUser, get_request_user, no_auth
from tests.unit._constants import RoutePath

USER_ID = "11111111-1111-4111-1111-111111111111"


async def _whoami(user: Annotated[RequestUser | None, Depends(get_request_user)]) -> dict[str, str | None]:
    if user is None:
        return {"user_id": None}
    return {"user_id": user.user_id}


def _build_client() -> TestClient:
    app = FastAPI()
    app.add_api_route(RoutePath.WHOAMI, _whoami, methods=["GET"], dependencies=[Depends(no_auth)])
    return TestClient(app)


class TestNoAuthForwardedHeaders:
    def test_header_ignored_by_default(self, mocker: MockerFixture):
        """No TRUST_FORWARDED_IDENTITY_HEADERS env → X-User-Id is not trusted."""
        mocker.patch("api.security.get_optional_env", return_value=None)
        client = _build_client()
        headers: dict[str, str] = {ForwardedIdentityHeader.USER_ID: USER_ID}
        response = client.get(RoutePath.WHOAMI, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"user_id": None}

    def test_header_ignored_when_flag_not_true(self, mocker: MockerFixture):
        mocker.patch("api.security.get_optional_env", return_value="false")
        client = _build_client()
        headers: dict[str, str] = {ForwardedIdentityHeader.USER_ID: USER_ID}
        response = client.get(RoutePath.WHOAMI, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"user_id": None}

    def test_user_id_honored_when_flag_true(self, mocker: MockerFixture):
        """With the trust flag enabled, the runner reads X-User-Id and only that."""
        mocker.patch("api.security.get_optional_env", return_value="true")
        client = _build_client()
        # Extra X-User-* headers are noise — the runner ignores them by design.
        headers: dict[str, str] = {
            ForwardedIdentityHeader.USER_ID: USER_ID,
            "x-user-email": "evil@example.com",
            "x-user-sub": "spoofed#1",
            "x-auth-method": "gateway",
        }
        response = client.get(RoutePath.WHOAMI, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"user_id": USER_ID}

    @pytest.mark.parametrize("user_id", ["", "anonymous"])
    def test_missing_or_anonymous_user_id_stays_anonymous(self, mocker: MockerFixture, user_id: str):
        """No `X-User-Id`, or the literal "anonymous" sentinel, means no user."""
        mocker.patch("api.security.get_optional_env", return_value="true")
        client = _build_client()
        headers: dict[str, str] = {ForwardedIdentityHeader.USER_ID: user_id} if user_id else {}
        response = client.get(RoutePath.WHOAMI, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"user_id": None}
