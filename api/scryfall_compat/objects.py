"""Scryfall response objects: card reconstruction, envelopes, and the text rendering.

Everything here is pure — dicts in, dicts out — so the payload shape can be tested without a
database or a request. `routes.py` owns the HTTP and SQL sides.

The one subtle piece is `to_scryfall_card`. `cards.raw_card_blob` holds the card object Scryfall
sent, but not quite untouched: `preprocess_card` adds three internal keys to it and normalizes an
absent `flavor_text` to `""`. Both are exactly reversible, and reversing them is the whole of the
function.

There is no column holding a pristine copy alongside it, and there deliberately isn't one: the blob
being answerable is a property of the importer, maintained there rather than worked around here.
The one case where the blob is *not* the card — a multi-face row written before the merged-row work
— is handled as a fallback rather than as a stored duplicate, because it is a fixed window that one
import closes.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

# Keys `preprocess_card` adds to the object it snapshots into raw_card_blob. Stripping them, and
# undoing the flavor_text normalization below, inverts the snapshot. A multi-face row carries only
# `card_name`; a single-face one carries all three.
_IMPORTER_ADDED_KEYS = ("card_name", "face_name", "face_idx")

# Sizes Scryfall serves under `image_uris`, and the `version` vocabulary of the image format.
IMAGE_VERSIONS = ("small", "normal", "large", "png", "art_crop", "border_crop")
DEFAULT_IMAGE_VERSION = "large"

# Scryfall pages every card list at 175, and clients page by following `next_page` rather than by
# computing offsets, so this has to match or a client's page count silently disagrees with ours.
PAGE_SIZE = 175

# Scryfall caps a collection POST at 75 identifiers and 422s past it.
MAX_COLLECTION_IDENTIFIERS = 75

# Scryfall caps an autocomplete catalog at 20 names.
MAX_AUTOCOMPLETE_VALUES = 20


def to_scryfall_card(row: dict[str, Any]) -> dict[str, Any]:
    """Recover the Scryfall card object for one `magic.cards` row.

    Args:
        row: A row carrying at least `raw_card_blob`.

    Returns:
        The card object as Scryfall's bulk data holds it.
    """
    card = {key: value for key, value in row["raw_card_blob"].items() if key not in _IMPORTER_ADDED_KEYS}
    # Scryfall omits flavor_text on printings that have none; the importer writes "" so that
    # negated flavor filters treat flavorless prints as empty. Absent and "" are the same state,
    # and "" is one Scryfall never sends, so dropping it is the exact inverse.
    if card.get("flavor_text") == "":
        del card["flavor_text"]

    if card.get("object") == "card_face":
        # A multi-face row written by an importer that still promoted the front face into the
        # blob, i.e. one not rewritten since the merged-row work. Rows are rewritten by an import,
        # not by a migration, so this is a window of at most one import cycle after deploy rather
        # than a state to design around. Nothing here can rebuild the card — the face is all that
        # survives — so it is at least presented as the card it came from, rather than leaking
        # `card_face` into a payload that claims to be a card.
        card["object"] = "card"
        card["name"] = row["raw_card_blob"].get("card_name", card.get("name"))
    return card


def error_object(*, code: str, status: int, details: str, warnings: list[str] | None = None) -> dict[str, Any]:
    """Build Scryfall's error object.

    Args:
        code: Scryfall's machine-readable error slug, e.g. "not_found".
        status: The HTTP status the response carries.
        details: Human-readable explanation.
        warnings: Non-fatal notes about the request, when there are any.

    Returns:
        The error object, with `warnings` present only when non-empty.
    """
    error: dict[str, Any] = {"object": "error", "code": code, "status": status, "details": details}
    if warnings:
        error["warnings"] = warnings
    return error


def not_found_error(details: str) -> dict[str, Any]:
    """Build the 404 error object.

    Args:
        details: Human-readable explanation.

    Returns:
        The error object.
    """
    return error_object(code="not_found", status=404, details=details)


def bad_request_error(details: str, *, warnings: list[str] | None = None) -> dict[str, Any]:
    """Build the 400 error object.

    Args:
        details: Human-readable explanation.
        warnings: Non-fatal notes about the request.

    Returns:
        The error object.
    """
    return error_object(code="bad_request", status=400, details=details, warnings=warnings)


def card_list(  # noqa: PLR0913
    cards: list[dict[str, Any]],
    *,
    total_cards: int | None = None,
    has_more: bool = False,
    next_page: str | None = None,
    not_found: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build Scryfall's List object.

    Key order follows Scryfall's own so a byte-comparing client sees the same document.

    Args:
        cards: The page of objects.
        total_cards: Unpaginated match count; omitted on lists that do not paginate.
        has_more: Whether a further page exists.
        next_page: Absolute URL of the next page, when there is one.
        not_found: Identifiers a collection request could not resolve.
        warnings: Non-fatal notes about the request.

    Returns:
        The List object.
    """
    result: dict[str, Any] = {"object": "list"}
    if total_cards is not None:
        result["total_cards"] = total_cards
    if not_found is not None:
        result["not_found"] = not_found
    result["has_more"] = has_more
    if next_page is not None:
        result["next_page"] = next_page
    if warnings:
        result["warnings"] = warnings
    result["data"] = cards
    return result


def catalog_object(values: list[str]) -> dict[str, Any]:
    """Build Scryfall's Catalog object.

    Args:
        values: The catalog entries.

    Returns:
        The Catalog object.
    """
    return {"object": "catalog", "total_values": len(values), "data": values}


def ruling_object(row: dict[str, Any]) -> dict[str, Any]:
    """Build one Scryfall Ruling object from a `magic.rulings` row.

    Args:
        row: A row with oracle_id, source, published_at and comment.

    Returns:
        The Ruling object.
    """
    return {
        "object": "ruling",
        "oracle_id": str(row["oracle_id"]),
        "source": row["source"],
        "published_at": row["published_at"].isoformat(),
        "comment": row["comment"],
    }


def build_page_url(base_url: str, params: dict[str, Any], page: int) -> str:
    """Build the absolute `next_page` URL for a search result.

    Scryfall spells every effective parameter into `next_page` rather than echoing only what the
    client sent, and clients follow the URL verbatim, so the query string is rebuilt from the
    resolved values.

    Args:
        base_url: Scheme and host the request arrived on, plus the route path.
        params: Effective query parameters, excluding `page`.
        page: The page number the URL should fetch.

    Returns:
        The absolute URL.
    """
    query = dict(sorted(params.items()))
    query["page"] = page
    return f"{base_url}?{urllib.parse.urlencode(sorted(query.items()))}"


def _face_of(card: dict[str, Any], face: str) -> dict[str, Any]:
    """Return the requested face of a card, falling back to the card itself.

    Args:
        card: A Scryfall card object.
        face: "back" for the second face; anything else selects the card/front.

    Returns:
        The face object, or the card when it has no distinct faces.
    """
    faces = card.get("card_faces") or []
    back_face_count = 2
    if face == "back" and len(faces) >= back_face_count:
        return faces[1]
    return card


def image_uri(card: dict[str, Any], *, version: str, face: str) -> str | None:
    """Return the image URL for a card at a given size and face.

    Args:
        card: A Scryfall card object.
        version: One of IMAGE_VERSIONS.
        face: "front" or "back".

    Returns:
        The image URL, or None when the card carries no image of that size.
    """
    selected = _face_of(card, face)
    uris = selected.get("image_uris") or card.get("image_uris") or {}
    return uris.get(version)


def _render_face(face: dict[str, Any]) -> str:
    """Render one card face in Scryfall's plain-text format.

    Args:
        face: A card or card_face object.

    Returns:
        The rendered face, without a trailing newline.
    """
    heading = face.get("name", "")
    mana_cost = face.get("mana_cost")
    if mana_cost:
        heading = f"{heading} {mana_cost}"

    lines = [heading]
    if face.get("type_line"):
        lines.append(face["type_line"])
    if face.get("oracle_text"):
        lines.append(face["oracle_text"])
    if face.get("power") is not None and face.get("toughness") is not None:
        lines.append(f"{face['power']}/{face['toughness']}")
    elif face.get("loyalty") is not None:
        lines.append(f"Loyalty: {face['loyalty']}")
    elif face.get("defense") is not None:
        lines.append(f"Defense: {face['defense']}")
    return "\n".join(lines)


def card_to_text(card: dict[str, Any]) -> str:
    """Render a card in Scryfall's `format=text` layout.

    Args:
        card: A Scryfall card object.

    Returns:
        The rendered card. Multi-face cards render every face, separated by a blank line.
    """
    faces = card.get("card_faces") or []
    if faces:
        return "\n\n".join(_render_face(face) for face in faces)
    return _render_face(card)
