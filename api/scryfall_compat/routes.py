"""The Scryfall-compatible `/cards/*` routes.

`ScryfallCardsRoutes` is a mixin on `APIResource`: it is a separate class only so that the
compatibility layer lands in its own file rather than growing `api_resource.py`, and it depends on
`_search`, `_run_query` and `_require_setup_complete` from the class it is mixed into.

Two conventions run through every handler here:

- **Every parameter is annotated `str`.** The generic binder coerces by annotation and raises its
  own `400` on a bad value, which would put a non-Scryfall error body on the wire. Parsing the
  values in the handler keeps every failure inside the Scryfall error object.
- **The router is prefix-based.** `_resolve_action` matches the full path first and then falls back
  to the first segment, so the five named sub-routes (`search`, `named`, `autocomplete`, `random`,
  `collection`) register their exact paths and everything else — `/cards`, `/cards/:id`,
  `/cards/:code/:number/:lang`, the five external-id namespaces, and the rulings variants — arrives
  at `scryfall_cards` as up to three positional segments.

What is *not* identical to api.scryfall.com is recorded in
docs/issues/local-scryfall-cards-api.md; the short version is that the corpus is a filtered subset
of Scryfall's, so a card this instance never imported 404s here and resolves there.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from typing import TYPE_CHECKING, Any

import falcon
import orjson

from api.enums import CardOrdering, PreferOrder, SortDirection, UniqueOn
from api.parsing import generate_sql_query, parse_scryfall_query
from api.parsing.card_query_nodes import fold_accents
from api.scryfall_compat import objects
from api.scryfall_compat.objects import (
    DEFAULT_IMAGE_VERSION,
    IMAGE_VERSIONS,
    MAX_AUTOCOMPLETE_VALUES,
    MAX_COLLECTION_IDENTIFIERS,
    PAGE_SIZE,
    bad_request_error,
    card_list,
    card_to_text,
    catalog_object,
    error_object,
    not_found_error,
    ruling_object,
    to_scryfall_card,
)
from api.utils.routing import route

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Columns every card lookup needs: the blob `to_scryfall_card` reads, plus the id it is re-sorted
# by when a batch comes back in an order the caller did not ask for.
_CARD_COLUMNS = "scryfall_id, raw_card_blob"

# Path segments that name an external id namespace rather than a set code.
_EXTERNAL_ID_NAMESPACES = ("multiverse", "mtgo", "arena", "tcgplayer", "cardmarket")

# Blob keys each namespace matches. Scryfall's MTGO and TCGplayer routes each accept two ids —
# the regular printing's and the foil/etched printing's — and both resolve to the same card.
_EXTERNAL_ID_KEYS: dict[str, tuple[str, ...]] = {
    "mtgo": ("mtgo_id", "mtgo_foil_id"),
    "arena": ("arena_id",),
    "tcgplayer": ("tcgplayer_id", "tcgplayer_etched_id"),
    "cardmarket": ("cardmarket_id",),
}

# Scryfall's `order` vocabulary, which `CardOrdering` covers except for the two below. Built from
# the enum rather than listed, so an ordering added there is accepted here without a second edit;
# the extra member that is not Scryfall's (`cubecobra`) is a harmless superset.
_ORDER_MAP: dict[str, CardOrdering] = {str(member): member for member in CardOrdering}

# The two Scryfall orders with no counterpart. `penny` needs penny_rank lifted out of raw_card_blob
# into a column; `review` is Scryfall-internal with no public input and is not reproducible at all.
# Both fall back to `name`, which is what Scryfall does with an order it does not recognize
# (measured 2026-08-09: it falls back silently), and add a warning saying so.
_SCRYFALL_ONLY_ORDERS = ("penny", "review")

# Scryfall's `dir` vocabulary. `auto` is not resolved here -- it reaches `_search` as AUTO and is
# folded against the ordering there, so this route and /search agree on what auto means.
_DIRECTION_MAP: dict[str, SortDirection] = {
    "asc": SortDirection.ASC,
    "desc": SortDirection.DESC,
    "auto": SortDirection.AUTO,
}

_UNIQUE_MAP: dict[str, UniqueOn] = {
    "cards": UniqueOn.CARD,
    "art": UniqueOn.ARTWORK,
    "prints": UniqueOn.PRINTING,
}

# Scryfall's own wording, down to the typographic apostrophe, so a client that string-matches on
# `details` behaves the same.
_NO_MATCH_DETAILS = (
    "Your query didn’t match any cards. Adjust your search terms or refer to the syntax guide "  # noqa: RUF001
    "at https://scryfall.com/docs/syntax"
)
_EMPTY_QUERY_DETAILS = "You didn't enter anything to search for."

# CSV columns for `format=csv`. Fixed rather than derived from the page's cards so the header does
# not change between pages of one result set. Nested objects are flattened with `_`, matching how
# Scryfall spells `image_uris_normal` and `prices_usd` in its own export.
_CSV_SCALAR_COLUMNS = (
    "object",
    "id",
    "oracle_id",
    "multiverse_ids",
    "mtgo_id",
    "mtgo_foil_id",
    "tcgplayer_id",
    "cardmarket_id",
    "name",
    "lang",
    "released_at",
    "uri",
    "scryfall_uri",
    "layout",
    "highres_image",
    "image_status",
    "mana_cost",
    "cmc",
    "type_line",
    "oracle_text",
    "power",
    "toughness",
    "loyalty",
    "colors",
    "color_identity",
    "keywords",
    "games",
    "reserved",
    "foil",
    "nonfoil",
    "finishes",
    "oversized",
    "promo",
    "reprint",
    "variation",
    "set_id",
    "set",
    "set_name",
    "set_type",
    "set_uri",
    "set_search_uri",
    "scryfall_set_uri",
    "rulings_uri",
    "prints_search_uri",
    "collector_number",
    "digital",
    "rarity",
    "flavor_text",
    "card_back_id",
    "artist",
    "artist_ids",
    "illustration_id",
    "border_color",
    "frame",
    "full_art",
    "textless",
    "booster",
    "story_spotlight",
    "edhrec_rank",
    "penny_rank",
)
_CSV_NESTED_COLUMNS = (
    ("image_uris", IMAGE_VERSIONS),
    ("prices", ("usd", "usd_foil", "usd_etched", "eur", "eur_foil", "tix")),
    ("related_uris", ("gatherer", "tcgplayer_infinite_articles", "tcgplayer_infinite_decks", "edhrec")),
    ("purchase_uris", ("tcgplayer", "cardmarket", "cardhoarder")),
)

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Thresholds for the typo-tolerant stage of `?fuzzy=`. A candidate must score at least the floor,
# and the best must lead the next distinct card name by at least the lead — closer than that and
# the query does not identify either card, so it is `ambiguous` rather than a guess. The floor sits
# deliberately above pg_trgm's default 0.3 similarity_threshold, so the index-assisted `%`
# prefilter always admits a strict superset of what the floor keeps.
FUZZY_SIMILARITY_FLOOR = 0.4
FUZZY_SIMILARITY_LEAD = 0.05

# Returned by the similarity stage when two names are too close to choose between. A distinct
# object rather than a flag so the caller compares with `is` and cannot confuse it with a row.
_AMBIGUOUS: dict[str, Any] = {"ambiguous": True}

# Hosts an absolute self-URL should address over plain HTTP. Everything else is assumed to be
# reached over TLS, which is what `next_page` has to say for a client to follow it.
_PLAINTEXT_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")  # noqa: S104

# Spelled out rather than falcon.MEDIA_JSON, which omits the charset Scryfall sends.
_JSON_CONTENT_TYPE = "application/json; charset=utf-8"


def _is_uuid(value: str) -> bool:
    """Return whether a path segment is shaped like a UUID.

    Args:
        value: The segment to test.

    Returns:
        True when the segment is a canonical 8-4-4-4-12 UUID.
    """
    return bool(_UUID_RE.match(value))


def _as_bool(value: str | None, *, default: bool = False) -> bool:
    """Parse a Scryfall boolean query parameter.

    Args:
        value: The raw parameter value, or None when absent.
        default: What an absent parameter means.

    Returns:
        The parsed flag; anything other than a recognized true spelling is False.
    """
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _as_int(value: str | None) -> int | None:
    """Parse an integer query parameter or path segment.

    Args:
        value: The raw value, or None when absent.

    Returns:
        The integer, or None when absent or unparseable.
    """
    if value is None:
        return None
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return None


def _self_base_url(request: falcon.Request | None, request_host: str, path: str) -> str:
    """Build the absolute URL of a route on this host.

    A `next_page` a client cannot follow is worse than no pagination at all, so the scheme is the
    request's own as corrected by `Forwarded` / `X-Forwarded-Proto` — the only signal that knows
    about a TLS-terminating proxy, behind which the request itself arrives as plain `http`. A
    deployment that terminates TLS in front of this service must send one of those headers, as it
    must already for `X-Proxy-Host` to give the right host.

    Guessing `https` from the host name instead was tried and is worse: it silently breaks any
    plain-HTTP deployment on a real hostname, which is a configuration this project supports,
    to paper over one that is misconfigured. The host only decides when there is no request to
    read, which is an internal caller rather than a served request.

    Args:
        request: The request being answered, when the handler has one.
        request_host: Host the request arrived on.
        path: Absolute route path, leading slash included.

    Returns:
        The absolute URL, with no query string.
    """
    host = request_host or "api.scryfall.com"
    if request is not None:
        return f"{request.forwarded_scheme}://{host}{path}"
    scheme = "http" if host.split(":")[0] in _PLAINTEXT_HOSTS else "https"
    return f"{scheme}://{host}{path}"


def _flatten_for_csv(card: dict[str, Any]) -> dict[str, Any]:
    """Flatten a card object onto the fixed CSV column set.

    Args:
        card: A Scryfall card object.

    Returns:
        A mapping from column name to cell value; list values are joined on commas.
    """
    row: dict[str, Any] = {}
    for column in _CSV_SCALAR_COLUMNS:
        value = card.get(column)
        row[column] = ",".join(str(item) for item in value) if isinstance(value, list) else value
    for parent, children in _CSV_NESTED_COLUMNS:
        nested = card.get(parent) or {}
        for child in children:
            row[f"{parent}_{child}"] = nested.get(child)
    return row


def _csv_columns() -> list[str]:
    """Return the CSV header in column order.

    Returns:
        Every scalar column followed by the flattened nested columns.
    """
    columns = list(_CSV_SCALAR_COLUMNS)
    for parent, children in _CSV_NESTED_COLUMNS:
        columns.extend(f"{parent}_{child}" for child in children)
    return columns


def _cards_to_csv(cards: Sequence[dict[str, Any]]) -> str:
    """Render a page of cards as CSV.

    Args:
        cards: The card objects to render.

    Returns:
        The CSV document, header row included.
    """
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_csv_columns(), extrasaction="ignore")
    writer.writeheader()
    for card in cards:
        writer.writerow(_flatten_for_csv(card))
    return buffer.getvalue()


class ScryfallCardsRoutes:
    """The `/cards/*` routes, mixed into `APIResource`.

    Every handler returns the value that becomes the response body, or None after writing the
    response itself (the text and CSV formats, which are not JSON). Errors are returned as
    Scryfall error objects with the matching status rather than raised, so the generic Falcon
    error serializer never sees them.
    """

    # ---------------------------------------------------------------- response plumbing

    def _scryfall_respond(
        self,
        falcon_response: falcon.Response | None,
        payload: dict[str, Any],
        *,
        pretty: bool = False,
    ) -> dict[str, Any] | None:
        """Write a JSON payload, honoring the error status it carries and `pretty`.

        Args:
            falcon_response: The response to write to.
            payload: The Scryfall object to serialize.
            pretty: Whether to emit indented JSON.

        Returns:
            The payload when the caller should let the framework serialize it, or None when this
            method already wrote the body.
        """
        if falcon_response is not None:
            falcon_response.content_type = _JSON_CONTENT_TYPE
            status = payload.get("status") if payload.get("object") == "error" else None
            if isinstance(status, int):
                falcon_response.status = falcon.util.code_to_http_status(status)
            if pretty:
                falcon_response.text = orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode()
                return None
        return payload

    def _respond_text(self, falcon_response: falcon.Response | None, body: str, content_type: str) -> None:
        """Write a non-JSON body.

        Args:
            falcon_response: The response to write to.
            body: The rendered document.
            content_type: Its media type.
        """
        if falcon_response is not None:
            falcon_response.content_type = content_type
            falcon_response.text = body

    def _render_card(  # noqa: PLR0913
        self,
        card: dict[str, Any],
        *,
        falcon_response: falcon.Response | None,
        card_format: str,
        face: str,
        version: str,
        pretty: bool,
    ) -> dict[str, Any] | None:
        """Emit one card in the requested format.

        Args:
            card: The Scryfall card object.
            falcon_response: The response to write to.
            card_format: "json", "text" or "image".
            face: "front" or "back".
            version: One of IMAGE_VERSIONS.
            pretty: Whether JSON output is indented.

        Returns:
            The payload to serialize, or None when the body was written here.

        Raises:
            falcon.HTTPFound: For `format=image`, redirecting to the image itself.
        """
        if card_format == "text":
            self._respond_text(falcon_response, card_to_text(card), "text/plain; charset=utf-8")
            return None
        if card_format == "image":
            location = objects.image_uri(card, version=version, face=face)
            if not location:
                return self._scryfall_respond(
                    falcon_response,
                    not_found_error("No image is available for this card in that version."),
                    pretty=pretty,
                )
            raise falcon.HTTPFound(location)
        return self._scryfall_respond(falcon_response, card, pretty=pretty)

    # ---------------------------------------------------------------- card lookups

    def _run_uncached(self, *, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Run a query that must not be memoized, and return its rows.

        `_run_query` keys its cache on the SQL text and the bound parameters, which is right for
        every lookup here except the random draw: that one is deliberately non-deterministic for a
        fixed query and parameter set, so caching it would replay one card forever.

        Args:
            query: The SQL to run.
            params: Bound parameters.

        Returns:
            The result rows.
        """
        with self._conn_pool.connection() as conn, conn.cursor() as cursor:
            self._set_statement_timeout(cursor, 10_000)
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def _fetch_one_card(self, where: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Fetch the single best printing matching a predicate.

        Ties are broken by prefer_score, so a lookup that spans printings (by name, by oracle id)
        returns the same representative printing the rest of the API would pick.

        Args:
            where: SQL predicate, referencing `card` as the table alias.
            params: Bound parameters for the predicate.

        Returns:
            The card, or None when nothing matched.
        """
        rows = self._run_query(
            query=(
                f"SELECT {_CARD_COLUMNS} FROM magic.cards AS card WHERE {where} "
                "ORDER BY prefer_score DESC NULLS LAST, released_at DESC LIMIT 1"
            ),
            params=params,
            explain=False,
        )["result"]
        return to_scryfall_card(rows[0]) if rows else None

    def _cards_by_ids(self, scryfall_ids: Sequence[str]) -> list[dict[str, Any]]:
        """Fetch cards by scryfall id, preserving the order of the ids given.

        Args:
            scryfall_ids: The ids to fetch.

        Returns:
            The cards, in `scryfall_ids` order; ids that matched nothing are skipped.
        """
        if not scryfall_ids:
            return []
        rows = self._run_query(
            # A comma-joined string rather than a list: _run_query passes list parameters through
            # maybe_json(), which binds them as jsonb, and jsonb does not cast to uuid[].
            query=(
                f"SELECT {_CARD_COLUMNS} FROM magic.cards AS card WHERE scryfall_id = ANY(string_to_array(%(ids)s, ',')::uuid[])"
            ),
            params={"ids": ",".join(scryfall_ids)},
            explain=False,
        )["result"]
        by_id = {str(row["scryfall_id"]): to_scryfall_card(row) for row in rows}
        return [by_id[card_id] for card_id in scryfall_ids if card_id in by_id]

    # ---------------------------------------------------------------- GET /cards/search

    @route(paths=("cards/search",))
    def scryfall_cards_search(  # noqa: PLR0913
        self,
        *,
        falcon_response: falcon.Response | None = None,
        request: falcon.Request | None = None,
        request_host: str = "",
        q: str | None = None,
        unique: str = "cards",
        order: str = "name",
        dir: str = "auto",  # noqa: A002  -- Scryfall's parameter name
        page: str = "1",
        format: str = "json",  # noqa: A002  -- Scryfall's parameter name
        pretty: str = "false",
        include_extras: str = "false",
        include_multilingual: str = "false",
        include_variations: str = "false",
        **_: object,
    ) -> dict[str, Any] | None:
        """Search for cards, paginated 175 at a time.

        `include_extras`, `include_multilingual` and `include_variations` are accepted and have no
        effect: the corpus holds no tokens, emblems or funny-set cards for `include_extras` to add,
        and it holds every printing and language it has imported unconditionally.

        Args:
            falcon_response: The Falcon response to write to.
            request: The Falcon request, read for the scheme `next_page` should use.
            request_host: Host the request arrived on, used to build `next_page`.
            q: The search query.
            unique: Rollup mode -- cards, art or prints.
            order: Sort key.
            dir: Sort direction -- auto, asc or desc.
            page: 1-based page number.
            format: Response format -- json or csv.
            pretty: Whether to indent JSON output.
            include_extras: Accepted, ignored.
            include_multilingual: Accepted, ignored.
            include_variations: Accepted, ignored.

        Returns:
            A List object of cards, or a Scryfall error object.
        """
        is_pretty = _as_bool(pretty)
        if not q or not q.strip():
            return self._scryfall_respond(falcon_response, bad_request_error(_EMPTY_QUERY_DETAILS), pretty=is_pretty)

        # `or 1` would swallow page=0 into page=1; an unparseable page defaults, a non-positive
        # one is rejected below.
        parsed_page = _as_int(page)
        page_number = 1 if parsed_page is None else parsed_page
        if page_number < 1:
            return self._scryfall_respond(
                falcon_response,
                bad_request_error("The page parameter must be a positive integer."),
                pretty=is_pretty,
            )

        warnings: list[str] = []
        unique_on = _UNIQUE_MAP.get(unique.lower())
        if unique_on is None:
            warnings.append(f"Unrecognized unique mode {unique!r}; rolled up by card instead.")
            unique_on = UniqueOn.CARD

        orderby = _ORDER_MAP.get(order.lower())
        if orderby is None:
            if order.lower() in _SCRYFALL_ONLY_ORDERS:
                warnings.append(f"This server cannot sort by {order!r} yet; sorted by name instead.")
            else:
                warnings.append(f"Unrecognized order {order!r}; sorted by name instead.")
            orderby = CardOrdering.NAME

        # An unrecognized direction falls back to AUTO, which is also the parameter's default --
        # Scryfall ignores one it does not know rather than erroring.
        direction = _DIRECTION_MAP.get(dir.lower(), SortDirection.AUTO)

        try:
            result = self._search(
                query=q,
                orderby=orderby,
                direction=direction,
                fields=["scryfall_id"],
                limit=PAGE_SIZE,
                offset=(page_number - 1) * PAGE_SIZE,
                unique=unique_on,
                prefer=PreferOrder.DEFAULT,
            )
        except falcon.HTTPBadRequest as err:
            return self._scryfall_respond(
                falcon_response,
                bad_request_error(str(err.description or "The query could not be parsed."), warnings=warnings),
                pretty=is_pretty,
            )

        warnings.extend(result.get("warnings") or [])
        total_cards = result["total_cards"]
        cards = self._cards_by_ids([str(row["scryfall_id"]) for row in result["cards"]])
        if not cards:
            return self._scryfall_respond(
                falcon_response,
                error_object(code="not_found", status=404, details=_NO_MATCH_DETAILS, warnings=warnings),
                pretty=is_pretty,
            )

        has_more = (page_number - 1) * PAGE_SIZE + len(cards) < total_cards
        next_page = None
        if has_more:
            next_page = objects.build_page_url(
                _self_base_url(request, request_host, "/cards/search"),
                {
                    "dir": dir,
                    "format": format,
                    "include_extras": str(_as_bool(include_extras)).lower(),
                    "include_multilingual": str(_as_bool(include_multilingual)).lower(),
                    "include_variations": str(_as_bool(include_variations)).lower(),
                    "order": order,
                    "q": q,
                    "unique": unique,
                },
                page_number + 1,
            )

        if format.lower() == "csv":
            self._respond_text(falcon_response, _cards_to_csv(cards), "text/csv; charset=utf-8")
            return None
        return self._scryfall_respond(
            falcon_response,
            card_list(cards, total_cards=total_cards, has_more=has_more, next_page=next_page, warnings=warnings),
            pretty=is_pretty,
        )

    # ---------------------------------------------------------------- GET /cards/named

    @route(paths=("cards/named",))
    def scryfall_cards_named(  # noqa: PLR0913
        self,
        *,
        falcon_response: falcon.Response | None = None,
        exact: str | None = None,
        fuzzy: str | None = None,
        set: str | None = None,  # noqa: A002  -- Scryfall's parameter name
        format: str = "json",  # noqa: A002  -- Scryfall's parameter name
        face: str = "front",
        version: str = DEFAULT_IMAGE_VERSION,
        pretty: str = "false",
        **_: object,
    ) -> dict[str, Any] | None:
        """Return one card by exact or fuzzy name.

        Args:
            falcon_response: The Falcon response to write to.
            exact: A name to match exactly, ignoring case.
            fuzzy: A name to match loosely.
            set: Restrict the search to one set code.
            format: Response format -- json, text or image.
            face: Which face an image request wants.
            version: Which image size an image request wants.
            pretty: Whether to indent JSON output.

        Returns:
            A card object, or a Scryfall error object.
        """
        is_pretty = _as_bool(pretty)
        self._require_setup_complete()
        if not (exact or fuzzy):
            return self._scryfall_respond(
                falcon_response,
                bad_request_error("You must provide a fuzzy or exact name parameter."),
                pretty=is_pretty,
            )

        params: dict[str, Any] = {}
        clauses = []
        if set:
            clauses.append("lower(card_set_code) = lower(%(set_code)s)")
            params["set_code"] = set

        if exact:
            # Scryfall's exact match ignores case and diacritics, and matches a single face of a
            # multi-face card as well as the combined "Front // Back" this corpus stores.
            params["folded"] = fold_accents(exact.strip().lower())
            clauses.append(
                "(lower(card_name_folded) = %(folded)s "
                "OR lower(split_part(card_name_folded, ' // ', 1)) = %(folded)s "
                "OR lower(split_part(card_name_folded, ' // ', 2)) = %(folded)s)",
            )
            card = self._fetch_one_card(" AND ".join(clauses), params)
            if card is None:
                return self._scryfall_respond(
                    falcon_response,
                    not_found_error(f"No cards found matching “{exact}”"),
                    pretty=is_pretty,
                )
            return self._render_card(
                card,
                falcon_response=falcon_response,
                card_format=format.lower(),
                face=face,
                version=version,
                pretty=is_pretty,
            )

        return self._named_fuzzy(
            fuzzy or "",
            base_clauses=clauses,
            base_params=params,
            falcon_response=falcon_response,
            card_format=format.lower(),
            face=face,
            version=version,
            pretty=is_pretty,
        )

    def _named_fuzzy(  # noqa: PLR0913
        self,
        fuzzy: str,
        *,
        base_clauses: list[str],
        base_params: dict[str, Any],
        falcon_response: falcon.Response | None,
        card_format: str,
        face: str,
        version: str,
        pretty: bool,
    ) -> dict[str, Any] | None:
        """Resolve a fuzzy name: exact, then all-words-present, then typo-tolerant similarity.

        The three stages mirror what Scryfall resolves in practice — `lightning bolt` exactly,
        `bolt` by containment, `lighning bolt` by trigram distance — and each stage that finds more
        than one distinct card name reports `ambiguous` rather than guessing between them.

        Args:
            fuzzy: The name fragment to match.
            base_clauses: Predicates already established (the set filter).
            base_params: Their bound parameters.
            falcon_response: The Falcon response to write to.
            card_format: "json", "text" or "image".
            face: Which face an image request wants.
            version: Which image size an image request wants.
            pretty: Whether to indent JSON output.

        Returns:
            A card object, or a Scryfall error object.
        """
        needle = fold_accents(fuzzy.strip().lower())
        words = [word for word in re.split(r"[^\w']+", needle) if word]
        if not words:
            return self._scryfall_respond(
                falcon_response,
                bad_request_error("You must provide a fuzzy or exact name parameter."),
                pretty=pretty,
            )

        chosen = self._fuzzy_exact_candidate(needle, base_clauses, base_params)
        if chosen is None:
            candidates = self._fuzzy_containment_candidates(words, base_clauses, base_params)
            if len(candidates) > 1:
                return self._ambiguous(falcon_response, fuzzy, pretty=pretty)
            if candidates:
                chosen = candidates[0]

        if chosen is None:
            chosen = self._fuzzy_similarity_candidate(needle, base_clauses, base_params)
            if chosen is _AMBIGUOUS:
                return self._ambiguous(falcon_response, fuzzy, pretty=pretty)

        if not chosen:
            return self._scryfall_respond(
                falcon_response,
                not_found_error(f"No cards found matching “{fuzzy}”"),
                pretty=pretty,
            )

        cards = self._cards_by_ids([str(chosen["scryfall_id"])])
        if not cards:
            return self._scryfall_respond(
                falcon_response,
                not_found_error(f"No cards found matching “{fuzzy}”"),
                pretty=pretty,
            )
        return self._render_card(
            cards[0],
            falcon_response=falcon_response,
            card_format=card_format,
            face=face,
            version=version,
            pretty=pretty,
        )

    def _ambiguous(self, falcon_response: falcon.Response | None, name: str, *, pretty: bool) -> dict[str, Any] | None:
        """Emit Scryfall's `ambiguous` error.

        Args:
            falcon_response: The Falcon response to write to.
            name: The name that matched more than one card.
            pretty: Whether to indent JSON output.

        Returns:
            The error object.
        """
        return self._scryfall_respond(
            falcon_response,
            error_object(
                code="ambiguous",
                status=404,
                details=f"Too many cards match ambiguous name “{name}”. Add more words to refine your search.",
            ),
            pretty=pretty,
        )

    def _best_printing(self, where: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Return the id and name of the best-scoring printing matching a predicate.

        Args:
            where: SQL predicate over `magic.cards AS card`.
            params: Bound parameters.

        Returns:
            A row with scryfall_id and card_name, or None.
        """
        rows = self._run_query(
            query=(
                f"SELECT scryfall_id, card_name FROM magic.cards AS card WHERE {where} "
                "ORDER BY prefer_score DESC NULLS LAST, released_at DESC LIMIT 1"
            ),
            params=params,
            explain=False,
        )["result"]
        return rows[0] if rows else None

    def _fuzzy_exact_candidate(
        self,
        needle: str,
        base_clauses: list[str],
        base_params: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return the card whose folded name is exactly the folded query, if there is one.

        Args:
            needle: The accent-folded, lowercased query.
            base_clauses: Predicates already established (the set filter).
            base_params: Their bound parameters.

        Returns:
            The matching printing, or None.
        """
        params = {**base_params, "needle": needle}
        clauses = [*base_clauses, "lower(card_name_folded) = %(needle)s"]
        return self._best_printing(" AND ".join(clauses), params)

    def _fuzzy_containment_candidates(
        self,
        words: list[str],
        base_clauses: list[str],
        base_params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return one printing per distinct card name containing every query word.

        Args:
            words: The folded query, split into words.
            base_clauses: Predicates already established (the set filter).
            base_params: Their bound parameters.

        Returns:
            Up to two rows -- enough to tell "one match" from "ambiguous" without fetching more.
        """
        params = dict(base_params)
        clauses = list(base_clauses)
        for index, word in enumerate(words):
            params[f"word_{index}"] = f"%{word}%"
            clauses.append(f"lower(card_name_folded) LIKE %(word_{index})s")
        return self._run_query(
            query=(
                "SELECT DISTINCT ON (card_name) card_name, scryfall_id "
                f"FROM magic.cards AS card WHERE {' AND '.join(clauses)} "
                "ORDER BY card_name, prefer_score DESC NULLS LAST LIMIT 2"
            ),
            params=params,
            explain=False,
        )["result"]

    def _fuzzy_similarity_candidate(
        self,
        needle: str,
        base_clauses: list[str],
        base_params: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return the typo-tolerant match, `_AMBIGUOUS` when two names are too close to separate.

        A candidate must clear FUZZY_SIMILARITY_FLOOR, and the best must lead the next distinct
        card name by FUZZY_SIMILARITY_LEAD. The floor sits above pg_trgm's default 0.3 threshold,
        so the index-assisted `%` prefilter is always a strict superset of what the floor admits
        and no decision rests on a row the prefilter dropped.

        Args:
            needle: The accent-folded, lowercased query.
            base_clauses: Predicates already established (the set filter).
            base_params: Their bound parameters.

        Returns:
            The matching printing, `_AMBIGUOUS`, or None.
        """
        params = {**base_params, "needle": needle, "floor": FUZZY_SIMILARITY_FLOOR}
        # `%%` escapes psycopg's placeholder marker: the bare `%` operator would be read as the
        # start of one. OPERATOR(magic.%) is pg_trgm's similarity match, which the folded-name GIN
        # index serves.
        clauses = [*base_clauses, "lower(card_name_folded) OPERATOR(magic.%%) %(needle)s"]
        rows = self._run_query(
            query=(
                "SELECT DISTINCT ON (card_name) card_name, scryfall_id, "
                "magic.similarity(lower(card_name_folded), %(needle)s) AS score "
                f"FROM magic.cards AS card WHERE {' AND '.join(clauses)} "
                "AND magic.similarity(lower(card_name_folded), %(needle)s) >= %(floor)s "
                "ORDER BY card_name, prefer_score DESC NULLS LAST"
            ),
            params=params,
            explain=False,
        )["result"]
        if not rows:
            return None
        ranked = sorted(rows, key=lambda row: row["score"], reverse=True)
        if len(ranked) > 1 and ranked[0]["score"] - ranked[1]["score"] < FUZZY_SIMILARITY_LEAD:
            return _AMBIGUOUS
        return ranked[0]

    # ---------------------------------------------------------------- GET /cards/autocomplete

    @route(paths=("cards/autocomplete",))
    def scryfall_cards_autocomplete(
        self,
        *,
        falcon_response: falcon.Response | None = None,
        q: str | None = None,
        pretty: str = "false",
        include_extras: str = "false",  # noqa: ARG002  -- declared so the 404 route listing shows it
        **_: object,
    ) -> dict[str, Any] | None:
        """Return up to 20 card names matching a partial name.

        Args:
            falcon_response: The Falcon response to write to.
            q: The partial name.
            pretty: Whether to indent JSON output.
            include_extras: Accepted, ignored -- the corpus holds no extras to include.

        Returns:
            A Catalog object of card names.
        """
        is_pretty = _as_bool(pretty)
        self._require_setup_complete()
        needle = (q or "").strip()
        min_query_length = 2
        if len(needle) < min_query_length:
            return self._scryfall_respond(falcon_response, catalog_object([]), pretty=is_pretty)

        rows = self._run_query(
            query=(
                "SELECT card_name, min(CASE WHEN lower(card_name) LIKE %(prefix)s THEN 0 ELSE 1 END) AS rank "
                "FROM magic.cards AS card WHERE lower(card_name) LIKE %(needle)s "
                "GROUP BY card_name ORDER BY rank, length(card_name), card_name LIMIT %(limit)s"
            ),
            params={"prefix": f"{needle.lower()}%", "needle": f"%{needle.lower()}%", "limit": MAX_AUTOCOMPLETE_VALUES},
            explain=False,
        )["result"]
        return self._scryfall_respond(falcon_response, catalog_object([row["card_name"] for row in rows]), pretty=is_pretty)

    # ---------------------------------------------------------------- GET /cards/random

    @route(paths=("cards/random",))
    def scryfall_cards_random(  # noqa: PLR0913
        self,
        *,
        falcon_response: falcon.Response | None = None,
        q: str | None = None,
        format: str = "json",  # noqa: A002  -- Scryfall's parameter name
        face: str = "front",
        version: str = DEFAULT_IMAGE_VERSION,
        pretty: str = "false",
        **_: object,
    ) -> dict[str, Any] | None:
        """Return one random card, optionally restricted by a search query.

        Args:
            falcon_response: The Falcon response to write to.
            q: An optional search query the card must match.
            format: Response format -- json, text or image.
            face: Which face an image request wants.
            version: Which image size an image request wants.
            pretty: Whether to indent JSON output.

        Returns:
            A card object, or a Scryfall error object.
        """
        is_pretty = _as_bool(pretty)
        self._require_setup_complete()
        where, params = "TRUE", {}
        if q and q.strip():
            try:
                where, params = generate_sql_query(parse_scryfall_query(q))
            except ValueError:
                return self._scryfall_respond(
                    falcon_response,
                    bad_request_error(f'Failed to parse query: "{q}"'),
                    pretty=is_pretty,
                )

        # This response must not be cached at either layer. The HTTP cache would pin one card as
        # "the" random card for the generation, and _run_query's cache would do the same a level
        # down -- the draw's SQL text and parameters are identical on every call, so its first
        # result would be replayed forever. Hence no-store here and an uncached draw below.
        if falcon_response is not None:
            falcon_response.set_header("Cache-Control", "no-store")

        # Two statements rather than ORDER BY random(): the count is deterministic, so it can go
        # through the cache, and the offset scan stops as soon as it has one row where a sort would
        # order the whole match set to throw all but one away.
        matched = self._run_query(
            query=f"SELECT count(1) AS total FROM magic.cards AS card WHERE {where}",
            params=params,
            explain=False,
        )["result"][0]["total"]
        if not matched:
            return self._scryfall_respond(falcon_response, not_found_error(_NO_MATCH_DETAILS), pretty=is_pretty)

        rows = self._run_uncached(
            query=(
                f"SELECT {_CARD_COLUMNS} FROM magic.cards AS card WHERE {where} "
                "OFFSET floor(random() * %(matched)s)::bigint LIMIT 1"
            ),
            params={**params, "matched": matched},
        )
        if not rows:
            return self._scryfall_respond(falcon_response, not_found_error(_NO_MATCH_DETAILS), pretty=is_pretty)
        return self._render_card(
            to_scryfall_card(rows[0]),
            falcon_response=falcon_response,
            card_format=format.lower(),
            face=face,
            version=version,
            pretty=is_pretty,
        )

    # ---------------------------------------------------------------- POST /cards/collection

    @route(paths=("cards/collection",), methods=("POST",))
    def scryfall_cards_collection(
        self,
        *,
        falcon_response: falcon.Response | None = None,
        request: falcon.Request | None = None,
        pretty: str = "false",
        **_: object,
    ) -> dict[str, Any] | None:
        """Resolve up to 75 card identifiers in one request.

        Args:
            falcon_response: The Falcon response to write to.
            request: The Falcon request, whose JSON body carries the identifiers.
            pretty: Whether to indent JSON output.

        Returns:
            A List object whose `data` holds the cards found and whose `not_found` holds the
            identifiers that resolved to nothing, or a Scryfall error object.
        """
        is_pretty = _as_bool(pretty)
        self._require_setup_complete()
        try:
            body = request.get_media() if request is not None else None
        except (falcon.MediaMalformedError, falcon.MediaNotFoundError):
            body = None
        if not isinstance(body, dict) or not isinstance(body.get("identifiers"), list):
            return self._scryfall_respond(
                falcon_response,
                error_object(
                    code="validation_error",
                    status=422,
                    details="The request body must be a JSON object with an `identifiers` array.",
                ),
                pretty=is_pretty,
            )

        identifiers = body["identifiers"]
        if len(identifiers) > MAX_COLLECTION_IDENTIFIERS:
            return self._scryfall_respond(
                falcon_response,
                error_object(
                    code="validation_error",
                    status=422,
                    details=f"A maximum of {MAX_COLLECTION_IDENTIFIERS} card references may be submitted at once.",
                ),
                pretty=is_pretty,
            )

        found: list[dict[str, Any]] = []
        not_found: list[dict[str, Any]] = []
        seen: set[str] = set()
        for identifier in identifiers:
            card = self._resolve_identifier(identifier) if isinstance(identifier, dict) else None
            if card is None:
                not_found.append(identifier)
                continue
            if card["id"] in seen:
                continue
            seen.add(card["id"])
            found.append(card)

        return self._scryfall_respond(falcon_response, card_list(found, not_found=not_found), pretty=is_pretty)

    def _resolve_identifier(self, identifier: dict[str, Any]) -> dict[str, Any] | None:
        """Resolve one collection identifier to a card.

        Args:
            identifier: One entry of the request's `identifiers` array.

        Returns:
            The card it names, or None when nothing matched or the shape is not one Scryfall
            defines.
        """
        if "id" in identifier and _is_uuid(str(identifier["id"])):
            return self._fetch_one_card("scryfall_id = %(value)s", {"value": str(identifier["id"])})
        if "oracle_id" in identifier and _is_uuid(str(identifier["oracle_id"])):
            return self._fetch_one_card("oracle_id = %(value)s", {"value": str(identifier["oracle_id"])})
        if "illustration_id" in identifier and _is_uuid(str(identifier["illustration_id"])):
            return self._fetch_one_card("illustration_id = %(value)s", {"value": str(identifier["illustration_id"])})
        if "mtgo_id" in identifier:
            return self._card_by_external_id("mtgo", _as_int(str(identifier["mtgo_id"])))
        if "multiverse_id" in identifier:
            return self._card_by_multiverse_id(_as_int(str(identifier["multiverse_id"])))
        if "set" in identifier and "collector_number" in identifier:
            return self._fetch_one_card(
                "lower(card_set_code) = lower(%(set_code)s) AND collector_number = %(number)s",
                {"set_code": str(identifier["set"]), "number": str(identifier["collector_number"])},
            )
        if "name" in identifier:
            clauses = ["lower(card_name) = lower(%(name)s) OR lower(split_part(card_name, ' // ', 1)) = lower(%(name)s)"]
            params: dict[str, Any] = {"name": str(identifier["name"])}
            if identifier.get("set"):
                clauses = [f"({clauses[0]})", "lower(card_set_code) = lower(%(set_code)s)"]
                params["set_code"] = str(identifier["set"])
            return self._fetch_one_card(" AND ".join(clauses), params)
        return None

    # ---------------------------------------------------------------- GET /cards and /cards/...

    @route()
    def cards(  # noqa: PLR0913
        self,
        identifier: str = "",
        number: str = "",
        suffix: str = "",
        *,
        falcon_response: falcon.Response | None = None,
        request: falcon.Request | None = None,
        request_host: str = "",
        page: str = "1",
        format: str = "json",  # noqa: A002  -- Scryfall's parameter name
        face: str = "front",
        version: str = DEFAULT_IMAGE_VERSION,
        pretty: str = "false",
        **_: object,
    ) -> dict[str, Any] | None:
        """Serve every `/cards/*` route the five named sub-routes do not claim.

        The path shapes, by segment count:

        - `/cards` -- every card, paginated.
        - `/cards/:id` -- one card by Scryfall id.
        - `/cards/:namespace/:id` -- one card by multiverse, MTGO, Arena, TCGplayer or Cardmarket id.
        - `/cards/:id/rulings` -- the rulings for one card.
        - `/cards/:code/:number` -- one card by set code and collector number.
        - `/cards/:code/:number/:lang` -- the same, in one language.
        - `/cards/:namespace/:id/rulings` and `/cards/:code/:number/rulings` -- rulings, addressed
          the same two ways.

        Args:
            identifier: First path segment: a Scryfall id, an external id namespace, or a set code.
            number: Second path segment: an external id, a collector number, or "rulings".
            suffix: Third path segment: a language code or "rulings".
            falcon_response: The Falcon response to write to.
            request: The Falcon request, read for the scheme `next_page` should use.
            request_host: Host the request arrived on, used to build `next_page`.
            page: 1-based page number, for the unfiltered `/cards` listing.
            format: Response format -- json, text or image.
            face: Which face an image request wants.
            version: Which image size an image request wants.
            pretty: Whether to indent JSON output.

        Returns:
            A card, List or Catalog object, or a Scryfall error object.
        """
        is_pretty = _as_bool(pretty)
        self._require_setup_complete()

        if not identifier:
            return self._all_cards_page(
                falcon_response=falcon_response,
                request=request,
                request_host=request_host,
                page=page,
                pretty=is_pretty,
            )

        wants_rulings = "rulings" in (number, suffix)
        card = self._resolve_path_card(identifier, number, suffix, wants_rulings=wants_rulings)
        if card is None:
            return self._scryfall_respond(
                falcon_response,
                not_found_error(
                    "The requested object or REST method was not found. Please double-check your URI and try again.",
                ),
                pretty=is_pretty,
            )
        if wants_rulings:
            return self._scryfall_respond(falcon_response, self._rulings_for(card), pretty=is_pretty)
        return self._render_card(
            card, falcon_response=falcon_response, card_format=format.lower(), face=face, version=version, pretty=is_pretty
        )

    def _resolve_path_card(self, identifier: str, number: str, suffix: str, *, wants_rulings: bool) -> dict[str, Any] | None:
        """Resolve the card a `/cards/...` path addresses.

        Args:
            identifier: First path segment.
            number: Second path segment.
            suffix: Third path segment.
            wants_rulings: Whether a trailing "rulings" segment was consumed from the path.

        Returns:
            The card, or None when the path addresses nothing.
        """
        # Drop the trailing "rulings" so the rest reads as a plain card address.
        if wants_rulings:
            if suffix == "rulings":
                suffix = ""
            else:
                number, suffix = "", ""

        if identifier in _EXTERNAL_ID_NAMESPACES:
            external_id = _as_int(number)
            if external_id is None:
                return None
            if identifier == "multiverse":
                return self._card_by_multiverse_id(external_id)
            return self._card_by_external_id(identifier, external_id)

        if not number:
            if not _is_uuid(identifier):
                return None
            return self._fetch_one_card("scryfall_id = %(value)s", {"value": identifier})

        clauses = ["lower(card_set_code) = lower(%(set_code)s)", "collector_number = %(number)s"]
        params: dict[str, Any] = {"set_code": identifier, "number": number}
        # Scryfall defaults the language segment to English rather than to "any language".
        clauses.append("raw_card_blob ->> 'lang' = %(lang)s")
        params["lang"] = suffix or "en"
        return self._fetch_one_card(" AND ".join(clauses), params)

    def _card_by_external_id(self, namespace: str, external_id: int | None) -> dict[str, Any] | None:
        """Fetch a card by one of the external id namespaces.

        Args:
            namespace: One of the keys of `_EXTERNAL_ID_KEYS`.
            external_id: The id to match.

        Returns:
            The card, or None when nothing matched.
        """
        keys = _EXTERNAL_ID_KEYS.get(namespace)
        if external_id is None or not keys:
            return None
        clauses = [f"(raw_card_blob ->> '{key}')::bigint = %(value)s" for key in keys]
        return self._fetch_one_card(f"({' OR '.join(clauses)})", {"value": external_id})

    def _card_by_multiverse_id(self, multiverse_id: int | None) -> dict[str, Any] | None:
        """Fetch a card by Gatherer multiverse id.

        Args:
            multiverse_id: The id to match.

        Returns:
            The card, or None when nothing matched.
        """
        if multiverse_id is None:
            return None
        return self._fetch_one_card(
            "raw_card_blob -> 'multiverse_ids' @> %(value)s::jsonb",
            {"value": str(multiverse_id)},
        )

    def _all_cards_page(
        self,
        *,
        falcon_response: falcon.Response | None,
        request: falcon.Request | None,
        request_host: str,
        page: str,
        pretty: bool,
    ) -> dict[str, Any] | None:
        """Serve one page of the unfiltered `/cards` listing.

        Args:
            falcon_response: The Falcon response to write to.
            request: The Falcon request, read for the scheme `next_page` should use.
            request_host: Host the request arrived on.
            page: 1-based page number.
            pretty: Whether to indent JSON output.

        Returns:
            A List object of cards, or a Scryfall error object.
        """
        # `or 1` would swallow page=0 into page=1; an unparseable page defaults, a non-positive
        # one is rejected below.
        parsed_page = _as_int(page)
        page_number = 1 if parsed_page is None else parsed_page
        if page_number < 1:
            return self._scryfall_respond(
                falcon_response,
                bad_request_error("The page parameter must be a positive integer."),
                pretty=pretty,
            )
        total = self._run_query(query="SELECT count(1) AS total FROM magic.cards", explain=False)["result"][0]["total"]
        rows = self._run_query(
            query=(
                f"SELECT {_CARD_COLUMNS} FROM magic.cards AS card "
                "ORDER BY card_name, card_set_code, collector_number_int, collector_number "
                "LIMIT %(limit)s OFFSET %(offset)s"
            ),
            params={"limit": PAGE_SIZE, "offset": (page_number - 1) * PAGE_SIZE},
            explain=False,
        )["result"]
        if not rows:
            return self._scryfall_respond(falcon_response, not_found_error(_NO_MATCH_DETAILS), pretty=pretty)

        cards = [to_scryfall_card(row) for row in rows]
        has_more = (page_number - 1) * PAGE_SIZE + len(cards) < total
        next_page = None
        if has_more:
            next_page = objects.build_page_url(_self_base_url(request, request_host, "/cards"), {}, page_number + 1)
        return self._scryfall_respond(
            falcon_response,
            card_list(cards, total_cards=total, has_more=has_more, next_page=next_page),
            pretty=pretty,
        )

    def _rulings_for(self, card: dict[str, Any]) -> dict[str, Any]:
        """Build the rulings List object for a card.

        Args:
            card: The card whose oracle id the rulings hang off.

        Returns:
            A List object of Ruling objects, empty when the card has none.
        """
        oracle_id = card.get("oracle_id")
        if not oracle_id:
            return card_list([])
        rows = self._run_query(
            query=(
                "SELECT oracle_id, source, published_at, comment FROM magic.rulings "
                "WHERE oracle_id = %(oracle_id)s ORDER BY published_at, comment"
            ),
            params={"oracle_id": str(oracle_id)},
            explain=False,
        )["result"]
        return card_list([ruling_object(row) for row in rows])
