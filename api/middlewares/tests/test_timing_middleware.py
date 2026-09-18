"""Tests for TimingMiddleware: the request it logs, and its response-side duties beyond the timing line."""

from __future__ import annotations

import gzip

import falcon
import falcon.testing
import pytest

from api.middlewares.compression.compression_mod import CompressionMiddleware
from api.middlewares.timing import TimingMiddleware, is_server_error

# Comfortably over CompressionMiddleware's MIN_SIZE, so the compressed stack below really compresses
# instead of taking the short-response bail-out and leaving the interaction untested.
PADDING = "x" * 500


class _Padded:
    """A route whose body is big enough to be worth compressing."""

    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        del req
        resp.media = {"pad": PADDING}


def _app(*downstream: object) -> falcon.App:
    """An app with TimingMiddleware first, so its process_response runs last as it does in get_api."""
    app = falcon.App(middleware=[TimingMiddleware(), *downstream])
    app.add_route("/x", _Padded())
    return app


def _get(
    app: falcon.App,
    *,
    user_agent: str | None,
    accept_encoding: str | None = None,
) -> tuple[str, dict[str, str], bytes]:
    """GET /x, with the User-Agent header genuinely absent when `user_agent` is None.

    `simulate_get` cannot express "no User-Agent": `falcon.testing.create_environ` injects a default
    one whenever the caller does not name it, so the header has to be deleted from the environ.

    Args:
        app: The app to call as a WSGI callable.
        user_agent: User-Agent to send, or None to send no such header at all.
        accept_encoding: Accept-Encoding to send, or None to send no such header.

    Returns:
        The response status, its headers lowercased by name, and the raw body bytes.
    """
    headers = {} if accept_encoding is None else {"Accept-Encoding": accept_encoding}
    env = falcon.testing.create_environ(path="/x", headers=headers)
    if user_agent is None:
        del env["HTTP_USER_AGENT"]
    else:
        env["HTTP_USER_AGENT"] = user_agent
    start_response = falcon.testing.StartResponseMock()
    body = b"".join(app(env, start_response))
    return start_response.status, {name.lower(): value for name, value in start_response.headers}, body


class TestMissingUserAgent:
    """The timing line logs the User-Agent; a request without one must still be an ordinary request.

    `req.get_header("User-Agent", "-")` passed `"-"` as `required`, not `default`, so the missing
    header raised HTTPMissingHeader from process_response -- a 400 on every route.
    """

    def test_request_without_user_agent_is_200(self) -> None:
        status, headers, body = _get(_app(), user_agent=None)
        assert status == falcon.HTTP_200, body
        # `in`, not startswith: with a downstream middleware recording spans, "total" is not first.
        assert "total;dur=" in headers["server-timing"], headers["server-timing"]

    def test_request_with_user_agent_is_200(self) -> None:
        status, _headers, body = _get(_app(), user_agent="probe/1.0")
        assert status == falcon.HTTP_200, body

    def test_response_stays_decodable_behind_compression(self) -> None:
        """The client-visible damage, not just the status code.

        TimingMiddleware is first in `get_api`'s list, so its process_response runs *after*
        CompressionMiddleware has replaced the body and set Content-Encoding. Falcon does not
        unwind those headers when a later process_response raises, so the 400 went out claiming
        an encoding its plain-JSON body did not have and the client could not decode it. A status
        assertion alone does not cover that, so this decompresses the body it was handed.
        """
        status, headers, body = _get(_app(CompressionMiddleware()), user_agent=None, accept_encoding="gzip")
        assert status == falcon.HTTP_200, body
        assert headers.get("content-encoding") == "gzip", headers
        assert PADDING in gzip.decompress(body).decode()
        assert "total;dur=" in headers["server-timing"], headers["server-timing"]


class _CacheableThenFails:
    """A handler that sets a public Cache-Control and then raises, as /search used to."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        del req
        resp.set_header("Cache-Control", "public, max-age=90")
        raise self._error


class _Ok:
    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        del req
        resp.set_header("Cache-Control", "public, max-age=90")
        resp.media = {"ok": True}


def _client(handler: object) -> falcon.testing.TestClient:
    app = falcon.App(middleware=[TimingMiddleware()])
    app.add_route("/x", handler)
    return falcon.testing.TestClient(app)


class TestServerErrorsAreNotCacheable:
    """Falcon keeps headers a handler set before raising; a 5xx must not go out with a public max-age."""

    @pytest.mark.parametrize(
        argnames=["error", "status"],
        argvalues=[
            (falcon.HTTPServiceUnavailable(title="down"), falcon.HTTP_503),
            (falcon.HTTPInternalServerError(title="broken"), falcon.HTTP_500),
        ],
        ids=["503", "500"],
    )
    def test_cache_control_is_stripped_from_5xx(self, error: Exception, status: str) -> None:
        result = _client(_CacheableThenFails(error)).simulate_get("/x")
        assert result.status == status
        assert result.headers.get("Cache-Control") is None

    def test_cache_control_survives_on_success(self) -> None:
        result = _client(_Ok()).simulate_get("/x")
        assert result.status == falcon.HTTP_200
        assert result.headers.get("Cache-Control") == "public, max-age=90"

    def test_cache_control_survives_on_4xx(self) -> None:
        """A 4xx is the route's own decision (a 400 is even deliberately cacheable); only 5xx is stripped."""
        result = _client(_CacheableThenFails(falcon.HTTPBadRequest(title="bad"))).simulate_get("/x")
        assert result.status == falcon.HTTP_400
        assert result.headers.get("Cache-Control") == "public, max-age=90"


@pytest.mark.parametrize(
    argnames=["status", "expected"],
    argvalues=[
        ("500 Internal Server Error", True),
        ("503 Service Unavailable", True),
        (502, True),
        ("200 OK", False),
        ("404 Not Found", False),
        (None, False),
    ],
)
def test_is_server_error(status: str | int | None, expected: bool) -> None:
    assert is_server_error(status) is expected
