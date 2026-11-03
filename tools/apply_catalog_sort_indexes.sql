-- Replace the featuretable single-column sort indexes with
-- (column, alert_id, locus_id) composites.
--
-- Safe to re-run (IF NOT EXISTS / IF EXISTS). Do NOT use db.create_all() on a
-- populated production DB, and do NOT re-run tools/alertsdb_schema.sql's CREATE
-- TABLE against it.
--
--   sqlite3 /path/to/ztf_alerts_stream.db < tools/apply_catalog_sort_indexes.sql
--
-- Why: the catalog pages with a keyset cursor over (sort_column, alert_id,
-- locus_id) (poi_broker/services/catalog_list.py). With only a single-column
-- index, SQLite cannot produce that ORDER BY from the index and falls back to
-- sorting the whole table. Measured on the 3,18M-row production table:
--
--   first page of any sort, without composites : 2.2 s   (full sort)
--   first page of any sort, with composites    : 0.000 s (index scan)
--
-- Each composite's leftmost prefix is the bare column, so it also serves that
-- column's equality and range filters -- which is why the old single-column
-- indexes are dropped afterwards rather than kept.
--
-- alert_id keeps its single-column index: the PK autoindex is already
-- (alert_id, locus_id, date_alert_mjd), so ordering by (alert_id, locus_id)
-- needs no composite. ant_passband is a filter column, never a sort column.
--
-- OPERATIONAL NOTES
--   * CREATE INDEX is a single transaction: it either completes or rolls back,
--     leaving the table untouched on failure. Readers are never blocked, but it
--     holds SQLite's single writer lock for its duration (~3 s per index on the
--     production table, ~20 s for all six). Schedule it off-peak if alert
--     ingestion is running.
--   * The CREATEs all run before any DROP, so no column is ever left without an
--     index. If the script is interrupted, re-running it is safe.
--   * Expect roughly +930 MB net: about +1.2 GB of composites minus the ~265 MB
--     of single-column indexes they replace.
--   * ANALYZE at the end refreshes sqlite_stat1 so the planner prefers the
--     composites. Skipping it leaves the planner guessing.

CREATE INDEX IF NOT EXISTS idx_featuretable_sort_date_alert_mjd
    ON featuretable (date_alert_mjd, alert_id, locus_id);
CREATE INDEX IF NOT EXISTS idx_featuretable_sort_ztf_object_id
    ON featuretable (ztf_object_id, alert_id, locus_id);
CREATE INDEX IF NOT EXISTS idx_featuretable_sort_locus_id
    ON featuretable (locus_id, alert_id, locus_id);
CREATE INDEX IF NOT EXISTS idx_featuretable_sort_locus_ra
    ON featuretable (locus_ra, alert_id, locus_id);
CREATE INDEX IF NOT EXISTS idx_featuretable_sort_locus_dec
    ON featuretable (locus_dec, alert_id, locus_id);
CREATE INDEX IF NOT EXISTS idx_featuretable_sort_ant_mag_corrected
    ON featuretable (ant_mag_corrected, alert_id, locus_id);

-- Only now that each column has a composite (whose leftmost prefix is the bare
-- column) is it safe to retire the single-column index.
DROP INDEX IF EXISTS idx_featuretable_date_alert_mjd;
DROP INDEX IF EXISTS idx_featuretable_ztf_object_id;
DROP INDEX IF EXISTS idx_featuretable_locus_id;
DROP INDEX IF EXISTS idx_featuretable_locus_ra;
DROP INDEX IF EXISTS idx_featuretable_locus_dec;
DROP INDEX IF EXISTS idx_featuretable_ant_mag_corrected;

ANALYZE;
