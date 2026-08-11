// Legalities pack into a u64: 2 bits per format, positions handed out append-only
// by a global registry the first time a format name appears in loaded data, so
// bit assignments stay stable across reloads and engine instances. A format the
// card's JSONB omits reads as not_legal. 32 formats fit; Scryfall ships 22.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering as AtomicOrdering};
use std::sync::{Arc, OnceLock, RwLock};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use rkyv::Archived;

const LEGALITY_NOT_LEGAL: u64 = 0;
pub(crate) const LEGALITY_LEGAL: u64 = 1;
pub(crate) const LEGALITY_RESTRICTED: u64 = 2;
pub(crate) const LEGALITY_BANNED: u64 = 3;
pub(crate) const MAX_FORMATS: usize = 32;

static FORMAT_SHIFTS: OnceLock<RwLock<HashMap<String, u8>>> = OnceLock::new();

pub(crate) fn format_shifts() -> &'static RwLock<HashMap<String, u8>> {
    FORMAT_SHIFTS.get_or_init(|| RwLock::new(HashMap::new()))
}

/// Bumped by every writer of `FORMAT_SHIFTS`; what tells a cached order it is stale.
static FORMAT_GENERATION: AtomicU64 = AtomicU64::new(0);

type FormatOrder = Arc<Vec<(String, u8)>>;

fn format_order_cell() -> &'static RwLock<(u64, FormatOrder)> {
    static CELL: OnceLock<RwLock<(u64, FormatOrder)>> = OnceLock::new();
    // Generation 1 against FORMAT_GENERATION's 0, so the initial empty value never reads as fresh.
    CELL.get_or_init(|| RwLock::new((u64::MAX, Arc::new(Vec::new()))))
}

/// The registry's `(format, shift)` pairs, alphabetical, built once per registry change.
///
/// `legality_bits_to_pydict` is a FIELD_TABLE extractor, so it runs ONCE PER ROW. It used to build
/// this vector itself every time: a read lock, a clone of all 22 format names, and a sort, to
/// decode a word that is a pure function of one `u64` and orders identically for every card in the
/// store. A 175-card page of /cards/search -- which asks for `legalities` on every card object --
/// therefore paid 175 locks, 175 sorts and ~3,850 String allocations to produce 175 copies of one
/// answer. `fields=legalities` on /search (#877) pays it per row too.
///
/// The registry only grows, and only on import or archive attach, so the sorted form is cached and
/// invalidated by generation rather than by lock discipline: a reader never blocks a writer, and a
/// rebuild that races a write is DISCARDED rather than published, so a stale order cannot be
/// served. Worst case is a redundant rebuild.
fn format_order() -> FormatOrder {
    let generation = FORMAT_GENERATION.load(AtomicOrdering::Acquire);
    if let Ok(cached) = format_order_cell().read()
        && cached.0 == generation
    {
        return Arc::clone(&cached.1);
    }
    let mut entries: Vec<(String, u8)> = match format_shifts().read() {
        Ok(shifts) => shifts.iter().map(|(k, v)| (k.clone(), *v)).collect(),
        Err(_) => Vec::new(),
    };
    entries.sort();
    let built: FormatOrder = Arc::new(entries);
    if let Ok(mut slot) = format_order_cell().write() {
        // Only publish if the registry did not move while we were building it.
        if FORMAT_GENERATION.load(AtomicOrdering::Acquire) == generation {
            *slot = (generation, Arc::clone(&built));
        }
    }
    built
}

/// Mark the cached order stale. Called by every writer of `FORMAT_SHIFTS`, after it releases the
/// write lock -- never while holding it, so the two locks are never held at once in either order.
fn invalidate_format_order() {
    FORMAT_GENERATION.fetch_add(1, AtomicOrdering::AcqRel);
}

/// Bit shift for a format already seen in loaded data; None matches nothing.
pub(crate) fn format_shift(format: &str) -> Option<u8> {
    format_shifts().read().ok()?.get(format).copied()
}

/// Bit shift for a format, assigning the next free slot if unseen (reload path).
pub(crate) fn format_shift_or_assign(format: &str) -> Option<u8> {
    if let Some(shift) = format_shift(format) {
        return Some(shift);
    }
    let mut shifts = format_shifts().write().ok()?;
    if let Some(&shift) = shifts.get(format) {
        return Some(shift); // assigned while we waited for the write lock
    }
    if shifts.len() >= MAX_FORMATS {
        return None;
    }
    let shift = (shifts.len() * 2) as u8;
    shifts.insert(format.to_string(), shift);
    drop(shifts);
    invalidate_format_order();
    Some(shift)
}

fn legality_code(status: &str) -> u64 {
    match status {
        "legal"      => LEGALITY_LEGAL,
        "restricted" => LEGALITY_RESTRICTED,
        "banned"     => LEGALITY_BANNED,
        _            => LEGALITY_NOT_LEGAL,
    }
}

pub(crate) fn jsonb_obj_to_legality_bits(d: &Bound<PyDict>, key: &str) -> u64 {
    d.get_item(key)
        .ok()
        .flatten()
        .and_then(|v| {
            v.cast::<PyDict>().ok().map(|m| {
                m.iter()
                    .filter_map(|(k, v)| {
                        let format = k.extract::<String>().ok()?;
                        let status = v.extract::<String>().ok()?;
                        let shift = format_shift_or_assign(&format)?;
                        Some(legality_code(&status) << shift)
                    })
                    .fold(0u64, |bits, b| bits | b)
            })
        })
        .unwrap_or_default()
}

/// Decode a packed legality word into a `{format: status}` Python dict covering every
/// format the registry knows, alphabetically — the field-extraction counterpart of
/// `jsonb_obj_to_legality_bits`. A format absent from the imported JSONB round-trips
/// as "not_legal", exactly as the encoder treated it.
pub(crate) fn legality_bits_to_pydict<'a>(py: Python<'a>, bits: u64) -> PyResult<pyo3::Bound<'a, PyDict>> {
    let dict = PyDict::new(py);
    for (format, shift) in format_order().iter() {
        let word = match (bits >> shift) & 0b11 {
            LEGALITY_LEGAL => "legal",
            LEGALITY_RESTRICTED => "restricted",
            LEGALITY_BANNED => "banned",
            _ => "not_legal",
        };
        dict.set_item(format, word)?;
    }
    Ok(dict)
}

/// Adopt the archive's format→shift assignments into this process's registry.
/// Cheap no-op (one read lock) once the registry has caught up.
pub(crate) fn sync_format_shifts(archived: &Archived<HashMap<String, u8>>) {
    let behind = format_shifts().read().map(|m| m.len() < archived.len()).unwrap_or(false);
    if !behind {
        return;
    }
    if let Ok(mut shifts) = format_shifts().write() {
        for (format, shift) in archived.iter() {
            shifts.insert(format.as_str().to_string(), *shift);
        }
    }
    invalidate_format_order();
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The cached order must follow the registry, or a format assigned after the first decode
    /// would be missing from every `legalities` value for the life of the process.
    ///
    /// Asserts only properties that hold under parallel tests against a shared global registry:
    /// the registry grows monotonically, so CONTAINMENT and SORTEDNESS are stable, while an
    /// exact-equality assertion would race anything else that loads a store.
    #[test]
    fn format_order_follows_the_registry() {
        let sorted = |v: &[(String, u8)]| v.windows(2).all(|w| w[0] <= w[1]);

        let before = format_order();
        assert!(sorted(&before), "cached order must be sorted");

        // A name no fixture uses, so this cannot collide with a real format.
        let probe = "zzz_test_only_format";
        let assigned = format_shift_or_assign(probe);
        if assigned.is_none() {
            return; // registry already full (MAX_FORMATS); nothing to assert
        }

        let after = format_order();
        assert!(sorted(&after), "still sorted after an assignment");
        assert!(
            after.iter().any(|(name, _)| name == probe),
            "a format assigned after the order was cached must appear in it"
        );

        // And the rebuild is cached, not repeated: a second call with no writer in between
        // returns the same allocation.
        assert!(Arc::ptr_eq(&after, &format_order()), "second call must reuse the cached order");
    }
}
