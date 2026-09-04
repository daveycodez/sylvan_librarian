"""Tests for date and year search functionality."""

import itertools

import pytest

from api.parsing import AttributeNode, BinaryOperatorNode, QueryContext, StringValueNode, set_dates
from api.parsing.rewrite import InvalidDateValueError


@pytest.mark.parametrize(
    argnames=("searchattr", "searchoperator", "searchvalue"),
    argvalues=list(
        itertools.product(
            ["date"],
            [":", "=", ">", "<", ">=", "<="],
            ["2025-02-02", "2025"],
        ),
    )
    + list(
        itertools.product(
            ["year"],
            [":", "=", ">", "<", ">=", "<="],
            ["2025"],  # Year only accepts 4-digit years
        ),
    ),
)
def test_date_year_search_parsing(parse_query, searchattr: str, searchoperator: str, searchvalue: str) -> None:
    """Test that date and year searches parse correctly with all operators."""
    query_str = f"{searchattr}{searchoperator}{searchvalue}"
    parsed = parse_query(query_str)

    # Should parse to a BinaryOperatorNode
    assert isinstance(parsed.root, BinaryOperatorNode)
    assert isinstance(parsed.root.lhs, AttributeNode)
    assert parsed.root.operator == searchoperator

    # RHS should be a StringValueNode with the search value
    assert isinstance(parsed.root.rhs, StringValueNode)
    assert parsed.root.rhs.value == searchvalue


@pytest.mark.parametrize(
    argnames=("query", "expected_sql_fragment"),
    argvalues=[
        # Date searches should use the full date
        ("date:2025-02-02", "card.released_at = "),
        ("date=2025-02-02", "card.released_at = "),
        ("date>2025-02-02", "card.released_at > "),
        ("date<2025-02-02", "card.released_at < "),
        ("date>=2025-02-02", "card.released_at >= "),
        ("date<=2025-02-02", "card.released_at <= "),
        # Year searches should use date ranges for index usage
        ("year:2025", "<= card.released_at AND card.released_at <"),
        ("year=2025", "<= card.released_at AND card.released_at <"),
        ("year>2025", "card.released_at >="),
        ("year<2025", "card.released_at <"),
        ("year>=2025", "card.released_at >="),
        ("year<=2025", "card.released_at <"),
    ],
)
def test_date_year_sql_generation(parse_query, query: str, expected_sql_fragment: str) -> None:
    """Test that date and year searches generate correct SQL."""
    parsed = parse_query(query)
    context = QueryContext()
    sql = parsed.to_sql(context)

    # Check that the SQL contains the expected fragment
    assert expected_sql_fragment in sql
    # Check that parameters were added to context (1 for date, 1 or 2 for year)
    assert len(context) >= 1


def test_date_search_full_date(parse_query) -> None:
    """Test date search with full date format."""
    parsed = parse_query("date:2025-02-02")
    context = QueryContext()
    sql = parsed.to_sql(context)

    assert "card.released_at = " in sql
    # Should have a parameter with the date string
    assert "2025-02-02" in context.values()


def test_year_search_numeric(parse_query) -> None:
    """Test year search with numeric year."""
    parsed = parse_query("year:2025")
    context = QueryContext()
    sql = parsed.to_sql(context)

    # Year search should convert to date range: 2025-01-01 <= released_at < 2026-01-01
    assert "card.released_at" in sql
    assert "<= card.released_at AND card.released_at <" in sql
    # Should have parameters with date strings
    assert "2025-01-01" in context.values()
    assert "2026-01-01" in context.values()


def test_year_search_rejects_date_format(parse_query) -> None:
    """Test year search rejects date format (YYYY-MM-DD)."""
    # Year search should only accept 4-digit years
    # Parsing with date format should fail
    with pytest.raises(ValueError, match="Failed to parse query"):
        parse_query("year:2025-02-02")


def test_date_year_combined_query(parse_query) -> None:
    """Test combining date/year searches with other conditions."""
    parsed = parse_query("year:2025 AND cmc=3")
    context = QueryContext()
    sql = parsed.to_sql(context)

    assert "card.released_at" in sql
    assert "card.cmc = " in sql
    assert "2025-01-01" in context.values()
    assert 3 in context.values()


# ── `date:` takes a set code ──────────────────────────────────────────────────
#
# Scryfall resolves a set code in a `date:` value to that set's release date and compares against
# it as a full day: `date>=hob` = `date>=2026-08-14` (1200 cards on api.scryfall.com, 2026-09-03).
# The registry the rewrite consults is process-global, so every test here pins it explicitly.

_SET_RELEASE_DATES = {
    "hob": "2026-08-14",
    "3ed": "1994-04-11",
    "40k": "2022-10-07",
    "2x2": "2022-07-08",
    "10e": "2007-07-13",
}


@pytest.fixture
def known_sets(monkeypatch) -> None:
    monkeypatch.setattr(set_dates, "_SET_RELEASE_DATES", dict(_SET_RELEASE_DATES))


@pytest.fixture
def no_sets(monkeypatch) -> None:
    monkeypatch.setattr(set_dates, "_SET_RELEASE_DATES", {})


def _sql_and_values(parsed) -> tuple[str, list]:
    context = QueryContext()
    return parsed.to_sql(context), list(context.values())


@pytest.mark.parametrize("operator", [":", "=", ">", "<", ">=", "<="])
def test_set_code_resolves_to_full_release_date(parse_query, known_sets, operator: str) -> None:
    """Every operator compares against the set's release day, not a window (`date:hob` is `= 2026-08-14`)."""
    parsed = parse_query(f"date{operator}hob")
    assert isinstance(parsed.root, BinaryOperatorNode)
    assert parsed.root.operator == operator
    assert isinstance(parsed.root.rhs, StringValueNode)
    assert parsed.root.rhs.value == "2026-08-14"
    sql, values = _sql_and_values(parsed)
    assert f"card.released_at {'=' if operator == ':' else operator} " in sql
    assert values == ["2026-08-14"]


@pytest.mark.parametrize(
    ("query", "expected_date"),
    [
        ("date>=hob", "2026-08-14"),
        ("date>=HOB", "2026-08-14"),
        ('date>="hob"', "2026-08-14"),
        ("date>='HOB'", "2026-08-14"),
        # Leading-digit codes: the lexer must keep them a single word, not NUMBER + WORD.
        ("date>=3ed", "1994-04-11"),
        ("date>=10e", "2007-07-13"),
        ("date>=40k", "2022-10-07"),
        ("date>=2x2", "2022-07-08"),
    ],
)
def test_set_code_spellings_resolve(parse_query, known_sets, query: str, expected_date: str) -> None:
    parsed = parse_query(query)
    assert parsed.root.rhs.value == expected_date
    _, values = _sql_and_values(parsed)
    assert values == [expected_date]


def test_resolved_leaf_serializes_iso_date_for_engine(parse_query, known_sets) -> None:
    """The engine reads the AST's JSON; it must see a date it can parse, never the code."""
    parsed = parse_query("date>=hob")
    rhs = parsed.root.to_json()["kwargs"]["rhs"]
    assert rhs == {"node_type": "StringValueNode", "kwargs": {"value": "2026-08-14"}}


def test_set_code_resolves_inside_compounds_and_negation(parse_query, known_sets) -> None:
    parsed = parse_query("-date>=hob or (t:goblin date<3ed)")
    _, values = _sql_and_values(parsed)
    assert "2026-08-14" in values
    assert "1994-04-11" in values
    assert "hob" not in values
    assert "3ed" not in values


@pytest.mark.parametrize("query", ["date:2025", "date:2025-02-02", "date:2021-02"])
def test_date_shapes_are_left_alone(parse_query, known_sets, query: str) -> None:
    """A value that already is a date shape never goes through the registry."""
    parsed = parse_query(query)
    assert parsed.root.rhs.value == query.removeprefix("date:")


@pytest.mark.parametrize(
    ("query", "named_value"),
    [
        ("date>=zzzz", "zzzz"),
        ("date>=ZZZZ", "zzzz"),  # Scryfall lower-cases the value in its sentence
        ('date:"no such set"', "no such set"),
        # Zero-padding strict, as on Scryfall: `date:2021-02` is a month, `date:2021-2` is this error.
        ("date:2021-2", "2021-2"),
        ("date:2025-2-2", "2025-2-2"),
    ],
)
def test_unknown_code_or_malformed_date_is_named(parse_query, known_sets, query: str, named_value: str) -> None:
    with pytest.raises(ValueError, match=f"Invalid date or unknown set code “{named_value}”"):
        parse_query(query)


def test_unknown_error_is_a_value_error_subclass_with_user_message(parse_query, known_sets) -> None:
    with pytest.raises(InvalidDateValueError) as exc_info:
        parse_query("date>=zzzz")
    assert exc_info.value.user_message == "Invalid date or unknown set code “zzzz”"


def test_empty_registry_reports_every_code_unknown(parse_query, no_sets) -> None:
    """A worker that has not loaded the table yet says so rather than guessing."""
    with pytest.raises(ValueError, match="unknown set code “hob”"):
        parse_query("date>=hob")


def test_year_takes_no_set_code(parse_query, known_sets) -> None:
    """`year>=hob` is a 404 on Scryfall; here it stays the plain parse failure it always was."""
    with pytest.raises(ValueError, match="Failed to parse query"):
        parse_query("year>=hob")
