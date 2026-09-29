const API_BASE = String(window.TOOL1_CONFIG?.apiBaseUrl || "").replace(/\/$/, "");
const SETTINGS_STORAGE_KEY = `tool1_dashboard_settings_v1:${API_BASE}`;
function apiUrl(path) {
  return API_BASE ? `${API_BASE}/${path.replace(/^\//, "")}` : new URL(path.replace(/^\//, ""), window.location.href).href;
}
async function apiFetch(path, options) {
  try { return await fetch(apiUrl(path), options); }
  catch { throw new Error("Cannot reach the Tool1 backend. Check its address and that it is running."); }
}
function displayLabel(value) {
  return String(value).replaceAll("ACDCPF PF", "Tool5 (acdcpf) PF");
}

const TIME_SERIES_GROUPS = {
  losses: {
    label: "Losses",
    elementLabel: "System total",
    metrics: {
      total_active_losses_mw: "Total active loss (MW)",
      delta_mw: "OPF - PF loss (MW)",
    },
  },
  ac_bus_voltages: {
    label: "AC bus voltages",
    elementLabel: "AC bus",
    metrics: {
      v_pu: "Voltage (p.u.)",
      angle_deg: "Angle (deg)",
    },
  },
  dc_bus_voltages: {
    label: "DC bus voltages",
    elementLabel: "DC bus",
    metrics: {
      v_pu: "Voltage (p.u.)",
      v_kv: "Voltage (kV)",
    },
  },
  storage_units: {
    label: "Storage",
    elementLabel: "Storage unit",
    metrics: {
      p_mw: "P (MW)",
      p_charge_mw: "Charge P (MW)",
      p_discharge_mw: "Discharge P (MW)",
      q_mvar: "Q (MVAr)",
      soc_percent: "SOC (%)",
      energy_mwh: "Energy (MWh)",
      loading_percent: "Loading (%)",
    },
  },
};

const state = {
  config: null,
  networkInputs: null,
  selectedTable: null,
  tableEdits: {},
  resultTables: {},
  timeSeries: null,
  timeSeriesSelection: {group: null, element: null, metric: null},
  loadedGridResultId: null,
  pollHandle: null,
};

const byId = (id) => document.getElementById(id);

document.addEventListener("DOMContentLoaded", async () => {
  try {
  await loadConfig();
  wireEvents();
  applySavedSettings();
  await refreshNetworkInputs();
  applySavedTableEdits();
  await refreshStatus();
  renderPendingSummary();
  } catch (error) { byId("statusMessage").textContent = error.message; }
});

async function loadConfig() {
  state.config = await getJson("/api/config");
  populateCases();
  populateScenarios();
  populateControls();
  populateControlScopes();
  updateCaseDependentControls();
}

function wireEvents() {
  byId("importNetworkButton").addEventListener("click", importCustomNetwork);
  byId("customFormat").addEventListener("change", () => {
    byId("customLossRow").classList.toggle("hidden", byId("customFormat").value !== "tool7");
  });
  byId("gridCaseSelect").addEventListener("change", async () => {
    updateCaseDependentControls({preserveUserChoices: false});
    await refreshNetworkInputs();
    renderPendingSummary();
  });
  byId("vscScenarioSelect").addEventListener("change", async () => {
    await refreshNetworkInputs();
    renderPendingSummary();
  });
  byId("profileSelect").addEventListener("change", () => {
    byId("customProfileRow").classList.toggle(
      "hidden",
      byId("profileSelect").value !== "custom_path",
    );
    renderPendingSummary();
  });
  byId("customProfilePath").addEventListener("input", renderPendingSummary);
  byId("useMarginCheck").addEventListener("change", () => {
    byId("marginRow").classList.toggle("hidden", !byId("useMarginCheck").checked);
    renderPendingSummary();
  });
  byId("controlMarginInput").addEventListener("input", renderPendingSummary);
  byId("controlScopeSelect").addEventListener("change", renderPendingSummary);
  byId("skipOpfCheck").addEventListener("change", renderPendingSummary);
  byId("pyflowCheck").addEventListener("change", renderPendingSummary);
  byId("writeExportsCheck").addEventListener("change", renderPendingSummary);
  for (const id of ["markdownCheck", "csvCheck", "xlsxCheck", "htmlCheck"]) {
    byId(id).addEventListener("change", renderPendingSummary);
  }
  byId("runButton").addEventListener("click", submitRun);
  byId("checkLimitsButton").addEventListener("click", refreshLimitInventory);
  byId("refreshStatusButton").addEventListener("click", refreshStatus);
  byId("tableSelect").addEventListener("change", () => {
    state.selectedTable = byId("tableSelect").value;
    saveSettings();
    renderEditableTable();
  });
  byId("resetTableButton").addEventListener("click", async () => {
    clearSavedTableEditsForCurrentCase();
    await refreshNetworkInputs();
    renderPendingSummary();
  });
  byId("resultTableSelect").addEventListener("change", () => {
    renderResultTable(currentResultTables());
  });
  byId("timeSeriesGroupSelect").addEventListener("change", () => {
    state.timeSeriesSelection.group = byId("timeSeriesGroupSelect").value;
    state.timeSeriesSelection.element = null;
    state.timeSeriesSelection.metric = null;
    renderTimeSeriesPanel();
  });
  byId("timeSeriesElementSelect").addEventListener("change", () => {
    state.timeSeriesSelection.element = byId("timeSeriesElementSelect").value;
    renderTimeSeriesPanel();
  });
  byId("timeSeriesMetricSelect").addEventListener("change", () => {
    state.timeSeriesSelection.metric = byId("timeSeriesMetricSelect").value;
    renderTimeSeriesChart();
  });
}

function populateCases() {
  const select = byId("gridCaseSelect");
  select.innerHTML = state.config.cases
    .map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.display_name)}</option>`)
    .join("");
  select.value = state.config.defaults.grid_case;
}

function populateScenarios() {
  const select = byId("vscScenarioSelect");
  select.innerHTML = state.config.vsc_scenarios
    .map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.description)}</option>`)
    .join("");
  select.value = state.config.defaults.vsc_scenario;
}

function populateControls() {
  const container = byId("controlCheckboxes");
  container.innerHTML = state.config.control_tokens
    .map((item) => `
      <label class="inline-check">
        <input type="checkbox" data-control-token="${escapeHtml(item.token)}" />
        ${escapeHtml(item.label)}
      </label>
    `)
    .join("");
  container.querySelectorAll("input").forEach((input) => {
    input.addEventListener("change", renderPendingSummary);
  });
}

function populateControlScopes() {
  const select = byId("controlScopeSelect");
  select.innerHTML = state.config.control_scopes
    .map((item) => `<option value="${escapeHtml(item.key)}">${escapeHtml(item.label)}</option>`)
    .join("");
  select.value = state.config.defaults.control_scope;
}

function updateCaseDependentControls({preserveUserChoices = false} = {}) {
  const selectedCase = activeCase();
  byId("vscScenarioSelect").disabled = Boolean(selectedCase.custom);
  byId("pyflowCheck").disabled = !selectedCase.supports_pyflow;
  if (selectedCase.custom) byId("vscScenarioSelect").value = "original";
  byId("importDetails").textContent = selectedCase.import_report ? JSON.stringify(selectedCase.import_report, null, 2) : "";
  byId("caseDescription").textContent = selectedCase.description;
  if (!preserveUserChoices) {
    byId("skipOpfCheck").checked = Boolean(selectedCase.default_pf_only);
    byId("pyflowCheck").checked = Boolean(selectedCase.supports_pyflow);
    for (const [token, enabled] of Object.entries(selectedCase.default_controls)) {
      const input = document.querySelector(`[data-control-token="${token}"]`);
      if (input) input.checked = Boolean(enabled);
    }
  }
  const profileSelect = byId("profileSelect");
  const previousProfile = preserveUserChoices ? profileSelect.value : "none";
  profileSelect.innerHTML = selectedCase.profiles
    .map((profile) => `<option value="${escapeHtml(profile.key)}">${escapeHtml(profile.label)}</option>`)
    .join("");
  profileSelect.value = Array.from(profileSelect.options).some((option) => option.value === previousProfile)
    ? previousProfile
    : "none";
  byId("customProfileRow").classList.toggle("hidden", profileSelect.value !== "custom_path");
}

async function importCustomNetwork() {
  const button = byId("importNetworkButton");
  button.disabled = true;
  try {
    const ac = byId("customAcFile").files[0];
    const dc = byId("customDcFile").files[0];
    if (!ac) throw new Error("Select an AC Python case file.");
    if (ac.size > 5 * 1024 * 1024 || (dc && dc.size > 5 * 1024 * 1024)) throw new Error("Each case file must be at most 5 MiB.");
    const response = await apiFetch("/api/networks/import", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ac_name: ac.name, ac_text: await ac.text(), dc_name: dc?.name,
        dc_text: dc ? await dc.text() : null, format: dc ? byId("customFormat").value : null,
        loss_units: dc && byId("customFormat").value === "tool7" ? byId("customLossUnits").value : null,
        dc_voltage_convention: byId("customDcVoltage").value}),
    });
    if (!response.ok) throw new Error(await responseText(response));
    const imported = await response.json();
    state.config = await getJson("/api/config");
    populateCases();
    byId("gridCaseSelect").value = imported.import_id;
    updateCaseDependentControls();
    await refreshNetworkInputs();
    renderPendingSummary();
    const counts = imported.report.counts;
    byId("importMessage").textContent = `Imported ${counts.ac_bus} AC buses, ${counts.dc_bus} DC buses, ${counts.vsc} converters. Review the interpreted ratings and warnings in Import details before running. Import success does not establish solve feasibility.`;
    byId("importDetails").textContent = JSON.stringify(imported.report, null, 2);
    byId("importDetails").parentElement.open = true;
  } catch (error) {
    byId("importMessage").textContent = `Import failed: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function refreshNetworkInputs() {
  const caseKey = byId("gridCaseSelect").value;
  const scenario = byId("vscScenarioSelect").value;
  state.networkInputs = await getJson(`/api/network-inputs/${encodeURIComponent(caseKey)}/${encodeURIComponent(scenario)}`);
  state.tableEdits = savedTableEditsForCurrentCase();
  const tableNames = Object.keys(state.networkInputs.tables || {});
  const select = byId("tableSelect");
  select.innerHTML = tableNames
    .map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(state.networkInputs.tables[name].label)}</option>`)
    .join("");
  state.selectedTable = tableNames.includes(state.selectedTable) ? state.selectedTable : tableNames[0] || null;
  if (state.selectedTable) select.value = state.selectedTable;
  byId("tableMessage").textContent = state.networkInputs.message || "";
  renderEditableTable();
}

function renderEditableTable() {
  const container = byId("editableTable");
  if (!state.networkInputs || !state.networkInputs.editable || !state.selectedTable) {
    container.innerHTML = "<p class=\"helper-text\">No editable Tool5 (acdcpf) tables for this case.</p>";
    return;
  }
  const table = state.networkInputs.tables[state.selectedTable];
  const rows = editedRowsForTable(state.selectedTable);
  container.innerHTML = renderTable(rows, {
    editableColumns: new Set(table.editable_columns),
    readOnlyColumns: new Set(["element_id", ...table.read_only_columns]),
    tableName: state.selectedTable,
  });
  container.querySelectorAll("td[contenteditable=true]").forEach((cell) => {
    cell.addEventListener("input", () => {
      updateTableEditFromCell(cell);
      renderPendingSummary();
    });
  });
  container.querySelectorAll("input[data-boolean-cell]").forEach((input) => {
    input.addEventListener("change", () => {
      updateTableEditFromBooleanInput(input);
      renderPendingSummary();
    });
  });
}

function renderPendingSummary() {
  byId("limitMessage").textContent = "Check pending limits to inspect the current inputs, including unsaved edits. This does not run PF or OPF.";
  byId("limitInventory").innerHTML = "";
  const payload = buildRunPayload();
  const controls = selectedControlLabels().join(", ") || "No OPF controls selected";
  const exportFormats = selectedExportFormats().join(", ") || "none selected";
  const edits = collectTableOverrides();
  const editedRows = Object.values(edits).reduce((total, rows) => total + rows.length, 0);
  const rows = [
    ["Grid case", activeCase().display_name],
    ["Run type", payload.skip_opf ? "Tool5 (acdcpf) PF only" : "Tool5 (acdcpf) PF + Tool1 (acdcopf) OPF"],
    ["VSC setpoints", byId("vscScenarioSelect").selectedOptions[0]?.textContent || ""],
    ["Enabled controls", controls],
    ["Control scope", byId("controlScopeSelect").selectedOptions[0]?.textContent || ""],
    ["Control margin", payload.control_margin_percent === null ? "Full technical bounds" : `+/-${payload.control_margin_percent}% of rating`],
    ["Profile", byId("profileSelect").selectedOptions[0]?.textContent || "No profile"],
    ["Operational edits", `${editedRows} changed row(s)`],
    ["PyFlow reference", payload.include_pyflow_reference ? "Enabled when comparable" : "Disabled"],
    ["Exports", payload.write_exports ? exportFormats : "not written"],
  ];
  byId("pendingSummary").innerHTML = rows
    .map(([name, value]) => `
      <div class="summary-item">
        <span>${escapeHtml(name)}</span>
        <strong>${escapeHtml(value)}</strong>
      </div>
    `)
    .join("");
  saveSettings();
}

async function submitRun() {
  const button = byId("runButton");
  button.disabled = true;
  byId("statusMessage").textContent = "Submitting run...";
  try {
    const response = await apiFetch("/api/runs", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(buildRunPayload()),
    });
    if (!response.ok) throw new Error(await responseText(response));
    byId("statusMessage").textContent = "Run submitted. Solver is running in the local backend.";
    startPolling();
    await refreshStatus();
  } catch (error) {
    byId("statusMessage").textContent = `Could not submit run: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function refreshLimitInventory() {
  const button = byId("checkLimitsButton");
  const request = buildRunPayload();
  button.disabled = true;
  byId("limitInventory").innerHTML = "";
  byId("limitMessage").textContent = "Checking pending inputs...";
  try {
    const response = await apiFetch(`/api/network-limits/${encodeURIComponent(request.grid_case)}/${encodeURIComponent(request.vsc_setpoint_scenario)}`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({table_overrides: request.table_overrides}),
    });
    if (!response.ok) throw new Error(await responseText(response));
    const {rows} = await response.json();
    if (JSON.stringify(buildRunPayload()) !== JSON.stringify(request)) {
      byId("limitMessage").textContent = "Inputs changed during the check. Check pending limits again.";
      return;
    }
    const missing = rows.filter((row) => row.status === "missing").length;
    byId("limitMessage").textContent = `${missing} missing limit fields. Inventory is before profile scaling; this is not a feasibility certificate.`;
    byId("limitInventory").innerHTML = renderTable(rows, {});
  } catch (error) {
    byId("limitMessage").textContent = `Could not check limits: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function refreshStatus() {
  const payload = await getJson("/api/runs/current");
  updateStatusPolling(payload.state);
  byId("workerState").textContent = payload.state;
  byId("statusMessage").textContent = payload.last_error || payload.message || "";
  const result = payload.result;
  byId("pfLoss").textContent = formatMw(result?.losses?.pf_mw);
  byId("opfLoss").textContent = formatMw(result?.losses?.opf_mw);
  byId("lossDelta").textContent = formatMw(result?.losses?.delta_mw);
  renderRunStatus(result?.runs || []);
  state.timeSeries = result?.time_series || null;
  renderTimeSeriesPanel();
  renderResultSelector(result?.tables || {});
  updateGridFrame(result);
  byId("downloadLinks").innerHTML = (result?.downloads || []).map(item => `<a href="${escapeHtml(apiUrl(item.url))}" download>${escapeHtml(item.name)}</a>`).join(" &middot; ");
}

function updateGridFrame(result) {
  const frame = byId("gridFrame");
  if (!frame || !result?.grid_html_available || !result.generated_at) return;

  const resultId = String(result.generated_at);
  if (state.loadedGridResultId === resultId && frame.dataset.loadedResultId === resultId) {
    return;
  }

  const targetPath = `/api/runs/current/grid-html?ts=${encodeURIComponent(resultId)}`;
  const targetUrl = apiUrl(targetPath);
  if (frame.src === targetUrl) {
    state.loadedGridResultId = resultId;
    frame.dataset.loadedResultId = resultId;
    return;
  }

  state.loadedGridResultId = resultId;
  frame.dataset.loadedResultId = resultId;
  frame.src = targetUrl;
}

function renderRunStatus(runs) {
  byId("runStatusTable").innerHTML = renderTable(runs.map(({display_label, tool5, ...run}) => ({...run, label: display_label || displayLabel(run.label), policy: tool5?.policy || "", control_changes: tool5?.control_changes?.length || 0})), {});
}

function renderResultSelector(tables) {
  state.resultTables = tables || {};
  const select = byId("resultTableSelect");
  const names = Object.keys(state.resultTables);
  const previousValue = select.value;
  select.innerHTML = names
    .map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name.replaceAll("_", " "))}</option>`)
    .join("");
  if (names.includes(previousValue)) select.value = previousValue;
  renderResultTable(state.resultTables);
}

function renderResultTable(tables) {
  const name = byId("resultTableSelect").value || Object.keys(tables)[0];
  byId("resultTable").innerHTML = name ? renderTable(tables[name] || [], {}) : "";
}

function currentResultTables() {
  return state.resultTables || {};
}

function renderTimeSeriesPanel() {
  const groupSelect = byId("timeSeriesGroupSelect");
  const elementSelect = byId("timeSeriesElementSelect");
  const metricSelect = byId("timeSeriesMetricSelect");
  const availableGroups = Object.keys(TIME_SERIES_GROUPS).filter(hasTimeSeriesGroupData);

  if (!state.timeSeries?.available || !availableGroups.length) {
    groupSelect.innerHTML = "";
    elementSelect.innerHTML = "";
    metricSelect.innerHTML = "";
    byId("timeSeriesChart").replaceChildren();
    byId("timeSeriesLegend").innerHTML = "";
    byId("timeSeriesNote").textContent = "Run a study to see time-series loss, voltage, and storage curves.";
    return;
  }

  const previousGroup = state.timeSeriesSelection.group || groupSelect.value;
  groupSelect.innerHTML = availableGroups
    .map((group) => `<option value="${escapeHtml(group)}">${escapeHtml(TIME_SERIES_GROUPS[group].label)}</option>`)
    .join("");
  state.timeSeriesSelection.group = availableGroups.includes(previousGroup)
    ? previousGroup
    : availableGroups[0];
  groupSelect.value = state.timeSeriesSelection.group;

  const elements = timeSeriesElements(state.timeSeriesSelection.group);
  const previousElement = state.timeSeriesSelection.element || elementSelect.value;
  elementSelect.innerHTML = elements
    .map((element) => `<option value="${escapeHtml(element.id)}">${escapeHtml(element.label)}</option>`)
    .join("");
  state.timeSeriesSelection.element = elements.some((element) => element.id === previousElement)
    ? previousElement
    : elements[0]?.id || null;
  elementSelect.value = state.timeSeriesSelection.element || "";

  const metrics = timeSeriesMetrics(
    state.timeSeriesSelection.group,
    state.timeSeriesSelection.element,
  );
  const previousMetric = state.timeSeriesSelection.metric || metricSelect.value;
  metricSelect.innerHTML = metrics
    .map((metric) => `<option value="${escapeHtml(metric)}">${escapeHtml(metricLabel(state.timeSeriesSelection.group, metric))}</option>`)
    .join("");
  state.timeSeriesSelection.metric = metrics.includes(previousMetric)
    ? previousMetric
    : metrics[0] || null;
  metricSelect.value = state.timeSeriesSelection.metric || "";

  renderTimeSeriesChart();
}

function hasTimeSeriesGroupData(group) {
  if (group === "losses") {
    return (state.timeSeries?.losses || []).some((row) => (
      isFiniteValue(row.pf_loss_mw)
      || isFiniteValue(row.opf_loss_mw)
      || isFiniteValue(row.delta_mw)
    ));
  }
  return Boolean(state.timeSeries?.[group]?.length);
}

function timeSeriesElements(group) {
  if (group === "losses") {
    return [{id: "system", label: "System total"}];
  }

  const spec = TIME_SERIES_GROUPS[group];
  const seen = new Map();
  for (const row of state.timeSeries?.[group] || []) {
    const id = String(row.id ?? row.name ?? "");
    if (!id || seen.has(id)) continue;
    const name = row.name || `${spec.elementLabel} ${id}`;
    const bus = row.bus === null || row.bus === undefined ? "" : ` | bus ${row.bus}`;
    seen.set(id, {id, label: `${name}${bus}`});
  }
  return Array.from(seen.values()).sort((left, right) => left.label.localeCompare(right.label));
}

function timeSeriesMetrics(group, elementId) {
  const spec = TIME_SERIES_GROUPS[group];
  const metrics = Object.keys(spec?.metrics || {});
  return metrics.filter((metric) => timeSeriesMetricHasValues(group, elementId, metric));
}

function timeSeriesMetricHasValues(group, elementId, metric) {
  const series = timeSeriesChartSeries(group, elementId, metric);
  return series.some((line) => line.values.some((point) => isFiniteValue(point.value)));
}

function renderTimeSeriesChart() {
  const svg = byId("timeSeriesChart");
  const legend = byId("timeSeriesLegend");
  const note = byId("timeSeriesNote");
  svg.replaceChildren();
  legend.innerHTML = "";

  const group = state.timeSeriesSelection.group;
  const elementId = state.timeSeriesSelection.element;
  const metric = state.timeSeriesSelection.metric;
  if (!group || !metric) {
    note.textContent = "No numeric time-series values are available for this selection.";
    return;
  }

  const series = timeSeriesChartSeries(group, elementId, metric)
    .filter((line) => line.values.some((point) => isFiniteValue(point.value)));
  const values = series.flatMap((line) => line.values.map((point) => point.value)).filter(isFiniteValue).map(Number);
  if (!series.length || !values.length) {
    note.textContent = "No numeric time-series values are available for this selection.";
    return;
  }

  const chart = {left: 62, right: 28, top: 26, bottom: 58, width: 980, height: 320};
  chart.innerWidth = chart.width - chart.left - chart.right;
  chart.innerHeight = chart.height - chart.top - chart.bottom;
  const timeline = state.timeSeries?.timeline || [];
  const axis = chartAxis(Math.min(...values), Math.max(...values), shouldIncludeZero(metric));
  const xScale = (index) => chart.left + (
    timeline.length <= 1 ? chart.innerWidth / 2 : (chart.innerWidth * index) / (timeline.length - 1)
  );
  const yScale = (value) => chart.top + chart.innerHeight * (1 - (Number(value) - axis.min) / (axis.max - axis.min));

  drawTimeSeriesAxes(svg, chart, timeline, axis, xScale, yScale, metricLabel(group, metric));
  for (const line of series) {
    drawTimeSeriesLine(svg, line, xScale, yScale);
  }

  legend.innerHTML = series
    .map((line) => `<span class="legend-item"><span class="dot" style="background:${line.color}"></span>${escapeHtml(line.label)}</span>`)
    .join("");
  const pointsLabel = timeline.length === 1 ? "1 snapshot" : `${timeline.length} snapshots`;
  note.textContent = `${metricLabel(group, metric)} shown for ${pointsLabel}. Tool5 supplies the PF baseline; Tool1 supplies the optimized result when available.`;
}

function timeSeriesChartSeries(group, elementId, metric) {
  const timeline = state.timeSeries?.timeline || [];
  if (group === "losses") {
    const rows = rowsByTimeIndex(state.timeSeries?.losses || []);
    if (metric === "delta_mw") {
      return [
        {
          label: "Tool1 OPF - Tool5 PF",
          color: "#f97316",
          values: timeline.map((point) => ({
            label: timeLabel(point),
            value: rows.get(String(point.time_index))?.delta_mw,
          })),
        },
      ];
    }
    return [
      {
        label: "Tool5 (acdcpf) PF",
        color: "#2563eb",
        values: timeline.map((point) => ({
          label: timeLabel(point),
          value: rows.get(String(point.time_index))?.pf_loss_mw,
        })),
      },
      {
        label: "Tool1 (acdcopf) OPF",
        color: "#f97316",
        values: timeline.map((point) => ({
          label: timeLabel(point),
          value: rows.get(String(point.time_index))?.opf_loss_mw,
        })),
      },
    ];
  }

  const rows = (state.timeSeries?.[group] || []).filter((row) => String(row.id ?? row.name ?? "") === String(elementId));
  const runs = [
    ["ACDCPF PF", "#2563eb"],
    ["Tool1 (acdcopf) objective", "#f97316"],
  ];
  return runs.map(([runLabel, color]) => {
    const byIndex = rowsByTimeIndex(rows.filter((row) => row.run === runLabel));
    return {
      label: displayLabel(runLabel),
      color,
      values: timeline.map((point) => ({
        label: timeLabel(point),
        value: byIndex.get(String(point.time_index))?.[metric],
      })),
    };
  });
}

function rowsByTimeIndex(rows) {
  const byIndex = new Map();
  for (const row of rows) {
    byIndex.set(String(row.time_index), row);
  }
  return byIndex;
}

function drawTimeSeriesAxes(svg, chart, timeline, axis, xScale, yScale, yLabel) {
  for (const tick of axis.ticks) {
    const y = yScale(tick);
    addSvg(svg, "line", {x1: chart.left, y1: y, x2: chart.width - chart.right, y2: y, class: "chart-grid"});
    addSvg(svg, "text", {x: chart.left - 10, y: y + 4, class: "chart-axis-label", "text-anchor": "end"}).textContent = formatAxis(tick);
  }
  addSvg(svg, "line", {x1: chart.left, y1: chart.top, x2: chart.left, y2: chart.height - chart.bottom, class: "chart-axis"});
  addSvg(svg, "line", {x1: chart.left, y1: chart.height - chart.bottom, x2: chart.width - chart.right, y2: chart.height - chart.bottom, class: "chart-axis"});
  addSvg(svg, "text", {x: chart.left - 48, y: chart.top + 8, class: "chart-axis-label", "text-anchor": "start"}).textContent = yLabel;

  const labelStep = Math.max(1, Math.ceil(timeline.length / 8));
  timeline.forEach((point, index) => {
    if (index % labelStep !== 0 && index !== timeline.length - 1) return;
    const x = xScale(index);
    addSvg(svg, "line", {x1: x, y1: chart.height - chart.bottom, x2: x, y2: chart.height - chart.bottom + 5, class: "chart-axis"});
    addSvg(svg, "text", {x, y: chart.height - 30, class: "chart-axis-label", "text-anchor": "middle"}).textContent = timeLabel(point);
  });
}

function drawTimeSeriesLine(svg, line, xScale, yScale) {
  const path = line.values.reduce((parts, point, index) => {
    if (!isFiniteValue(point.value)) return parts;
    const command = parts.length ? "L" : "M";
    parts.push(`${command} ${xScale(index)} ${yScale(point.value)}`);
    return parts;
  }, []).join(" ");
  if (path) {
    addSvg(svg, "path", {d: path, class: "chart-line", stroke: line.color});
  }
  line.values.forEach((point, index) => {
    if (!isFiniteValue(point.value)) return;
    const circle = addSvg(svg, "circle", {
      cx: xScale(index),
      cy: yScale(point.value),
      r: 4,
      class: "chart-point",
      fill: line.color,
    });
    addSvg(circle, "title", {}).textContent = `${line.label} ${point.label}: ${formatValue(point.value)}`;
  });
}

function chartAxis(rawMin, rawMax, includeZero) {
  let min = Number(rawMin);
  let max = Number(rawMax);
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return {min: 0, max: 1, ticks: [0, 0.25, 0.5, 0.75, 1]};
  }
  if (includeZero) {
    min = Math.min(0, min);
    max = Math.max(0, max);
  }
  if (Math.abs(max - min) < 1e-9) {
    const pad = Math.max(Math.abs(max) * 0.05, 1e-3);
    min -= pad;
    max += pad;
  }
  const range = max - min;
  const pad = range * 0.08;
  const paddedMin = min - pad;
  const paddedMax = max + pad;
  const step = niceStep((paddedMax - paddedMin) / 4);
  const axisMin = Math.floor(paddedMin / step) * step;
  const axisMax = Math.ceil(paddedMax / step) * step;
  const ticks = [];
  for (let value = axisMin; value <= axisMax + step * 0.5; value += step) {
    ticks.push(Math.abs(value) < step * 1e-9 ? 0 : Number(value.toFixed(10)));
  }
  return {min: axisMin, max: axisMax === axisMin ? axisMin + step : axisMax, ticks};
}

function niceStep(rawStep) {
  if (!Number.isFinite(rawStep) || rawStep <= 0) return 1;
  const exponent = Math.floor(Math.log10(rawStep));
  const fraction = rawStep / Math.pow(10, exponent);
  const niceFraction = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return niceFraction * Math.pow(10, exponent);
}

function shouldIncludeZero(metric) {
  return !["v_pu", "v_kv", "angle_deg", "soc_percent"].includes(metric);
}

function metricLabel(group, metric) {
  return TIME_SERIES_GROUPS[group]?.metrics?.[metric] || metric;
}

function timeLabel(point) {
  const text = String(point?.timestamp || point?.time_index || "");
  const match = text.match(/T([0-9]{2}:[0-9]{2})/);
  return match ? match[1] : `t=${point?.time_index ?? ""}`;
}

function isFiniteValue(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value));
}

function addSvg(parent, tag, attributes) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes || {})) {
    element.setAttribute(name, value);
  }
  parent.appendChild(element);
  return element;
}

function buildRunPayload() {
  return {
    grid_case: byId("gridCaseSelect").value,
    vsc_setpoint_scenario: byId("vscScenarioSelect").value,
    profile_key: byId("profileSelect").value,
    profile_path: byId("customProfilePath").value,
    opf_controls: selectedControls(),
    control_device_scope: byId("controlScopeSelect").value,
    control_margin_percent: byId("useMarginCheck").checked ? Number(byId("controlMarginInput").value || 0) : null,
    table_overrides: collectTableOverrides(),
    skip_opf: byId("skipOpfCheck").checked,
    pf_policy: byId("pfPolicy").value,
    include_pyflow_reference: byId("pyflowCheck").checked,
    write_exports: byId("writeExportsCheck").checked,
    include_markdown: byId("markdownCheck").checked,
    include_csv: byId("csvCheck").checked,
    include_xlsx: byId("xlsxCheck").checked,
    include_html: byId("htmlCheck").checked,
  };
}

function selectedControls() {
  const values = {};
  document.querySelectorAll("[data-control-token]").forEach((input) => {
    values[input.dataset.controlToken] = input.checked;
  });
  return values;
}

function selectedControlLabels() {
  return state.config.control_tokens
    .filter((item) => selectedControls()[item.token])
    .map((item) => item.label);
}

function selectedExportFormats() {
  const formats = [];
  if (byId("markdownCheck").checked) formats.push("Markdown");
  if (byId("csvCheck").checked) formats.push("CSV");
  if (byId("xlsxCheck").checked) formats.push("Excel");
  if (byId("htmlCheck").checked) formats.push("HTML");
  return formats;
}

function collectTableOverrides() {
  if (!state.networkInputs?.editable) return {};
  const overrides = {};
  for (const [tableName, table] of Object.entries(state.networkInputs.tables)) {
    const currentRows = editedRowsForTable(tableName);
    const changedRows = currentRows.filter((row, index) => hasRowChanged(row, table.rows[index], table.editable_columns));
    if (changedRows.length) overrides[tableName] = changedRows;
  }
  return overrides;
}

function editedRowsForTable(tableName) {
  const table = state.networkInputs.tables[tableName];
  const edits = new Map((state.tableEdits[tableName] || []).map((row) => [String(row.element_id), row]));
  return table.rows.map((baseRow) => {
    const row = {...baseRow};
    const saved = edits.get(String(baseRow.element_id)) || {};
    for (const column of table.editable_columns) {
      if (Object.hasOwn(saved, column)) row[column] = saved[column];
    }
    return row;
  });
}

function updateTableEditFromCell(cell) {
  const tableName = cell.dataset.table;
  if (!tableName || !state.networkInputs?.tables?.[tableName]) return;
  ensureTableEditRows(tableName);
  state.tableEdits[tableName][Number(cell.dataset.row)][cell.dataset.col] = coerceCellValue(cell.textContent);
}

function updateTableEditFromBooleanInput(input) {
  const tableName = input.dataset.table;
  if (!tableName || !state.networkInputs?.tables?.[tableName]) return;
  ensureTableEditRows(tableName);
  state.tableEdits[tableName][Number(input.dataset.row)][input.dataset.col] = input.checked;
}

function ensureTableEditRows(tableName) {
  state.tableEdits[tableName] = editedRowsForTable(tableName);
}

function applySavedSettings() {
  const saved = loadSavedSettings();
  if (!saved) return;
  if (saved.grid_case?.startsWith("custom_") && !state.config.cases.some((item) => item.key === saved.grid_case)) {
    byId("importMessage").textContent = "Your previous custom network is no longer loaded. Import the case files again after restarting the dashboard.";
    return;
  }

  setSelectValueIfAvailable("gridCaseSelect", saved.grid_case);
  updateCaseDependentControls({preserveUserChoices: true});
  setSelectValueIfAvailable("vscScenarioSelect", saved.vsc_setpoint_scenario);
  setSelectValueIfAvailable("profileSelect", saved.profile_key);
  byId("customProfilePath").value = saved.profile_path || "";
  byId("customProfileRow").classList.toggle("hidden", byId("profileSelect").value !== "custom_path");

  for (const [token, enabled] of Object.entries(saved.opf_controls || {})) {
    const input = document.querySelector(`[data-control-token="${token}"]`);
    if (input) input.checked = Boolean(enabled);
  }
  setSelectValueIfAvailable("controlScopeSelect", saved.control_device_scope);

  byId("useMarginCheck").checked = saved.control_margin_percent !== null && saved.control_margin_percent !== undefined;
  byId("controlMarginInput").value = saved.control_margin_percent ?? 10;
  byId("marginRow").classList.toggle("hidden", !byId("useMarginCheck").checked);

  byId("skipOpfCheck").checked = Boolean(saved.skip_opf);
  byId("pfPolicy").value = saved.pf_policy || "unconstrained";
  byId("pyflowCheck").checked = Boolean(saved.include_pyflow_reference);
  byId("writeExportsCheck").checked = saved.write_exports !== false;
  byId("markdownCheck").checked = saved.include_markdown !== false;
  byId("csvCheck").checked = saved.include_csv !== false;
  byId("xlsxCheck").checked = saved.include_xlsx !== false;
  byId("htmlCheck").checked = saved.include_html !== false;
  state.selectedTable = saved.selected_table || null;
  if (activeCase().custom) {
    byId("vscScenarioSelect").value = "original";
    byId("pyflowCheck").checked = false;
  }
}

function applySavedTableEdits() {
  state.tableEdits = savedTableEditsForCurrentCase();
  renderEditableTable();
}

function saveSettings() {
  if (!state.config) return;
  const current = loadSavedSettings() || {};
  const payload = buildRunPayload();
  const snapshot = {
    ...current,
    ...payload,
    backend_session: state.config.session_id,
    selected_table: state.selectedTable,
    table_edits_by_case: {
      ...(current.table_edits_by_case || {}),
      [settingsCaseKey()]: state.tableEdits,
    },
  };
  try {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(snapshot));
  } catch {
    // Local storage can be disabled in hardened browsers. The dashboard still works without it.
  }
}

function loadSavedSettings() {
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function savedTableEditsForCurrentCase() {
  const saved = loadSavedSettings();
  if (saved?.grid_case?.startsWith("custom_") && saved.backend_session !== state.config.session_id) return {};
  return saved?.table_edits_by_case?.[settingsCaseKey()] || {};
}

function clearSavedTableEditsForCurrentCase() {
  state.tableEdits = {};
  const saved = loadSavedSettings();
  if (!saved?.table_edits_by_case) return;
  delete saved.table_edits_by_case[settingsCaseKey()];
  try {
    localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(saved));
  } catch {
    // Ignore persistence failures; reset still applies to the current in-memory dashboard.
  }
}

function settingsCaseKey() {
  return `${byId("gridCaseSelect").value}::${byId("vscScenarioSelect").value}`;
}

function setSelectValueIfAvailable(id, value) {
  if (value === undefined || value === null) return;
  const select = byId(id);
  if (Array.from(select.options).some((option) => option.value === String(value))) {
    select.value = String(value);
  }
}

function hasRowChanged(current, base, editableColumns) {
  return editableColumns.some((column) => String(current[column] ?? "") !== String(base[column] ?? ""));
}

function renderTable(rows, options) {
  if (!rows || !rows.length) return "<p class=\"helper-text\">No rows to display.</p>";
  const columns = Object.keys(rows[0]);
  const editable = options.editableColumns || new Set();
  const tableName = options.tableName || "";
  return `
    <table>
      <thead>
        <tr>${columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr>
      </thead>
      <tbody>
        ${rows.map((row, rowIndex) => `
          <tr>
            ${columns.map((column) => {
              const canEdit = editable.has(column);
              const cellValue = row[column];
              if (canEdit && isBooleanColumn(column)) {
                return `
                  <td data-table="${escapeHtml(tableName)}" data-row="${rowIndex}" data-col="${escapeHtml(column)}">
                    <label class="switch-cell" title="${truthy(cellValue) ? "In service" : "Out of service"}">
                      <input
                        type="checkbox"
                        data-boolean-cell="true"
                        data-table="${escapeHtml(tableName)}"
                        data-row="${rowIndex}"
                        data-col="${escapeHtml(column)}"
                        ${truthy(cellValue) ? "checked" : ""}
                      />
                      <span>${truthy(cellValue) ? "In service" : "Out"}</span>
                    </label>
                  </td>
                `;
              }
              return `<td ${canEdit ? "contenteditable=\"true\"" : ""} data-table="${escapeHtml(tableName)}" data-row="${rowIndex}" data-col="${escapeHtml(column)}">${escapeHtml(typeof cellValue === "string" ? displayLabel(cellValue) : cellValue)}</td>`;
            }).join("")}
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function isBooleanColumn(column) {
  return column === "in_service" || column === "tap_controllable";
}

function truthy(value) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  return ["true", "1", "yes", "y"].includes(String(value ?? "").trim().toLowerCase());
}

function activeCase() {
  return state.config.cases.find((item) => item.key === byId("gridCaseSelect").value) || state.config.cases[0];
}

function startPolling() {
  if (state.pollHandle) return;
  state.pollHandle = setInterval(() => refreshStatus().catch(error => { byId("statusMessage").textContent = error.message; stopPolling(); }), 2500);
}

function stopPolling() {
  if (!state.pollHandle) return;
  clearInterval(state.pollHandle);
  state.pollHandle = null;
}

function updateStatusPolling(workerState) {
  if (workerState === "queued" || workerState === "running") {
    startPolling();
    return;
  }
  stopPolling();
}

async function getJson(url) {
  const response = await apiFetch(url);
  if (!response.ok) throw new Error(await responseText(response));
  return response.json();
}

async function responseText(response) {
  const text = await response.text();
  try {
    const payload = JSON.parse(text);
    return payload.detail || JSON.stringify(payload);
  } catch {
    return text || `HTTP ${response.status}`;
  }
}

function formatMw(value) {
  return value === null || value === undefined ? "n/a" : `${Number(value).toFixed(4)} MW`;
}

function formatValue(value) {
  if (!isFiniteValue(value)) return "n/a";
  const number = Number(value);
  if (Math.abs(number) >= 1000) return number.toExponential(4);
  if (Math.abs(number) >= 100) return number.toFixed(2);
  if (Math.abs(number) >= 10) return number.toFixed(3);
  return number.toFixed(4);
}

function formatAxis(value) {
  if (!isFiniteValue(value)) return "n/a";
  const number = Number(value);
  if (Math.abs(number) >= 1000) return number.toExponential(1);
  if (Math.abs(number) >= 100) return number.toFixed(0);
  if (Math.abs(number) >= 10) return number.toFixed(1);
  return number.toFixed(3);
}

function coerceCellValue(value) {
  const text = String(value ?? "").trim();
  if (text === "") return null;
  if (["true", "false"].includes(text.toLowerCase())) return text.toLowerCase() === "true";
  const number = Number(text);
  return Number.isFinite(number) ? number : text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
