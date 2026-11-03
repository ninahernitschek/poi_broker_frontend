# Existing JSON response contracts

Inspected 2026-10-14. Mixed response helpers are not, by themselves, an open
runtime defect. Preserve these frontend contracts when changing an endpoint:

| Endpoint family | Success | Error handling |
| --- | --- | --- |
| `/query_features` | JSON object of feature values via `jsonify` | JSON `{error}`; missing alert is intentionally 404 |
| `/query_crossmatches` | JSON array, including `[]`, through `safe_serialize` | Plain-text 400/500; the modal displays `xhr.responseText` through `textContent` |
| Bokeh plot endpoints | JSON `{div, script}`; missing data is a 200 warning payload | Invalid parameters are 400; actual failures are 5xx |
| Observing plot | JSON image/Moon data or a successful visibility message | JSON `{error}` for invalid input/failure |
| CSV download endpoints | CSV response | Endpoint-specific text/status responses |

`safe_serialize` also handles legacy binary/date values through
`serialize_fallback`; replacing it with another JSON encoder requires checking
that data contract. JSON encoding does not replace HTML escaping. The modal's
crossmatch and feature tables bind values through `textContent`; Bokeh payloads
follow a separate controlled embedding path.

A future consistent error envelope would need coordinated endpoint, caller,
and regression-test updates. There is no blanket response-helper rewrite
planned as part of hygiene cleanup. Existing checks live in
`tests/test_helpers.py`, `tests/test_smoke_routes.py`, and
`tests/test_frontend_xss_rendering.py`.
