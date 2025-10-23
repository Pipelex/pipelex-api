import pytest


@pytest.mark.gha_disabled
class TestMain:
    def test_main(self):
        import api.main  # noqa: F401, PLC0415
