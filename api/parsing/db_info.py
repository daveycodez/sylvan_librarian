"""Database field information and mappings for Scryfall queries."""

from __future__ import annotations

from enum import StrEnum


class FieldType(StrEnum):
    """Enumeration of supported database field types."""

    JSONB_ARRAY = "jsonb_array"
    JSONB_OBJECT = "jsonb_object"
    NUMERIC = "numeric"
    TEXT = "text"
    DATE = "date"


class ParserClass(StrEnum):
    """Enumeration of parser classes for different field types."""

    NUMERIC = "numeric"  # Supports arithmetic operations (cmc, power, etc.)
    MANA = "mana"  # Mana cost fields with special mana value parsing
    RARITY = "rarity"  # Rarity fields with string-to-numeric conversion
    LEGALITY = "legality"  # Format/legal fields with JSON handling
    COLOR = "color"  # Color fields (card colors and color identity)
    TEXT = "text"  # Simple text fields (name, artist, oracle text)
    DATE = "date"  # Date fields with full date values
    YEAR = "year"  # Year fields with 4-digit year values


class FieldInfo:
    """Information about a database field and its search aliases."""

    def __init__(self, *, db_column_name: str, field_type: FieldType, search_aliases: list[str], parser_class: ParserClass) -> None:
        """Initialize field information.

        Args:
            db_column_name: The actual database column name.
            field_type: The type of the field.
            search_aliases: List of search aliases for this field.
            parser_class: The parser class to use for this field. If None, defaults based on field_type.
        """
        self.db_column_name = db_column_name
        self.field_type = field_type
        self.search_aliases = search_aliases
        # Default parser class based on field type if not specified
        if parser_class is None:
            parser_class = ParserClass.NUMERIC if field_type == FieldType.NUMERIC else ParserClass.TEXT
        self.parser_class = parser_class

    def __repr__(self: FieldInfo) -> str:
        """Return a string representation of the field info."""
        return (
            "FieldInfo("
            f"db_column_name={self.db_column_name}, "
            f"field_type={self.field_type}, "
            f"search_aliases={self.search_aliases}, "
            f"parser_class={self.parser_class}"
            ")"
        )


DB_COLUMNS = [
    FieldInfo(
        db_column_name="card_artist",
        field_type=FieldType.TEXT,
        search_aliases=["artist", "a"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_colors",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["color", "colors", "colour", "colours", "c"],
        parser_class=ParserClass.COLOR,
    ),
    FieldInfo(
        db_column_name="card_color_identity",
        field_type=FieldType.JSONB_OBJECT,
        # `commander:` is how players search a commander's colour identity -- a deck built
        # around it must stay within it, so a commander query is a color-identity query.
        search_aliases=["color_identity", "coloridentity", "id", "identity", "ci", "commander"],
        parser_class=ParserClass.COLOR,
    ),
    FieldInfo(
        db_column_name="card_frame_data",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["frame"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_keywords",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["keyword", "kw"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_name",
        field_type=FieldType.TEXT,
        search_aliases=["name"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_subtypes",
        field_type=FieldType.JSONB_ARRAY,
        search_aliases=["subtype", "subtypes"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_types",
        field_type=FieldType.JSONB_ARRAY,
        search_aliases=["type", "types", "t"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="cmc",
        field_type=FieldType.NUMERIC,
        search_aliases=["cmc", "mv", "manavalue"],
        parser_class=ParserClass.NUMERIC,
    ),
    FieldInfo(
        db_column_name="creature_power",
        field_type=FieldType.NUMERIC,
        search_aliases=["power", "pow"],
        parser_class=ParserClass.NUMERIC,
    ),
    FieldInfo(
        db_column_name="creature_toughness",
        field_type=FieldType.NUMERIC,
        search_aliases=["toughness", "tou"],
        parser_class=ParserClass.NUMERIC,
    ),
    FieldInfo(
        db_column_name="planeswalker_loyalty",
        field_type=FieldType.NUMERIC,
        search_aliases=["loyalty", "loy"],
        parser_class=ParserClass.NUMERIC,
    ),
    FieldInfo(
        db_column_name="edhrec_rank",
        field_type=FieldType.NUMERIC,
        search_aliases=[],
        parser_class=ParserClass.NUMERIC,
    ),
    FieldInfo(
        db_column_name="mana_cost_jsonb",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["mana", "m"],
        parser_class=ParserClass.MANA,
    ),
    FieldInfo(
        db_column_name="devotion",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["devotion"],
        parser_class=ParserClass.MANA,
    ),
    FieldInfo(
        db_column_name="price_usd",
        field_type=FieldType.NUMERIC,
        search_aliases=["usd"],
        parser_class=ParserClass.NUMERIC,
    ),
    FieldInfo(
        db_column_name="price_eur",
        field_type=FieldType.NUMERIC,
        search_aliases=["eur"],
        parser_class=ParserClass.NUMERIC,
    ),
    FieldInfo(
        db_column_name="price_tix",
        field_type=FieldType.NUMERIC,
        search_aliases=["tix"],
        parser_class=ParserClass.NUMERIC,
    ),
    FieldInfo(
        db_column_name="produced_mana",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["produces"],
        parser_class=ParserClass.COLOR,
    ),
    FieldInfo(
        db_column_name="raw_card_blob",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=[],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="oracle_id",
        field_type=FieldType.TEXT,
        search_aliases=["oracleid", "oracle_id"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="oracle_text",
        field_type=FieldType.TEXT,
        search_aliases=["oracle", "o"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="flavor_text",
        field_type=FieldType.TEXT,
        search_aliases=["flavor", "ft"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_oracle_tags",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["oracle_tags", "otag", "oracletag", "function"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_art_tags",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["art_tags", "art", "atag", "arttag"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_is_tags",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["is", "has"],
        parser_class=ParserClass.TEXT,
    ),
    # A distinct FieldInfo from "is" above, sharing its db_column_name, so a `not:` leaf
    # generates the identical SQL/explanation as `is:` on its own -- rewrite.py's
    # negate_not_prefix distinguishes the two via original_attribute and supplies the
    # negation Scryfall's docs describe ("not: is the same as -is:").
    FieldInfo(
        db_column_name="card_is_tags",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["not"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_rarity_int",
        field_type=FieldType.NUMERIC,
        search_aliases=["rarity", "r"],
        parser_class=ParserClass.RARITY,
    ),
    FieldInfo(
        db_column_name="card_set_code",
        field_type=FieldType.TEXT,
        search_aliases=["set", "s", "e"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="collector_number",
        field_type=FieldType.TEXT,
        search_aliases=["number", "cn"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="collector_number_int",
        field_type=FieldType.NUMERIC,
        search_aliases=["number", "cn"],
        parser_class=ParserClass.NUMERIC,
    ),  # No direct aliases - will be routed
    FieldInfo(
        db_column_name="card_legalities",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["format", "f", "legal", "banned", "restricted"],
        parser_class=ParserClass.LEGALITY,
    ),
    FieldInfo(
        db_column_name="card_lang",
        field_type=FieldType.TEXT,
        search_aliases=["lang", "language"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_set_type",
        field_type=FieldType.TEXT,
        search_aliases=["set_type", "settype", "st"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_layout",
        field_type=FieldType.TEXT,
        search_aliases=["layout"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_border",
        field_type=FieldType.TEXT,
        search_aliases=["border"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="card_watermark",
        field_type=FieldType.TEXT,
        search_aliases=["watermark", "wm"],
        parser_class=ParserClass.TEXT,
    ),
    FieldInfo(
        db_column_name="released_at",
        field_type=FieldType.DATE,
        search_aliases=["date"],
        parser_class=ParserClass.DATE,
    ),
    FieldInfo(
        db_column_name="released_at",
        field_type=FieldType.DATE,
        search_aliases=["year"],
        parser_class=ParserClass.YEAR,
    ),
]

KNOWN_CARD_ATTRIBUTES = set()
NUMERIC_CARD_ATTRIBUTES: set[str] = set()
SEARCH_NAME_TO_DB_NAME = {}

ALIAS_TO_FIELD_INFOS: dict[str, list[FieldInfo]] = {}
COLNAME_TO_FIELD_INFOS: dict[str, list[FieldInfo]] = {}
PARSER_CLASS_TO_FIELD_INFOS: dict[ParserClass, list[FieldInfo]] = {}

for col in DB_COLUMNS:
    for ialias in col.search_aliases:
        ALIAS_TO_FIELD_INFOS.setdefault(ialias.lower(), []).append(col)

    COLNAME_TO_FIELD_INFOS.setdefault(col.db_column_name, []).append(col)
    PARSER_CLASS_TO_FIELD_INFOS.setdefault(col.parser_class, []).append(col)

    KNOWN_CARD_ATTRIBUTES.add(col.db_column_name.lower())
    KNOWN_CARD_ATTRIBUTES.update(alias.lower() for alias in col.search_aliases)
    if col.parser_class == ParserClass.NUMERIC:
        NUMERIC_CARD_ATTRIBUTES.add(col.db_column_name.lower())
        NUMERIC_CARD_ATTRIBUTES.update(alias.lower() for alias in col.search_aliases)
    SEARCH_NAME_TO_DB_NAME[col.db_column_name.lower()] = col.db_column_name

    for ialias in col.search_aliases:
        SEARCH_NAME_TO_DB_NAME[ialias.lower()] = col.db_column_name


CARD_SUPERTYPES = {
    "Basic",
    "Legendary",
    "Snow",
    "World",
}

CARD_TYPES = {
    "Artifact",
    "Conspiracy",
    "Creature",
    "Enchantment",
    "Instant",
    "Kindred",  # new name for tribal
    "Land",
    "Planeswalker",
    "Sorcery",
    "Tribal",
}

FORMAT_CODE_TO_NAME = {
    "m": "modern",
    "s": "standard",
    "l": "legacy",
    "p": "pauper",
    "c": "commander",
    "v": "vintage",
    "h": "historic",
}

# The `is:` values derivable from a single boolean SQL expression against a card's own row,
# synced in one set-based statement after each import (see _sync_boolean_is_tags) -- no
# per-tag API sweep, unlike CUSTOM_IS_TAGS below, and no accumulation in the import loop.
# Each expression must reference the row alias `cards` (it runs inside a correlated
# subquery, not a plain WHERE) -- adding a tag here is the whole change. Most read
# `cards.raw_card_blob`; hybrid/phyrexian read `cards.mana_cost_text` instead, per
# docs/issues/done/00713-is-tag-recovery.md's own reasoning for putting them here rather
# than in the query-rewrite table: the DSL only does exact-symbol containment, so a
# rewrite would be a brittle ~15-term OR over an open, growing symbol set. Density-gated
# at ~2% of the corpus (see docs/issues/00985): reserved (1.1%) and gamechanger (0.4%)
# were the original two; the rest were added after a corpus-wide survey of every is: tag
# on Scryfall's syntax page found these sitting at or under masterpiece's 1.8%.
# foil/nonfoil/reprint/booster/hires (50-97%) are deliberately NOT here -- "higher
# cardinality, memory check first" -- and stay a candidate for a separate, more careful
# pass -- except for the three below, whose memory question is now ANSWERED rather than
# assumed: the Cloudflare port's builder ran twice over the same corpus (2026-08-16 all_cards,
# 31,724 cards / 517,746 rows) and the archives total 363.02 MiB without foil/promo/reprint and
# 364.17 MiB with them, +1.16 MiB / +0.32%, because a value carried by that share of the corpus
# stores as a bitmap plane rather than a posting list.
#
# It lives HERE rather than in admin_resource because the parser reads it too: the keys are half of
# `rewrite.SUPPORTED_IS_VALUES`, the complete list of `is:` values this parser can answer, and
# api/parsing cannot import api/admin_resource.
BOOLEAN_IS_TAGS: dict[str, str] = {
    # Alphabetized by key. Expressions read either a plain top-level boolean (reserved,
    # gamechanger, spotlight), promo_types/keywords/finishes array membership, or a
    # single-field lookup (set_type, preview.source).
    "arena_league": "cards.raw_card_blob->'promo_types' @> '\"arenaleague\"'",
    "booster": "cards.raw_card_blob->'booster' = 'true'::jsonb",
    "boosterfun": "cards.raw_card_blob->'promo_types' @> '\"boosterfun\"'",
    "buyabox": "cards.raw_card_blob->'promo_types' @> '\"buyabox\"'",
    "convention": "cards.raw_card_blob->'promo_types' @> '\"convention\"'",
    "datestamped": "cards.raw_card_blob->'promo_types' @> '\"datestamped\"'",
    "digital": "cards.raw_card_blob->'digital' = 'true'::jsonb",
    "etched": "cards.raw_card_blob->'finishes' @> '\"etched\"'",
    "fnm": "cards.raw_card_blob->'promo_types' @> '\"fnm\"'",
    "foil": "cards.raw_card_blob->'foil' = 'true'::jsonb",
    "fullart": "cards.raw_card_blob->'full_art' = 'true'::jsonb",
    "gamechanger": "cards.raw_card_blob->'game_changer' = 'true'::jsonb",
    "gameday": "cards.raw_card_blob->'promo_types' @> '\"gameday\"'",
    "giftbox": "cards.raw_card_blob->'promo_types' @> '\"giftbox\"'",
    "glossy": "cards.raw_card_blob->'promo_types' @> '\"glossy\"'",
    "hires": "cards.raw_card_blob->'highres_image' = 'true'::jsonb",
    "hybrid": r"cards.mana_cost_text ~ '\{[WUBRG]/[WUBRG]\}'",
    "instore": "cards.raw_card_blob->'promo_types' @> '\"instore\"'",
    "intro_pack": "cards.raw_card_blob->'promo_types' @> '\"intropack\"'",
    "judge_gift": "cards.raw_card_blob->'promo_types' @> '\"judgegift\"'",
    "league": "cards.raw_card_blob->'promo_types' @> '\"league\"'",
    "masterpiece": "cards.raw_card_blob->>'set_type' = 'masterpiece'",
    "media_insert": "cards.raw_card_blob->'promo_types' @> '\"mediainsert\"'",
    # "Partner with <name>" cards carry a plain "Partner" keyword alongside it (verified
    # against the corpus), so checking for "Partner" alone already covers both.
    "nonfoil": "cards.raw_card_blob->'nonfoil' = 'true'::jsonb",
    "partner": "cards.raw_card_blob->'keywords' @> '\"Partner\"'",
    "phyrexian": r"cards.mana_cost_text ~ '\{[WUBRG]/P\}'",
    "planeswalker_deck": "cards.raw_card_blob->'promo_types' @> '\"planeswalkerdeck\"'",
    "player_rewards": "cards.raw_card_blob->'promo_types' @> '\"playerrewards\"'",
    "prerelease": "cards.raw_card_blob->'promo_types' @> '\"prerelease\"'",
    "promo": "cards.raw_card_blob->'promo' = 'true'::jsonb",
    "rebalanced": "cards.raw_card_blob->'promo_types' @> '\"rebalanced\"'",
    "release": "cards.raw_card_blob->'promo_types' @> '\"release\"'",
    "reprint": "cards.raw_card_blob->'reprint' = 'true'::jsonb",
    "reserved": "cards.raw_card_blob->'reserved' = 'true'::jsonb",
    "scryfallpreview": "cards.raw_card_blob->'preview'->>'source' = 'Scryfall'",
    "set_promo": "cards.raw_card_blob->'promo_types' @> '\"setpromo\"'",
    "spotlight": "cards.raw_card_blob->'story_spotlight' = 'true'::jsonb",
    "stamped": "cards.raw_card_blob->'promo_types' @> '\"stamped\"'",
    "textless": "cards.raw_card_blob->'textless' = 'true'::jsonb",
    "universesbeyond": "cards.raw_card_blob->'promo_types' @> '\"universesbeyond\"'",
    "variation": "cards.raw_card_blob->'variation' = 'true'::jsonb",
}
