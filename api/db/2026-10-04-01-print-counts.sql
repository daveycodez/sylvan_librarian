-- Scryfall's count keywords -- `prints`, `sets`, `paperprints`, `papersets`, `illustrations` and
-- `artists` -- each compare a number about the card's OTHER printings that no row can see at
-- query time: `prints>=50` is "cards with at least fifty printings". So, like the other
-- import-derived columns, the numbers are decided when the data is written.
--
-- Five are CARD-level and are written identically onto every row of the card (per oracle_id);
-- `artist_count` is the row's own. All six are maintained by _sync_print_counts in
-- api/admin_resource.py after every import, and backfilled once below so the keywords answer as
-- soon as this deploys rather than after the next import. NULL means "not yet counted" and, like
-- any NULL number, satisfies neither a comparison nor its negation.
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS card_print_count smallint;
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS card_set_count smallint;
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS card_paper_print_count smallint;
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS card_paper_set_count smallint;
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS card_illustration_count smallint;
ALTER TABLE magic.cards ADD COLUMN IF NOT EXISTS artist_count smallint;

COMMENT ON COLUMN magic.cards.card_print_count IS 'Card-level: distinct (set code, collector number) slots over every row of this oracle_id. Scryfall''s `prints`.';
COMMENT ON COLUMN magic.cards.card_set_count IS 'Card-level: distinct set codes over every row of this oracle_id. Scryfall''s `sets`.';
COMMENT ON COLUMN magic.cards.card_paper_print_count IS 'Card-level: card_print_count over the rows whose games include paper. Scryfall''s `paperprints`.';
COMMENT ON COLUMN magic.cards.card_paper_set_count IS 'Card-level: card_set_count over the rows whose games include paper. Scryfall''s `papersets`.';
COMMENT ON COLUMN magic.cards.card_illustration_count IS 'Card-level: distinct illustration ids over every row of this oracle_id. Scryfall''s `illustrations`.';
COMMENT ON COLUMN magic.cards.artist_count IS 'Printing-level: how many artists this printing credits (the length of artist_ids). Scryfall''s `artists`.';

-- The backfill: the statement _build_print_counts_sql() runs per chunk, here over the whole table.
-- Idempotent -- only rows whose numbers differ are rewritten -- and no index is added: the engine
-- answers these keywords from memory, and the SQL fallback's scan of six smallints is cheap.
WITH per_card AS (
    SELECT
        cards.oracle_id,
        count(DISTINCT (lower(cards.card_set_code), cards.collector_number)) AS prints,
        count(DISTINCT lower(cards.card_set_code)) AS sets,
        count(DISTINCT (lower(cards.card_set_code), cards.collector_number))
            FILTER (WHERE COALESCE(cards.raw_card_blob->'games', '[]'::jsonb) ? 'paper') AS paper_prints,
        count(DISTINCT lower(cards.card_set_code))
            FILTER (WHERE COALESCE(cards.raw_card_blob->'games', '[]'::jsonb) ? 'paper') AS paper_sets,
        count(DISTINCT cards.illustration_id) AS illustrations
    FROM magic.cards cards
    WHERE cards.oracle_id IS NOT NULL
    GROUP BY cards.oracle_id
), proposed AS (
    SELECT
        cards.scryfall_id,
        LEAST(per_card.prints, 32767)::smallint AS card_print_count,
        LEAST(per_card.sets, 32767)::smallint AS card_set_count,
        LEAST(per_card.paper_prints, 32767)::smallint AS card_paper_print_count,
        LEAST(per_card.paper_sets, 32767)::smallint AS card_paper_set_count,
        LEAST(per_card.illustrations, 32767)::smallint AS card_illustration_count,
        LEAST(
            CASE WHEN jsonb_typeof(cards.raw_card_blob->'artist_ids') = 'array'
                 THEN jsonb_array_length(cards.raw_card_blob->'artist_ids') ELSE 0 END,
            32767
        )::smallint AS artist_count
    FROM magic.cards cards
    JOIN per_card ON per_card.oracle_id = cards.oracle_id
)
UPDATE magic.cards
SET
    card_print_count = proposed.card_print_count,
    card_set_count = proposed.card_set_count,
    card_paper_print_count = proposed.card_paper_print_count,
    card_paper_set_count = proposed.card_paper_set_count,
    card_illustration_count = proposed.card_illustration_count,
    artist_count = proposed.artist_count
FROM proposed
WHERE
    cards.scryfall_id = proposed.scryfall_id AND
    (cards.card_print_count, cards.card_set_count, cards.card_paper_print_count,
     cards.card_paper_set_count, cards.card_illustration_count, cards.artist_count)
    IS DISTINCT FROM
    (proposed.card_print_count, proposed.card_set_count, proposed.card_paper_print_count,
     proposed.card_paper_set_count, proposed.card_illustration_count, proposed.artist_count);
