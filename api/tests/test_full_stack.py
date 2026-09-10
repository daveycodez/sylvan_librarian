"""Requests through the same Falcon app production serves: every middleware, the sink, the error serializer.

The unit tests drive `APIResource._handle` directly or assemble one or two middlewares by hand, so a
bug that only shows up in the interaction between them -- a header a late middleware rejects after an
early one has already transformed the body, say -- passes every one of them. These build the app via
`ApiWorker.get_api`, the factory `entrypoint.py` uses, against the session postgres container.
"""

from __future__ import annotations

import multiprocessing
import time
from typing import TYPE_CHECKING

import falcon
import falcon.testing
import pytest

from api.api_worker import ApiWorker
from api.settings import settings
from api.tests.support import override_attr

if TYPE_CHECKING:
    from collections.abc import Generator

    from api.api_resource import APIResource


@pytest.fixture(name="app", scope="module")
def app_fixture(postgres_container: None) -> Generator[falcon.App]:
    """The production app, schema set up against the session container, import fast-pathed.

    `last_import_time` is now so `APIResource.__init__`'s own `import_data()` call takes its fast path
    rather than fetching Scryfall bulk data; the schema is set up for real (an unset
    `schema_setup_event`) so the query-log writer and the SQL search path have their tables.
    """
    app = ApiWorker.get_api(
        import_guard=multiprocessing.RLock(),
        last_import_time=multiprocessing.Value("d", time.time(), lock=True),
        schema_setup_event=multiprocessing.Event(),
        cache_generation=multiprocessing.Value("i", 0, lock=True),
        engine_reload_guard=multiprocessing.Lock(),
    )
    resource = _resource_of(app)
    override_attr(resource.app_context, "setup_complete", lambda: True)
    yield app
    resource.app_context.reader_pool.close()
    resource.app_context.writer_pool.close()


def _resource_of(app: falcon.App) -> APIResource:
    """The APIResource behind the app's single sink."""
    (_prefix, sink, _), *_ = app._sinks
    return sink.__self__


@pytest.fixture(name="client")
def client_fixture(app: falcon.App) -> falcon.testing.TestClient:
    return falcon.testing.TestClient(app)


def _simulate_without_user_agent(app: falcon.App, path: str) -> tuple[str, bytes]:
    """GET `path` with no User-Agent header at all.

    `simulate_get` cannot express this: `falcon.testing.create_environ` injects a default User-Agent
    whenever the caller does not name one, so the header has to be deleted from the environ.
    """
    env = falcon.testing.create_environ(path=path)
    del env["HTTP_USER_AGENT"]
    start_response = falcon.testing.StartResponseMock()
    body = b"".join(app(env, start_response))
    return start_response.status, body


class TestMissingUserAgent:
    """A request with no User-Agent header is an ordinary request.

    TimingMiddleware logged the User-Agent with `req.get_header("User-Agent", "-")`, whose second
    positional parameter is `required`, not a default: every UA-less request -- curl -H 'User-Agent:',
    many monitors, some proxies -- got a 400 "Missing header value" from the *last* middleware to run,
    after compression had already set Content-Encoding on the body it was about to discard.
    """

    def test_get_pid_without_user_agent_is_200(self, app: falcon.App) -> None:
        status, body = _simulate_without_user_agent(app, "/get_pid")
        assert status == falcon.HTTP_200, body

    def test_with_user_agent_is_still_200(self, client: falcon.testing.TestClient) -> None:
        result = client.simulate_get("/get_pid")
        assert result.status == falcon.HTTP_200


class TestResponseCacheInvalidation:
    """An import must not leave the cross-worker response cache serving the old corpus."""

    def test_bumping_the_generation_misses_the_response_cache(self, app: falcon.App, client: falcon.testing.TestClient) -> None:
        resource = _resource_of(app)
        saved = settings.enable_cache
        settings.enable_cache = True
        try:
            # robots.txt: cacheable (no no-store header) and independent of the database.
            assert client.simulate_get("/robots.txt").headers.get("X-Cache") == "miss"
            assert client.simulate_get("/robots.txt").headers.get("X-Cache") == "hit"
            resource.app_context.bump_cache_generation()
            assert client.simulate_get("/robots.txt").headers.get("X-Cache") == "miss"
        finally:
            settings.enable_cache = saved
