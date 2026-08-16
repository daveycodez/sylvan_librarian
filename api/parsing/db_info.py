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
        search_aliases=["color", "colors", "c"],
        parser_class=ParserClass.COLOR,
    ),
    FieldInfo(
        db_column_name="card_color_identity",
        field_type=FieldType.JSONB_OBJECT,
        search_aliases=["color_identity", "coloridentity", "id", "identity", "ci"],
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
        search_aliases=["is"],
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
DB_NAME_TO_FIELD_TYPE = {}

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
    DB_NAME_TO_FIELD_TYPE[col.db_column_name] = col.field_type

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

COLOR_CODE_TO_NAME = {
    "b": "black",
    "c": "colorless",
    "g": "green",
    "r": "red",
    "u": "blue",
    "w": "white",
}

COLOR_NAME_TO_CODE = {v: k for k, v in COLOR_CODE_TO_NAME.items()}

# The colour values that are a COUNT rather than a set of letters.
#
# `c:m` is not "the colour m" -- there is no such colour. It is Scryfall's word for MULTICOLOURED,
# and it compares the NUMBER of colours in the column, which is why it cannot live in
# COLOR_NAME_TO_CODE beside `white`: there are no letters to expand. `gold` and the
# `multicolor` spellings are the same value under other names; every one of the six answers the
# identical count (`c:m` = `c:gold` = `c:multicolor` = `c:multicolored` = `c:multicolour` =
# `c:multicoloured` = 44 in Kaldheim, where `c:2` = 43 and `c>=2` = 44).
#
# THE OPERATOR TABLE IS MEASURED, and it is not "substitute the number 2". Corpus-wide against
# api.scryfall.com, 2026-08-16:
#
#   c:m = c=m = c>m = c>=m = 4,607 = `c>=2`          (`c=2` is 3,811 and `c>2` is 796)
#   c<m = c!=m           = 29,049 = `c<2`            (`c!=2` is 29,836)
#   c<=m                 = 33,599 = EVERY CARD       (`c<=2` is 32,812)
#
# `>` is the surprise on the high side -- `c>m` is `c>=2`, not `c>2` -- and `!=` is the surprise on
# the low side: `c!=m` is `c<2`, the negation of "is multicoloured", NOT `c!=2`, which would also
# admit the 796 three-and-more-colour cards. `<=` is a tautology rather than `c<=2`, pinned against
# a second term so it cannot be read as "the whole corpus": `c<=m t:creature` = `t:creature`
# = `c<=5 t:creature` = 18,753 where `c<=2 t:creature` = 18,140.
#
# The identity spellings take the same table on their own column: `id:m` = `id=m` = `id>m` =
# `id>=m` = 5,831 = `id>=2`, `id<m` = `id!=m` = 27,768 = `id<2` (`id!=2` is 28,824), and
# `id<=m` = 33,599 = every card (`id<=2` is 32,543).
#
# `produces:` is DELIBERATELY NOT lowered, and the reason is a measurement rather than caution.
# `produces:m` really is a count on Scryfall -- 1,460, which is `produces>=2` -- but it is a count
# over SIX values, colorless among them: `produces=1 produces:c` = 481, exactly the cards that
# produce colorless and nothing else, so a C-only producer counts ONE there. Both of this project's
# count implementations read the five WUBRG keys and would call that card zero
# (`magic.color_identity_mask` on the SQL side, the popcount in card_engine's ColorCountCmp on the
# other), and the five-colour union agrees on both sides at 2,121 while Scryfall's `produces>=1` is
# 2,603 -- the 482-card difference IS the colorless producers. So `produces:m` stays the error it
# already was rather than a count that is quietly short by 481 cards; making it work is a
# six-value count, which is its own change on both halves.
COLOR_COUNT_NAMES = frozenset(
    {
        "m",
        "gold",
        "multicolor",
        "multicolour",
        "multicolored",
        "multicoloured",
    }
)

FORMAT_CODE_TO_NAME = {
    "m": "modern",
    "s": "standard",
    "l": "legacy",
    "p": "pauper",
    "c": "commander",
    "v": "vintage",
    "h": "historic",
}

FORMAT_NAME_TO_CODE = {v: k for k, v in FORMAT_CODE_TO_NAME.items()}
