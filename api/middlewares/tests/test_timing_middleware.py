"""Tests for TimingMiddleware's handling of the request it logs."""

from __future__ import annotations

import falcon
import falcon.testing

from api.middlewares.timing import TimingMiddleware


class _Ok:
    def on_get(self, req: falcon.Request, resp: falcon.Response) -> None:
        del req
        resp.media = {"ok": True}


def _app() -> falcon.App:
    app = falcon.App(middleware=[TimingMiddleware()])
    app.add_route("/x", _Ok())
    return app


class TestMissingUserAgent:
    """The timing line logs the User-Agent; a request without one must still be an ordinary request.

    `req.get_header("User-Agent", "-")` passed `"-"` as `required`, not `default`, so the missing
    header raised HTTPMissingHeader from process_response -- a 400 on every route.
    """

    def test_request_without_user_agent_is_200(self) -> None:
        # `simulate_get` cannot express "no User-Agent": `create_environ` injects a default one
        # whenever the caller does not name it, so the header is deleted from the environ instead.
        env = falcon.testing.create_environ(path="/x")
        del env["HTTP_USER_AGENT"]
        start_response = falcon.testing.StartResponseMock()
        body = b"".join(_app()(env, start_response))
        assert start_response.status == falcon.HTTP_200, body
        headers = {name.lower(): value for name, value in start_response.headers}
        assert headers["server-timing"].startswith("total;dur=")

    def test_request_with_user_agent_is_200(self) -> None:
        result = falcon.testing.TestClient(_app()).simulate_get("/x", headers={"User-Agent": "probe/1.0"})
        assert result.status == falcon.HTTP_200
