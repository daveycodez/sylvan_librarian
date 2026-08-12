"""Integration tests for the Scryfall-compatible `/sets`, `/catalog` and `/symbology` routes.

Everything here dispatches through `_handle`, the same path a real request takes. The reference
tables are global rather than per-card, so unlike the `/cards/*` tests this module cannot scope its
fixtures to rows it owns: it seeds all three tables and asserts against exactly what it seeded.
That is safe because nothing else in the suite writes them, and it is stated here so a future test
that does write them knows what it would break.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import falcon
import falcon.testing
import orjson
import pytest
from psycopg.types.json import Jsonb

if TYPE_CHECKING:
    from api.api_resource import APIResource

BOLT_SET_ID = "aaaaaaaa-0000-4000-8000-aaaaaaaaaaaa"
OLD_SET_ID = "bbbbbbbb-0000-4000-8000-bbbbbbbbbbbb"
TCGPLAYER_ID = 90210


def _set_object(set_id: str, code: str, name: str, released: str, tcgplayer_id: int | None) -> dict[str, Any]:
    """A Set object shaped like Scryfall's, including the fields no card carries."""
    payload = {
        "object": "set",
        "id": set_id,
        "code": code,
        "mtgo_code": code,
        "arena_code": code,
        "name": name,
        "uri": f"https://api.scryfall.com/sets/{set_id}",
        "scryfall_uri": f"https://scryfall.com/sets/{code}",
        "search_uri": f"https://api.scryfall.com/cards/search?q=e%3A{code}",
        "released_at": released,
        "set_type": "expansion",
        "card_count": 135,
        "digital": False,
        "nonfoil_only": False,
        "foil_only": False,
        "icon_svg_uri": f"https://svgs.scryfall.io/sets/{code}.svg",
    }
    if tcgplayer_id is not None:
        payload["tcgplayer_id"] = tcgplayer_id
    return payload


SETS = [
    _set_object(BOLT_SET_ID, "zzt", "Compat Test Set", "2026-01-01", TCGPLAYER_ID),
    _set_object(OLD_SET_ID, "zzo", "Compat Older Set", "1999-01-01", None),
]

SYMBOLS = [
    {"object": "card_symbol", "symbol": "{ZT}", "svg_uri": "https://svgs.test/zt.svg", "english": "compat tap", "cmc": 0.0},
    {"object": "card_symbol", "symbol": "{ZW}", "svg_uri": "https://svgs.test/zw.svg", "english": "compat white", "cmc": 1.0},
]

CREATURE_TYPES = ["Compat Beast", "Compat Wizard"]


@pytest.fixture(name="reference_corpus", scope="module")
def reference_corpus_fixture(api_resource: APIResource) -> APIResource:
    """Seed the three reference tables once, then hand back the resource."""
    with api_resource._conn_pool.connection() as conn, conn.cursor() as cursor:
        cursor.execute("DELETE FROM magic.sets")
        cursor.executemany(
            "INSERT INTO magic.sets (id, code, tcgplayer_id, position, set_object) VALUES (%s, %s, %s, %s, %s)",
            [
                (entry["id"], entry["code"], entry.get("tcgplayer_id"), position, Jsonb(entry))
                for position, entry in enumerate(SETS)
            ],
        )
        cursor.execute("DELETE FROM magic.card_symbols")
        cursor.executemany(
            "INSERT INTO magic.card_symbols (symbol, position, symbol_object) VALUES (%s, %s, %s)",
            [(entry["symbol"], position, Jsonb(entry)) for position, entry in enumerate(SYMBOLS)],
        )
        cursor.execute(
            "INSERT INTO magic.catalogs (name, entries) VALUES (%s, %s) "
            "ON CONFLICT (name) DO UPDATE SET entries = EXCLUDED.entries",
            ("creature-types", Jsonb(CREATURE_TYPES)),
        )
        conn.commit()
    api_resource._clear_caches()
    return api_resource


def dispatch(api: APIResource, path: str, query_string: str = "", *, method: str = "GET") -> falcon.Response:
    """Run one request through `_handle` and return the Falcon response."""
    environ = falcon.testing.create_environ(path=path, query_string=query_string, method=method, body="")
    req = falcon.Request(environ)
    resp = falcon.Response()
    api._handle(req, resp)
    return resp


def payload(resp: falcon.Response) -> dict[str, Any]:
    """Decode a response body regardless of whether it was set as media or as text."""
    if resp.media is not None:
        return resp.media
    return orjson.loads(resp.render_body())


class TestSetsListing:
    """GET /sets."""

    def test_returns_a_list_object(self, reference_corpus: APIResource) -> None:
        body = payload(dispatch(reference_corpus, "/sets"))
        assert body["object"] == "list"
        assert body["has_more"] is False
        assert [entry["code"] for entry in body["data"]] == ["zzt", "zzo"]

    def test_preserves_scryfalls_own_ordering(self, reference_corpus: APIResource) -> None:
        """Ordered by the stored position, not recomputed — sets sharing a date have no derivable order."""
        body = payload(dispatch(reference_corpus, "/sets"))
        assert body["data"][0]["released_at"] > body["data"][1]["released_at"]

    def test_serves_the_fields_no_card_carries(self, reference_corpus: APIResource) -> None:
        """The whole reason sets are mirrored rather than derived."""
        first = payload(dispatch(reference_corpus, "/sets"))["data"][0]
        for field in ("icon_svg_uri", "mtgo_code", "arena_code", "card_count", "tcgplayer_id"):
            assert field in first, field


class TestSetLookup:
    """GET /sets/:code, /sets/:id and /sets/tcgplayer/:id."""

    def test_by_set_code(self, reference_corpus: APIResource) -> None:
        body = payload(dispatch(reference_corpus, "/sets/zzt"))
        assert body["object"] == "set"
        assert body["id"] == BOLT_SET_ID

    def test_set_codes_are_case_insensitive(self, reference_corpus: APIResource) -> None:
        assert payload(dispatch(reference_corpus, "/sets/ZZT"))["id"] == BOLT_SET_ID

    def test_by_scryfall_id(self, reference_corpus: APIResource) -> None:
        assert payload(dispatch(reference_corpus, f"/sets/{BOLT_SET_ID}"))["code"] == "zzt"

    def test_by_tcgplayer_id(self, reference_corpus: APIResource) -> None:
        assert payload(dispatch(reference_corpus, f"/sets/tcgplayer/{TCGPLAYER_ID}"))["code"] == "zzt"

    def test_an_unknown_code_is_a_404(self, reference_corpus: APIResource) -> None:
        resp = dispatch(reference_corpus, "/sets/nope")
        assert resp.status == falcon.HTTP_404
        assert payload(resp)["object"] == "error"

    def test_an_unknown_tcgplayer_id_is_a_404(self, reference_corpus: APIResource) -> None:
        assert dispatch(reference_corpus, "/sets/tcgplayer/1").status == falcon.HTTP_404

    def test_a_non_numeric_tcgplayer_id_is_a_404_not_a_500(self, reference_corpus: APIResource) -> None:
        """The segment reaches the handler as text, so the int() has to be guarded."""
        assert dispatch(reference_corpus, "/sets/tcgplayer/abc").status == falcon.HTTP_404

    def test_a_set_with_no_tcgplayer_id_is_still_addressable_by_code(self, reference_corpus: APIResource) -> None:
        assert payload(dispatch(reference_corpus, "/sets/zzo"))["id"] == OLD_SET_ID


class TestCatalog:
    """GET /catalog/:name."""

    def test_returns_a_catalog_object(self, reference_corpus: APIResource) -> None:
        body = payload(dispatch(reference_corpus, "/catalog/creature-types"))
        assert body["object"] == "catalog"
        assert body["total_values"] == len(CREATURE_TYPES)
        assert body["data"] == CREATURE_TYPES

    def test_an_unknown_catalog_is_a_404(self, reference_corpus: APIResource) -> None:
        resp = dispatch(reference_corpus, "/catalog/not-a-catalog")
        assert resp.status == falcon.HTTP_404
        assert payload(resp)["object"] == "error"

    def test_a_known_but_unimported_catalog_is_empty_rather_than_missing(self, reference_corpus: APIResource) -> None:
        """The name is real, so 404 would tell a client the endpoint does not exist."""
        body = payload(dispatch(reference_corpus, "/catalog/watermarks"))
        assert body["object"] == "catalog"
        assert body["total_values"] == 0

    def test_the_name_is_case_insensitive(self, reference_corpus: APIResource) -> None:
        assert payload(dispatch(reference_corpus, "/catalog/Creature-Types"))["data"] == CREATURE_TYPES


class TestSymbology:
    """GET /symbology."""

    def test_returns_every_symbol_in_order(self, reference_corpus: APIResource) -> None:
        body = payload(dispatch(reference_corpus, "/symbology"))
        assert body["object"] == "list"
        assert [entry["symbol"] for entry in body["data"]] == ["{ZT}", "{ZW}"]

    def test_serves_the_fields_no_card_carries(self, reference_corpus: APIResource) -> None:
        assert payload(dispatch(reference_corpus, "/symbology"))["data"][0]["svg_uri"].startswith("https://")


class TestParseMana:
    """GET /symbology/parse-mana."""

    def test_parses_a_cost(self, reference_corpus: APIResource) -> None:
        body = payload(dispatch(reference_corpus, "/symbology/parse-mana", "cost=RUW"))
        assert body["object"] == "mana_cost"
        assert body["cost"] == "{U}{R}{W}"
        assert body["cmc"] == 3.0

    def test_a_missing_cost_is_a_400(self, reference_corpus: APIResource) -> None:
        resp = dispatch(reference_corpus, "/symbology/parse-mana")
        assert resp.status == falcon.HTTP_400
        assert payload(resp)["object"] == "error"

    def test_an_unparseable_cost_is_a_422(self, reference_corpus: APIResource) -> None:
        """Scryfall answers 422 here, not 400."""
        resp = dispatch(reference_corpus, "/symbology/parse-mana", "cost=%7BQ%7D")
        assert resp.status == falcon.HTTP_422
        assert payload(resp)["object"] == "error"

    def test_it_answers_without_any_imported_data(self, reference_corpus: APIResource) -> None:
        """A pure function of the parameter, so it works before the first import."""
        assert payload(dispatch(reference_corpus, "/symbology/parse-mana", "cost=0"))["cost"] == "{0}"


class TestSharedBehaviour:
    """Conventions the reference routes inherit from the cards surface."""

    @pytest.mark.parametrize("path", ["/sets", "/sets/zzt", "/catalog/creature-types", "/symbology"])
    def test_every_route_sets_a_cache_tier(self, reference_corpus: APIResource, path: str) -> None:
        """These routes previously sent no Cache-Control at all, so no CDN cached them."""
        resp = dispatch(reference_corpus, path)
        assert resp.headers["cache-control"] == "public, max-age=57600"

    @pytest.mark.parametrize("path", ["/sets", "/catalog/creature-types", "/symbology"])
    def test_pretty_indents_the_body(self, reference_corpus: APIResource, path: str) -> None:
        resp = dispatch(reference_corpus, path, "pretty=true")
        assert b"\n  " in resp.render_body()

    def test_errors_carry_the_scryfall_error_shape(self, reference_corpus: APIResource) -> None:
        body = payload(dispatch(reference_corpus, "/sets/nope"))
        assert body["object"] == "error"
        assert body["status"] == 404
        assert isinstance(body["details"], str)


class TestThroughTheFullApp:
    """The same routes through a real falcon.App, for wire-level concerns."""

    def _client(self, api: APIResource) -> falcon.testing.TestClient:
        app = falcon.App()
        app.add_sink(api._handle, prefix="/")
        return falcon.testing.TestClient(app)

    def test_content_type_is_scryfalls(self, reference_corpus: APIResource) -> None:
        result = self._client(reference_corpus).simulate_get("/sets")
        assert result.headers["content-type"] == "application/json; charset=utf-8"

    def test_a_set_lookup_round_trips(self, reference_corpus: APIResource) -> None:
        result = self._client(reference_corpus).simulate_get("/sets/zzt")
        assert json.loads(result.text)["code"] == "zzt"
