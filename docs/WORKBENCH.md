# Workbench integration

Tool1 exposes a JSON/HTTP API; Tool5 is its pinned installed PF dependency. Run one backend process per workbench session or mutually trusted single-user workspace. There is one active run and one current result per process, no authentication, no durable job store, and no independent client isolation. The workbench owns routing, authentication, and orchestration. Do not run multiple Uvicorn workers against this in-memory session model.

## Endpoints

- `GET /api/health`: application/backend versions, IPOPT discovery, session ID.
- `GET /api/config`: built-in and imported cases, profiles, control choices, session ID.
- `POST /api/networks/import`: `ac_name`, `ac_text`, optional `dc_name`, `dc_text`, `format` (`standard` or `tool7`), `loss_units` (`physical` or `per_unit`). Returns `import_id` and interpretation report.
- `GET /api/network-inputs/{import_id}/original`: editable tables, source IDs, read-only fields.
- `POST /api/network-limits/{import_id}/original`: inspect pending `table_overrides`.
- `POST /api/runs`: submit a request. Returns `submitted`, not numerical success.
- `GET /api/runs/current`: poll `queued`, `running`, `succeeded`, or `failed`; inspect each run's `success`, `message`, and `physics`.
- `GET /api/runs/current/exports/{filename}`: download only an export listed in the current result's `downloads`.
- `GET /api/runs/current/grid-html`: interactive result report.

Import IDs expire on restart. A second submission while busy returns HTTP 409. Validation errors return 400/422, unavailable IPOPT returns 503, unexpected application errors return 500. Errors contain `detail` and `error` (`code`, `message`, optional `fields`). Solver infeasibility is a completed failed run in the polling response, not an HTTP transport failure.

Use `skip_opf` to request PF-only execution. Older request field spellings are no longer accepted. Use `display_label` for product names. Server file paths in legacy result fields are informational; use `downloads` URLs for retrieval.

## Example flow

```javascript
const base = "http://127.0.0.1:8520";
async function api(path, data) {
  const response = await fetch(base + path, data === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail);
  return body;
}
const imported = await api("/api/networks/import", {
  ac_name: acFile.name, ac_text: await acFile.text()
});
const inputs = await api(`/api/network-inputs/${imported.import_id}/original`);
// Edit only columns listed as editable. Keep element_id; source_* is read-only provenance.
await api("/api/runs", {
  grid_case: imported.import_id, skip_opf: true,
  include_pyflow_reference: false, table_overrides: {}
});
const status = await api("/api/runs/current"); // repeat until terminal state
// Download base + status.result.downloads[i].url after completion.
```

Profiles use `profile_key` from config or `custom_path` plus `profile_path`. A custom path is on the backend machine, not the browser machine; provide it through shared workbench storage. This version does not define the complementary Tool #7 CSV exchange format.

## Routing

For separate origins, set `--cors-origin` to the exact frontend origin (repeat for more origins), or use comma-separated `TOOL1_CORS_ORIGINS`. No wildcard is enabled by default.

For a proxy stripping `/tools/tool1` from requests, start the backend with `--root-path /tools/tool1`. Set the frontend API base URL to `https://workbench.example/tools/tool1`. Returned `/api/...` download paths are relative to that configured base, not to the domain root.

The independent frontend supports `--prefix /demo/tool1` and `--backend-url ...`. Static hosting can edit `config.js` instead; assets use relative paths. Keep a trailing slash at the frontend mount URL.

API-only backend: `tool1-backend`; no dashboard is mounted. Combined convenience: `tool1-dashboard`. OpenAPI describes request bodies. Backend CORS is not an authentication mechanism.
