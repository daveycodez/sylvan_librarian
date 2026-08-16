"""Scryfall's colour-COUNT values: `m`, `gold`, and the four `multicolor` spellings.

`c:m` is not "the colour m" -- there is no such colour. It is Scryfall's word for MULTICOLOURED,
and it compares the NUMBER of colours in the column, which is the comparison this branch already
builds for `c>=2`. Every value and every operator was measured against api.scryfall.com on
2026-08-16; the counts, and the two operator readings that are NOT "substitute the number 2", are
written out at db_info.COLOR_COUNT_NAMES.
"""

import pytest

from api.parsing import generate_sql_query, parse_scryfall_query
from api.parsing.pyparsing_based import parse_search_query

# The colour-COUNT names, as the numeric comparison each one means. `m` is not a colour and spells
# no letters: it is Scryfall's word for MULTICOLOURED and compares the NUMBER of colours in the
# column, so the operator does not survive verbatim either. Every pair below was measured
# corpus-wide against api.scryfall.com on 2026-08-16 -- see db_info.COLOR_COUNT_NAMES for the
# counts, including the two readings that are NOT "substitute the number 2": `c>m` is `c>=2` rather
# than `c>2` (4,607 against 796), and `c!=m` is `c<2` rather than `c!=2` (29,049 against 29,836).
COLOR_COUNT_CASES = [
    # every spelling of the name, on the same operator
    ("c:m", "c>=2"),
    ("c:gold", "c>=2"),
    ("c:multicolor", "c>=2"),
    ("c:multicolour", "c>=2"),
    ("c:multicolored", "c>=2"),
    ("c:multicoloured", "c>=2"),
    # every operator, on the same spelling
    ("c=m", "c>=2"),
    ("c>m", "c>=2"),
    ("c>=m", "c>=2"),
    ("c<m", "c<2"),
    ("c!=m", "c<2"),
    ("c<=m", "c>=0"),  # a tautology: `c<=m t:creature` = `t:creature` = 18,753
    # the colour aliases and the identity column take the same table
    ("color:m", "c>=2"),
    ("colors:gold", "c>=2"),
    ("id:m", "id>=2"),
    ("identity:gold", "id>=2"),
    ("ci>m", "ci>=2"),
    ("id<m", "id<2"),
    ("id!=multicoloured", "id<2"),
    ("id<=m", "id>=0"),
    # case, quoting and negation all reach the same lowering
    ("c:M", "c>=2"),
    ("c:GOLD", "c>=2"),
    ('c:"m"', "c>=2"),
    ("-c:m", "-c>=2"),
]


@pytest.mark.parametrize(
    argnames=("query", "canonical_query"),
    argvalues=COLOR_COUNT_CASES,
    ids=[q for q, _ in COLOR_COUNT_CASES],
)
def test_color_count_name_matches_number(query: str, canonical_query: str) -> None:
    """A colour-COUNT name produces exactly the SQL its numeric comparison does, in both parsers."""
    assert generate_sql_query(parse_scryfall_query(query)) == generate_sql_query(parse_scryfall_query(canonical_query))
    assert generate_sql_query(parse_search_query(query)) == generate_sql_query(parse_search_query(canonical_query))


# produced_mana is left out of the lowering on purpose: `produces:m` IS a count on Scryfall, but a
# count over SIX values, colorless among them (`produces=1 produces:c` = 481 -- the cards that
# produce colorless and nothing else), where every count on this side reads the five WUBRG keys.
# Answering it with the five-key count would be short by those 481 cards, so it stays an error.
@pytest.mark.parametrize(
    argnames="invalid_query",
    argvalues=["produces:m", "produces:gold", "produces<multicolored", "produces>=2"],
)
def test_produced_mana_counts_are_still_refused(invalid_query: str) -> None:
    """A count on produced_mana is refused, by name and by bare number alike."""
    with pytest.raises(ValueError, match=r"[Nn]umeric comparison is not supported|Invalid color string"):
        generate_sql_query(parse_scryfall_query(invalid_query))


@pytest.mark.parametrize(
    argnames="invalid_query",
    argvalues=["c:mw", "c:wm", "c:mc", "c:mm", "c!=mw", "id:mw", "c:mono", "produces:mw"],
)
def test_m_beside_another_color_is_still_invalid(invalid_query: str) -> None:
    """`m` beside another colour letter is neither a name nor a letter set, and stays a parse error.

    Scryfall dropped the combination outright -- it answers "Using “m” with other colors is no
    longer supported" and IGNORES the term -- so quietly reading `c:mw` as a count would answer a
    different question from the one asked.
    """
    with pytest.raises(ValueError, match="Failed to parse query"):
        parse_scryfall_query(invalid_query)
