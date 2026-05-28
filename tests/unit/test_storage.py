from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from api.exception_handlers import register_exception_handlers
from api.routes.storage import (
    expires_at_from_presigned,
    is_presigned,
    parse_storage_uri,
)
from api.routes.storage import (
    router as storage_router,
)
from api.security import RequestUser, get_request_user

USER_A = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
FILE_HASH = "cccccccc-cccc-4ccc-cccc-cccccccccccc"

UPLOAD_URI = f"pipelex-storage://{USER_A}/assets/{FILE_HASH}.pdf"
RUN_URI = f"pipelex-storage://{USER_A}/results/run-123/assets/{FILE_HASH}.png"
STRANGER_URI = f"pipelex-storage://{USER_B}/assets/{FILE_HASH}.pdf"

PRESIGNED_URL = (
    "https://example-bucket.s3.us-west-2.amazonaws.com/"
    f"{USER_A}/assets/{FILE_HASH}.pdf"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Credential=AKIA%2F20260416%2Fus-west-2%2Fs3%2Faws4_request"
    "&X-Amz-Date=20260416T120000Z"
    "&X-Amz-Expires=900"
    "&X-Amz-SignedHeaders=host"
    "&X-Amz-Signature=deadbeef"
)

NON_PRESIGNED_URL = f"https://example-bucket.s3.us-west-2.amazonaws.com/{USER_A}/assets/{FILE_HASH}.pdf"


def _build_client(user: RequestUser | None, mocker: MockerFixture, storage_url: str | None) -> TestClient:
    """Build a FastAPI TestClient with auth and storage provider mocked."""
    app = FastAPI()
    app.include_router(storage_router)
    register_exception_handlers(app)

    async def _override_user() -> RequestUser | None:
        return user

    app.dependency_overrides[get_request_user] = _override_user

    fake_storage = mocker.AsyncMock()
    fake_storage.public_url = mocker.AsyncMock(return_value=storage_url)
    mocker.patch("api.routes.storage.get_storage_provider", return_value=fake_storage)

    return TestClient(app)


class TestStorageEndpoint:
    @pytest.mark.parametrize(
        ("uri", "expected_user", "expected_ext"),
        [
            (UPLOAD_URI, USER_A, "pdf"),
            (RUN_URI, USER_A, "png"),
            (f"pipelex-storage://{USER_A}/a/b/c/d/e/file.tar", USER_A, "tar"),
        ],
    )
    def test_parse_storage_uri_valid(self, uri: str, expected_user: str, expected_ext: str):
        user_id, ext = parse_storage_uri(uri)
        assert user_id == expected_user
        assert ext == expected_ext

    @pytest.mark.parametrize(
        "uri",
        [
            "https://example.com/file.pdf",  # wrong scheme
            "pipelex-storage://not-a-uuid/assets/x.pdf",  # bad user_id
            f"pipelex-storage://{USER_A}/..//assets/x.pdf",  # traversal
            f"pipelex-storage://{USER_A}/./assets/x.pdf",  # single-dot
            f"pipelex-storage://{USER_A}/assets/x",  # no extension
            f"pipelex-storage://{USER_A}/assets/x.",  # trailing dot
            f"pipelex-storage://{USER_A}/assets/.hidden",  # leading dot only
            f"pipelex-storage://{USER_A}/assets/x.p@f",  # bad ext chars
            f"pipelex-storage://{USER_A}/",  # empty trailing
            f"pipelex-storage://{USER_A}",  # only user_id
            "pipelex-storage://",  # empty path
            "",  # empty
        ],
    )
    def test_parse_storage_uri_invalid(self, uri: str):
        with pytest.raises(ValueError, match=r".+"):
            parse_storage_uri(uri)

    def test_is_presigned_detects_signature(self):
        assert is_presigned(PRESIGNED_URL) is True
        assert is_presigned(NON_PRESIGNED_URL) is False

    def test_expires_at_uses_amz_headers(self):
        expires_at = expires_at_from_presigned(PRESIGNED_URL)
        expected = datetime(2026, 4, 16, 12, 15, 0, tzinfo=UTC)
        assert expires_at == expected

    def test_expires_at_falls_back_when_no_amz_params(self):
        before = datetime.now(UTC)
        expires_at = expires_at_from_presigned("https://example.com/x?foo=bar")
        after = datetime.now(UTC) + timedelta(seconds=900)
        assert before + timedelta(seconds=890) <= expires_at <= after

    def test_happy_path_upload_uri(self, mocker: MockerFixture):
        user = RequestUser(user_id=USER_A)
        client = _build_client(user, mocker, PRESIGNED_URL)

        response = client.post("/resolve-storage-url", json={"uri": UPLOAD_URI})

        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        assert body["url"] == PRESIGNED_URL
        assert body["content_type"] == "application/pdf"
        assert body["expires_at"].startswith("2026-04-16T12:15:00")

    def test_happy_path_pipeline_output_uri(self, mocker: MockerFixture):
        """Pipeline-generated URIs (with /results/{run_id}/assets/...) must resolve too."""
        user = RequestUser(user_id=USER_A)
        client = _build_client(user, mocker, PRESIGNED_URL)

        response = client.post("/resolve-storage-url", json={"uri": RUN_URI})

        assert response.status_code == 200
        assert response.json()["url"] == PRESIGNED_URL

    def test_unauthenticated_returns_401(self, mocker: MockerFixture):
        client = _build_client(None, mocker, PRESIGNED_URL)
        response = client.post("/resolve-storage-url", json={"uri": UPLOAD_URI})
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json()["error_type"] == "Unauthenticated"

    def test_anonymous_user_returns_401(self, mocker: MockerFixture):
        user = RequestUser(user_id="anonymous")
        client = _build_client(user, mocker, PRESIGNED_URL)
        response = client.post("/resolve-storage-url", json={"uri": UPLOAD_URI})
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.json()["error_type"] == "Unauthenticated"

    def test_cross_user_returns_403(self, mocker: MockerFixture):
        user = RequestUser(user_id=USER_A)
        client = _build_client(user, mocker, PRESIGNED_URL)

        response = client.post("/resolve-storage-url", json={"uri": STRANGER_URI})

        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "Forbidden"

    def test_malformed_uri_returns_400(self, mocker: MockerFixture):
        user = RequestUser(user_id=USER_A)
        client = _build_client(user, mocker, PRESIGNED_URL)

        response = client.post("/resolve-storage-url", json={"uri": f"pipelex-storage://{USER_A}/../secret.pdf"})

        assert response.status_code == 400
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "InvalidUri"

    def test_signed_urls_disabled_returns_500(self, mocker: MockerFixture):
        """When storage falls back to a non-presigned URL, endpoint must 500 (not hand out a broken URL)."""
        user = RequestUser(user_id=USER_A)
        client = _build_client(user, mocker, NON_PRESIGNED_URL)

        response = client.post("/resolve-storage-url", json={"uri": UPLOAD_URI})

        assert response.status_code == 500
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "PresignFailed"

    def test_storage_returns_none_returns_500(self, mocker: MockerFixture):
        user = RequestUser(user_id=USER_A)
        client = _build_client(user, mocker, None)

        response = client.post("/resolve-storage-url", json={"uri": UPLOAD_URI})

        assert response.status_code == 500
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "PresignFailed"

    def test_extra_fields_rejected(self, mocker: MockerFixture):
        user = RequestUser(user_id=USER_A)
        client = _build_client(user, mocker, PRESIGNED_URL)

        response = client.post("/resolve-storage-url", json={"uri": UPLOAD_URI, "extra": "nope"})

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "ValidationError"

    def test_uri_too_long_rejected(self, mocker: MockerFixture):
        user = RequestUser(user_id=USER_A)
        client = _build_client(user, mocker, PRESIGNED_URL)

        long_uri = f"pipelex-storage://{USER_A}/" + ("a/" * 300) + f"{FILE_HASH}.pdf"
        response = client.post("/resolve-storage-url", json={"uri": long_uri})

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "ValidationError"

    @pytest.mark.parametrize(
        "backend_error",
        [
            OSError("permission denied"),
            BotoCoreError(),
            ClientError({"Error": {"Code": "ExpiredToken", "Message": "boom"}}, "GeneratePresignedUrl"),
        ],
    )
    def test_backend_storage_failure_returns_presign_failed(self, mocker: MockerFixture, backend_error: Exception):
        """Pipelex storage providers have documented wrapping gaps (pipelex-changes.md Stage 7 #12/#13):
        `LocalStorageProvider.public_url` leaks raw `OSError`, S3 presign leaks `BotoCoreError` /
        `ClientError` on credential-retrieval or endpoint-resolution failures. The narrow catch
        in `resolve_storage_url` must classify these as `PresignFailed` (500) instead of
        letting them escape to the catch-all `handle_unexpected_error`.
        """
        user = RequestUser(user_id=USER_A)
        app = FastAPI()
        app.include_router(storage_router)
        register_exception_handlers(app)

        async def _override_user() -> RequestUser | None:
            return user

        app.dependency_overrides[get_request_user] = _override_user

        fake_storage = mocker.AsyncMock()
        fake_storage.public_url = mocker.AsyncMock(side_effect=backend_error)
        mocker.patch("api.routes.storage.get_storage_provider", return_value=fake_storage)

        client = TestClient(app)
        response = client.post("/resolve-storage-url", json={"uri": UPLOAD_URI})

        assert response.status_code == 500
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["error_type"] == "PresignFailed"
