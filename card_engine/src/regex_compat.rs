//! Query-regex compilation: accepting the dialect the SQL path accepts.
//!
//! `o:/.../` is documented against PostgreSQL's `~*`
//! (docs/changelog/2025-02-02-regex-search.md), and two things it accepts the
//! `regex` crate does not:
//!
//! - **Lookaround.** `(?!…)`, `(?=…)`, `(?<=…)`, `(?<!…)`. The `regex` crate
//!   omits these by design — they are what costs it its linear-time guarantee.
//!   Lookahead is on the documented feature list.
//! - **Word-boundary escapes.** ARE spells them `\y`/`\Y`/`\m`/`\M`, and ARE's
//!   `\Z` is Rust's `\z`. `\y` and `\Z` have exact `regex`-crate spellings, so
//!   they are rewritten in place; `\m`/`\M` have none and become lookaround.
//!
//! And one thing NEITHER dialect has: Scryfall's `\s…` shorthands, which its own docs page
//! calls "not formal character classes, it is just shorthand we have added". They are expanded
//! here — see [`SCRYFALL_SHORTHANDS`].
//!
//! Both were engine *declines* — a `build_filter` error that
//! `_search`'s blanket handler turned into a silent PostgreSQL fallback. That
//! made the SQL path load-bearing for a documented feature rather than a
//! crash net.
//!
//! A pattern the `regex` crate accepts still compiles on it, unchanged. The
//! backtracking engine is entered only where the fast one cannot go, which
//! keeps every existing optimization — most importantly the #734 trigram
//! narrowing, whose `regex_syntax::parse` reads the same pattern string.

use std::sync::Arc;

use regex::Regex;

use super::filter::REGEX_BACKTRACK_LIMIT;

/// A compiled query regex, on whichever engine can express it.
///
/// `Clone` is cheap on both arms: `regex::Regex` is internally `Arc`-based, and
/// the backtracking arm is behind an `Arc` here for the same reason — see
/// `FilterExpr`'s `Clone` note.
#[derive(Clone, Debug)]
pub(crate) enum CompiledRegex {
    /// The linear-time engine. Every pattern that can be, is.
    Fast(Regex),
    /// The backtracking engine: lookaround and backreferences only.
    Backtrack(Arc<fancy_regex::Regex>),
}

/// The inline flags every query regex is compiled with, and the exact prefix the two callers that
/// read a compiled pattern back (`regex_tier`, `regex_required_factors`) strip before parsing it.
///
/// `i` is the `~*` operator the SQL path uses. `m` makes `^` and `$` match at every line boundary
/// rather than only at the ends of the string, which is what Scryfall does — measured against
/// api.scryfall.com on 2026-08-16, `o:/^Whenever you cast/ e:khm` returns Firja, Judge of Valor
/// (khm/209), whose oracle text is `"Flying, lifelink\nWhenever you cast your second spell each
/// turn, …"`, and `o:/lifelink$/ e:khm` returns it too. Oracle text is the only multi-line column,
/// so this changes nothing on the single-line ones (name, type line, artist, set code).
///
/// It does NOT turn on `s`: `.` still stops at a newline, verified the same way
/// (`o:/Flying.Whenever/ e:khm` is empty on Scryfall while `o:/Flying\nWhenever/ e:khm` is not).
/// Together that is exactly PostgreSQL ARE's newline-sensitive mode — the SQL path spells the
/// same pair `(?n)` — so the two paths still accept and answer one dialect.
///
/// Keep this a single `(?…)` group: the strippers match it by literal prefix.
pub(crate) const QUERY_REGEX_FLAGS: &str = "(?im)";

impl CompiledRegex {
    /// Compile a query pattern under [`QUERY_REGEX_FLAGS`].
    ///
    /// The error string is the linear engine's, not the backtracking one's: if
    /// both reject the pattern it is malformed rather than merely non-linear,
    /// and the first message is the one that names the actual syntax problem.
    pub(crate) fn new(pattern: &str) -> Result<Self, String> {
        let translated = translate_query_escapes(pattern);
        let cased = format!("{QUERY_REGEX_FLAGS}{translated}");
        match Regex::new(&cased) {
            Ok(re) => Ok(CompiledRegex::Fast(re)),
            Err(linear_err) => match fancy_regex::RegexBuilder::new(&cased)
                .backtrack_limit(REGEX_BACKTRACK_LIMIT)
                .build()
            {
                Ok(re) => Ok(CompiledRegex::Backtrack(Arc::new(re))),
                Err(_) => Err(format!("invalid regex '{pattern}': {linear_err}")),
            },
        }
    }

    /// Does this pattern match anywhere in `haystack`, budget permitting?
    ///
    /// The linear arm cannot fail — it has no step budget to exhaust — so the
    /// `Err` case is reachable only from a lookaround or backreference pattern
    /// that ran past [`REGEX_BACKTRACK_LIMIT`]. `filter::regex_is_match` is the
    /// caller that turns that into the query-level `UnsupportedRegexError`
    /// rather than a silent non-match; see its note.
    #[inline]
    pub(crate) fn try_is_match(&self, haystack: &str) -> Result<bool, fancy_regex::Error> {
        match self {
            CompiledRegex::Fast(re) => Ok(re.is_match(haystack)),
            CompiledRegex::Backtrack(re) => re.is_match(haystack),
        }
    }

    /// [`try_is_match`](Self::try_is_match) with an exhausted budget read as
    /// "no match".
    ///
    /// For callers outside a query's execution — tests, and anywhere the
    /// failure latch is not being read afterwards.
    #[inline]
    pub(crate) fn is_match(&self, haystack: &str) -> bool {
        self.try_is_match(haystack).unwrap_or(false)
    }

    /// The compiled pattern source, [`QUERY_REGEX_FLAGS`] prefix included.
    ///
    /// Feeds `regex_tier` (cost) and the #734 literal-factor extraction. The
    /// latter parses this with `regex_syntax`, which fails on a backtracking
    /// pattern and yields no factors — so those patterns lose the trigram
    /// narrow and scan, which is correct, just not fast.
    pub(crate) fn as_str(&self) -> &str {
        match self {
            CompiledRegex::Fast(re) => re.as_str(),
            CompiledRegex::Backtrack(re) => re.as_str(),
        }
    }

    /// True when this pattern needed the backtracking engine. Cost only.
    #[inline]
    pub(crate) fn is_backtracking(&self) -> bool {
        matches!(self, CompiledRegex::Backtrack(_))
    }
}

/// One mana symbol, as Scryfall's `\sm` shorthand means it.
///
/// MEASURED, not derived from the symbology table: every alternative below is a card the corpus
/// actually holds, and the whole expression was checked by asking api.scryfall.com for BOTH counts
/// on 2026-08-28 — `o:/\sm/` and `o:/<this>/` are 11,057 apiece, corpus-wide.
///
/// Four alternatives exist only because a probe found the card that needs them, and each is one
/// card wide: `{½}` (Cheap Ass), `{H}` — Scryfall's spelling of the generic Phyrexian symbol
/// (Rage Extractor) — `{HR}`/`{HW}` half-mana (Mons's Goblin Waiters), and `{P}`, which is
/// Bloomburrow's PAWPRINT and not Phyrexian at all (the five `Season of …` cards). The last is why
/// `\smp` below cannot simply reuse this: Scryfall counts `{P}` as a mana symbol and NOT as a
/// Phyrexian one, contradicting its own docs page, which offers `{P}` as a `\smp` example.
const MANA_SYMBOL: &str = r"\{(?:[0-9]+|[wubrgcsxyz]|[^{}]*/[^{}]*|h[wubrg]?|p|½)\}";

/// The same vocabulary MINUS the bare `{P}`, which is what `\smr` repeats over.
///
/// Scryfall's `\sm` counts the Bloomburrow PAWPRINT as a mana symbol and its two DERIVED
/// shorthands do not: `\smp` excludes it (42, not 47) and so does `\smr`. `o:/\smr/` is 1,189 on
/// api.scryfall.com (2026-08-28); reusing `MANA_SYMBOL` here answers 1,194, and the five extras
/// are exactly the `Season of …` cards, whose `{P}{P} —` mode lines are a repeated pawprint and
/// nothing else.
const REPEATABLE_MANA_SYMBOL: &str = r"\{(?:[0-9]+|[wubrgcsxyz]|[^{}]*/[^{}]*|h[wubrg]?|½)\}";

/// Scryfall's non-standard regex shorthands, as `(suffix after \s, expansion)`.
///
/// <https://scryfall.com/docs/regular-expressions> documents these as "not formal character
/// classes, it is just shorthand we have added", and they are the reason a `\s` in a query regex
/// cannot be read as whitespace without looking at what follows it. THE FAILURE IS SILENT:
/// `o:/\smp/` answers 42 on api.scryfall.com and answers ZERO under a whitespace reading, because
/// no oracle text contains whitespace followed by "mp" — and `o:/\sm/`, worse, answers a plausible
/// 10,791 against Scryfall's 11,057, a wrong number that looks like a right one.
///
/// EVERY EXPANSION IS A MEASURED EQUALITY, established by asking api.scryfall.com for the count of
/// the shorthand and the count of the expansion and requiring them to agree, corpus-wide,
/// 2026-08-28:
///
/// | shorthand | means                        | count  | expansion agrees |
/// |-----------|------------------------------|--------|------------------|
/// | `\ss`     | any card symbol              | 12,446 | yes              |
/// | `\sm`     | any mana symbol              | 11,057 | yes              |
/// | `\sc`     | any COLORED mana symbol      |  6,676 | yes              |
/// | `\smh`    | any hybrid card symbol       |    172 | yes              |
/// | `\smp`    | any Phyrexian card symbol    |     42 | yes              |
/// | `\smr`    | any REPEATED mana symbol     |  1,189 | see below        |
/// | `\spt`    | an X/X power/toughness       |  3,185 | yes              |
/// | `\spp`    | a +X/+X                      |  7,160 | yes              |
/// | `\smm`    | a -X/-X                      |    841 | yes              |
///
/// `\sc` excludes the half-mana symbols and `\smh` excludes the MONOCOLOR Phyrexian ones, both
/// measured rather than assumed: `\{[^{}]*[wubrg][^{}]*\}` is 6,677 against `\sc`'s 6,676 (the
/// extra is `{HR}`), and every symbol carrying a `/` is 213 against `\smh`'s 172 (the 41
/// difference is exactly `o:/\/p}/`, the `{X/P}` cards).
///
/// LONGEST MATCH. `\smm` is the -X/-X shorthand and not `\sm` followed by a literal `m`, and the
/// same holds for `\smr`/`\smh`/`\smp` against `\sm`. Scryfall reads them the same way and its
/// choice is observable: `o:/\smana/` is 404 there — `\sm` then "ana" — where a whitespace reading
/// answers 2,784, the count for whitespace followed by "mana".
///
/// Each expansion is wrapped in `(?:…)` so a quantifier binds to the whole shorthand: `\sm{2}` is
/// two mana symbols, not one symbol whose closing brace repeats.
const SCRYFALL_SHORTHANDS: &[(&str, &str)] = &[
    // Three characters first, so the longest match wins.
    ("mh", r"(?:\{(?:[^{}]*/[^{}]*/[^{}]*|[^{}]*/[^{}p])\})"),
    ("mp", r"(?:\{(?:[^{}]*/p|h)\})"),
    ("mm", r"(?:-[0-9x*]+/-[0-9x*]+)"),
    ("pt", r"(?:[0-9x*]+/[0-9x*]+)"),
    ("pp", r"(?:\+[0-9x*]+/\+[0-9x*]+)"),
    ("s", r"(?:\{[^{}]*\})"),
    ("c", r"(?:\{[0-9wubrgcpxyz/½]*[wubrg][0-9wubrgcpxyz/½]*\})"),
];

/// Rewrite PostgreSQL ARE escapes that the `regex` crate spells differently or
/// cannot spell at all.
///
/// | ARE  | meaning              | rewritten to      |
/// |------|----------------------|-------------------|
/// | `\y` | word boundary        | `\b`              |
/// | `\Y` | not a word boundary  | `\B`              |
/// | `\m` | start of a word      | `(?<!\w)(?=\w)`   |
/// | `\M` | end of a word        | `(?<=\w)(?!\w)`   |
/// | `\Z` | end of string        | `\z`              |
///
/// `\y`/`\Y`/`\Z` have exact equivalents, so a pattern using only those stays
/// on the linear engine. `\m`/`\M` do not, and their lookaround rewrite sends
/// the pattern to the backtracking engine — correct, and rare enough to be
/// worth the access path.
///
/// The `\s…` half is [`SCRYFALL_SHORTHANDS`] plus `\smr`, which is the one shorthand no static
/// expansion can express: "the SAME mana symbol twice" needs a backreference, so it compiles a
/// named group and `\k<…>` and therefore lands on `fancy_regex` — losing the #734 trigram narrow
/// along with it. Every other shorthand stays on the linear engine, and the group is NAMED (and
/// numbered per occurrence) so it cannot collide with a capture the user wrote.
///
/// Bracket expressions are copied through untouched: inside `[…]` these are
/// ordinary escapes, not constraints. A `]` in the first position of a class is
/// literal (POSIX), so it does not close it. Scryfall does NOT skip classes — `o:/[\sm]/` comes
/// back "parentheses () not balanced" there, its own substitution having broken the class — and
/// reproducing that particular bug would turn a query that reads perfectly well ("whitespace or
/// the letter m") into an error.
pub(crate) fn translate_query_escapes(pattern: &str) -> String {
    let chars: Vec<char> = pattern.chars().collect();
    let mut out = String::with_capacity(pattern.len());
    // Position within the current bracket expression, if any: `Some(n)` means
    // n characters have been consumed since `[`, which is how the leading-`]`
    // rule is applied without a second scan.
    let mut class_pos: Option<usize> = None;
    // Distinguishes the capture groups two `\smr`s in one pattern would otherwise share.
    let mut smr_seq = 0usize;
    let mut i = 0usize;

    while i < chars.len() {
        let c = chars[i];
        if c == '\\' {
            i += 1;
            let Some(&next) = chars.get(i) else {
                out.push('\\');
                break;
            };
            i += 1;
            if class_pos.is_some() {
                out.push('\\');
                out.push(next);
                class_pos = class_pos.map(|n| n + 2);
                continue;
            }
            if next == 's' {
                if chars.get(i) == Some(&'m') && chars.get(i + 1) == Some(&'r') {
                    out.push_str(&format!("(?:(?<smr{smr_seq}>{REPEATABLE_MANA_SYMBOL})\\k<smr{smr_seq}>)"));
                    smr_seq += 1;
                    i += 2;
                    continue;
                }
                if chars.get(i) == Some(&'m') && !matches!(chars.get(i + 1), Some('h' | 'p' | 'm')) {
                    out.push_str(&format!("(?:{MANA_SYMBOL})"));
                    i += 1;
                    continue;
                }
                if let Some((suffix, expansion)) = SCRYFALL_SHORTHANDS
                    .iter()
                    .find(|(suffix, _)| suffix.chars().enumerate().all(|(k, sc)| chars.get(i + k) == Some(&sc)))
                {
                    out.push_str(expansion);
                    i += suffix.chars().count();
                    continue;
                }
            }
            match next {
                'y' => out.push_str(r"\b"),
                'Y' => out.push_str(r"\B"),
                'm' => out.push_str(r"(?<!\w)(?=\w)"),
                'M' => out.push_str(r"(?<=\w)(?!\w)"),
                'Z' => out.push_str(r"\z"),
                other => {
                    out.push('\\');
                    out.push(other);
                }
            }
            continue;
        }

        match class_pos {
            None => {
                if c == '[' {
                    class_pos = Some(0);
                }
            }
            // A leading `^` negates without occupying the first position, so
            // `[^]…]` gets the same literal-`]` treatment as `[]…]`.
            Some(0) if c == '^' => {}
            // `[]…]`: a `]` in the first position is a literal member.
            Some(0) if c == ']' => class_pos = Some(1),
            Some(_) if c == ']' => class_pos = None,
            Some(n) => class_pos = Some(n + 1),
        }
        out.push(c);
        i += 1;
    }
    out
}
