"""Unit tests for the per-request logging contextvars."""

import pytest

from api.logging_context import bound_request_context, get_request_id, get_route_path


class TestLoggingContext:
    def test_getters_return_none_outside_request(self):
        assert get_request_id() is None
        assert get_route_path() is None

    def test_getters_return_bound_values(self):
        with bound_request_context(request_id="REQ123", route_path="/v1/start"):
            assert get_request_id() == "REQ123"
            assert get_route_path() == "/v1/start"

    def test_context_resets_on_clean_exit(self):
        with bound_request_context(request_id="REQ123", route_path="/v1/start"):
            pass
        assert get_request_id() is None
        assert get_route_path() is None

    def test_context_resets_when_body_raises(self):
        def _raise_inside_context() -> None:
            with bound_request_context(request_id="REQ123", route_path="/x"):
                msg = "boom"
                raise RuntimeError(msg)

        with pytest.raises(RuntimeError):
            _raise_inside_context()
        assert get_request_id() is None
        assert get_route_path() is None
