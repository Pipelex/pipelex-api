import base64
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from api.routes.uploader import MAX_UPLOAD_BASE64_CHARS
from api.routes.uploader import router as uploader_router
from api.security import RequestUser, get_request_user

USER_A = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"

VALID_B64 = base64.b64encode(b"hello world").decode("ascii")
INVALID_B64 = "not valid base64!!!"


def _build_client(
    user: RequestUser | None,
    mocker: MockerFixture,
    store_uri: str = "pipelex-storage://x/assets/y.bin",
) -> tuple[TestClient, Any]:
    """Build a FastAPI TestClient with auth and storage provider mocked. Returns (client, store_mock)."""
    app = FastAPI()
    app.include_router(uploader_router)

    async def _override_user() -> RequestUser | None:
        return user

    app.dependency_overrides[get_request_user] = _override_user

    fake_storage = mocker.AsyncMock()
    fake_storage.store = mocker.AsyncMock(return_value=store_uri)
    mocker.patch("api.routes.uploader.get_storage_provider", return_value=fake_storage)

    return TestClient(app), fake_storage.store


class TestUploadEndpoint:
    def test_unauthenticated_returns_401(self, mocker: MockerFixture):
        client, _ = _build_client(None, mocker)
        response = client.post("/upload", json={"filename": "a.txt", "data": VALID_B64})
        assert response.status_code == 401
        assert response.json()["detail"]["error_type"] == "Unauthenticated"

    def test_anonymous_user_returns_401(self, mocker: MockerFixture):
        user = RequestUser(user_id="anonymous")
        client, _ = _build_client(user, mocker)
        response = client.post("/upload", json={"filename": "a.txt", "data": VALID_B64})
        assert response.status_code == 401
        assert response.json()["detail"]["error_type"] == "Unauthenticated"

    def test_invalid_base64_returns_400(self, mocker: MockerFixture):
        user = RequestUser(user_id=USER_A)
        client, _ = _build_client(user, mocker)
        response = client.post("/upload", json={"filename": "a.txt", "data": INVALID_B64})
        assert response.status_code == 400
        assert response.json()["detail"]["error_type"] == "InvalidBase64"

    def test_oversized_payload_rejected_at_validation(self, mocker: MockerFixture):
        user = RequestUser(user_id=USER_A)
        client, store_mock = _build_client(user, mocker)
        oversized = "A" * (MAX_UPLOAD_BASE64_CHARS + 1)
        response = client.post("/upload", json={"filename": "big.bin", "data": oversized})
        assert response.status_code == 422
        store_mock.assert_not_awaited()

    def test_extra_fields_rejected(self, mocker: MockerFixture):
        user = RequestUser(user_id=USER_A)
        client, _ = _build_client(user, mocker)
        response = client.post("/upload", json={"filename": "a.txt", "data": VALID_B64, "extra": "nope"})
        assert response.status_code == 422

    def test_happy_path(self, mocker: MockerFixture):
        user = RequestUser(user_id=USER_A)
        store_uri = f"pipelex-storage://{USER_A}/assets/abc.txt"
        client, _ = _build_client(user, mocker, store_uri=store_uri)

        response = client.post("/upload", json={"filename": "hello.txt", "data": VALID_B64, "content_type": "text/plain"})

        assert response.status_code == 200
        body = response.json()
        assert body["uri"] == store_uri
        assert body["filename"] == "hello.txt"

    @pytest.mark.parametrize(
        ("filename", "expected_ext"),
        [
            ("resume.pdf", "pdf"),
            ("photo.png", "png"),
            ("noext", "bin"),
        ],
    )
    def test_storage_key_uses_authenticated_user_id(self, mocker: MockerFixture, filename: str, expected_ext: str):
        """The S3 key must be scoped to the JWT user_id, never to 'anonymous'."""
        user = RequestUser(user_id=USER_A)
        client, store_mock = _build_client(user, mocker)

        client.post("/upload", json={"filename": filename, "data": VALID_B64})

        store_mock.assert_awaited_once()
        key = store_mock.await_args.kwargs["key"]
        assert key.startswith(f"{USER_A}/assets/")
        assert key.endswith(f".{expected_ext}")
