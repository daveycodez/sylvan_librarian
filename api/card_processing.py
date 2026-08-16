"""Card processing functions."""

from __future__ import annotations

import copy
import functools
import re
from typing import TYPE_CHECKING, Any

from api.parsing.card_query_nodes import calculate_devotion, fold_accents, mana_cost_str_to_dict

if TYPE_CHECKING:
    from collections.abc import Callable


def extract_image_location_uuid(card: dict[str, Any]) -> str:
    """Extract the image location UUID from a card."""
    for image_location in card.get("image_uris", {}).values():
        if ".jpg" in image_location:
            return image_location.rpartition("/")[-1].partition(".")[0]
    msg = f"No image location found for card: {card}"
    raise AssertionError(msg)


# Card types that can exist as a permanent on the battlefield. Devotion (MTG
# comprehensive rules) is defined only over permanents' mana costs, confirmed
# against the real Scryfall API (devotion: never matches a pure Instant/Sorcery,
# e.g. the real Lightning Bolt), so calculate_devotion()'s result is discarded
# for any card with no type in this set. Title-cased to match parse_type_line().
PERMANENT_CARD_TYPES = {"Artifact", "Battle", "Creature", "Enchantment", "Land", "Planeswalker"}


def parse_type_line(type_line: str) -> tuple[list[str], list[str]]:
    """Parse the type line of a card."""
    card_types, _, card_subtypes = (x.strip().split() for x in type_line.title().partition("\u2014"))
    return card_types, card_subtypes or []


def maybeify(func: Callable) -> Callable:
    """Convert value to int (via float first), returning None if conversion fails."""

    @functools.wraps(func)
    def wrapper(val: str | int | float | None) -> int | None:
        if val is None:
            return None
        try:
            return func(val)
        except (ValueError, TypeError):
            return None

    return wrapper


@maybeify
def maybe_float(val: str | int | float | None) -> float | None:
    """Convert value to float, returning None if conversion fails."""
    return float(val)


@maybeify
def maybe_int(val: str | int | float | None) -> int | None:
    """Convert value to int (via float first), returning None if conversion fails."""
    return int(float(val))


def rarity_text_to_int(rarity_text: str) -> int:
    """Convert rarity text to int."""
    rarity_map = {
        "common": 0,
        "uncommon": 1,
        "rare": 2,
        "special": 3,
        "mythic": 4,
        "bonus": 5,
    }
    return rarity_map.get(rarity_text.lower(), -1)


def extract_collector_number_int(collector_number: str | int | float | None) -> int | None:
    """Extract the integer part of a collector number."""
    if collector_number is None:
        return None
    # Implement magic.extract_collector_number_int in Python
    # Extract numeric characters using regex, similar to the database function
    numeric_part = re.sub(r"[^0-9]", "", str(collector_number))
    if numeric_part:
        try:
            int_val = int(numeric_part)
            # PostgreSQL integer range is -2^31 to 2^31-1
            if -(2**31) <= int_val <= 2**31 - 1:
                return int_val
        except (ValueError, OverflowError):
            pass
    return None  # Field will be null by default


# Face-merge policy for multi-face cards (#400, #873). Scryfall AND's search predicates at the
# CARD level, each satisfiable by any face — measured against api.scryfall.com 2026-08-08:
# `t:sorcery t:land` returns the MDFC lands (no single face is both), o: conjunctions match
# across faces (Ral, Monsoon Mage), and `c:b` matches Westvale Abbey's back-face-only color.
# One row per printing carrying any-face unions reproduces those semantics directly; one row
# per face would instead break every cross-face conjunction (no face-row satisfies both terms)
# on top of colliding on the scryfall_id primary key, which is how the back face silently won
# until now. Front-face scalars (cmc, mana cost, illustration, image, prices) match Scryfall's
# own top-level fields, verified on its card objects.
_FACE_LIST_UNIONS = ("card_types", "card_subtypes")
_FACE_FLAG_UNIONS = ("card_colors", "card_keywords", "produced_mana")
# The printed keyword list is a LIST, so its face merge is an order-preserving union rather than a
# dict update.
_FACE_PRINTED_LIST_UNIONS = ("card_keywords_printed",)
_FACE_JOINED_TEXTS = ("oracle_text", "flavor_text", "type_line")
# Copied per GROUP from the first face that has any of the group, so the numeric columns and
# their _text twins always describe the same face (the schema's check constraints couple them).
_FACE_STAT_GROUPS = (
    ("creature_power", "creature_toughness", "creature_power_text", "creature_toughness_text"),
    ("planeswalker_loyalty", "planeswalker_loyalty_text"),
)
# Joins face texts. "\n" so substring/regex matches cannot span faces in practice (`.` does not
# cross newlines), "//" because that is the face separator Scryfall itself renders.
_FACE_TEXT_SEPARATOR = "\n//\n"

# What `card_faces` stores per face, in Scryfall's own key names and value shapes.
#
# The merged row above is what the query planner filters on; this is what a face IS. Keeping it
# structurally (rather than inside raw_card_blob) is what lets the ENGINE answer face-level
# questions: the store is the only thing an engine-path request reads, and a JSONB column is not
# in it. It also retires the merge's one documented residual — when several faces carry a stat
# group (Brutal Cathar's 2/2 // 3/3) the merged row keeps only the front's, while Scryfall matches
# either; per-face power/toughness/loyalty make the back searchable again.
#
# `object` is the constant "card_face" and `image_uris` is a pure function of the card's id and the
# face's position, so neither is stored; both are re-emitted on read.
_FACE_OBJECT_FIELDS = (
    "name",
    # The face's printed-language text, in Scryfall's own key positions (printed_name after name,
    # printed_type_line after type_line, printed_text after oracle_text). Presence varies per face
    # per printing — a prepare-layout Spanish printing localizes the front face's name and type
    # line and NOTHING else — and _face_records keeps absent keys absent, so the absence
    # round-trips exactly instead of becoming null or borrowed English.
    "printed_name",
    "mana_cost",
    "type_line",
    "printed_type_line",
    "oracle_text",
    "printed_text",
    "power",
    "toughness",
    "loyalty",
    # Battles print their defense on the FACE (Invasion of Alara's front face is `defense: 7`) and
    # no column holds it, so leaving it out drops the number from every battle's card object.
    "defense",
    "colors",
    "color_indicator",
    "flavor_text",
    "artist",
    "artist_id",
    "illustration_id",
)


def _face_records(card_faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Snapshot each face's own fields, front first.

    Args:
        card_faces: The card's raw `card_faces` array, as Scryfall sent it.

    Returns:
        One dict per face, carrying only the keys the face actually has. Absent keys stay absent
        rather than becoming null, because Scryfall omits them and a reconstructed face has to
        agree key-for-key.
    """
    return [{field: face[field] for field in _FACE_OBJECT_FIELDS if field in face} for face in card_faces]


def _printed_name_folded(card: dict[str, Any], face_records: list[dict[str, Any]]) -> str | None:
    """The printed FULL name, folded exactly like card_name_folded, for the engine's printed-name index.

    Per-face printed_names joined " // " — each face falling back to its English name when Scryfall
    omitted printed_name there, so a prepare-layout printing whose second face has no printed name
    still forms the full "Front // Back" key. The card's own top-level printed_name when it has no
    faces. None when no printed name exists anywhere: an English printing contributes nothing to
    the printed-name index.

    Args:
        card: The card-level object (top-level printed_name lives here for single-faced cards).
        face_records: The `_face_records` snapshot, front first (empty for single-faced cards).

    Returns:
        The lowercased, accent-folded full printed name, or None.
    """
    if any("printed_name" in face for face in face_records):
        full = " // ".join(face.get("printed_name") or face.get("name") or "" for face in face_records)
    else:
        full = card.get("printed_name")
        if not full:
            return None
    return fold_accents(full.lower())


# Keys that do NOT go in card_compat_blob, because a column already holds them or they are a pure
# function of one. Kept subtractive, and mirrored in 2026-08-10-01-engine-card-objects.sql: the
# residue is "whatever is left", so a Scryfall key nobody has seen yet lands in the blob by default
# instead of being silently dropped the first time it appears.
#
# `prices` is deliberately absent from this set even though price_usd/eur/tix are columns --
# usd_foil, usd_etched and eur_foil are not, and keeping the object whole costs a few bytes against
# losing three fields.
_COMPAT_BLOB_EXCLUDED = frozenset(
    {
        # stored in a column of their own
        "id", "oracle_id", "name", "released_at", "layout", "mana_cost", "cmc", "type_line",
        "oracle_text", "power", "toughness", "loyalty", "colors", "color_identity", "keywords",
        "set", "set_name", "collector_number", "rarity", "flavor_text", "artist",
        "illustration_id", "border_color", "edhrec_rank", "legalities", "produced_mana",
        "watermark", "reserved", "game_changer", "frame",
        "printed_name", "printed_type_line", "printed_text",
        # pure functions of id / set / collector_number / oracle_id, re-emitted on read
        "object", "uri", "scryfall_uri", "image_uris", "rulings_uri", "prints_search_uri",
        "set_uri", "set_search_uri", "scryfall_set_uri", "card_back_id", "related_uris",
        "purchase_uris", "resource_id",
        # its own column
        "card_faces",
        # added by this module before the snapshot is taken
        "card_name", "face_name", "face_idx", "scryfall_id",
    },
)  # fmt: skip


def _compat_blob(card: dict[str, Any]) -> dict[str, Any]:
    """The Scryfall keys that no column holds and no derivation recovers.

    Args:
        card: The card object as Scryfall sent it, before this module's own keys matter.

    Returns:
        The residue, ready to store as card_compat_blob.
    """
    return {key: value for key, value in card.items() if key not in _COMPAT_BLOB_EXCLUDED}


def _merge_processed_faces(faces: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse fully-processed per-face rows into the card's single searchable row.

    The first face (the front) supplies the row and with it every identity and display
    scalar; later faces fold in per the policy tables above. Known residual, sized in
    the tests: when several faces carry a stat group (Brutal Cathar's 2/2 // 3/3), only
    the first face's values are searchable — Scryfall also matches the back's.

    Args:
        faces: Non-empty list of processed rows, one per surviving face, front first.

    Returns:
        The merged row (the front face's dict, mutated in place).
    """
    merged, *rest = faces
    for face in rest:
        for key in _FACE_LIST_UNIONS:
            seen = merged[key]
            seen.extend(value for value in face[key] if value not in seen)
        for key in _FACE_FLAG_UNIONS:
            merged[key].update(face[key])
        for key in _FACE_PRINTED_LIST_UNIONS:
            seen = merged[key]
            seen.extend(value for value in face[key] if value not in seen)
        for key in _FACE_JOINED_TEXTS:
            parts = [part for part in (merged.get(key), face.get(key)) if part]
            merged[key] = (" // " if key == "type_line" else _FACE_TEXT_SEPARATOR).join(parts)
        for group in _FACE_STAT_GROUPS:
            if all(merged.get(field) is None for field in group) and any(face.get(field) is not None for field in group):
                for field in group:
                    merged[field] = face.get(field)
    return merged


def preprocess_card(card: dict[str, Any]) -> list[dict[str, Any]]:  # noqa: PLR0915,C901,PLR0912
    """Preprocess a card to remove invalid cards and add necessary fields.

    A multi-face card (transform, MDFC, split, adventure, flip) is merged into ONE row
    carrying the front face's identity and every face's searchable data — see
    `_merge_processed_faces`. Single-faced cards return a list with one dictionary.
    Returns an empty list for invalid/filtered cards.
    """
    if not set(card["legalities"].values()) & {"legal", "restricted"}:
        return []
    if "playtest" in card.get("promo_types", []):
        return []
    if "paper" not in card.get("games", []):
        return []
    if card.get("set_type") == "funny":
        return []

    # Filter out unplayable cards: Cards and Tokens
    type_line = card.get("type_line")
    if type_line:
        card_types, card_subtypes = parse_type_line(type_line)
        if "Card" in card_types or "Token" in card_types:
            return []

    # Filter out "X // X" cards (same name on both faces, e.g. "Name // Name")
    card_name = card.get("name", "")
    if "//" in card_name:
        left_name, _, right_name = card_name.partition("//")
        if left_name.strip() == right_name.strip():
            return []

    if "raw_card_blob" in card:
        # Already processed, don't need to re-process
        return [card]

    # Lift the card name before processing faces, because it shouldn't be clobbered by card_faces
    if "card_name" not in card:
        # Non-recursive case: first time seeing this card
        card["card_name"] = card.get("name")
    else:
        # Recursive case: processing a face
        card["face_name"] = card.get("name")

    # Handle cards with card_faces (DFCs): process each face through the full pipeline below,
    # then collapse the per-face rows into the card's one searchable row.
    card_faces = card.get("card_faces")
    if card_faces:
        for creature_attribute in ["creature_power", "creature_toughness"]:
            card.pop(creature_attribute, None)
            card.pop(f"{creature_attribute}_text", None)
        face_rows = []
        for face_data in card_faces:
            # Merge card-level data with face-specific data
            # Precedence: face_data (name, type_line, etc.) > card (legalities, games, etc.)
            merged = copy.deepcopy(card) | face_data
            merged.pop("card_faces", None)  # Don't keep recursing
            face_rows.extend(preprocess_card(merged))
        if not face_rows:
            return []
        merged_row = _merge_processed_faces(face_rows)
        # The blob is the card-level object with its faces re-attached — what Scryfall sent, not a
        # face promoted to look like a card. Every searchable field is already merged onto the row
        # above, so the blob has no derivation left to do, and keeping it verbatim is what makes it
        # answerable: a card object cannot be rebuilt from a face (`card_faces` is gone, `name` and
        # `type_line` are the front's, and which fields a real card carries at top level varies by
        # layout — a split card has `mana_cost` and `image_uris` there, a transform card does not).
        #
        # The one consumer that read a *face* field off the blob is `image_uris`, which for a
        # transform card now lives only under `card_faces`; every reader coalesces to
        # `card_faces->0` (scripts/copy_images_to_s3.py, scripts/prefer_weights.py). Everything else
        # read from the blob — lang, set_type, games, finishes, frame_effects, image_status,
        # reserved, game_changer — is card-level and identical either way.
        merged_row["raw_card_blob"] = copy.deepcopy(card) | {"card_faces": card_faces}
        # The engine's copy of the same thing. raw_card_blob is a Postgres column and the SQL path
        # is a fallback, so anything only the blob carries is unanswerable on the engine path.
        merged_row["card_faces"] = _face_records(card_faces)
        merged_row["card_compat_blob"] = _compat_blob(card)
        # The printed-language COLUMNS are the card-level triple. The per-face recursion above
        # merged each face's own printed keys into its row, so without this the front face's
        # printed_name would masquerade as the card's — the per-face halves ride card_faces.
        merged_row["printed_name"] = card.get("printed_name")
        merged_row["printed_type_line"] = card.get("printed_type_line")
        merged_row["printed_text"] = card.get("printed_text")
        merged_row["printed_name_folded"] = _printed_name_folded(card, merged_row["card_faces"])
        return [merged_row]

    # Single face case - set defaults
    card.setdefault("face_name", card.get("name"))
    card.setdefault("face_idx", 1)

    # Scryfall omits flavor_text entirely when a printing has none (unlike oracle_text, which it
    # always sends, empty string included, even for vanilla cards). Normalize to '' so negated
    # flavor-text filters treat "no flavor text" as empty, matching Scryfall's own search behavior
    # (confirmed empirically: -flavor:<impossible> includes flavorless prints on scryfall.com) and
    # the engine's existing unwrap_or_default() handling.
    card["flavor_text"] = card.get("flavor_text") or ""

    # Store the original card data before modifications for raw_card_blob
    raw_card_data = copy.deepcopy(card)
    card["raw_card_blob"] = raw_card_data
    card["card_compat_blob"] = _compat_blob(raw_card_data)
    card["scryfall_id"] = card["id"]

    card_types, card_subtypes = parse_type_line(card["type_line"])
    card["card_types"] = card_types
    card["card_subtypes"] = card_subtypes

    card["planeswalker_loyalty"] = maybe_int(card.get("loyalty"))
    if "Creature" in card_types or {"Vehicle", "Spacecraft"} & set(card_subtypes):
        card["creature_power"] = maybe_int(card.get("power"))
        card["creature_toughness"] = maybe_int(card.get("toughness"))
        card["creature_power_text"] = card.get("power")
        card["creature_toughness_text"] = card.get("toughness")
    else:
        # Explicit None (not pop) so these keys appear as JSON null in the processed blob.
        # An absent key falls through to the existing DB row's value during upsert merging;
        # an explicit null overrides it, keeping creature_power_text/creature_toughness_text
        # in sync with creature_power/creature_toughness for the check constraint.
        card["creature_power_text"] = None
        card["creature_toughness_text"] = None
        card["creature_power"] = None
        card["creature_toughness"] = None

    # objects of keys to true
    card["card_colors"] = dict.fromkeys(card["colors"], True)
    card["card_color_identity"] = dict.fromkeys(card["color_identity"], True)
    # Lowercased so the stored key matches what `keyword:` looks up -- Scryfall's own spelling is
    # inconsistently cased ("First strike", "Doctor's companion"), and lowercase is the same
    # normalization the oracle/art/is tag collections already use on both sides.
    card["card_keywords"] = dict.fromkeys((keyword.lower() for keyword in card.get("keywords", [])), True)
    # ...and again AS PRINTED, for the card object. The fold above is not invertible: only 455 of
    # the 885 distinct keywords in the 2026-08-16 all_cards bulk come back from capitalizing the
    # folded form ("Battle Cry", "AV Bead", "Bio-plasmic Barrage" do not), and Scryfall's order is
    # neither the folded dict's nor alphabetical ("Flying" before "Flash" on Brazen Borrower). A
    # LIST, not a {key: true} object, because it exists for its order. `keyword:` keeps binding the
    # folded keys; this one is only ever emitted.
    card["card_keywords_printed"] = list(dict.fromkeys(card.get("keywords", [])))
    card["produced_mana"] = dict.fromkeys(card.get("produced_mana", []), True)
    # Scryfall's TOP-LEVEL color_indicator -- the printed colour dot a card whose mana cost cannot
    # state its colours carries (a meld result, a coloured back). 546 printings in the bulk have
    # one and no column held it, so the card object emitted it on none of them. Not face-merged:
    # the two-image layouts keep theirs on the faces and send no top-level copy at all.
    card["color_indicator"] = dict.fromkeys(card.get("color_indicator", []), True)

    card["edhrec_rank"] = card.get("edhrec_rank")

    # Extract frame data - combine frame version and frame effects into single JSONB object
    frame_data = {}
    # Add frame version if present (titlecased for consistency)
    frame_version = card.get("frame")
    if frame_version:
        frame_data[frame_version.title()] = True
    # Add frame effects if present (titlecased for consistency)
    frame_effects = card.get("frame_effects", [])
    for effect in frame_effects:
        frame_data[effect.title()] = True
    card["card_frame_data"] = frame_data

    # Extract pricing data if available - ensure they are floats for jsonb_populate_record
    prices = card.get("prices", {})
    card["price_usd"] = maybe_float(prices.get("usd"))
    card["price_eur"] = maybe_float(prices.get("eur"))
    card["price_tix"] = maybe_float(prices.get("tix"))

    # Extract set code for dedicated column (lowercased for case-insensitive search;
    # Scryfall codes are lowercase already, this just makes the invariant explicit)
    set_code = card.get("set")
    card["card_set_code"] = set_code.lower() if isinstance(set_code, str) else set_code

    # Extract layout and border for dedicated columns (lowercased for case-insensitive search)
    if "layout" in card:
        card["card_layout"] = card["layout"].lower()
    if "border_color" in card:
        card["card_border"] = card["border_color"].lower()
    if "watermark" in card:
        card["card_watermark"] = card["watermark"].lower()
    # The printing's language ("en", "ja", ...; Scryfall sends it lowercase already, lowered here
    # to make the invariant explicit like the columns above). `lang` itself deliberately stays in
    # card_compat_blob — the card object reads it from there — while card_lang is the search column.
    if "lang" in card:
        card["card_lang"] = card["lang"].lower()
    # The printed-language triple, verbatim. Explicit None (not absent) when Scryfall omitted the
    # key, so an upsert overrides any stale value instead of keeping it, same reasoning as the
    # creature stat columns above.
    card["printed_name"] = card.get("printed_name")
    card["printed_type_line"] = card.get("printed_type_line")
    card["printed_text"] = card.get("printed_text")
    card["printed_name_folded"] = _printed_name_folded(card, [])

    mana_cost_text = card.get("mana_cost", "")
    card["mana_cost_jsonb"] = mana_cost_str_to_dict(mana_cost_text)
    # Nonpermanents (Instant/Sorcery) never contribute devotion, regardless of
    # their mana cost - see PERMANENT_CARD_TYPES.
    is_permanent = bool(PERMANENT_CARD_TYPES & set(card_types))
    card["devotion"] = calculate_devotion(mana_cost_text) if is_permanent else {}

    # Map field names to match database column names for jsonb_populate_record
    # Don't overwrite card_name if already set (for DFCs, it's set before processing faces)
    if "card_name" not in card:
        card["card_name"] = card.get("name")
    # Accent-folded lowercase name, precomputed once at import so fuzzy name: search can
    # match "eowyn" against "Éowyn" without folding diacritics on every query (#649).
    card["card_name_folded"] = fold_accents(card["card_name"].lower())
    card["mana_cost_text"] = card.get("mana_cost")
    card["planeswalker_loyalty_text"] = card.get("loyalty")
    card["card_artist"] = card.get("artist")

    # Handle CMC and edhrec_rank conversion using helper function
    card["cmc"] = maybe_int(card.get("cmc"))

    # Handle rarity conversion - implement in Python to avoid SQL boilerplate
    rarity_text = card.get("rarity", "").lower()
    if rarity_text:
        card["card_rarity_text"] = rarity_text
        card["card_rarity_int"] = rarity_text_to_int(rarity_text)

    # Handle collector number - implement extraction in Python to avoid SQL boilerplate
    collector_number = card.get("collector_number")
    card["collector_number"] = collector_number
    card["collector_number_int"] = extract_collector_number_int(collector_number)
    card["illustration_id"] = card.get("illustration_id")

    # Handle legalities and produced_mana defaults
    card.setdefault("card_legalities", card.get("legalities", {}))

    # Ensure all NOT NULL DEFAULT fields are set to avoid constraint violations
    for key in ["produced_mana", "color_indicator", "card_oracle_tags", "card_art_tags", "card_is_tags"]:
        card.setdefault(key, {})
    card.setdefault("card_keywords_printed", [])

    return [card]
