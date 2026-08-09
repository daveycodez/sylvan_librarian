"""Unit tests for the Scryfall response objects: no database, no request.

`api/tests/test_scryfall_cards_routes.py` covers the routes themselves against a real corpus.
"""

from __future__ import annotations

import datetime

import pytest

from api.scryfall_compat.objects import (
    build_page_url,
    card_list,
    card_to_text,
    catalog_object,
    error_object,
    image_uri,
    ruling_object,
    to_scryfall_card,
)


def blob(**overrides: object) -> dict:
    """A raw_card_blob as preprocess_card snapshots it for a single-face card."""
    return {
        "object": "card",
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "name": "Lightning Bolt",
        "mana_cost": "{R}",
        "type_line": "Instant",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "flavor_text": "",
        "card_name": "Lightning Bolt",
        "face_name": "Lightning Bolt",
        "face_idx": 1,
    } | overrides


class TestToScryfallCard:
    """A stored row becomes the card object Scryfall would have sent."""

    def test_importer_added_keys_are_stripped(self):
        card = to_scryfall_card({"raw_card_blob": blob()})
        assert "card_name" not in card
        assert "face_name" not in card
        assert "face_idx" not in card

    def test_normalized_empty_flavor_text_is_dropped(self):
        """The importer writes "" where Scryfall omits the key; absent and "" are one state."""
        card = to_scryfall_card({"raw_card_blob": blob()})
        assert "flavor_text" not in card

    def test_real_flavor_text_survives(self):
        card = to_scryfall_card({"raw_card_blob": blob(flavor_text="Kaboom.")})
        assert card["flavor_text"] == "Kaboom."

    def test_single_face_round_trips_the_rest_untouched(self):
        card = to_scryfall_card({"raw_card_blob": blob()})
        assert card == {
            "object": "card",
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "name": "Lightning Bolt",
            "mana_cost": "{R}",
            "type_line": "Instant",
            "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        }

    def test_a_multi_face_blob_round_trips_its_faces(self):
        """The importer stores the card, not a face, so there is nothing to reconstruct."""
        faces = [{"object": "card_face", "name": "Delver of Secrets"}, {"object": "card_face", "name": "Insectile Aberration"}]
        row = {
            "raw_card_blob": blob(
                name="Delver of Secrets // Insectile Aberration",
                card_name="Delver of Secrets // Insectile Aberration",
                card_faces=faces,
            ),
        }
        card = to_scryfall_card(row)
        assert card["object"] == "card"
        assert card["name"] == "Delver of Secrets // Insectile Aberration"
        assert card["card_faces"] == faces

    def test_a_pre_merge_face_blob_is_presented_as_its_card(self):
        """A row not rewritten since the merged-row work still holds a promoted front face.

        Rows are rewritten by an import, not by a migration, so this lasts at most one import
        cycle after deploy. The face is all that survives, so it is at least labeled `card` under
        the card's full name rather than shipped as a `card_face` claiming to be a card.
        """
        row = {
            "raw_card_blob": blob(
                object="card_face",
                name="Insectile Aberration",
                card_name="Delver of Secrets // Insectile Aberration",
            ),
        }
        card = to_scryfall_card(row)
        assert card["object"] == "card"
        assert card["name"] == "Delver of Secrets // Insectile Aberration"


class TestEnvelopes:
    """The List, Catalog, Ruling and error objects match Scryfall's shapes."""

    def test_error_object_omits_warnings_when_there_are_none(self):
        assert error_object(code="not_found", status=404, details="nope") == {
            "object": "error",
            "code": "not_found",
            "status": 404,
            "details": "nope",
        }

    def test_error_object_carries_warnings(self):
        error = error_object(code="bad_request", status=400, details="nope", warnings=["heads up"])
        assert error["warnings"] == ["heads up"]

    def test_card_list_key_order_matches_scryfall(self):
        listing = card_list([{"object": "card"}], total_cards=200, has_more=True, next_page="https://x/?page=2")
        assert list(listing) == ["object", "total_cards", "has_more", "next_page", "data"]

    def test_card_list_omits_pagination_it_was_not_given(self):
        assert card_list([]) == {"object": "list", "has_more": False, "data": []}

    def test_collection_list_carries_not_found(self):
        listing = card_list([], not_found=[{"name": "Nope"}])
        assert listing["not_found"] == [{"name": "Nope"}]

    def test_catalog_object_counts_its_values(self):
        assert catalog_object(["Bolt", "Shock"]) == {"object": "catalog", "total_values": 2, "data": ["Bolt", "Shock"]}

    def test_ruling_object_renders_the_date_as_iso(self):
        row = {
            "oracle_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "source": "wotc",
            "published_at": datetime.date(2004, 10, 4),
            "comment": "It does.",
        }
        assert ruling_object(row)["published_at"] == "2004-10-04"


class TestPageUrl:
    """`next_page` is absolute, sorted, and carries the page it fetches."""

    def test_page_is_appended_and_parameters_sorted(self):
        url = build_page_url("https://example.test/cards/search", {"q": "fire", "order": "name"}, 2)
        assert url == "https://example.test/cards/search?order=name&page=2&q=fire"

    def test_query_values_are_escaped(self):
        url = build_page_url("https://example.test/cards/search", {"q": "t:creature c:r"}, 3)
        assert "q=t%3Acreature+c%3Ar" in url


class TestImageUri:
    """`format=image` resolves the size and face the client asked for."""

    def test_front_face_size_is_selected(self):
        card = {"image_uris": {"png": "https://cdn/front.png", "large": "https://cdn/front.jpg"}}
        assert image_uri(card, version="png", face="front") == "https://cdn/front.png"

    def test_back_face_uses_the_second_face(self):
        card = {
            "image_uris": {"large": "https://cdn/front.jpg"},
            "card_faces": [
                {"image_uris": {"large": "https://cdn/front.jpg"}},
                {"image_uris": {"large": "https://cdn/back.jpg"}},
            ],
        }
        assert image_uri(card, version="large", face="back") == "https://cdn/back.jpg"

    def test_back_face_on_a_single_faced_card_falls_back_to_the_front(self):
        card = {"image_uris": {"large": "https://cdn/front.jpg"}}
        assert image_uri(card, version="large", face="back") == "https://cdn/front.jpg"

    def test_missing_size_is_reported_as_absent(self):
        assert image_uri({"image_uris": {}}, version="png", face="front") is None


class TestCardToText:
    """`format=text` renders the card the way Scryfall's text format does."""

    def test_instant_renders_name_cost_type_and_text(self):
        card = {
            "name": "Lightning Bolt",
            "mana_cost": "{R}",
            "type_line": "Instant",
            "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        }
        assert card_to_text(card) == ("Lightning Bolt {R}\nInstant\nLightning Bolt deals 3 damage to any target.")

    def test_creature_appends_power_and_toughness(self):
        card = {"name": "Grizzly Bears", "mana_cost": "{1}{G}", "type_line": "Creature — Bear", "power": "2", "toughness": "2"}
        assert card_to_text(card).endswith("\n2/2")

    def test_planeswalker_appends_loyalty(self):
        card = {"name": "Ajani", "mana_cost": "{2}{W}{W}", "type_line": "Legendary Planeswalker — Ajani", "loyalty": "4"}
        assert card_to_text(card).endswith("\nLoyalty: 4")

    def test_faces_are_separated_by_a_blank_line(self):
        card = {
            "name": "Delver of Secrets // Insectile Aberration",
            "card_faces": [
                {
                    "name": "Delver of Secrets",
                    "mana_cost": "{U}",
                    "type_line": "Creature — Human Wizard",
                    "power": "1",
                    "toughness": "1",
                },
                {
                    "name": "Insectile Aberration",
                    "mana_cost": "",
                    "type_line": "Creature — Human Insect",
                    "power": "3",
                    "toughness": "2",
                },
            ],
        }
        rendered = card_to_text(card)
        assert rendered.count("\n\n") == 1
        assert rendered.startswith("Delver of Secrets {U}")
        assert rendered.endswith("3/2")

    @pytest.mark.parametrize("missing", ["oracle_text", "mana_cost"])
    def test_absent_pieces_are_skipped_rather_than_rendered_empty(self, missing):
        card = {
            "name": "Vanilla",
            "mana_cost": "{G}",
            "type_line": "Creature — Bear",
            "oracle_text": "text",
            "power": "2",
            "toughness": "2",
        }
        del card[missing]
        assert "\n\n" not in card_to_text(card)
