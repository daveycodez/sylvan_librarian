"""Result-shape directives (sort:/order:/direction:/prefer:) and the `ci` identity alias.

Scryfall accepts presentation directives inside the query string itself — a query
like `t:goblin sort:edhrec` is valid there and filters exactly as `t:goblin`.
Rejecting them breaks any client that forwards Scryfall-shaped query strings
verbatim. Both parsers must consume a directive and contribute nothing to the
filter tree (the always-true node), keeping SQL identical to the directive-free
query.

`ci` mirrors Scryfall's alias for color identity (`ci<=bg` == `id<=bg`).
"""

import pytest

from api.parsing import generate_sql_query, parse_scryfall_query
from api.parsing.pyparsing_based import parse_search_query

# (query with directive, equivalent query without it)
DIRECTIVE_CASES = [
    ("t:goblin sort:edhrec", "t:goblin"),
    ("t:goblin order:name", "t:goblin"),
    ("t:goblin direction:asc", "t:goblin"),
    ("t:goblin prefer:oldest", "t:goblin"),
    ("sort:edhrec t:goblin", "t:goblin"),
    ('t:goblin sort:"edhrec"', "t:goblin"),
    ("t:planeswalker f:commander sort:edhrec", "t:planeswalker f:commander"),
    ("(t:goblin or t:elf) sort:edhrec", "t:goblin or t:elf"),
]


@pytest.mark.parametrize(
    argnames=["query", "equivalent"],
    argvalues=DIRECTIVE_CASES,
    ids=[q for q, _ in DIRECTIVE_CASES],
)
def test_directive_filters_like_equivalent(query: str, equivalent: str) -> None:
    """A directive-bearing query produces the same SQL as the query without it (hand parser)."""
    assert generate_sql_query(parse_scryfall_query(query)) == generate_sql_query(parse_scryfall_query(equivalent))


@pytest.mark.parametrize(
    argnames=["query", "equivalent"],
    argvalues=DIRECTIVE_CASES,
    ids=[q for q, _ in DIRECTIVE_CASES],
)
def test_directive_parser_parity(query: str, equivalent: str) -> None:
    """Both parsers agree on every directive-bearing query."""
    del equivalent
    assert generate_sql_query(parse_scryfall_query(query)) == generate_sql_query(parse_search_query(query))


def test_directive_needs_a_value() -> None:
    """A dangling directive prefix is still an error, not a silent no-op."""
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_scryfall_query("t:goblin sort:")


def test_directive_prefix_of_longer_word_is_a_name() -> None:
    """Words that merely START with a directive keep their name reading."""
    # "sorting" must not be consumed as "sort" + garbage.
    assert generate_sql_query(parse_scryfall_query("sorting")) == generate_sql_query(parse_search_query("sorting"))


CI_CASES = [
    ("ci<=bg", "id<=bg"),
    ("ci:wu", "id:wu"),
    ("ci>=rg", "identity>=rg"),
    ("t:land ci<=bg", "t:land id<=bg"),
]


@pytest.mark.parametrize(
    argnames=["ci_query", "id_query"],
    argvalues=CI_CASES,
    ids=[q for q, _ in CI_CASES],
)
def test_ci_is_an_identity_alias(ci_query: str, id_query: str) -> None:
    """`ci` produces identical SQL to the established identity aliases, in both parsers."""
    assert generate_sql_query(parse_scryfall_query(ci_query)) == generate_sql_query(parse_scryfall_query(id_query))
    assert generate_sql_query(parse_search_query(ci_query)) == generate_sql_query(parse_search_query(id_query))
