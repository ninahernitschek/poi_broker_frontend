# Large CSV download failure and deferred Huey initialization finding

Recorded October 12, 2026. Scope: the supplied Ubuntu server configuration and the authenticated test at https://poibroker.uantof.cl/, which the user described as slightly behind production and using the same configuration.

## Status and decision

**The large CSV download failure is a real observed issue; its cause is undiagnosed.** The user reported two earlier large-export failures, and the live test reproduced the visible symptom: the site reports export completion, but the large download stops. Small downloads succeed.

Diagnose this issue before making changes to Huey initialization, download handling, Gunicorn timeouts, or nginx configuration. No application or server configuration change was made during this investigation.

## Earlier flagged issue

The incoming review flag was rated **High**:

> Huey initialization order: the supplied web setup creates the queue before loading `.env`, so exports execute synchronously in Gunicorn.

The cited location is `poi_broker/__init__.py:15`.

The supplied code/configuration supports an initialization-order concern:

- `poi_broker/__init__.py` imports `.extensions` before `.settings`.
- `poi_broker/extensions.py` constructs the shared Huey instance at import time.
- `poi_broker/settings.py` calls `load_dotenv()` at import time, after queue construction.
- With no pre-exported Huey settings, `poi_broker/huey_config.py` selects `MemoryHuey(immediate=True)`.
- `server_config/poi_broker.service` starts `wsgi:app` and sets only `PATH`; it has no `EnvironmentFile` directive. The separate Huey worker unit does load `huey.env`.

An isolated copy of the real WSGI entrypoint reproduced this state with the supplied `.env` Huey settings: after import, the environment said `HUEY_BACKEND=sqlite`, but the active queue remained `MemoryHuey(immediate=True)`. A harmless task ran in the caller before returning. A control with SQLite settings exported before import selected `SqliteHuey(immediate=False)` and queued the task. Neither reproduction used production databases or changed the repository.

**That local reproduction does not establish the running site's effective environment.** The large live test returned from submission before processing finished, then showed an in-progress task and later successful completion. This contradicts the incoming claim that the tested site's entire export executes synchronously before the Gunicorn submission response returns. Do not describe the original High production diagnosis as confirmed. Preserve the flag for reconciliation with the effective deployed configuration and source, not as an established explanation for the download failure.

## Live evidence

| Test | Observation |
| --- | --- |
| Small export, task 10 | Exact alert-ID query matched one row. Downloaded `export_10.csv` was verified locally: one data row, 280 columns, expected alert ID. The user's screenshot also shows a second small download completed. |
| Large export, task 11 | Generated query: `featuretable.locus_ra < 260`; preview count: **741,513 rows**. Exactly one large export was submitted. |
| Submission and processing | Submission returned and displayed a queued task. A fresh export-page GET showed processing in progress about **56 seconds** after the click. Another fresh GET showed successful completion and a download link about **139 seconds** after the click. These are observation times, not an exact processing duration. |
| Site availability | The Help page loaded during submission. This was a single lightweight check, not a concurrency/load test. |
| Large download | The user confirmed the download stopped. Their browser Downloads screenshot shows `export_11.csv` as **Stopped**, while both small downloads completed. The menu supplied no detailed failure reason. No completed `export_11*` file was found in the user's Downloads folder during checks. |
| Historical observations | The user recalls successful exports up to about 250,000 rows and two failures around 500,000 to 1,000,000 rows. The site appeared normal, but no downloadable CSV was obtained; the queries worked in local development. Exact failure details and logs were not retained. |

Task 11 was submitted at approximately **1:27:46 p.m. PDT on October 12, 2026** (20:27:46 UTC). The screenshot labels the stopped download **1:31 p.m.** Use the request logs' timestamps to establish the exact download attempt window.

Browser download automation also stalled. That alone was not treated as evidence of a server failure; the user's independent stopped-download observation establishes the visible failure. The browser console contained no error explaining the stop. The large CSV's actual contents and row count have not been verified.

## Generation and delivery are separate

The supplied `poi_broker/tasks.py` closes the CSV writer before transitioning the export task to `SUCCESS`. The live page's completed state is consistent with successful generation, but the server file has not been independently inspected.

`poi_broker/routes/export.py:201` handles the separate authenticated `GET /export/download/<task_id>` request. It checks task ownership, `SUCCESS` status, and file existence, then returns Flask `send_file` at line 230. A completed background task therefore does not establish successful HTTP delivery to the browser.

The current configured default is 1,000,000 export rows, with rejection only above that count. The 741,513-row test is below the limit. The earlier deployed version and exact historical counts are unknown.

## Diagnostic follow-up

Before choosing a fix:

1. Inspect task 11 and its server-side CSV: stored status/path, readable file, byte size, and actual data-row count.
2. Correlate the nginx access/error entries for **`GET /export/download/11`** with Gunicorn service logs. Record HTTP status, bytes sent, request duration, and any worker exit, timeout, or connection error.
3. Inspect the effective nginx download/proxy configuration and Gunicorn configuration, including unit overrides or other startup configuration. The supplied files contain no nginx configuration; do not infer its timeout or buffering settings.
4. Reconcile the actual web-process Huey backend/immediate setting and deployed import order with the supplied configuration. Startup logs or process-level inspection can settle this separately from the download failure.
5. If needed, download the same already-generated task 11 CSV in a regular browser to distinguish a client-specific problem. A new large export is not needed for this check.

A Gunicorn/nginx/Python interruption is a hypothesis, not a verified cause. The screenshot's `Stopped` label does not distinguish a server failure from an intermediary, client, or network interruption. Keep the confirmed delivery symptom separate from both the original initialization flag and any proposed remedy.

## Saved session evidence

The local session evidence directory is:

`C:/Users/fgrimps/.codex/visualizations/2026/10/12/01a13198-9563-7bf0-89b0-784cd0bff076/`

It contains `poi-large-export-trace.json`, preview/queued/running/completed screenshots, `poi-large-export-download-stopped.png`, and `poi-large-export-test.md`. These are local session artifacts, not portable repository attachments. This document preserves the relevant observations for repository follow-up.
