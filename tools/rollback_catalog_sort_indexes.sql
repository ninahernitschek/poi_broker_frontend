-- Revert apply_catalog_sort_indexes.sql.
--
--   python tools/apply_sql.py tools/rollback_catalog_sort_indexes.sql --db <path> --yes
--
-- Only needed if the composite indexes turn out to hurt. Measured cost is the
-- 60 s build; this drops the six composites and restores the six
-- single-column sort indexes, so the catalog reverts to its previous
-- behaviour (a full sort on the first and last page of a date or locus_id
-- sort, ~9 s) while staying correct.
--
-- Order matters: create the replacements before dropping the composites, so no
-- column is ever left unindexed.
--
-- Does NOT undo the ant_mag_corrected repair; see apply_fix_bad_magnitudes.sql
-- for that (the pre-flight SELECT in that script prints the affected rows, so
-- capture its output before applying if you may want to reverse it).

CREATE INDEX IF NOT EXISTS idx_featuretable_date_alert_mjd
    ON featuretable (date_alert_mjd);
CREATE INDEX IF NOT EXISTS idx_featuretable_ztf_object_id
    ON featuretable (ztf_object_id);
CREATE INDEX IF NOT EXISTS idx_featuretable_locus_id
    ON featuretable (locus_id);
CREATE INDEX IF NOT EXISTS idx_featuretable_locus_ra
    ON featuretable (locus_ra);
CREATE INDEX IF NOT EXISTS idx_featuretable_locus_dec
    ON featuretable (locus_dec);
CREATE INDEX IF NOT EXISTS idx_featuretable_ant_mag_corrected
    ON featuretable (ant_mag_corrected);

DROP INDEX IF EXISTS idx_featuretable_sort_date_alert_mjd;
DROP INDEX IF EXISTS idx_featuretable_sort_ztf_object_id;
DROP INDEX IF EXISTS idx_featuretable_sort_locus_id;
DROP INDEX IF EXISTS idx_featuretable_sort_locus_ra;
DROP INDEX IF EXISTS idx_featuretable_sort_locus_dec;
DROP INDEX IF EXISTS idx_featuretable_sort_ant_mag_corrected;

ANALYZE;
