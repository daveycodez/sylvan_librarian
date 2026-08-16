"""Tests for `GET /symbology/parse-mana`'s cost parser.

Every expectation below is a **golden captured from api.scryfall.com on 2026-08-11**, not a value
worked out by hand, because two of the behaviours being pinned are undocumented: the canonical
colour reordering (`RUW` answers `{U}{R}{W}`) and the emission order (`2XWU` answers `{X}{2}{W}{U}`).
A hand-written expectation for those would only re-assert whatever the implementation happened to do.

The colour cases are exhaustive: all 31 non-empty subsets of WUBRG, each written both forwards and
backwards, so `_canonical_colors` is pinned over its whole domain rather than at a few samples.
"""

from __future__ import annotations

from typing import Any

import pytest

from api.scryfall_compat.mana import ManaCostError, parse_mana_cost

GOLDENS = [
    ("", {"cost": None, "colors": [], "cmc": 0.0, "colorless": True, "monocolored": False, "multicolored": False}),
    ("0", {"cost": "{0}", "colors": [], "cmc": 0.0, "colorless": True, "monocolored": False, "multicolored": False}),
    ("2WW", {"cost": "{2}{W}{W}", "colors": ["W"], "cmc": 4.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("XRR", {"cost": "{X}{R}{R}", "colors": ["R"], "cmc": 2.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("W2", {"cost": "{2}{W}", "colors": ["W"], "cmc": 3.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("RX", {"cost": "{X}{R}", "colors": ["R"], "cmc": 1.0, "colorless": False, "monocolored": True, "multicolored": False}),
    (
        "GWU2",
        {
            "cost": "{2}{G}{W}{U}",
            "colors": ["W", "U", "G"],
            "cmc": 5.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "2XWU",
        {"cost": "{X}{2}{W}{U}", "colors": ["W", "U"], "cmc": 4.0, "colorless": False, "monocolored": False, "multicolored": True},
    ),
    (
        "WUBRGC",
        {
            "cost": "{W}{U}{B}{R}{G}{C}",
            "colors": ["W", "U", "B", "R", "G"],
            "cmc": 6.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    ("CW", {"cost": "{W}{C}", "colors": ["W"], "cmc": 2.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("{2/W}", {"cost": "{2/W}", "colors": ["W"], "cmc": 2.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("{W/P}", {"cost": "{W/P}", "colors": ["W"], "cmc": 1.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("{HW}", {"cost": "{HW}", "colors": ["W"], "cmc": 0.5, "colorless": False, "monocolored": True, "multicolored": False}),
    ("{C}", {"cost": "{C}", "colors": [], "cmc": 1.0, "colorless": True, "monocolored": False, "multicolored": False}),
    ("{S}", {"cost": "{S}", "colors": [], "cmc": 1.0, "colorless": True, "monocolored": False, "multicolored": False}),
    ("11R", {"cost": "{11}{R}", "colors": ["R"], "cmc": 12.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("{10}", {"cost": "{10}", "colors": [], "cmc": 10.0, "colorless": True, "monocolored": False, "multicolored": False}),
    (
        "{W/U}{B/R}",
        {
            "cost": "{W/U}{B/R}",
            "colors": ["W", "U", "B", "R"],
            "cmc": 2.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "{1}{W/U}{W/U}",
        {"cost": "{1}{W/U}{W/U}", "colors": ["W", "U"], "cmc": 3.0, "colorless": False, "monocolored": False, "multicolored": True},
    ),
    (
        "{X}{X}{R}",
        {"cost": "{X}{X}{R}", "colors": ["R"], "cmc": 1.0, "colorless": False, "monocolored": True, "multicolored": False},
    ),
    (
        "{B/G}{B/G}",
        {"cost": "{B/G}{B/G}", "colors": ["B", "G"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True},
    ),
    ("{HR}{R}", {"cost": "{HR}{R}", "colors": ["R"], "cmc": 1.5, "colorless": False, "monocolored": True, "multicolored": False}),
    ("W", {"cost": "{W}", "colors": ["W"], "cmc": 1.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("U", {"cost": "{U}", "colors": ["U"], "cmc": 1.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("B", {"cost": "{B}", "colors": ["B"], "cmc": 1.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("R", {"cost": "{R}", "colors": ["R"], "cmc": 1.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("G", {"cost": "{G}", "colors": ["G"], "cmc": 1.0, "colorless": False, "monocolored": True, "multicolored": False}),
    ("WU", {"cost": "{W}{U}", "colors": ["W", "U"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("UW", {"cost": "{W}{U}", "colors": ["W", "U"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("WB", {"cost": "{W}{B}", "colors": ["W", "B"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("BW", {"cost": "{W}{B}", "colors": ["W", "B"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("WR", {"cost": "{R}{W}", "colors": ["W", "R"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("RW", {"cost": "{R}{W}", "colors": ["W", "R"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("WG", {"cost": "{G}{W}", "colors": ["W", "G"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("GW", {"cost": "{G}{W}", "colors": ["W", "G"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("UB", {"cost": "{U}{B}", "colors": ["U", "B"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("BU", {"cost": "{U}{B}", "colors": ["U", "B"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("UR", {"cost": "{U}{R}", "colors": ["U", "R"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("RU", {"cost": "{U}{R}", "colors": ["U", "R"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("UG", {"cost": "{G}{U}", "colors": ["U", "G"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("GU", {"cost": "{G}{U}", "colors": ["U", "G"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("BR", {"cost": "{B}{R}", "colors": ["B", "R"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("RB", {"cost": "{B}{R}", "colors": ["B", "R"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("BG", {"cost": "{B}{G}", "colors": ["B", "G"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("GB", {"cost": "{B}{G}", "colors": ["B", "G"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("RG", {"cost": "{R}{G}", "colors": ["R", "G"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    ("GR", {"cost": "{R}{G}", "colors": ["R", "G"], "cmc": 2.0, "colorless": False, "monocolored": False, "multicolored": True}),
    (
        "WUB",
        {
            "cost": "{W}{U}{B}",
            "colors": ["W", "U", "B"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "BUW",
        {
            "cost": "{W}{U}{B}",
            "colors": ["W", "U", "B"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "WUR",
        {
            "cost": "{U}{R}{W}",
            "colors": ["W", "U", "R"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "RUW",
        {
            "cost": "{U}{R}{W}",
            "colors": ["W", "U", "R"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "WUG",
        {
            "cost": "{G}{W}{U}",
            "colors": ["W", "U", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "GUW",
        {
            "cost": "{G}{W}{U}",
            "colors": ["W", "U", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "WBR",
        {
            "cost": "{R}{W}{B}",
            "colors": ["W", "B", "R"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "RBW",
        {
            "cost": "{R}{W}{B}",
            "colors": ["W", "B", "R"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "WBG",
        {
            "cost": "{W}{B}{G}",
            "colors": ["W", "B", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "GBW",
        {
            "cost": "{W}{B}{G}",
            "colors": ["W", "B", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "WRG",
        {
            "cost": "{R}{G}{W}",
            "colors": ["W", "R", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "GRW",
        {
            "cost": "{R}{G}{W}",
            "colors": ["W", "R", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "UBR",
        {
            "cost": "{U}{B}{R}",
            "colors": ["U", "B", "R"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "RBU",
        {
            "cost": "{U}{B}{R}",
            "colors": ["U", "B", "R"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "UBG",
        {
            "cost": "{B}{G}{U}",
            "colors": ["U", "B", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "GBU",
        {
            "cost": "{B}{G}{U}",
            "colors": ["U", "B", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "URG",
        {
            "cost": "{G}{U}{R}",
            "colors": ["U", "R", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "GRU",
        {
            "cost": "{G}{U}{R}",
            "colors": ["U", "R", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "BRG",
        {
            "cost": "{B}{R}{G}",
            "colors": ["B", "R", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "GRB",
        {
            "cost": "{B}{R}{G}",
            "colors": ["B", "R", "G"],
            "cmc": 3.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "WUBR",
        {
            "cost": "{W}{U}{B}{R}",
            "colors": ["W", "U", "B", "R"],
            "cmc": 4.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "RBUW",
        {
            "cost": "{W}{U}{B}{R}",
            "colors": ["W", "U", "B", "R"],
            "cmc": 4.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "WUBG",
        {
            "cost": "{G}{W}{U}{B}",
            "colors": ["W", "U", "B", "G"],
            "cmc": 4.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "GBUW",
        {
            "cost": "{G}{W}{U}{B}",
            "colors": ["W", "U", "B", "G"],
            "cmc": 4.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "WURG",
        {
            "cost": "{R}{G}{W}{U}",
            "colors": ["W", "U", "R", "G"],
            "cmc": 4.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "GRUW",
        {
            "cost": "{R}{G}{W}{U}",
            "colors": ["W", "U", "R", "G"],
            "cmc": 4.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "WBRG",
        {
            "cost": "{B}{R}{G}{W}",
            "colors": ["W", "B", "R", "G"],
            "cmc": 4.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "GRBW",
        {
            "cost": "{B}{R}{G}{W}",
            "colors": ["W", "B", "R", "G"],
            "cmc": 4.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "UBRG",
        {
            "cost": "{U}{B}{R}{G}",
            "colors": ["U", "B", "R", "G"],
            "cmc": 4.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "GRBU",
        {
            "cost": "{U}{B}{R}{G}",
            "colors": ["U", "B", "R", "G"],
            "cmc": 4.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "WUBRG",
        {
            "cost": "{W}{U}{B}{R}{G}",
            "colors": ["W", "U", "B", "R", "G"],
            "cmc": 5.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
    (
        "GRBUW",
        {
            "cost": "{W}{U}{B}{R}{G}",
            "colors": ["W", "U", "B", "R", "G"],
            "cmc": 5.0,
            "colorless": False,
            "monocolored": False,
            "multicolored": True,
        },
    ),
]

# Costs api.scryfall.com rejects with a 422 rather than parsing.
UNPARSEABLE = [
    "{T}",
    "{Q}",
]

# What Scryfall SAYS about an unreadable cost, not merely that it says something. Every pair is a
# golden captured from api.scryfall.com on 2026-08-16, one request each, and together they pin the
# rule the wording follows: the reported fragment is the input with everything Scryfall could read
# struck out, adjacent bare characters merge into one run, and a braced token keeps its braces.
UNPARSEABLE_MESSAGES = [
    ("{QQQ}", "{QQQ}"),
    ("{}", "{}"),
    ("{", "{"),
    ("!!!", "!!!"),
    ("é", "É"),
    # A triple hybrid is not a Magic symbol. This one is also the reason the rule above had to be
    # worked out at all: the recognized halves come out and only the punctuation is reported.
    ("{W/U/B}", "{//}"),
]


class TestParseManaGoldens:
    """Parity with Scryfall, case by case."""

    @pytest.mark.parametrize(("written", "expected"), GOLDENS)
    def test_matches_scryfall(self, written: str, expected: dict[str, Any]) -> None:
        parsed = parse_mana_cost(written)
        assert parsed["object"] == "mana_cost"
        assert {field: parsed[field] for field in expected} == expected

    @pytest.mark.parametrize("written", UNPARSEABLE)
    def test_a_non_mana_fragment_is_rejected(self, written: str) -> None:
        with pytest.raises(ManaCostError):
            parse_mana_cost(written)

    @pytest.mark.parametrize(("written", "fragment"), UNPARSEABLE_MESSAGES)
    def test_the_rejection_names_the_fragment_scryfall_names(self, written: str, fragment: str) -> None:
        """The `details` string is compared byte for byte by clients, so it is pinned that way."""
        with pytest.raises(ManaCostError) as raised:
            parse_mana_cost(written)
        expected = f"The string fragment(s) “{fragment}” could not be understood as part of mana cost."
        assert str(raised.value) == expected


class TestParseManaProperties:
    """Properties the goldens imply but do not state outright."""

    def test_an_empty_cost_is_null_but_an_explicit_zero_is_not(self) -> None:
        """The two differ upstream, so they cannot share a branch here."""
        assert parse_mana_cost("")["cost"] is None
        assert parse_mana_cost("0")["cost"] == "{0}"

    def test_case_and_whitespace_do_not_change_the_answer(self) -> None:
        assert parse_mana_cost("ruw") == parse_mana_cost("RUW")
        assert parse_mana_cost(" 2 W W ") == parse_mana_cost("2WW")

    def test_generic_pips_are_summed_into_one_symbol(self) -> None:
        assert parse_mana_cost("1{1}")["cost"] == "{2}"

    def test_consecutive_digits_are_one_number(self) -> None:
        """`11R` is eleven generic and a red pip, not two ones."""
        assert parse_mana_cost("11R")["cmc"] == 12.0

    def test_the_colors_list_is_always_wubrg_order(self) -> None:
        """Unlike `cost`, which is reordered canonically, `colors` is not."""
        assert parse_mana_cost("RUW")["cost"] == "{U}{R}{W}"
        assert parse_mana_cost("RUW")["colors"] == ["W", "U", "R"]

    def test_variable_pips_come_out_in_xyz_order_however_they_were_written(self) -> None:
        """`?cost=xyzzy` answers `{X}{Y}{Y}{Z}{Z}` on api.scryfall.com, not writing order."""
        assert parse_mana_cost("xyzzy")["cost"] == "{X}{Y}{Y}{Z}{Z}"
        assert parse_mana_cost("zyx")["cost"] == "{X}{Y}{Z}"

    def test_a_hybrid_has_exactly_two_halves(self) -> None:
        """`{W/U/B}` is not printable, and pricing it answered a three-coloured cost for one."""
        assert parse_mana_cost("{W/U}")["cost"] == "{W/U}"
        assert parse_mana_cost("{2/W}")["cmc"] == 2.0
        assert parse_mana_cost("{W/P}")["cmc"] == 1.0
        with pytest.raises(ManaCostError):
            parse_mana_cost("{W/U/B}")

    def test_every_unreadable_fragment_is_named_at_once(self) -> None:
        """The message says "fragment(s)" because it can name more than one, separated by a space."""
        with pytest.raises(ManaCostError) as raised:
            parse_mana_cost("{Q}W{T}")
        assert "“{Q} {T}”" in str(raised.value)
