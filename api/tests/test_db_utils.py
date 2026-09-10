"""Tests for api/utils/db_utils.py."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from api.utils import db_utils

if TYPE_CHECKING:
    import pytest

SENTINEL_PASSWORD = "hunter2-do-not-log-me"


class TestCredentialRedaction:
    """Pool construction logs its arguments; the password in the conninfo must not be among them."""

    def test_make_pool_does_not_log_the_password(self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        for name in [k for k in os.environ if k.startswith("PG")]:
            monkeypatch.delenv(name)
        monkeypatch.setenv("PGHOST", "db.example.internal")
        monkeypatch.setenv("PGDATABASE", "magic_db")
        monkeypatch.setenv("PGUSER", "magic_user")
        monkeypatch.setenv("PGPASSWORD", SENTINEL_PASSWORD)

        with (
            patch.object(db_utils.psycopg_pool, "ConnectionPool", return_value=MagicMock()) as pool_cls,
            patch.object(db_utils.atexit, "register"),
            caplog.at_level(logging.DEBUG, logger="api.utils.db_utils"),
        ):
            db_utils.make_pool()

        # The real pool still received the real secret ...
        assert f"password={SENTINEL_PASSWORD}" in pool_cls.call_args.kwargs["conninfo"]
        # ... and the log line named the connection without it.
        assert SENTINEL_PASSWORD not in caplog.text
        assert "Pool args" in caplog.text
        assert "host=db.example.internal" in caplog.text
        assert "dbname=magic_db" in caplog.text
        assert f"password={db_utils.REDACTED}" in caplog.text

    def test_redact_conninfo_masks_a_quoted_password(self) -> None:
        rendered = db_utils.redact_conninfo("host=h dbname=d user=u password='sp ace'")
        assert "sp ace" not in rendered
        assert rendered == f"dbname=d host=h password={db_utils.REDACTED} user=u"

    def test_redact_conninfo_does_not_echo_an_unparseable_string(self) -> None:
        assert SENTINEL_PASSWORD not in db_utils.redact_conninfo(f"password='{SENTINEL_PASSWORD}")

    def test_redact_credentials_leaves_non_secrets_alone(self) -> None:
        params = {"host": "localhost", "port": "5432", "password": SENTINEL_PASSWORD}
        assert db_utils.redact_credentials(params) == {"host": "localhost", "port": "5432", "password": db_utils.REDACTED}
        assert params["password"] == SENTINEL_PASSWORD, "the caller's dict is not mutated"
