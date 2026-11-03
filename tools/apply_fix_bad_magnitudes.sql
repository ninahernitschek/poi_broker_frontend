-- Repair 83 featuretable rows whose ant_mag_corrected holds a passband token
-- instead of a magnitude, by RELOCATING the value to ant_passband.
--
-- Safe to re-run (the predicate matches nothing once fixed).
--
--   sqlite3 -bail /path/to/ztf_alerts_stream.db < tools/apply_fix_bad_magnitudes.sql
--
-- DIAGNOSIS
--   The upstream alert schema codes the passband numerically, 1:g, 2:r, 3:i.
--   All 83 bad values are exactly those tokens: 79 rows hold 'R' (code 2) and
--   4 hold 'g' (code 1). Meanwhile ant_passband is NULL on 82 of the 83 --
--   the passband is sitting in the magnitude column while its own column sits
--   empty. That is a column mix-up in ingest, not a magnitude that failed to
--   parse.
--
--   All 83 come from a single batch spanning MJD 60368.5-60369.3; the 439,251
--   rows at MJD >= 61000 contain none, so this is historical.
--
-- WHY RELOCATE RATHER THAN DISCARD
--   'g' and 'R' are already valid ant_passband values in this application
--   (1,423,001 and 1,694,305 existing rows respectively), so moving the token
--   across needs no transformation and loses no information. Discarding it
--   would leave 83 rows with an unusable empty passband for no benefit.
--
-- WHY THE MAGNITUDE BECOMES NULL RATHER THAN A BACKFILLED NUMBER
--   Each bad row does carry brightest_alert_magnitude_ztf (14.6-16.8), but
--   that is the BRIGHTEST magnitude across all of the object's alerts, whereas
--   ant_mag_corrected is the CORRECTED magnitude of this detection. Different
--   quantities; copying one into the other would invent a plausible-looking
--   value and silently redefine what a magnitude sort means. NULL is honest.
--
-- IMPACT
--   Reachability is unchanged (2,166,326 rows listable under a magnitude sort
--   either way -- these 83 move from "excluded as wrong type" to "excluded as
--   NULL"). What is fixed: the latent cursor hazard (a TEXT cursor made SQLite
--   compare ascending against a descending ORDER BY and return the wrong rows)
--   and the 500 that mag_filter() raised on them. They also become filterable
--   by passband.
--
-- SINGLE TRANSACTION
--   Both UPDATEs and their guards run in one transaction. Unknown non-numeric
--   tokens abort before any repair; a surviving bad magnitude aborts before
--   COMMIT. The runner rolls back on error. The sqlite3 CLI must use -bail so
--   it stops at a failed guard rather than continuing the script.

BEGIN IMMEDIATE;

-- Pre-flight. Decode shows the token maps to passband codes 1 (g) and 2 (R).
SELECT alert_id,
       locus_id,
       date_alert_mjd,
       ant_mag_corrected,
       ant_passband,
       num_mag_values,
       brightest_alert_magnitude_ztf
FROM featuretable
WHERE typeof(ant_mag_corrected) NOT IN ('real', 'integer')
  AND ant_mag_corrected IS NOT NULL;

-- Only the diagnosed g/R tokens may be repaired. Reject unexpected values
-- rather than turning them into invalid passbands or discarding them.
CREATE TEMP TABLE _magnitude_repair_guard (
    ok INTEGER NOT NULL CHECK (ok = 1)
);
INSERT INTO _magnitude_repair_guard (ok)
SELECT CASE WHEN EXISTS (
    SELECT 1 FROM featuretable
    WHERE typeof(ant_mag_corrected) NOT IN ('real', 'integer')
      AND ant_mag_corrected IS NOT NULL
      AND ant_mag_corrected NOT IN ('g', 'R')
) THEN 0 ELSE 1 END;
DELETE FROM _magnitude_repair_guard;

-- Relocate the passband, then drop the non-numeric magnitude. The
-- ant_passband IS NULL guard avoids clobbering a passband that is already set:
-- exactly one of the 83 rows (ztf_candidate:2614538924615010011) already had
-- ant_passband = 'R', so its relocation would have been a redundant write.
UPDATE featuretable
SET ant_passband = ant_mag_corrected,
    ant_mag_corrected = NULL
WHERE typeof(ant_mag_corrected) NOT IN ('real', 'integer')
  AND ant_mag_corrected IS NOT NULL
  AND ant_mag_corrected IN ('g', 'R')
  AND ant_passband IS NULL;

-- Second statement: rows the guard above skipped because ant_passband was
-- already populated still carry a non-numeric magnitude. Keep their existing
-- passband; only the magnitude is unusable. Without this, one row survives and
-- can still 500 mag_filter() under a descending magnitude sort.
UPDATE featuretable
SET ant_mag_corrected = NULL
WHERE typeof(ant_mag_corrected) NOT IN ('real', 'integer')
  AND ant_mag_corrected IN ('g', 'R');

-- Post-check A: must be zero. This is the assertion that matters -- a single
-- surviving non-numeric magnitude is still reachable from page 1 of a
-- descending magnitude sort and still raises TypeError in mag_filter().
SELECT COUNT(*) AS remaining_non_numeric_magnitudes
FROM featuretable
WHERE typeof(ant_mag_corrected) NOT IN ('real', 'integer')
  AND ant_mag_corrected IS NOT NULL;

INSERT INTO _magnitude_repair_guard (ok)
SELECT CASE WHEN EXISTS (
    SELECT 1 FROM featuretable
    WHERE typeof(ant_mag_corrected) NOT IN ('real', 'integer')
      AND ant_mag_corrected IS NOT NULL
) THEN 0 ELSE 1 END;
DROP TABLE _magnitude_repair_guard;

-- Post-check B: the recovered passbands should now be present.
SELECT ant_passband, COUNT(*) AS recovered
FROM featuretable
WHERE alert_id IN (
    'ztf_candidate:2614538924615010030',
    'ztf_candidate:2614535591115015019',
    'ztf_candidate:2614536533415010003'
)
GROUP BY ant_passband;

COMMIT;

ANALYZE;
