"""Tests for the /named fuzzy card-name lookup endpoint."""

from __future__ import annotations

from typing import TYPE_CHECKING

import falcon
import falcon.testing
import pytest

from api.api_resource import NAMED_LOOKUP_FIELDS
from api.tests.helpers import make_raw_card

if TYPE_CHECKING:
    from api.api_resource import APIResource


def _insert_cards(api: APIResource, *names: str) -> None:
    """Insert one printing per name into the shared test database."""
    api._upsert_cards([make_raw_card(name=name) for name in names])


class TestNamedLookup:
    def test_exact_match_wins(self, api_resource: APIResource) -> None:
        _insert_cards(api_resource, "Lightning Bolt", "Lightning Strike")

        result = api_resource.named(fuzzy="Lightning Bolt")

        assert result["name"] == "Lightning Bolt"

    def test_exact_match_is_case_insensitive(self, api_resource: APIResource) -> None:
        _insert_cards(api_resource, "Lightning Bolt")

        result = api_resource.named(fuzzy="LIGHTNING bolt")

        assert result["name"] == "Lightning Bolt"

    def test_result_carries_default_fields_plus_scryfall_id(self, api_resource: APIResource) -> None:
        _insert_cards(api_resource, "Lightning Bolt")

        result = api_resource.named(fuzzy="Lightning Bolt")

        assert set(result) == set(NAMED_LOOKUP_FIELDS)
        assert result["scryfall_id"] is not None

    def test_typo_resolves_to_closest_name(self, api_resource: APIResource) -> None:
        _insert_cards(api_resource, "Lightning Bolt", "Lightning Strike", "Lightning Helix")

        result = api_resource.named(fuzzy="lighning bolt")

        assert result["name"] == "Lightning Bolt"

    def test_accented_name_matches_unaccented_query(self, api_resource: APIResource) -> None:
        _insert_cards(api_resource, "Éowyn of Fuzzmark")

        result = api_resource.named(fuzzy="eowyn of fuzzmark")

        assert result["name"] == "Éowyn of Fuzzmark"

    def test_multiple_printings_of_one_name_are_not_ambiguous(self, api_resource: APIResource) -> None:
        api_resource._upsert_cards(
            [
                make_raw_card(name="Reprinted Fuzz Sentinel"),
                make_raw_card(name="Reprinted Fuzz Sentinel"),
            ],
        )

        result = api_resource.named(fuzzy="reprinted fuz sentinel")

        assert result["name"] == "Reprinted Fuzz Sentinel"

    def test_two_equally_close_names_are_ambiguous(self, api_resource: APIResource) -> None:
        _insert_cards(api_resource, "Obelisk of Fuzz Alara", "Obelisk of Fuzz Bant")

        with pytest.raises(falcon.HTTPNotFound) as exc_info:
            api_resource.named(fuzzy="obelisk of fuzz")

        assert exc_info.value.title == "Ambiguous Name"

    def test_garbage_finds_nothing(self, api_resource: APIResource) -> None:
        _insert_cards(api_resource, "Lightning Bolt")

        with pytest.raises(falcon.HTTPNotFound) as exc_info:
            api_resource.named(fuzzy="zzxqjw vvkpqr")

        assert exc_info.value.title == "Not Found"

    def test_missing_fuzzy_parameter_is_rejected(self, api_resource: APIResource) -> None:
        with pytest.raises(falcon.HTTPBadRequest):
            api_resource.named()

    def test_blank_fuzzy_parameter_is_rejected(self, api_resource: APIResource) -> None:
        with pytest.raises(falcon.HTTPBadRequest):
            api_resource.named(fuzzy="   ")

    def test_registered_under_both_paths(self, api_resource: APIResource) -> None:
        assert "named" in api_resource.routes
        assert "cards/named" in api_resource.routes
        assert api_resource.routes["named"] is api_resource.routes["cards/named"]

    def test_full_dispatch_over_scryfall_path(self, api_resource: APIResource) -> None:
        _insert_cards(api_resource, "Lightning Bolt")

        req = falcon.Request(falcon.testing.create_environ(path="/cards/named", query_string="fuzzy=lighning+bolt"))
        resp = falcon.Response()
        api_resource._handle(req, resp)

        assert resp.status == falcon.HTTP_200
        assert resp.media["name"] == "Lightning Bolt"
