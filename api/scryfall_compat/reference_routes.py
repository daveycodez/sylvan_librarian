"""The Scryfall-compatible `/sets`, `/catalog/*` and `/symbology` routes.

`ScryfallReferenceRoutes` is a second mixin on `APIResource`, alongside `ScryfallCardsRoutes`. It is
separate because it is a separate kind of thing: these routes answer from the reference tables
mirrored by `api/scryfall_reference_import.py` rather than from the corpus, so none of them touch
the engine, the parser or `_search`.

The same two conventions as the cards routes apply — every parameter is annotated `str` so the
generic binder never puts a non-Scryfall error body on the wire, and the router matches a full path
before falling back to the first segment, which is what lets `/symbology/parse-mana` claim its exact
path while `/sets/tcgplayer/:id` arrives at `scryfall_sets` as positional segments.

One route is computed rather than mirrored: `/symbology/parse-mana`, in `mana.py`.
"""

from __future__ import annotations

import logging
from typing import Any

# A runtime import, not a type-checking one, even though `falcon` appears only in annotations here:
# `@route` registration runs every handler's annotations through `typing.get_type_hints`, which
# evaluates them for real. Behind `if TYPE_CHECKING` the name is absent and registration dies with
# `NameError: name 'falcon' is not defined` before the app can serve anything.
import falcon  # noqa: TC002

from api.scryfall_compat.mana import ManaCostError, parse_mana_cost
from api.scryfall_compat.objects import bad_request_error, card_list, catalog_object, error_object, not_found_error
from api.scryfall_compat.responder import ScryfallResponder
from api.utils.routing import route

logger = logging.getLogger(__name__)

# Cache tiers, matched to what api.scryfall.com sends on each of these routes (measured
# 2026-08-11). They are not the `public, max-age=57600` the card routes carry:
#
#   /sets, /sets/:code, /sets/tcgplayer/:id, /catalog/*, /symbology   ->  public
#   /symbology/parse-mana                    ->  max-age=0, private, must-revalidate
#
# Bare `public` with no max-age leaves freshness to the cache's heuristics, which is weaker than an
# explicit lifetime and is arguably a wart upstream. It is mirrored anyway, because a client that
# swaps its base URL should get the same caching behaviour it tuned against Scryfall — a response
# this service holds for 16 hours where Scryfall revalidates is a behavioural difference the client
# cannot see until it serves something stale.
_MIRRORED_CACHE_CONTROL = "public"

# parse-mana is the deterministic one, so caching it hard would be safe -- but Scryfall marks it
# private and must-revalidate, and parity is the point. `private` does not defeat this service's own
# CachingMiddleware (only `no-store` does, which is why /cards/random uses that), so the in-process
# cache still answers repeat parses.
_PARSE_MANA_CACHE_CONTROL = "max-age=0, private, must-revalidate"

# The twenty catalogs Scryfall documents. Listed rather than discovered so that a request for a name
# this instance has never imported 404s as an unknown catalog, instead of reporting an empty one and
# letting a client conclude Magic has no creature types.
CATALOG_NAMES = (
    "card-names",
    "artist-names",
    "word-bank",
    "supertypes",
    "card-types",
    "artifact-types",
    "battle-types",
    "creature-types",
    "enchantment-types",
    "land-types",
    "planeswalker-types",
    "spell-types",
    "powers",
    "toughnesses",
    "loyalties",
    "watermarks",
    "keyword-abilities",
    "keyword-actions",
    "ability-words",
    "flavor-words",
)

# Scryfall's own not-found wording on these routes, measured against api.scryfall.com on
# 2026-08-12. Neither is the generic body the cards surface sends, and the catalog one is that
# sentence WITHOUT its "Please double-check your URI and try again." tail -- same sentence,
# different ending, so they are spelled out separately rather than shared.
#
#   /sets/<code>, /sets/<id>, /sets/tcgplayer/<id>, /sets/tcgplayer
#       "No Magic set found for the given code or ID"
#   /catalog/<unknown>
#       "The requested object or REST method was not found."
_SET_MISS_DETAILS = "No Magic set found for the given code or ID"
_CATALOG_MISS_DETAILS = "The requested object or REST method was not found."

# The host a Catalog's own `uri` points at: Scryfall's, not this service's, which is the rule the
# card objects already follow for `uri`, `rulings_uri` and `prints_search_uri`.
_SCRYFALL_API = "https://api.scryfall.com"

# Path segment naming the external-id namespace under /sets, mirroring the /cards namespaces.
_SETS_TCGPLAYER_NAMESPACE = "tcgplayer"


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


def _set_reference_cache(falcon_response: falcon.Response | None, tier: str = _MIRRORED_CACHE_CONTROL) -> None:
    """Set a reference-route cache tier.

    Args:
        falcon_response: The response to write to, or None for an internal caller.
        tier: The Cache-Control value; defaults to the tier the mirrored routes share.
    """
    if falcon_response is not None:
        falcon_response.set_header("Cache-Control", tier)


class ScryfallReferenceRoutes(ScryfallResponder):
    """The `/sets`, `/catalog` and `/symbology` routes, mixed into `APIResource`.

    Depends on `_run_query` and `_require_setup_complete` from the class it is mixed into, the same
    way `ScryfallCardsRoutes` does.
    """

    # ---------------------------------------------------------------- GET /sets

    @route(paths=("sets",))
    def scryfall_sets(
        self,
        identifier: str = "",
        second: str = "",
        *,
        falcon_response: falcon.Response | None = None,
        pretty: str = "false",
        **_: object,
    ) -> dict[str, Any] | None:
        """Answer every `/sets` shape.

        Covers `/sets`, `/sets/:code`, `/sets/:id` and `/sets/tcgplayer/:id` — one handler because
        the router hands trailing segments to whichever route claims the first one.

        Args:
            identifier: A set code, a Scryfall set id, or the "tcgplayer" namespace.
            second: The TCGplayer id, when `identifier` named that namespace.
            falcon_response: The Falcon response to write to.
            pretty: Whether to indent JSON output.

        Returns:
            A List object of sets, one Set object, or a Scryfall error.
        """
        is_pretty = _as_bool(pretty)
        _set_reference_cache(falcon_response)
        self._require_setup_complete()

        if not identifier:
            return self._scryfall_respond(falcon_response, card_list(self._all_sets()), pretty=is_pretty)

        if identifier.lower() == _SETS_TCGPLAYER_NAMESPACE:
            if not second:
                # Scryfall answers the namespace-with-no-id path with its ordinary set miss, not
                # with a message about the id being absent. Clearer is not the goal here.
                return self._scryfall_respond(falcon_response, not_found_error(_SET_MISS_DETAILS), pretty=is_pretty)
            found = self._set_by_tcgplayer_id(second)
        elif second:
            # /sets takes at most one identifying segment; anything longer addresses nothing.
            found = None
        else:
            found = self._set_by_code_or_id(identifier)

        if found is None:
            return self._scryfall_respond(falcon_response, not_found_error(_SET_MISS_DETAILS), pretty=is_pretty)
        return self._scryfall_respond(falcon_response, found, pretty=is_pretty)

    def _all_sets(self) -> list[dict[str, Any]]:
        """Every set, in the order Scryfall returns them.

        Returns:
            The Set objects.
        """
        rows = self._run_query(
            query="SELECT set_object FROM magic.sets ORDER BY position",
            params={},
            explain=False,
        )["result"]
        return [row["set_object"] for row in rows]

    def _set_by_code_or_id(self, identifier: str) -> dict[str, Any] | None:
        """One set by set code or by Scryfall set id.

        A single query over both keys rather than a UUID test first: a set code is never shaped like
        a UUID, so the two can never both match, and one round trip answers either spelling.

        Args:
            identifier: The set code or set id.

        Returns:
            The Set object, or None when nothing matches.
        """
        rows = self._run_query(
            query=("SELECT set_object FROM magic.sets WHERE lower(code) = %(folded)s OR id::text = %(raw)s LIMIT 1"),
            params={"folded": identifier.lower(), "raw": identifier.lower()},
            explain=False,
        )["result"]
        return rows[0]["set_object"] if rows else None

    def _set_by_tcgplayer_id(self, raw_id: str) -> dict[str, Any] | None:
        """One set by its TCGplayer group id.

        Args:
            raw_id: The id as it appeared in the path.

        Returns:
            The Set object, or None when the id is unparseable or matches nothing.
        """
        try:
            tcgplayer_id = int(raw_id.strip())
        except (ValueError, AttributeError):
            return None
        rows = self._run_query(
            query="SELECT set_object FROM magic.sets WHERE tcgplayer_id = %(value)s LIMIT 1",
            params={"value": tcgplayer_id},
            explain=False,
        )["result"]
        return rows[0]["set_object"] if rows else None

    # ---------------------------------------------------------------- GET /catalog/:name

    @route(paths=("catalog",))
    def scryfall_catalog(
        self,
        name: str = "",
        *,
        falcon_response: falcon.Response | None = None,
        pretty: str = "false",
        **_: object,
    ) -> dict[str, Any] | None:
        """Return one catalog.

        Args:
            name: The catalog name, e.g. "creature-types".
            falcon_response: The Falcon response to write to.
            pretty: Whether to indent JSON output.

        Returns:
            A Catalog object, or a Scryfall error.
        """
        is_pretty = _as_bool(pretty)
        _set_reference_cache(falcon_response)
        self._require_setup_complete()

        wanted = name.strip().lower()
        if wanted not in CATALOG_NAMES:
            return self._scryfall_respond(falcon_response, not_found_error(_CATALOG_MISS_DETAILS), pretty=is_pretty)

        rows = self._run_query(
            query="SELECT entries FROM magic.catalogs WHERE name = %(name)s",
            params={"name": wanted},
            explain=False,
        )["result"]
        # A known catalog that has not been imported yet is empty rather than missing: the name is
        # real, so 404 would tell a client the endpoint does not exist.
        entries = rows[0]["entries"] if rows else []
        return self._scryfall_respond(
            falcon_response,
            catalog_object(list(entries), uri=f"{_SCRYFALL_API}/catalog/{wanted}"),
            pretty=is_pretty,
        )

    # ---------------------------------------------------------------- GET /symbology

    @route(paths=("symbology",))
    def scryfall_symbology(
        self,
        *,
        falcon_response: falcon.Response | None = None,
        pretty: str = "false",
        **_: object,
    ) -> dict[str, Any] | None:
        """Return every card symbol.

        Args:
            falcon_response: The Falcon response to write to.
            pretty: Whether to indent JSON output.

        Returns:
            A List object of CardSymbol objects.
        """
        is_pretty = _as_bool(pretty)
        _set_reference_cache(falcon_response)
        self._require_setup_complete()

        rows = self._run_query(
            query="SELECT symbol_object FROM magic.card_symbols ORDER BY position",
            params={},
            explain=False,
        )["result"]
        return self._scryfall_respond(
            falcon_response,
            card_list([row["symbol_object"] for row in rows]),
            pretty=is_pretty,
        )

    # ---------------------------------------------------------------- GET /symbology/parse-mana

    @route(paths=("symbology/parse-mana",))
    def scryfall_parse_mana(
        self,
        *,
        falcon_response: falcon.Response | None = None,
        cost: str | None = None,
        pretty: str = "false",
        **_: object,
    ) -> dict[str, Any] | None:
        """Parse a mana cost into Scryfall's ManaCost object.

        The one reference route that reads no table: it is a pure function of `cost`, so it answers
        the same before the first import as after it.

        Args:
            falcon_response: The Falcon response to write to.
            cost: The mana cost as written.
            pretty: Whether to indent JSON output.

        Returns:
            A ManaCost object, or a Scryfall error.
        """
        is_pretty = _as_bool(pretty)
        _set_reference_cache(falcon_response, _PARSE_MANA_CACHE_CONTROL)

        if cost is None:
            return self._scryfall_respond(
                falcon_response,
                bad_request_error("You must provide a cost parameter to parse."),
                pretty=is_pretty,
            )
        try:
            parsed = parse_mana_cost(cost)
        except ManaCostError as bad_cost:
            # 422 rather than 400, which is what Scryfall answers an unparseable fragment with.
            return self._scryfall_respond(
                falcon_response,
                error_object(code="validation_error", status=422, details=str(bad_cost)),
                pretty=is_pretty,
            )
        return self._scryfall_respond(falcon_response, parsed, pretty=is_pretty)
