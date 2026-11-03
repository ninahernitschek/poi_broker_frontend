# Point of Interest Community Broker

The Point of Interest Community Broker is a transient alert software (Rubin Observatory (LSST) Alert and Community Broker) that is currently tested with the ZTF alert stream.
Incoming alerts will be processed, annotated, classified and forwarded.

For a general overview of LSST Community Broker, see https://www.lsst.org/scientists/alert-brokers


This repository contains the frontend (website) for the Point of Interest broker.
The Point of Interest broker can be found at https://poibroker.uantof.cl/


## Motivation
 
Current and especially upcoming all-sky time-domain surveys, such as LSST, will deliver a vast amount of data each night, requiring for the development of flexible, straightforward tools for the analysis, selection and forwarding of information regarding astrophysical transients and variable objects. 
 
Our alert broker, called *Point of Interest*, is tailored towards the needs of astronomers looking for updated observations of variable stars in specific on-sky regions. Developed by a small team, where I'm the main developer responsible for this project, the *Point of Interest*' alert broker enables users to get updates on variable star observations from a straightforward, user-friendly web service. Data are processed in real time by big data/ machine learning algorithms and will be immediately available to the user community.


*Point of Interest* differs from other brokers in the focus on updates on variable stars, thus running a rather specific than the full analysis chain of streamed data. As a consequence, the broker is rather lightweight. *Point of Interest* users are encouraged to design their own on-sky regions they want receive updates for (such as for planned follow-up campaigns) or select from a list of on-sky regions which are particularly interesting for variable star observers, such as stellar streams, globular clusters and dwarf galaxies.

## Features

The catalog on `/` lists recent alerts. Click a `ztf_object_id` for the light curve, thumbnails, features, observing plan, cross-matches, and classification. Signed-in users also get:

- **Bookmarked filters** — save the current catalog filter URL from the main table and reopen it from the profile page.
- **Default feature-plot columns** — pick up to 10 features on `/settings`; those columns are pre-selected when a feature plot opens.
- **Custom observatories** — add name/lat/lon on `/settings` (timezone is resolved from coordinates). The last observatory used in the observing tool is remembered for the next session.
- **Watchlists** — build a visual query and receive a daily email digest of new matches (`tools/watchlist_digest.py`).
- **Favorites** — star a locus and organize favorites into groups on the profile page.
- **Bulk CSV export** — `/export` uses the same visual query builder. The count is checked first (1,000,000 row cap); the CSV is built in the background (Huey) and the page shows a download link when it is ready. Sign-in required. Local app + worker setup is in `docs/async_export/local_app_and_worker.md`.

## Installation
This repository contains the web frontend, including a small database for testing purposes.

After downloading the package, install all required packages for development:

`python -m pip install -r requirements.txt -r requirements-dev.txt`

Use the regular `requirements.txt` for deployments.

> [!NOTE]
> requirements2.txt shows the output of `pip freeze` and lists exact version numbers of the packages. However, this is mostly for reference. You may not be able to install these exact version on your specific system/environment.

Create a `.env` file in the project root based on the `.env.example` file and adjust the settings as needed.

Database locations are configured through environment variables:

- `ALERTS_DB_PATH`: path to the main alerts SQLite database.
- `USERS_DB_PATH`: path to the users/auth SQLite database.

Both values may be absolute paths or paths relative to the project root. By default the app assumes both files live in a sibling directory named `_broker_db` outside the repository, for example:

```
../_broker_db/ztf_alerts_stream.db
../_broker_db/users.db
```

> [!NOTE]
> If you don't have the database files, you can create them using the alerts and user database schema files found in the `/tools` folder. Sample alerts data can be found in the same directory.

```
sqlite3 alerts.db < alertsdb_schema.sql
sqlite3 users.db < usersdb_schema.sql 
sqlite3 alerts.db < alerts_dump.sql 
```
The main alerts database is intentionally excluded because it is too large to keep inside the repository.


## Usage

Start the web app from the terminal from the **project root** (the folder that **contains** the _sub-folder_ `poi_boker`) with 

`flask --app "poi_broker:create_app()" run --debug`

or 

`flask --app "poi_broker:create_app()" run --no-debug --no-reload`

In the web browser, enter `http://127.0.0.1:5000/` to view the front-ent.

### Watchlist digest (cron)

Daily watchlist emails are sent by `tools/watchlist_digest.py`, which uses its own `tools/.env` (see `tools/.env.example`). That file is **not** the web app `.env`.

The digest rebuilds each watchlist from stored `rules_json` through `create_app()`. It does **not** execute `sql_where` (that column is a display preview only). `create_app()` requires `SECRET_KEY`. If `tools/.env` omits it, the cron job used to crash before processing any watchlists. The script now sets a local CLI placeholder and logs a warning. Prefer copying the web app `SECRET_KEY` into `tools/.env`.

In case the website isn't displayed: do a

`cat app.log`

in your terminal window to see the correct URL.


Also, inspect the browser developer console (F12) to see if there are any errors, e.g. missing includes or JavaScript exceptions.

### Testing, Debugging and Profiling

Use focused tests while developing; commit hooks run lightweight checks only.
Pull requests and pushes to `main` run the full suite in CI. See
[development checks](pre-commit.md) for installation, the optional smoke hook,
CI triggers, and coverage requirements.

Run `pytest` (Fast parallel execution recommended for development)
```
python -m pytest -n 2 -q --durations=15
```

For more detailed testing and coverage requirements, please see `/tests/coverage.md`. Every module should aim for at least 50%-75% code coverage, with a total project goal of 75%+ coverage.

It is highly recommended to debug the app in a capable IDE like VS Code to leverage built-in debugging capabilities.

To set breakpoints in the CLI use pdb or the more modern `breakpoint()` function, for example in a route handler:
```
import pdb
from pprint import pprint
...
pdb.set_trace()
```

Use traceback to output the stack trace of an exception, for example in an error handler:
```
import traceback

try:
    ...
except Exception:
    tb = traceback.format_exc()
    return f"<pre>{tb}</pre>", 500

```


Use the logger class to log output to the app.log file instead of `print`'ing to the console
```
app.logger.info('Info')
app.logger.warning('Warn')
logging.error('Exception occurred', exc_info=True) #or: logging.exception()
```

The app can be profiled like any other python module, e.g.

```
python -m cProfile -o program.prof -m flask --app "poi_broker:create_app()" run --no-reload
```

However, it's more useful to use werkzeug's `ProfilerMiddleware` to profile routes. Activate it in `poi_broker/__init__.py` if needed.
