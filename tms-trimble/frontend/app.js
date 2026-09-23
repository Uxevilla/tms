"use strict";

/* ---------- Utilidades ---------- */

function $(sel, root = document) {
  return root.querySelector(sel);
}

/* ---------- Autenticación (token de sesión multi-tenant) ---------- */

const TOKEN_KEY = "tms_token";
const USER_KEY = "tms_user";
const SUPERADMIN_KEY = "tms_superadmin";

function _storeGet(k) {
  try { var v = localStorage.getItem(k); if (v) return v; } catch (_) {}
  try { return sessionStorage.getItem(k) || ""; } catch (_) { return ""; }
}
function _storeDel(k) {
  try { localStorage.removeItem(k); } catch (_) {}
  try { sessionStorage.removeItem(k); } catch (_) {}
}
function getToken() {
  if (window.TMS_SESSION && window.TMS_SESSION.token) return window.TMS_SESSION.token;
  return _storeGet(TOKEN_KEY);
}
function isSuperadmin() {
  if (window.TMS_SESSION) return !!window.TMS_SESSION.superadmin;
  return _storeGet(SUPERADMIN_KEY) === "1";
}

// Envolver fetch para inyectar el token y manejar 401 (sesión expirada)
const _fetch = window.fetch.bind(window);
window.fetch = function (url, opts = {}) {
  const token = getToken();
  const isLogin = String(url).includes("/api/login");
  if (token && !isLogin) {
    opts = opts || {};
    const h = new Headers(opts.headers || {});
    h.set("Authorization", "Bearer " + token);
    opts.headers = h;
  }
  return _fetch(url, opts).then((res) => {
    if (res.status === 401 && getToken() && !isLogin) logout(false);
    return res;
  });
};

function logout(redirect = true) {
  window.TMS_SESSION = null;
  _storeDel(TOKEN_KEY);
  _storeDel(USER_KEY);
  _storeDel(SUPERADMIN_KEY);
  if (redirect) {
    document.getElementById("login-overlay").hidden = false;
    document.getElementById("app").hidden = true;
  }
}

function boot() {
  if (window.__booted) return;
  window.__booted = true;
  loadHealth();
  loadTerminals();
  loadCategorias();
  loadRemolques();
  loadTarifasPeaje();
  loadHistory();
  loadFleetPositions();
  bindReplay();
  setDefaultReplayRange();
  setTimeout(() => fleetMap?.invalidateSize(), 300);
  (async () => {
    await Promise.all([loadConductores(), loadClientes(), loadVehiculos()]);
    loadHistory();
  })();
  setInterval(() => { loadHistory(); loadTerminals(); loadRemolques(); }, 15000);
  setInterval(() => fetch("/api/sync/files", { method: "POST" }).catch(() => {}), 60000);
}

function restoreSession() {
  if (!getToken()) {
    document.getElementById("login-overlay").hidden = false;
    document.getElementById("app").hidden = true;
    return;
  }
  document.getElementById("login-overlay").hidden = true;
  document.getElementById("app").hidden = false;
  const ul = document.getElementById("user-label");
  if (ul) ul.textContent = (window.TMS_SESSION && window.TMS_SESSION.user) || _storeGet(USER_KEY) || "—";
  const sa = isSuperadmin();
  const navEmp = document.getElementById("nav-empresas");
  if (navEmp) navEmp.hidden = !sa;
  // El super-admin solo gestiona empresas: ocultar las opciones de tenant.
  document.querySelectorAll(".side-item").forEach((b) => {
    if (b.dataset.screen !== "empresas") b.hidden = sa;
  });
  if (sa) {
    showScreen("empresas");
    loadEmpresas();
  } else {
    showScreen("planificador");
    boot();
  }
}

document.getElementById("btn-logout")?.addEventListener("click", () => logout());

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

const maestroCache = {}; // box.id -> { items, headers, rowBuilder, emptyMsg, onRowClick }

function maestroCellText(cell) {
  if (cell == null) return "";
  if (typeof cell === "string" || typeof cell === "number") return String(cell);
  return (cell.textContent || "").trim();
}

/* ---------- AG Grid genérico ---------- */

// Tabla de solo-lectura: rows = array de arrays de string/number.
function dataGrid(headers, rows, opts = {}) {
  if (typeof agGrid === "undefined") return el("p", { class: "muted", text: opts.emptyMsg || "Sin datos." });
  if (!rows || !rows.length) return el("p", { class: "muted", text: opts.emptyMsg || "Sin datos." });
  const host = el("div", { class: "ag-grid-host" });
  const rowData = rows.map((cells) => {
    const o = {};
    (cells || []).forEach((cell, i) => { o["c" + i] = (cell == null ? "" : cell); });
    return o;
  });
  const columnDefs = headers.map((h, i) => ({
    headerName: h,
    colId: "c" + i,
    sortable: true,
    filter: h ? (opts.numCols && opts.numCols.includes(i) ? "agNumberColumnFilter" : "agSetColumnFilter") : false,
    resizable: true,
    ...(opts.widths && opts.widths[i] ? { width: opts.widths[i] } : {}),
    ...(opts.numCols && opts.numCols.includes(i) ? { type: "rightAligned" } : {}),
  }));
  agGrid.createGrid(host, {
    theme: agGrid.themeQuartz,
    rowData,
    columnDefs,
    defaultColDef: { resizable: true, sortable: true, filter: true, minWidth: 80 },
    domLayout: "autoHeight",
    overlayNoRowsTemplate: `<span class="empty" style="padding:16px">${opts.emptyMsg || "Sin datos."}</span>`,
  });
  return host;
}

// Tabla con celdas construidas por función (admite botones/badges con listeners).
function builderGrid(headers, items, rowBuilder, onRowClick, emptyMsg = "Sin registros.") {
  if (typeof agGrid === "undefined") return el("p", { class: "muted", text: emptyMsg });
  if (!items || !items.length) return el("p", { class: "muted", text: emptyMsg });
  const host = el("div", { class: "ag-grid-host" });
  const columnDefs = headers.map((h, i) => ({
    headerName: h,
    colId: "m" + i,
    sortable: true,
    filter: h ? "agSetColumnFilter" : false,
    resizable: true,
    valueGetter: (p) => maestroCellText(rowBuilder(p.data)[i]),
    cellRenderer: (p) => {
      const cell = rowBuilder(p.data)[i];
      return (cell && cell.nodeType) ? cell : (cell == null ? "" : String(cell));
    },
  }));
  agGrid.createGrid(host, {
    theme: agGrid.themeQuartz,
    rowData: items,
    columnDefs,
    defaultColDef: { resizable: true, sortable: true, filter: true, minWidth: 80 },
    domLayout: "autoHeight",
    overlayNoRowsTemplate: '<span class="empty" style="padding:16px">Sin registros.</span>',
    ...(onRowClick ? { onRowClicked: (e) => {
      if (e.event && e.event.target && e.event.target.closest && e.event.target.closest("button,a,input,select,textarea")) return;
      onRowClick(e.data);
    } } : {}),
  });
  return host;
}

function renderMaestroTable(box) {
  const c = maestroCache[box.id];
  if (!c) return;
  box.innerHTML = "";
  if (!c.items.length) { box.innerHTML = `<p class="muted">${c.emptyMsg || "Sin registros."}</p>`; return; }
  box.append(builderGrid(c.headers, c.items, c.rowBuilder, c.onRowClick));
}

function maestroTable(box, headers, items, rowBuilder, emptyMsg, onRowClick) {
  if (!box) return;
  if (!items.length) { box.innerHTML = `<p class="muted">${emptyMsg || "Sin registros."}</p>`; delete maestroCache[box.id]; return; }
  maestroCache[box.id] = { items, headers, rowBuilder, emptyMsg, onRowClick };
  renderMaestroTable(box);
}

function delBtn(onClick) {
  const b = el("button", { type: "button", class: "btn-remove", text: "✕", title: "Quitar" });
  b.addEventListener("click", onClick);
  return b;
}

const editState = {};

function formFill(pairs) {
  pairs.forEach(([sel, val]) => { const e = $(sel); if (e) e.value = (val == null ? "" : val); });
}

function setEditBtn(btnId, editing, addLabel) {
  const b = $(btnId);
  if (!b) return;
  b.textContent = editing ? "Guardar cambios" : addLabel;
  b.classList.toggle("btn-primary", editing);
}

/* ---------- Direcciones ---------- */

const ADDRESS_FIELDS = [
  { cls: "f-nombre",  label: "Nombre" },
  { cls: "f-empresa", label: "Empresa" },
  { cls: "f-ciudad",  label: "Ciudad *", required: true },
  { cls: "f-cp",      label: "Código postal" },
  { cls: "f-calle",   label: "Calle" },
  { cls: "f-numero",  label: "Número" },
  { cls: "f-pais",    label: "País", value: "ES" },
];

function addressFieldsHTML() {
  const fields = ADDRESS_FIELDS.map((f) => `
    <label>
      <span>${f.label}</span>
      <input type="text" class="${f.cls}" ${f.value ? `value="${f.value}"` : ""}
             placeholder="${f.label.replace(" *", "")}" ${f.required ? "required" : ""}>
    </label>`).join("");

  return `
    <div class="geosearch">
      <label><span>Buscar lugar</span>
        <input type="text" class="f-search" placeholder="Ej: Puerta del Sol, Madrid" autocomplete="off">
        <div class="geosearch-results" hidden></div>
      </label>
    </div>
    <label style="display:block; margin: 8px 0 4px;"><span>Tipo de actividad</span>
      <select class="f-actividad">
        <option value="DESCARGA">Descarga</option>
        <option value="CARGA">Carga</option>
        <option value="REPOSTAJE">Repostaje</option>
        <option value="ITV">ITV</option>
        <option value="LAVADO">Lavado</option>
        <option value="CAMBIO DE REMOLQUE">Cambio de remolque</option>
      </select>
    </label>
    <div class="grid">
      ${fields}
      <label><span>Latitud</span><input type="number" step="any" class="f-lat" placeholder="40.4168"></label>
      <label><span>Longitud</span><input type="number" step="any" class="f-lng" placeholder="-3.7038"></label>
    </div>
    <div class="grid">
      <label><span>Fecha/hora inicio (planificada)</span><input type="datetime-local" class="f-fecha-inicio"></label>
      <label><span>Fecha/hora fin (planificada)</span><input type="datetime-local" class="f-fecha-fin"></label>
    </div>
    <label class="parada-comentario">Comentarios / Especificaciones
      <textarea class="f-comentario" rows="2" placeholder="Ej: contactar al jefe de muelle, descargar por el portón B..."></textarea>
    </label>`;
}

function localISOtoUTC(v) {
  // Convierte un valor "YYYY-MM-DDTHH:MM[:SS]" (hora LOCAL del operador) a ISO UTC.
  if (!v) return "";
  const m = String(v).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!m) return "";
  const [, Y, Mo, D, H, Mi, S] = m;
  const d = new Date(+Y, +Mo - 1, +D, +H, +Mi, +(S || 0), 0);
  return isNaN(d) ? "" : d.toISOString();
}

function readAddress(container) {
  const pick = (cls) => $(`.${cls}`, container)?.value.trim() || "";
  const num = (cls) => {
    const v = pick(cls);
    return v === "" ? null : Number(v);
  };
  return {
    nombre:  pick("f-nombre"),
    empresa: pick("f-empresa"),
    calle:   pick("f-calle"),
    numero:  pick("f-numero"),
    ciudad:  pick("f-ciudad"),
    cp:      pick("f-cp"),
    pais:    pick("f-pais") || "ES",
    lat:     num("f-lat"),
    lng:     num("f-lng"),
    comentario: pick("f-comentario"),
    actividad: pick("f-actividad"),
    fecha_inicio: localISOtoUTC(pick("f-fecha-inicio")),
    fecha_fin:    localISOtoUTC(pick("f-fecha-fin")),
  };
}

function readParada(item) {
  return readAddress(item);
}

function formatAddress(addr) {
  if (!addr) return "—";
  if (typeof addr === "string") return addr;
  if (typeof addr !== "object") return "—";
  const parts = [
    addr.nombre,
    [addr.calle, addr.numero].filter(Boolean).join(" "),
    addr.ciudad,
    addr.cp,
    addr.pais,
  ].filter(Boolean);
  return parts.join(", ") || "—";
}

function formatDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return isNaN(d) ? String(value) : d.toLocaleString("es-ES");
}

/* ---------- Búsqueda de lugares (geocoding vía /api/geocode) ---------- */

function setupGeosearch(container) {
  const input = $(".f-search", container);
  const box = $(".geosearch-results", container);
  if (!input || !box) return;

  let timer = null;
  let results = [];
  let active = -1;

  const setField = (cls, val) => {
    const f = $(`.${cls}`, container);
    if (f) f.value = val ?? "";
  };

  const apply = (r) => {
    setField("f-nombre", r.nombre);
    setField("f-calle", r.calle);
    setField("f-numero", r.numero);
    setField("f-ciudad", r.ciudad);
    setField("f-cp", r.cp);
    setField("f-pais", r.pais || "ES");
    setField("f-lat", r.lat ?? "");
    setField("f-lng", r.lng ?? "");
    input.value = r.display_name || "";
    box.hidden = true;
    box.innerHTML = "";
    results = [];
  };

  const render = (items) => {
    results = items;
    active = -1;
    box.innerHTML = "";
    if (!items.length) {
      box.innerHTML = '<div class="geosearch-empty">Sin resultados</div>';
      box.hidden = false;
      return;
    }
    items.forEach((r, i) => {
      const div = el("div", { class: "geosearch-item", text: r.display_name || "" });
      div.addEventListener("mousedown", (e) => { e.preventDefault(); apply(r); });
      box.append(div);
    });
    box.hidden = false;
  };

  const search = async () => {
    const q = input.value.trim();
    if (q.length < 3) { box.hidden = true; box.innerHTML = ""; return; }
    try {
      const res = await fetch(`/api/geocode?q=${encodeURIComponent(q)}&limit=5`);
      const data = await res.json();
      render(Array.isArray(data?.resultados) ? data.resultados : []);
    } catch (_) {
      render([]);
    }
  };

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(search, 400);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { box.hidden = true; return; }
    const items = box.querySelectorAll(".geosearch-item");
    if (!items.length) return;
    if (e.key === "ArrowDown") { e.preventDefault(); active = (active + 1) % items.length; }
    else if (e.key === "ArrowUp") { e.preventDefault(); active = (active - 1 + items.length) % items.length; }
    else if (e.key === "Enter") {
      e.preventDefault();
      const r = results[active >= 0 ? active : 0];
      if (r) apply(r);
    } else { return; }
    items.forEach((it, i) => it.classList.toggle("active", i === active));
  });

  document.addEventListener("click", (e) => {
    if (!container.contains(e.target)) box.hidden = true;
  });
}

/* ---------- Montaje de direcciones ---------- */

const origenEl = $('[data-address="origen"]');
origenEl.innerHTML = addressFieldsHTML();
$(".f-actividad", origenEl).value = "CARGA";
setupGeosearch(origenEl);

const destinoEl = $('[data-address="destino"]');
destinoEl.innerHTML = addressFieldsHTML();
setupGeosearch(destinoEl);

/* ---------- Paradas dinámicas ---------- */

function addParada() {
  const item = el("div", { class: "parada" });
  item.innerHTML = `
    <div class="parada-head">
      <span class="parada-title">Parada</span>
      <button type="button" class="btn btn-ghost btn-sm btn-map-parada" title="Fijar esta parada en el mapa">🎯</button>
      <button type="button" class="btn-remove" title="Quitar parada" aria-label="Quitar parada">✕</button>
    </div>
    ${addressFieldsHTML()}`;
  $(".btn-remove", item).addEventListener("click", () => item.remove());
  $(".btn-map-parada", item).addEventListener("click", () => setMapTarget("parada", item));
  setupGeosearch(item);
  $("#paradas-list").append(item);
  renumberParadas();
  return item;
}

function renumberParadas() {
  const items = $("#paradas-list").children;
  for (let i = 0; i < items.length; i++) {
    $(".parada-title", items[i]).textContent = `Parada ${i + 1}`;
  }
}

$("#btn-add-parada").addEventListener("click", addParada);
addParada(); // una parada inicial

/* ---------- Documentos (DMS) ---------- */

let selectedDocs = [];
let storedDocs = [];
const docsInput = $("#asig-docs-input");
const docsList = $("#asig-docs-list");

function fmtSize(bytes) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${(bytes / 1024).toFixed(1)} KB`;
}

function renderDocs() {
  docsList.innerHTML = "";
  const items = [];
  storedDocs.forEach((d, i) => {
    items.push({
      nombre: d.nombre, size: d.size,
      remove: async () => {
        await fetch(`/api/trips/${encodeURIComponent(asignarTripId)}/documentos/${d.id}`, { method: "DELETE" }).catch(() => {});
        storedDocs.splice(i, 1);
        renderDocs();
      },
    });
  });
  selectedDocs.forEach((f, i) => {
    items.push({ nombre: f.name, size: f.size, remove: () => { selectedDocs.splice(i, 1); renderDocs(); } });
  });
  if (!items.length) {
    docsList.innerHTML = '<p class="muted">Sin documentos.</p>';
    return;
  }
  items.forEach((it) => {
    const item = el("div", { class: "doc-item" },
      el("span", { class: "doc-name", text: `${it.nombre} (${fmtSize(it.size)})` }),
      el("button", { type: "button", class: "btn-remove", text: "✕", title: "Quitar" }),
    );
    $(".btn-remove", item).addEventListener("click", () => it.remove());
    docsList.append(item);
  });
}

const MAX_DOC_MB = 2; // límite por PDF en el DMS de Trimble

docsInput.addEventListener("change", () => {
  for (const f of docsInput.files) {
    if (f.size > MAX_DOC_MB * 1024 * 1024) {
      showError(`El documento «${f.name}» pesa ${(f.size / 1024 / 1024).toFixed(1)} MB. `
        + `Trimble admite hasta ${MAX_DOC_MB} MB por PDF. Comprímelo o redúcelo.`);
      continue;
    }
    selectedDocs.push(f);
  }
  docsInput.value = "";
  renderDocs();
});

let pedidoDocs = [];
const pedidoDocsInput = $("#pedido-docs-input");
const pedidoDocsList = $("#pedido-docs-list");

function renderPedidoDocs() {
  if (!pedidoDocsList) return;
  pedidoDocsList.innerHTML = "";
  if (!pedidoDocs.length) {
    pedidoDocsList.innerHTML = '<p class="muted">Sin documentos.</p>';
    return;
  }
  pedidoDocs.forEach((f, i) => {
    const sizeTxt = fmtSize(f.size);
    const item = el("div", { class: "doc-item" },
      el("span", { class: "doc-name", text: `${f.name} (${sizeTxt})` }),
      el("button", { type: "button", class: "btn-remove", text: "✕", title: "Quitar" }),
    );
    $(".btn-remove", item).addEventListener("click", () => { pedidoDocs.splice(i, 1); renderPedidoDocs(); });
    pedidoDocsList.append(item);
  });
}

pedidoDocsInput?.addEventListener("change", () => {
  for (const f of pedidoDocsInput.files) {
    if (f.size > MAX_DOC_MB * 1024 * 1024) {
      showError(`El documento «${f.name}» pesa más de ${MAX_DOC_MB} MB. Comprímelo.`);
      continue;
    }
    pedidoDocs.push(f);
  }
  pedidoDocsInput.value = "";
  renderPedidoDocs();
});
renderPedidoDocs();

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function loadTerminals() {
  const sel = $('[name="asig-terminal"]');
  if (!sel) return;
  const prev = sel.value;
  try {
    const res = await fetch("/api/terminals");
    const data = await res.json();
    const terminales = data?.terminales || [];
    sel.innerHTML = "";
    if (!terminales.length) {
      sel.append(el("option", { value: "", text: "Sin terminales disponibles" }));
      return;
    }
    let enCurso = new Set();
    try {
      const r2 = await fetch("/api/vehiculos/en-curso");
      const d2 = await r2.json();
      enCurso = new Set(d2?.en_curso || []);
    } catch (_) {}
    terminales.forEach((t) => {
      const opt = el("option", { value: t.id, text: (t.name || t.id) + (enCurso.has(t.id) ? " (en uso)" : "") });
      if (enCurso.has(t.id)) opt.disabled = true;
      sel.append(opt);
    });
    const opts = [...sel.options];
    if (prev && opts.some((o) => o.value === prev && !o.disabled)) sel.value = prev;
    else if (opts.some((o) => o.value === "APP_EUSEBIO" && !o.disabled)) sel.value = "APP_EUSEBIO";
  } catch (_) {
    sel.innerHTML = '<option value="">Error al cargar terminales</option>';
  }
}

let conductoresCache = [];
let vehiculosCache = [];
let clientesCache = [];
let transportistasCache = [];
let tarifasCache = {};

const PEAJE_LABELS = {
  ligero: "Ligero",
  pesado2: "Pesado 2 ejes",
  pesado3: "Pesado 3 ejes",
  pesado4: "Pesado 4+ ejes",
};

const CATEGORIA_LABELS = {
  tractora: "Tractora",
  rigido: "Rígido",
  ligero: "Ligero",
  turismo: "Turismo",
  semirremolque: "Semirremolque",
  remolque: "Remolque",
};

async function loadTarifasPeaje() {
  const box = $("#tarifas-peaje-list");
  if (!box) return;
  try {
    const res = await fetch("/api/tarifas-peaje");
    const d = await res.json();
    const tarifas = d.tarifas || [];
    tarifasCache = {};
    tarifas.forEach((t) => { tarifasCache[t.categoria] = t.eur_km; });
    maestroTable(box, ["Categoría", "€/km", ""], tarifas, (t) => {
      const input = el("input", { type: "number", step: "0.01", min: "0", value: String(t.eur_km) });
      const saveBtn = el("button", { type: "button", class: "btn btn-ghost", text: "Guardar" });
      saveBtn.addEventListener("click", async () => {
        const val = parseFloat(input.value) || 0;
        await fetch("/api/tarifas-peaje", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ categoria: t.categoria, eur_km: val }),
        }).catch(() => {});
        loadTarifasPeaje();
      });
      return [t.label, input, saveBtn];
    }, "Sin tarifas.");
  } catch (_) {
    box.innerHTML = '<p class="muted">No se pudieron cargar las tarifas.</p>';
  }
}

async function loadConductores() {
  const sel = $('[name="asig-conductor"]');
  const list = $("#conductores-list");
  try {
    const [localRes, trimRes] = await Promise.all([
      fetch("/api/conductores").then((r) => r.json()).catch(() => ({})),
      fetch("/api/drivers").then((r) => r.json()).catch(() => ({})),
    ]);
    const local = localRes.conductores || [];
    const trimble = trimRes.conductores || [];
    const merged = [];
    const cacheSeen = new Set();
    local.forEach((c) => { if (c.nombre && !cacheSeen.has(c.nombre)) { cacheSeen.add(c.nombre); merged.push({ nombre: c.nombre, id: c.id }); } });
    trimble.forEach((c) => { if (c.nombre && !cacheSeen.has(c.nombre)) { cacheSeen.add(c.nombre); merged.push({ nombre: c.nombre, id: null }); } });
    conductoresCache = merged;
    if (sel) {
      const prev = sel.value;
      sel.innerHTML = '<option value="">— Seleccionar conductor —</option>';
      const seen = new Set();
      const add = (nombre) => {
        if (!nombre || seen.has(nombre)) return;
        seen.add(nombre);
        sel.append(el("option", { value: nombre, text: nombre }));
      };
      local.forEach((c) => add(c.nombre));
      trimble.forEach((c) => add(c.nombre));
      if (prev && seen.has(prev)) sel.value = prev;
    }
    if (list) {
      if (!local.length) {
        list.innerHTML = '<p class="muted">Sin conductores locales registrados.</p>';
      } else {
        maestroTable(list, ["Nombre", "DNI", "Teléfono", "Email", "RRHH", ""], local, (c) => [
          c.nombre,
          c.dni || "—",
          c.telefono || "—",
          c.email || "—",
          c.empleado_id ? "✓" : "—",
          delBtn(async () => {
            await fetch(`/api/conductores/${c.id}`, { method: "DELETE" }).catch(() => {});
            loadConductores();
          }),
        ], "Sin conductores locales registrados.", (c) => {
          editState.conductor = c;
          formFill([["#con-nombre", c.nombre], ["#con-dni", c.dni], ["#con-telefono", c.telefono], ["#con-email", c.email]]);
          setEditBtn("#btn-add-conductor", true, "Añadir");
        });
      }
    }
  } catch (_) {
    if (list) list.innerHTML = '<p class="muted">No se pudieron cargar los conductores.</p>';
    if (sel) sel.innerHTML = '<option value="">Error al cargar conductores</option>';
  }
}

let categoriasCuenta = {};

async function loadCategorias() {
  const sel = $('[name="gasto-categoria"]');
  if (!sel) return;
  try {
    const res = await fetch("/api/categorias");
    const d = await res.json();
    const cats = d.categorias || [];
    categoriasCuenta = {};
    cats.forEach((c) => { categoriasCuenta[c.nombre] = c.cuenta || ""; });
    const prev = sel.value;
    sel.innerHTML = '<option value="">— Seleccionar —</option>';
    cats.forEach((c) => sel.append(el("option", { value: c.nombre, text: c.nombre })));
    if (prev && cats.some((c) => c.nombre === prev)) sel.value = prev;
  } catch (_) {
    sel.innerHTML = '<option value="">Error</option>';
  }
}

async function loadCuentasGasto() {
  const sel = $('[name="gasto-cuenta"]');
  if (!sel) return;
  try {
    const res = await fetch("/api/contabilidad/cuentas");
    const d = await res.json();
    const cuentas = (d.cuentas || []).filter((c) => c.grupo === "6");
    const prev = sel.value;
    sel.innerHTML = '<option value="">— Automática —</option>';
    cuentas.forEach((c) => sel.append(el("option", { value: c.codigo, text: `${c.codigo} · ${c.nombre}` })));
    if (prev) sel.value = prev;
  } catch (_) { sel.innerHTML = '<option value="">— Automática —</option>'; }
}

$('[name="gasto-categoria"]')?.addEventListener("change", () => {
  const cat = $('[name="gasto-categoria"]')?.value;
  const sel = $('[name="gasto-cuenta"]');
  if (sel && cat && categoriasCuenta[cat]) sel.value = categoriasCuenta[cat];
});

async function loadClientes() {
  const sel = $('[name="cliente"]');
  const list = $("#clientes-list");
  try {
    const res = await fetch("/api/clientes");
    const d = await res.json();
    const clientes = d.clientes || [];
    clientesCache = clientes;
    if (sel) {
      const prev = sel.value;
      sel.innerHTML = '<option value="">— Seleccionar —</option>';
      clientes.forEach((c) => sel.append(el("option", { value: String(c.id), text: c.nombre })));
      if (prev) sel.value = prev;
    }
    if (list) {
      if (!clientes.length) {
        list.innerHTML = '<p class="muted">Sin clientes registrados.</p>';
      } else {
        maestroTable(list, ["Nombre", "CIF", "Dirección", "Población", "Teléfono", "Email", ""], clientes, (c) => [
          c.nombre,
          c.cif || "—",
          c.direccion || "—",
          c.poblacion || "—",
          c.telefono || "—",
          c.email || "—",
          delBtn(async () => {
            await fetch(`/api/clientes/${c.id}`, { method: "DELETE" }).catch(() => {});
            loadClientes();
          }),
        ], "Sin clientes registrados.", (c) => {
          editState.cliente = c;
          formFill([["#cli-nombre", c.nombre], ["#cli-cif", c.cif], ["#cli-direccion", c.direccion],
                    ["#cli-poblacion", c.poblacion], ["#cli-telefono", c.telefono], ["#cli-email", c.email]]);
          setEditBtn("#btn-add-cliente", true, "Añadir");
        });
      }
    }
  } catch (_) {
    if (list) list.innerHTML = '<p class="muted">No se pudieron cargar los clientes.</p>';
  }
}

async function loadVehiculos() {
  const datalist = $("#terminales-list");
  const list = $("#vehiculos-list");
  try {
    const res = await fetch("/api/terminals");
    const d = await res.json();
    const terminales = d.terminales || [];
    if (datalist) {
      datalist.innerHTML = "";
      terminales.forEach((t) => datalist.append(el("option", { value: t.id, label: t.name || t.id })));
    }
  } catch (_) {}
  try {
    const res = await fetch("/api/vehiculos");
    const d = await res.json();
    const vehiculos = d.vehiculos || [];
    vehiculosCache = vehiculos;
    populateReplayVehicles();
    if (list) {
      if (!vehiculos.length) {
        list.innerHTML = '<p class="muted">Sin vehículos registrados.</p>';
      } else {
        maestroTable(list, ["ID", "Categoría", "Matrícula", "Marca/Modelo", "Año", "ITV", "Seguro", "Ejes", "MMA", "EURO", "Estado", ""], vehiculos, (v) => {
          const cat = CATEGORIA_LABELS[v.categoria] || v.categoria || "—";
          const marcaModelo = [v.marca, v.modelo].filter(Boolean).join(" ");
          const estado = v.disponible === false
            ? el("span", { class: "badge badge-warn", text: "en uso" })
            : el("span", { class: "badge badge-ok", text: "libre" });
          return [
            v.id || "—", cat, v.matricula || "—", marcaModelo || "—",
            v.anno || "—", v.itv || "—", v.seguro || "—",
            v.ejes || "—", v.mma || "—", v.clase_euro || "—", estado,
            delBtn(async () => {
              await fetch(`/api/vehiculos/${encodeURIComponent(v.id)}`, { method: "DELETE" }).catch(() => {});
              loadVehiculos(); loadRemolques();
            }),
          ];
        }, "Sin vehículos registrados.", (v) => {
          formFill([
            ["#veh-id", v.id], ["#veh-categoria", v.categoria], ["#veh-matricula", v.matricula],
            ["#veh-marca", v.marca], ["#veh-modelo", v.modelo], ["#veh-anno", v.anno],
            ["#veh-itv", v.itv], ["#veh-seguro", v.seguro], ["#veh-peaje", v.peaje_categoria],
            ["#veh-ptv-profile", v.ptv_profile], ["#veh-ejes", v.ejes], ["#veh-mma", v.mma],
            ["#veh-clase-euro", v.clase_euro], ["#veh-cap-peso", v.capacidad_peso], ["#veh-cap-palets", v.capacidad_palets],
          ]);
        });
      }
    }
  } catch (_) {
    if (list) list.innerHTML = '<p class="muted">No se pudieron cargar los vehículos.</p>';
  }
  loadMantenimientos();
  loadAvisosTaller();
  loadAlertas();
  loadTransportistas();
  loadLiquidaciones();
}

async function loadMantenimientos() {
  const sel = $("#mant-vehiculo");
  if (sel) {
    const prev = sel.value;
    sel.innerHTML = '<option value="">— Seleccionar —</option>';
    (vehiculosCache || []).forEach((v) => sel.append(el("option", { value: v.id, text: v.id + (v.matricula ? " · " + v.matricula : "") })));
    if (prev) sel.value = prev;
  }
  const box = $("#mantenimientos-list");
  if (!box) return;
  try {
    const res = await fetch("/api/mantenimientos");
    const d = await res.json();
    const mants = d.mantenimientos || [];
    box.innerHTML = "";
    if (!mants.length) { box.innerHTML = '<p class="muted">Sin mantenimientos registrados.</p>'; return; }
    maestroTable(box, ["Vehículo", "Tipo", "Fecha", "Km", "Coste", "Notas", "Hecho", "", ""], mants, (m) => {
      const toggleBtn = el("button", { type: "button", class: "btn-remove", text: m.hecho ? "↺" : "✓", title: "Marcar hecho/pendiente" });
      toggleBtn.addEventListener("click", async () => {
        await fetch(`/api/mantenimientos/${m.id}`, { method: "PATCH" }).catch(() => {});
        loadMantenimientos();
      });
      return [
        m.vehiculo_id || "—",
        m.tipo || "—",
        m.fecha || "—",
        m.km ? String(m.km) : "—",
        m.coste ? formatEUR(m.coste) : "—",
        m.notas || "—",
        m.hecho ? "✓" : "—",
        toggleBtn,
        delBtn(async () => {
          await fetch(`/api/mantenimientos/${m.id}`, { method: "DELETE" }).catch(() => {});
          loadMantenimientos();
        }),
      ];
    }, "Sin mantenimientos registrados.", (m) => {
      editState.mantenimiento = m;
      formFill([["#mant-vehiculo", m.vehiculo_id], ["#mant-tipo", m.tipo], ["#mant-fecha", m.fecha],
                ["#mant-km", m.km], ["#mant-coste", m.coste], ["#mant-notas", m.notas]]);
      setEditBtn("#btn-add-mant", true, "Añadir");
    });
  } catch (_) {
    box.innerHTML = '<p class="muted">No se pudieron cargar los mantenimientos.</p>';
  }
}

function loadAvisosTaller() {
  const box = $("#avisos-taller");
  if (!box) return;
  const hoy = new Date();
  const avisos = [];
  (vehiculosCache || []).forEach((v) => {
    [["ITV", v.itv], ["Seguro", v.seguro]].forEach(([tipo, fecha]) => {
      if (!fecha) return;
      const d = new Date(fecha);
      if (isNaN(d)) return;
      const dias = Math.ceil((d - hoy) / 86400000);
      if (dias < 0) avisos.push(`⚠️ ${v.id}: ${tipo} VENCIDO (${fecha})`);
      else if (dias <= 30) avisos.push(`🟡 ${v.id}: ${tipo} vence en ${dias} días (${fecha})`);
    });
  });
  box.innerHTML = avisos.length
    ? `<div class="alert" style="background:#fffbeb;border:1px solid #fde047;color:#a16207;">${avisos.map((a) => `<div>${a}</div>`).join("")}</div>`
    : "";
}

function sevBadge(sev) {
  const s = (sev || "").toUpperCase();
  const c = s === "ALTA" ? "#dc2626" : s === "MEDIA" ? "#d97706" : s === "BAJA" ? "#16a34a" : "#6b7280";
  return el("span", { style: `display:inline-block;background:${c};color:#fff;padding:2px 9px;border-radius:10px;font-size:11px;font-weight:600;`, text: sev || "—" });
}

function estadoBadge(est) {
  const e = est || "abierta";
  const c = e === "abierta" ? "#dc2626" : e === "resuelta" ? "#16a34a" : "#6b7280";
  return el("span", { style: `display:inline-block;background:${c};color:#fff;padding:2px 9px;border-radius:10px;font-size:11px;font-weight:600;`, text: e });
}

async function patchAlerta(id, estado) {
  await fetch(`/api/alertas/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ estado }) }).catch(() => {});
  loadAlertas();
}

async function loadAlertas() {
  const box = $("#alertas-list");
  if (!box) return;
  try {
    const res = await fetch("/api/alertas");
    const d = await res.json();
    const alertas = d.alertas || [];
    box.innerHTML = "";
    if (!alertas.length) { box.innerHTML = '<p class="muted">Sin alertas de inspección.</p>'; return; }
    maestroTable(box, ["Vehículo", "Código", "Severidad", "Mensaje", "Reporte", "Estado", ""], alertas, (a) => {
      const veh = a.vehiculo_id + (a.matricula && a.matricula !== a.vehiculo_id ? " · " + a.matricula : "");
      const actions = el("span", { style: "display:flex;gap:4px;" });
      if ((a.estado || "abierta") === "abierta") {
        const r = el("button", { type: "button", class: "btn-ghost", text: "✓", title: "Resolver" });
        r.addEventListener("click", () => patchAlerta(a.id, "resuelta"));
        const x = el("button", { type: "button", class: "btn-remove", text: "✕", title: "Descartar" });
        x.addEventListener("click", () => patchAlerta(a.id, "descartada"));
        actions.append(r, x);
      } else {
        const r = el("button", { type: "button", class: "btn-ghost", text: "↺", title: "Reabrir" });
        r.addEventListener("click", () => patchAlerta(a.id, "abierta"));
        actions.append(r);
      }
      return [
        veh,
        a.codigo || "—",
        sevBadge(a.severidad),
        a.mensaje || "—",
        a.reporte_id || "—",
        estadoBadge(a.estado),
        actions,
      ];
    }, "Sin alertas de inspección.");
  } catch (_) {
    box.innerHTML = '<p class="muted">No se pudieron cargar las alertas.</p>';
  }
}

$("#btn-add-mant")?.addEventListener("click", async () => {
  const vehiculo_id = $("#mant-vehiculo")?.value || "";
  const tipo = $("#mant-tipo")?.value || "";
  const fecha = $("#mant-fecha")?.value || "";
  if (!vehiculo_id || !tipo || !fecha) { showError("Indica vehículo, tipo y fecha del mantenimiento."); return; }
  const rec = editState.mantenimiento;
  const body = {
    vehiculo_id, tipo, fecha,
    km: parseInt($("#mant-km")?.value) || 0,
    coste: parseFloat($("#mant-coste")?.value) || 0,
    notas: $("#mant-notas")?.value.trim() || "",
    hecho: rec?.hecho ?? false,
  };
  const url = rec ? `/api/mantenimientos/${rec.id}` : "/api/mantenimientos";
  const res = await fetch(url, {
    method: rec ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar el mantenimiento."); return; }
  editState.mantenimiento = null;
  ["#mant-fecha", "#mant-km", "#mant-coste", "#mant-notas"].forEach((s) => { const e = $(s); if (e) e.value = ""; });
  setEditBtn("#btn-add-mant", false, "Añadir");
  loadMantenimientos();
});

async function loadTransportistas() {
  const sel = $("#liq-transportista");
  const list = $("#transportistas-list");
  try {
    const res = await fetch("/api/transportistas");
    const d = await res.json();
    const ts = d.transportistas || [];
    transportistasCache = ts;
    if (sel) {
      const prev = sel.value;
      sel.innerHTML = '<option value="">—</option>';
      ts.forEach((t) => sel.append(el("option", { value: String(t.id), text: t.nombre })));
      if (prev) sel.value = prev;
    }
    if (list) {
      if (!ts.length) { list.innerHTML = '<p class="muted">Sin transportistas registrados.</p>'; }
      else {
        maestroTable(list, ["Nombre", "CIF", "Teléfono", "Email", "Tarifa", ""], ts, (t) => [
          t.nombre,
          t.cif || "—",
          t.telefono || "—",
          t.email || "—",
          t.tarifa ? `${t.tarifa} €/km` : "—",
          delBtn(async () => {
            await fetch(`/api/transportistas/${t.id}`, { method: "DELETE" }).catch(() => {});
            loadTransportistas(); loadLiquidaciones();
          }),
        ], "Sin transportistas registrados.", (t) => {
          editState.transportista = t;
          formFill([["#tra-nombre", t.nombre], ["#tra-cif", t.cif], ["#tra-telefono", t.telefono],
                    ["#tra-email", t.email], ["#tra-tarifa", t.tarifa]]);
          setEditBtn("#btn-add-transportista", true, "Añadir");
        });
      }
    }
  } catch (_) {
    if (list) list.innerHTML = '<p class="muted">No se pudieron cargar los transportistas.</p>';
  }
}

async function loadLiquidaciones() {
  const resumenBox = $("#liquidaciones-resumen");
  const listBox = $("#liquidaciones-list");
  if (!listBox) return;
  try {
    const res = await fetch("/api/liquidaciones");
    const d = await res.json();
    const liq = d.liquidaciones || [];
    const por = d.por_transportista || [];
    if (resumenBox) {
      resumenBox.innerHTML = "";
      resumenBox.append(dataGrid(
        ["Transportista", "CIF", "Tarifa", "Pendiente", "Total"],
        por.map((r) => [r.nombre, r.cif || "—", r.tarifa ? `${r.tarifa} €/km` : "—", formatEUR(r.pendiente), formatEUR(r.total)])
      ));
    }
    listBox.innerHTML = "";
    if (!liq.length) { listBox.innerHTML = '<p class="muted">Sin liquidaciones.</p>'; return; }
    maestroTable(listBox, ["Transportista", "Fecha", "Importe", "Concepto", "Pagado", "", ""], liq, (l) => {
      const pagBtn = el("button", { type: "button", class: "btn-remove", text: l.pagado ? "↺" : "€", title: "Marcar pagado/pendiente" });
      pagBtn.addEventListener("click", async () => {
        await fetch(`/api/liquidaciones/${l.id}`, { method: "PATCH" }).catch(() => {});
        loadLiquidaciones();
      });
      return [
        l.transportista || "—",
        l.fecha || "—",
        formatEUR(l.importe),
        l.concepto || "—",
        l.pagado ? "✓ pagado" : "pendiente",
        pagBtn,
        delBtn(async () => {
          await fetch(`/api/liquidaciones/${l.id}`, { method: "DELETE" }).catch(() => {});
          loadLiquidaciones();
        }),
      ];
    }, "Sin liquidaciones.", (l) => {
      editState.liquidacion = l;
      formFill([["#liq-transportista", l.transportista_id], ["#liq-fecha", l.fecha],
                ["#liq-importe", l.importe], ["#liq-concepto", l.concepto]]);
      setEditBtn("#btn-add-liq", true, "Añadir");
    });
  } catch (_) {
    listBox.innerHTML = '<p class="muted">No se pudieron cargar las liquidaciones.</p>';
  }
}

$("#btn-add-transportista")?.addEventListener("click", async () => {
  const nombre = $("#tra-nombre")?.value.trim() || "";
  if (!nombre) { showError("Indica el nombre del transportista."); return; }
  const rec = editState.transportista;
  const body = {
    nombre,
    cif: $("#tra-cif")?.value.trim() || "",
    telefono: $("#tra-telefono")?.value.trim() || "",
    email: $("#tra-email")?.value.trim() || "",
    tarifa: parseFloat($("#tra-tarifa")?.value) || 0,
  };
  const url = rec ? `/api/transportistas/${rec.id}` : "/api/transportistas";
  const res = await fetch(url, { method: rec ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar el transportista."); return; }
  editState.transportista = null;
  ["#tra-nombre", "#tra-cif", "#tra-telefono", "#tra-email", "#tra-tarifa"].forEach((s) => { const e = $(s); if (e) e.value = ""; });
  setEditBtn("#btn-add-transportista", false, "Añadir");
  loadTransportistas();
});

$("#btn-add-liq")?.addEventListener("click", async () => {
  const transportista_id = parseInt($("#liq-transportista")?.value) || 0;
  const fecha = $("#liq-fecha")?.value || "";
  const importe = parseFloat($("#liq-importe")?.value) || 0;
  if (!transportista_id || !fecha || !importe) { showError("Indica transportista, fecha e importe."); return; }
  const rec = editState.liquidacion;
  const body = { transportista_id, fecha, importe, concepto: $("#liq-concepto")?.value.trim() || "", pagado: rec?.pagado ?? false };
  const url = rec ? `/api/liquidaciones/${rec.id}` : "/api/liquidaciones";
  const res = await fetch(url, { method: rec ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar la liquidación."); return; }
  editState.liquidacion = null;
  ["#liq-fecha", "#liq-importe", "#liq-concepto"].forEach((s) => { const e = $(s); if (e) e.value = ""; });
  setEditBtn("#btn-add-liq", false, "Añadir");
  loadLiquidaciones();
});

async function loadRemolques() {
  for (const [selName, cat] of [["asig-semirremolque", "semirremolque"], ["asig-remolque", "remolque"]]) {
    const sel = $(`[name="${selName}"]`);
    if (!sel) continue;
    const prev = sel.value;
    try {
      const res = await fetch(`/api/vehiculos?categoria=${cat}`);
      const d = await res.json();
      const vehiculos = d.vehiculos || [];
      sel.innerHTML = '<option value="">— Ninguno —</option>';
      vehiculos.forEach((v) => {
        const opt = el("option", { value: v.id, text: `${v.id}${v.matricula ? " · " + v.matricula : ""}${v.disponible === false ? " (en uso)" : ""}` });
        if (v.disponible === false) opt.disabled = true;
        sel.append(opt);
      });
      if (prev && [...sel.options].some((o) => o.value === prev && !o.disabled)) sel.value = prev;
    } catch (_) {
      sel.innerHTML = '<option value="">Error</option>';
    }
  }
}

renderDocs();

/* ---------- Envío ---------- */

$("#trip-form").addEventListener("submit", async (e) => {
  e.preventDefault();

  const origen = readAddress($('[data-address="origen"]'));
  const destino = readAddress($('[data-address="destino"]'));

  if (!origen.ciudad) { showError("El campo «Ciudad» del origen es obligatorio."); return; }
  if (!destino.ciudad) { showError("El campo «Ciudad» del destino es obligatorio."); return; }

  const paradas = [...$("#paradas-list").children].map((item) => readParada(item));

  const clienteSel = $('[name="cliente"]');
  const clienteNombre = clienteSel?.selectedOptions?.[0]?.textContent?.trim() || "";
  const clienteId = clienteSel?.value || "";

  const payload = {
    origen,
    paradas,
    destino,
    tipo_carga: $('[name="tipo_carga"]').value,
    cliente:   clienteNombre,
    cliente_id: clienteId ? parseInt(clienteId) : null,
    precio:    parseFloat($('[name="precio"]').value) || 0,
    iva:       parseFloat($('[name="iva"]').value) || 0,
    peso:      parseFloat($('[name="peso"]').value) || 0,
    palets:    parseInt($('[name="palets"]').value) || 0,
  };

  const btn = $("#btn-submit");
  btn.disabled = true;
  btn.textContent = "Guardando…";

  try {
    const url = editingTripId ? `/api/trips/${encodeURIComponent(editingTripId)}` : "/api/trips";
    const method = editingTripId ? "PUT" : "POST";
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));

    if (res.ok) {
      const tripId = data.trip_id || editingTripId;
      if (tripId && pedidoDocs.length) {
        const docs = [];
        for (const f of pedidoDocs) docs.push({ nombre: f.name, contenido: await fileToBase64(f) });
        await fetch(`/api/trips/${encodeURIComponent(tripId)}/documentos`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ documentos: docs }),
        }).catch(() => {});
        pedidoDocs = [];
        renderPedidoDocs();
      }
      editingTripId = null;
      resetPedidoForm();
      goBack();
      loadHistory();
    } else {
      showError(data?.detail?.error || data?.detail || `Error inesperado (HTTP ${res.status}).`);
    }
  } catch (err) {
    showError("No se pudo conectar con el backend: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Guardar pedido";
  }
});

$("#ocr-factura-input")?.addEventListener("change", async () => {
  const file = $("#ocr-factura-input").files[0];
  if (!file) return;
  const box = $("#ocr-factura-result");
  box.textContent = "Leyendo documento…";
  const b64 = await fileToBase64(file);
  try {
    const res = await fetch("/api/ocr", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ imagen: b64 }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d?.detail?.error || `HTTP ${res.status}`);
    if (d.numero) $('[name="factura"]').value = d.numero;
    box.textContent = `OCR: Nº ${d.numero || "—"} · CIF ${d.cif || "—"} · Base ${d.base ?? "—"} € · IVA ${d.iva ?? "—"} € · Total ${d.total ?? "—"} €. Revisa y corrige si hace falta.`;
  } catch (err) {
    box.textContent = "No se pudo leer: " + err.message;
  }
  $("#ocr-factura-input").value = "";
});

function resetPedidoForm() {
  $("#trip-form").reset();
  $("#paradas-list").innerHTML = "";
  addParada();
  pedidoDocs = [];
  renderPedidoDocs();
  const oa = $(".f-actividad", $('[data-address="origen"]'));
  if (oa) oa.value = "CARGA";
  const da = $(".f-actividad", $('[data-address="destino"]'));
  if (da) da.value = "DESCARGA";
  $("#km-result").hidden = true;
  $("#km-result").innerHTML = "";
  if (map && routeLayer) { map.removeLayer(routeLayer); routeLayer = null; }
}

/* ---------- Resultado ---------- */

function showError(message) {
  $("#result-title").textContent = "Error";
  $("#result-card").hidden = false;
  $("#result-body").innerHTML = "";
  $("#result-body").append(
    el("div", { class: "alert alert-error", role: "alert" },
      el("strong", { text: "No se pudo enviar el viaje" }),
      el("span", { text: message }),
    ),
  );
  $("#result-card").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------- Historial ---------- */

let viajesCache = [];

let historySignature = "";

async function loadHistory() {
  // Pinta la parrilla del planificador (activos) y el historial (todos, filtrables por estado)
  try {
    const res = await fetch("/api/trips/status");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const viajes = data?.viajes || [];
    viajesCache = viajes;

    // Si no ha cambiado nada, no repintamos (evita el parpadeo del refresco automático)
    const sig = viajes.map((v) => [
      v.id, v.estado || "", v.terminal || "", v.conductor || "", v.cliente || "",
      v.tipo_carga || "", v.precio || 0, v.gastos || 0, v.km_total || 0, v.km_vacio || 0,
      v.factura || "", v.semirremolque_id || "", v.remolque_id || "", v.estado_pago || "",
      v.iva || "", v.referencia || "",
    ].join("\u0001")).join("\u0002");
    if (sig === historySignature) return;
    historySignature = sig;

    const finales = new Set(["finalizado", "finished", "error", "cancelado", "canceled", "rechazado", "refused"]);
    const activos = viajes.filter((v) => !finales.has((v.estado || "").toLowerCase()));

    renderPlanificador(activos);
    renderHistorial(viajes);
    renderTractorasLibres();

    const cp = $("#count-planificador");
    if (cp) {
      const pendientes = activos.filter((v) => (v.estado || "").toLowerCase() === "sin_asignar").length;
      cp.hidden = !pendientes;
      cp.textContent = pendientes;
    }
  } catch (err) {
    const box = $("#planificador-body");
    if (box) box.innerHTML = `<tr><td colspan="12"><div class="alert alert-error">No se pudo cargar: ${err.message}</div></td></tr>`;
    const hb = $("#history-body");
    if (hb) hb.innerHTML = `<tr><td colspan="15"><div class="alert alert-error">No se pudo cargar: ${err.message}</div></td></tr>`;
  }
}

const TIPOS_CARGA = ["General", "ADR", "Refrigerada", "Líquidos", "Granel", "Otro"];

function esPedido(v) {
  return (v.estado || "").toLowerCase() === "sin_asignar";
}

function planDragHandle(v) {
  if (!esPedido(v)) return "";
  const h = el("span", { class: "drag-handle", title: "Arrastrar a una tractora libre" });
  h.textContent = "⠿";
  h.draggable = true;
  h.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", v.id);
    e.dataTransfer.effectAllowed = "move";
  });
  return h;
}

function planEstadoRenderer(p) {
  return `<span class="badge badge-${estadoClass(p.value)}">${p.value || "—"}</span>`;
}

function planAccionesRenderer(p) {
  const v = p.data;
  const enviar = esPedido(v) ? '<button type="button" class="btn btn-primary ag-btn" data-action="enviar" title="Asignar y enviar">➤</button>' : "";
  return `<div class="ag-actions">
    ${enviar}
    <button type="button" class="btn btn-ghost ag-btn" data-action="editar" title="Editar">✏️</button>
    <button type="button" class="btn btn-ghost ag-btn" data-action="dup" title="Duplicar">⧉</button>
    ${v.terminal ? '<button type="button" class="btn btn-ghost ag-btn" data-action="chat" title="Chat">💬</button>' : ""}
    <button type="button" class="btn btn-ghost btn-delete ag-btn" data-action="del" title="Eliminar">🗑️</button>
  </div>`;
}

let planificadorGridApi = null;

function initPlanificadorGrid() {
  if (planificadorGridApi) return planificadorGridApi;
  const elx = document.getElementById("planificador-grid");
  if (!elx || typeof agGrid === "undefined") return null;
  planificadorGridApi = agGrid.createGrid(elx, {
    theme: agGrid.themeQuartz,
    columnDefs: [
      { headerName: "", field: "_drag", cellRenderer: (p) => planDragHandle(p.data), width: 40, sortable: false, filter: false, pinned: "left", resizable: false },
      { headerName: "Origen", field: "origen", sortable: true, filter: true, valueGetter: (p) => formatAddress(p.data.origen), cellRenderer: (p) => `<span class="cell-link">${formatAddress(p.data.origen)}</span>`, minWidth: 140 },
      { headerName: "Destino", field: "destino", sortable: true, filter: true, valueGetter: (p) => formatAddress(p.data.destino), cellRenderer: (p) => `<span class="cell-link">${formatAddress(p.data.destino)}</span>`, minWidth: 140 },
      { headerName: "Cliente", field: "cliente", sortable: true, filter: "agSetColumnFilter", cellRenderer: (p) => selectCliente(p.data, esPedido(p.data)), width: 150 },
      { headerName: "Carga", field: "tipo_carga", sortable: true, filter: "agSetColumnFilter", cellRenderer: (p) => selectTipoCarga(p.data, esPedido(p.data)), width: 120 },
      { headerName: "Precio", field: "precio", sortable: true, filter: "agNumberColumnFilter", cellRenderer: (p) => numberCell(p.data, esPedido(p.data)), width: 100, type: "rightAligned" },
      { headerName: "Conductor", field: "conductor", sortable: true, filter: "agSetColumnFilter", cellRenderer: (p) => selectConductor(p.data, esPedido(p.data)), width: 150 },
      { headerName: "Vehículo", field: "terminal", sortable: true, filter: "agSetColumnFilter", cellRenderer: (p) => selectVehiculo(p.data, esPedido(p.data), "tractora", "terminal"), width: 150 },
      { headerName: "Semirrem.", field: "semirremolque_id", sortable: true, filter: "agSetColumnFilter", cellRenderer: (p) => selectVehiculo(p.data, esPedido(p.data), "semirremolque", "semirremolque_id"), width: 150 },
      { headerName: "Remolque", field: "remolque_id", sortable: true, filter: "agSetColumnFilter", cellRenderer: (p) => selectVehiculo(p.data, esPedido(p.data), "remolque", "remolque_id"), width: 150 },
      { headerName: "Km", field: "km_total", sortable: true, filter: "agNumberColumnFilter", valueGetter: (p) => (p.data.km_total != null ? Number(p.data.km_total) : null), valueFormatter: (p) => (p.value != null ? `${p.value} km` : "—"), width: 90, type: "rightAligned" },
      { headerName: "Estado", field: "estado", sortable: true, filter: "agSetColumnFilter", cellRenderer: planEstadoRenderer, width: 120 },
      { headerName: "", field: "_acc", cellRenderer: planAccionesRenderer, sortable: false, filter: false, pinned: "right", width: 170 },
    ],
    rowData: [],
    defaultColDef: { resizable: true, sortable: true, filter: true, minWidth: 80 },
    getRowId: (p) => String(p.data.id),
    overlayNoRowsTemplate: '<span class="empty" style="padding:16px">No hay viajes activos.</span>',
    onCellClicked: (e) => {
      const t = e.event && e.event.target;
      if (t && t.closest) {
        const btn = t.closest("[data-action]");
        if (btn) {
          const action = btn.getAttribute("data-action");
          const v = e.data;
          if (action === "enviar") openAsignar(v.id, v);
          else if (action === "editar") openPedidoForm(v.id);
          else if (action === "dup") duplicarTrip(v.id);
          else if (action === "chat") openPlanificadorChat(v);
          else if (action === "del") deleteTrip(v.id);
          return;
        }
        if (t.closest("select, button, input, a")) return;
      }
      openPedidoForm(e.data.id);
    },
  });
  return planificadorGridApi;
}

function renderPlanificador(activos) {
  const api = initPlanificadorGrid();
  if (!api) return;
  const rows = activos.slice().sort((a, b) => String(b.creado || "").localeCompare(String(a.creado || "")));
  api.setGridOption("rowData", rows);
}

function openPlanificadorChat(v) {
  const wrap = $("#planificador-detail");
  const panel = $("#planificador-detail-body");
  if (!wrap || !panel) return;
  wrap.hidden = false;
  chatsAbiertos.delete(panel);
  renderChatPanel(v, panel);
  chatsAbiertos.set(panel, v);
  const t = $("#planificador-detail-title");
  if (t) t.textContent = `${v.referencia || v.id || "Viaje"} · chat`;
  wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

document.addEventListener("click", (e) => {
  if (e.target.closest && e.target.closest("#planificador-detail-close")) {
    const wrap = $("#planificador-detail");
    const panel = $("#planificador-detail-body");
    if (wrap) wrap.hidden = true;
    if (panel) { panel.innerHTML = ""; chatsAbiertos.delete(panel); }
  }
});

async function renderTractorasLibres() {
  const box = $("#tractoras-libres");
  if (!box) return;
  box.innerHTML = "";
  box.append(el("span", { class: "muted", text: "🚛 Arrastra un pedido a una tractora libre:" }));
  try {
    const res = await fetch("/api/vehiculos");
    const d = await res.json();
    const libres = (d.vehiculos || []).filter((v) => v.categoria === "tractora" && v.disponible !== false);
    if (!libres.length) {
      box.append(el("span", { class: "muted", text: "ninguna libre" }));
      return;
    }
    libres.forEach((v) => {
      const card = el("div", { class: "tractora-drop", text: `🚛 ${v.id}${v.matricula ? " · " + v.matricula : ""}` });
      card.addEventListener("dragover", (e) => { e.preventDefault(); card.classList.add("drag-over"); });
      card.addEventListener("dragleave", () => card.classList.remove("drag-over"));
      card.addEventListener("drop", (e) => {
        e.preventDefault();
        card.classList.remove("drag-over");
        const tripId = e.dataTransfer.getData("text/plain");
        if (tripId) openAsignar(tripId, { terminal: v.id });
      });
      box.append(card);
    });
  } catch (_) {}
}

function selectCliente(v, esPedido) {
  const sel = el("select");
  sel.append(el("option", { value: "", text: "—" }));
  (clientesCache || []).forEach((c) => sel.append(el("option", { value: c.nombre, text: c.nombre })));
  sel.value = v.cliente || "";
  if (esPedido) sel.addEventListener("change", () => patchTrip(v.id, "cliente", sel.value));
  else sel.disabled = true;
  return sel;
}

function selectTipoCarga(v, esPedido) {
  const sel = el("select");
  sel.append(el("option", { value: "", text: "—" }));
  TIPOS_CARGA.forEach((t) => sel.append(el("option", { value: t, text: t })));
  sel.value = v.tipo_carga || "";
  if (esPedido) sel.addEventListener("change", () => patchTrip(v.id, "tipo_carga", sel.value));
  else sel.disabled = true;
  return sel;
}

function selectConductor(v, esPedido) {
  const sel = el("select");
  sel.append(el("option", { value: "", text: "—" }));
  const seen = new Set();
  (conductoresCache || []).forEach((c) => {
    if (c.nombre && !seen.has(c.nombre)) { seen.add(c.nombre); sel.append(el("option", { value: c.nombre, text: c.nombre })); }
  });
  sel.value = v.conductor || "";
  if (esPedido) sel.addEventListener("change", () => patchTrip(v.id, "conductor", sel.value));
  else sel.disabled = true;
  return sel;
}

function selectVehiculo(v, esPedido, categoria, field) {
  const sel = el("select");
  sel.append(el("option", { value: "", text: "—" }));
  (vehiculosCache || []).filter((ve) => ve.categoria === categoria).forEach((ve) => {
    sel.append(el("option", { value: ve.id, text: ve.id + (ve.matricula ? " · " + ve.matricula : "") }));
  });
  sel.value = v[field] || "";
  if (esPedido) sel.addEventListener("change", () => patchTrip(v.id, field, sel.value));
  else sel.disabled = true;
  return sel;
}

function numberCell(v, esPedido) {
  const inp = el("input", { type: "number", step: "0.01", min: "0", placeholder: "0" });
  inp.value = v.precio || "";
  if (esPedido) inp.addEventListener("change", () => patchTrip(v.id, "precio", parseFloat(inp.value) || 0));
  else inp.disabled = true;
  return inp;
}

async function patchTrip(tripId, field, value) {
  try {
    await fetch(`/api/trips/${encodeURIComponent(tripId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [field]: value }),
    });
  } catch (_) {}
}

/* ---------- Ficha completa (click en línea / nuevo pedido) ---------- */

let editingTripId = null;

function isoToLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function setField(container, cls, value) {
  const f = $(`.${cls}`, container);
  if (f) f.value = value ?? "";
}

function fillAddress(container, addr) {
  if (!addr) return;
  setField(container, "f-nombre", addr.nombre);
  setField(container, "f-empresa", addr.empresa);
  setField(container, "f-calle", addr.calle);
  setField(container, "f-numero", addr.numero);
  setField(container, "f-ciudad", addr.ciudad);
  setField(container, "f-cp", addr.cp);
  setField(container, "f-pais", addr.pais);
  setField(container, "f-lat", addr.lat);
  setField(container, "f-lng", addr.lng);
  setField(container, "f-comentario", addr.comentario);
  setField(container, "f-fecha-inicio", isoToLocalInput(addr.fecha_inicio));
  setField(container, "f-fecha-fin", isoToLocalInput(addr.fecha_fin));
  if (addr.actividad) setField(container, "f-actividad", addr.actividad);
}

function fillPedidoForm(d) {
  const payload = d.payload || {};
  const trip = d.trip || {};
  const origenAddr = payload.origen || (trip.origen ? { ciudad: trip.origen } : null);
  const destinoAddr = payload.destino || (trip.destino ? { ciudad: trip.destino } : null);
  fillAddress($('[data-address="origen"]'), origenAddr);
  fillAddress($('[data-address="destino"]'), destinoAddr);
  const paradas = payload.paradas || [];
  $("#paradas-list").innerHTML = "";
  paradas.forEach((p) => {
    const item = addParada();
    fillAddress(item, p);
    if (p.actividad) { const act = $(".f-actividad", item); if (act) act.value = p.actividad; }
  });
  if (!paradas.length) addParada();

  const setVal = (sel, val) => { const e = $(sel); if (e) e.value = val ?? ""; };
  setVal('[name="tipo_carga"]', trip.tipo_carga);
  setVal('[name="precio"]', trip.precio);
  setVal('[name="iva"]', trip.iva ?? 21);
  setVal('[name="peso"]', payload.peso);
  setVal('[name="palets"]', payload.palets);
  const clienteSel = $('[name="cliente"]');
  if (clienteSel && trip.cliente) {
    const opt = [...clienteSel.options].find((o) => o.textContent.trim() === trip.cliente);
    if (opt) clienteSel.value = opt.value;
  }
}

const ESTADOS_FINALES = new Set(["finalizado", "finished", "error", "cancelado", "canceled", "rechazado", "refused"]);

async function openPedidoForm(tripId) {
  previousScreen = currentScreen;
  if (currentScreen === "contabilidad") previousContabTab = contabilidadTab;
  if (tripId) {
    let d;
    try {
      const res = await fetch(`/api/trips/${encodeURIComponent(tripId)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      d = await res.json();
    } catch (err) {
      showError("No se pudo cargar el pedido: " + err.message);
      return;
    }
    if (ESTADOS_FINALES.has(((d.trip && d.trip.estado) || "").toLowerCase())) {
      showError("El viaje está finalizado y no se puede modificar.");
      return;
    }
    editingTripId = tripId;
    resetPedidoForm();
    const title = $("#pedido-form-title");
    if (title) title.textContent = "Editar pedido";
    fillPedidoForm(d);
    showScreen("nuevo");
    return;
  }
  editingTripId = null;
  resetPedidoForm();
  const title = $("#pedido-form-title");
  if (title) title.textContent = "Nuevo pedido";
  showScreen("nuevo");
}

/* ---------- Asignar camión (modal) ---------- */

let asignarTripId = null;
let asignarPeso = 0, asignarPalets = 0;

function setSel(sel, value) {
  const e = $(sel);
  if (e && value) {
    const opt = [...e.options].find((o) => o.value === value);
    if (opt) e.value = value;
  }
}

function openAsignar(tripId, tripData) {
  asignarTripId = tripId;
  const info = $("#asignar-trip-info");
  if (info) info.textContent = `${tripData?.origen || ""} → ${tripData?.destino || ""}`;
  const t = tripData || {};
  setSel('[name="asig-terminal"]', t.terminal);
  setSel('[name="asig-semirremolque"]', t.semirremolque_id);
  setSel('[name="asig-remolque"]', t.remolque_id);
  setSel('[name="asig-conductor"]', t.conductor);
  selectedDocs = [];
  storedDocs = [];
  renderDocs();
  hideAsignarError();
  $("#asignar-modal").hidden = false;
  sugerirTractor(tripId);
  loadStoredDocs(tripId);
}

async function loadStoredDocs(tripId) {
  try {
    const res = await fetch(`/api/trips/${encodeURIComponent(tripId)}/documentos`);
    const d = await res.json();
    storedDocs = (d.documentos || []).map((x) => ({ id: x.id, nombre: x.nombre, contenido: x.contenido, size: x.size }));
    renderDocs();
  } catch (_) {}
}

async function sugerirTractor(tripId) {
  const box = $("#asig-sugerencia");
  if (!box) return;
  box.textContent = "";
  try {
    const res = await fetch(`/api/trips/${encodeURIComponent(tripId)}`);
    const d = await res.json();
    const origen = d.payload?.origen;
    asignarPeso = parseFloat(d.payload?.peso) || 0;
    asignarPalets = parseInt(d.payload?.palets) || 0;
    if (!origen || origen.lat == null || origen.lng == null) return;
    const r2 = await fetch(`/api/vehiculos/cercano?lat=${origen.lat}&lng=${origen.lng}`);
    const d2 = await r2.json();
    const c = d2.cercano;
    if (c) {
      box.textContent = `📍 Tractor libre más cercana al origen: ${c.id}${c.matricula ? " (" + c.matricula + ")" : ""} a ${c.dist} km`;
      const sel = $('[name="asig-terminal"]');
      if (sel && !sel.value) setSel('[name="asig-terminal"]', c.id);
    }
  } catch (_) {}
}

function closeAsignar() {
  $("#asignar-modal").hidden = true;
  asignarTripId = null;
}

$("#btn-cerrar-asignar").addEventListener("click", closeAsignar);
$("#asignar-modal").addEventListener("click", (e) => {
  if (e.target === $("#asignar-modal")) closeAsignar();
});

/* ---------- Chat con el chofer (desplegable tipo chat) ---------- */

const chatsAbiertos = new Map(); // panel DOM -> {v}

function chatBubble(m) {
  const esSaliente = m.tipo === "enviado";
  const bubble = el("div", { class: "chat-bubble " + (esSaliente ? "chat-out" : "chat-in") });
  const meta = el("div", { class: "chat-meta" });
  meta.textContent = (esSaliente ? "Tú" : (m.source || "Chofer")) + (m.time ? " · " + formatDate(m.time) : "");
  bubble.append(meta);
  if (m.subject && m.subject !== "AFRE") bubble.append(el("div", { class: "chat-subject", text: m.subject }));
  bubble.append(el("div", { class: "chat-body", text: m.body || "" }));
  return bubble;
}

function buildChatCompose(v, panel, listBox) {
  const subject = el("input", { class: "msg-input", type: "text", placeholder: "Asunto (opcional)" });
  const body = el("textarea", { class: "msg-input msg-textarea", rows: 1, placeholder: "Escribe un mensaje…" });
  const btn = el("button", { type: "button", class: "btn btn-primary", text: "Enviar" });
  btn.addEventListener("click", async () => {
    const s = subject.value.trim();
    const b = body.value.trim();
    if (!s && !b) return;
    btn.disabled = true; btn.textContent = "Enviando…";
    try {
      const res = await fetch(`/api/trips/${encodeURIComponent(v.id)}/mensajes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject: s, body: b, needreply: false }),
      });
      const data = await res.json();
      if (data.ok) {
        subject.value = ""; body.value = "";
        await renderChatList(v, listBox);
      } else {
        showError("No se pudo enviar el mensaje: " + (data.error || "error"));
      }
    } catch (err) {
      showError("No se pudo enviar el mensaje: " + err.message);
    }
    btn.disabled = false; btn.textContent = "Enviar";
  });
  const box = el("div", { class: "msg-compose" });
  box.append(subject, body, btn);
  return box;
}

async function renderChatList(v, listBox) {
  try {
    const res = await fetch(`/api/trips/${encodeURIComponent(v.id)}/mensajes`);
    const data = await res.json();
    const mensajes = (data?.mensajes || []).filter((m) => m.tipo !== "cuestionario");
    listBox.innerHTML = "";
    if (!mensajes.length) {
      listBox.append(el("p", { class: "muted", text: "Sin conversación todavía." }));
    } else {
      const list = el("div", { class: "chat-list" });
      mensajes.slice().reverse().forEach((m) => list.append(chatBubble(m)));
      listBox.append(list);
    }
  } catch (err) {
    listBox.innerHTML = el("div", { class: "alert alert-error" },
      el("span", { text: "No se pudo cargar la conversación: " + err.message })).outerHTML;
  }
}

function renderChatPanel(v, panel) {
  panel.innerHTML = "";
  const listBox = el("div", { class: "chat-list-box" });
  panel.append(listBox, buildChatCompose(v, panel, listBox));
  renderChatList(v, listBox);
}

// auto-refresco de los chats abiertos (respuestas del chofer)
setInterval(async () => {
  if (!chatsAbiertos.size) return;
  try { await fetch("/api/sync/mensajes", { method: "POST" }); } catch (_) {}
  for (const [panel, v] of [...chatsAbiertos]) {
    if (!panel.isConnected) { chatsAbiertos.delete(panel); continue; }
    const listBox = panel.querySelector(".chat-list-box");
    if (listBox) renderChatList(v, listBox);
  }
}, 8000);

/* ---------- (filtros del historial: ahora los gestiona AG Grid) ---------- */

function showAsignarError(msg) {
  const box = $("#asignar-error");
  box.hidden = false;
  box.innerHTML = "";
  box.append(el("strong", { text: "No se pudo asignar" }), el("span", { text: msg }));
}
function hideAsignarError() {
  const box = $("#asignar-error");
  box.hidden = true;
  box.innerHTML = "";
}

$("#btn-confirmar-asignar").addEventListener("click", async () => {
  if (!asignarTripId) return;
  const terminal = $('[name="asig-terminal"]')?.value || "";
  if (!terminal) { showAsignarError("Selecciona la tractora (vehículo)."); return; }

  const veh = vehiculosCache.find((x) => x.id === terminal);
  if (veh && veh.capacidad_peso > 0 && asignarPeso > veh.capacidad_peso) {
    if (!confirm(`⚠️ ${terminal} superaría su capacidad de peso (${asignarPeso} kg > ${veh.capacidad_peso} kg). ¿Continuar?`)) return;
  }
  if (veh && veh.capacidad_palets > 0 && asignarPalets > veh.capacidad_palets) {
    if (!confirm(`⚠️ ${terminal} superaría su capacidad de palets (${asignarPalets} > ${veh.capacidad_palets}). ¿Continuar?`)) return;
  }

  const documentos = [];
  for (const d of storedDocs) {
    documentos.push({ nombre: d.nombre, contenido: d.contenido });
  }
  for (const f of selectedDocs) {
    if (f.size > MAX_DOC_MB * 1024 * 1024) {
      showAsignarError(`El documento «${f.name}» pesa más de ${MAX_DOC_MB} MB. Comprímelo.`);
      return;
    }
    documentos.push({ nombre: f.name, contenido: await fileToBase64(f) });
  }

  const conductorNombre = $('[name="asig-conductor"]')?.value || "";
  const conductorLocal = conductoresCache.find((c) => c.nombre === conductorNombre);

  const body = {
    terminal,
    semirremolque_id: $('[name="asig-semirremolque"]')?.value || "",
    remolque_id: $('[name="asig-remolque"]')?.value || "",
    conductor: conductorNombre,
    conductor_id: conductorLocal ? conductorLocal.id : null,
    conduccion_acumulada_min: (parseFloat($('[name="asig-conduccion"]')?.value) || 0) * 60,
    ecmr_provider: $('[name="asig-ecmr"]')?.value || "",
    ecmr_id: $('[name="asig-ecmr-id"]')?.value.trim() || "",
    documentos,
  };

  const btn = $("#btn-confirmar-asignar");
  btn.disabled = true;
  btn.textContent = "Asignando…";
  try {
    const res = await fetch(`/api/trips/${encodeURIComponent(asignarTripId)}/asignar`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      closeAsignar();
      showScreen("planificador");
      loadHistory();
    } else {
      showAsignarError(data?.detail?.error || data?.detail || `Error (HTTP ${res.status}).`);
    }
  } catch (err) {
    showAsignarError("No se pudo conectar: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Asignar y enviar a Trimble";
  }
});

$("#btn-crear-ecmr")?.addEventListener("click", async () => {
  const carrier = $('[name="ecmr-carrier-email"]')?.value.trim() || "";
  const consignorEmail = $('[name="ecmr-consignor-email"]')?.value.trim() || "";
  const consigneeEmail = $('[name="ecmr-consignee-email"]')?.value.trim() || "";
  if (!carrier) { alert("Indica el email del transportista (carrier)."); return; }
  let trip = { origen: "", destino: "" };
  try {
    const r = await fetch(`/api/trips/${encodeURIComponent(asignarTripId)}`);
    const d = await r.json();
    trip = { origen: d.trip?.origen || "", destino: d.trip?.destino || "" };
  } catch (_) {}
  const btn = $("#btn-crear-ecmr");
  const status = $("#ecmr-crear-status");
  btn.disabled = true; btn.textContent = "Creando…";
  try {
    const res = await fetch("/api/ecmr/crear", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        carrier_email: carrier,
        consignor: { email: consignorEmail, nombre: "" },
        consignee: { email: consigneeEmail, nombre: "" },
        goods: [],
        trip,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.freightDocumentId) {
      const idInput = $('[name="asig-ecmr-id"]');
      if (idInput) idInput.value = String(data.freightDocumentId);
      const provSel = $('[name="asig-ecmr"]');
      if (provSel && !provSel.value) provSel.value = "transfollow";
      if (status) status.textContent = `✓ e-CMR ${data.freightDocumentId} creado`;
    } else {
      if (status) status.textContent = "";
      showAsignarError(data?.detail?.error || data?.detail || "No se pudo crear el e-CMR.");
    }
  } catch (err) {
    showAsignarError("No se pudo conectar: " + err.message);
  } finally {
    btn.disabled = false; btn.textContent = "Crear e-CMR";
  }
});

function renderHistorial(historial) {
  const api = initHistorialGrid();
  if (!api) return;
  const rows = historial.slice().sort((a, b) => String(b.creado || "").localeCompare(String(a.creado || "")));
  api.setGridOption("rowData", rows);
}

/* ---------- Historial en AG Grid ---------- */

let historialGridApi = null;

function cobroCellRenderer(p) {
  const v = p.data;
  const opts = ["pendiente", "emitida", "cobrada"].map((s) =>
    `<option value="${s}"${s === (v.estado_pago || "pendiente") ? " selected" : ""}>${s.charAt(0).toUpperCase() + s.slice(1)}</option>`
  ).join("");
  return `<select class="cobro-sel" data-trip="${v.id}">${opts}</select>`;
}

function estadoCellRenderer(p) {
  return `<span class="badge badge-${estadoClass(p.value)}">${p.value || "—"}</span>`;
}

function accionesCellRenderer() {
  return `<div class="ag-actions">
    <button type="button" class="btn btn-ghost" data-action="info" title="Info del viaje">ℹ️</button>
    <button type="button" class="btn btn-ghost" data-action="files" title="Archivos del conductor">📎</button>
    <button type="button" class="btn btn-ghost" data-action="chat" title="Mensajería con el chofer">💬</button>
    <button type="button" class="btn btn-ghost" data-action="dup" title="Duplicar viaje">⧉</button>
    <button type="button" class="btn btn-ghost btn-delete" data-action="del" title="Eliminar">🗑️</button>
  </div>`;
}

const HISTORIAL_COLS = [
  { headerName: "Referencia", field: "referencia", width: 110, valueGetter: (p) => p.data.referencia || "—" },
  { headerName: "Fecha", field: "creado", width: 160, valueGetter: (p) => p.data.creado || "", valueFormatter: (p) => (p.value ? formatDate(p.value) : "—") },
  { headerName: "Origen", field: "origen", width: 180, valueGetter: (p) => formatAddress(p.data.origen) },
  { headerName: "Destino", field: "destino", width: 180, valueGetter: (p) => formatAddress(p.data.destino) },
  { headerName: "Cliente", field: "cliente", width: 140, filter: "agSetColumnFilter", valueGetter: (p) => p.data.cliente || "—" },
  { headerName: "Conductor", field: "conductor", width: 140, filter: "agSetColumnFilter", valueGetter: (p) => p.data.conductor || "—" },
  { headerName: "Vehículo", field: "vehiculo", width: 120, filter: "agSetColumnFilter", valueGetter: (p) => p.data.terminal || p.data.matricula || "—" },
  { headerName: "Carga", field: "tipo_carga", width: 110, filter: "agSetColumnFilter", valueGetter: (p) => p.data.tipo_carga || "—" },
  { headerName: "Precio", field: "precio", width: 100, filter: "agNumberColumnFilter", valueGetter: (p) => (p.data.precio != null ? Number(p.data.precio) : null), valueFormatter: (p) => (p.value != null ? formatEUR(p.value) : "—") },
  { headerName: "Gastos", field: "gastos", width: 100, filter: "agNumberColumnFilter", valueGetter: (p) => (p.data.gastos != null ? Number(p.data.gastos) : null), valueFormatter: (p) => (p.value != null ? formatEUR(p.value) : "—") },
  { headerName: "Margen", field: "margen", width: 100, filter: "agNumberColumnFilter", valueGetter: (p) => ((p.data.precio || p.data.gastos) ? Number((p.data.precio || 0) - (p.data.gastos || 0)) : null), valueFormatter: (p) => (p.value != null ? formatEUR(p.value) : "—") },
  { headerName: "Km", field: "km_total", width: 90, filter: "agNumberColumnFilter", valueGetter: (p) => (p.data.km_total != null ? Number(p.data.km_total) : null), valueFormatter: (p) => (p.value != null ? `${p.value} km` : "—") },
  { headerName: "Factura", field: "factura", width: 120, valueGetter: (p) => p.data.factura || "—" },
  { headerName: "Cobro", field: "estado_pago", width: 120, filter: "agSetColumnFilter", cellRenderer: cobroCellRenderer },
  { headerName: "Estado", field: "estado", width: 120, filter: "agSetColumnFilter", cellRenderer: estadoCellRenderer },
  { headerName: "", field: "acciones", cellRenderer: accionesCellRenderer, sortable: false, filter: false, pinned: "right", width: 170 },
];

function initHistorialGrid() {
  if (historialGridApi) return historialGridApi;
  const elx = document.getElementById("history-grid");
  if (!elx || typeof agGrid === "undefined") return null;
  historialGridApi = agGrid.createGrid(elx, {
    theme: agGrid.themeQuartz,
    columnDefs: HISTORIAL_COLS,
    rowData: [],
    defaultColDef: { resizable: true, sortable: true, filter: true, minWidth: 70 },
    getRowId: (p) => String(p.data.id),
    pagination: true,
    paginationPageSize: 50,
    paginationPageSizeSelector: [25, 50, 100, 200],
    overlayNoRowsTemplate: '<span class="empty" style="padding:16px">No hay viajes.</span>',
    onCellClicked: (e) => {
      const t = e.event && e.event.target;
      if (t && t.closest) {
        const btn = t.closest("[data-action]");
        if (btn) {
          const action = btn.getAttribute("data-action");
          const v = e.data;
          if (action === "info") openHistorialDetail(v, "info");
          else if (action === "files") openHistorialDetail(v, "files");
          else if (action === "chat") openHistorialDetail(v, "chat");
          else if (action === "dup") duplicarTrip(v.id);
          else if (action === "del") deleteTrip(v.id);
          return;
        }
        if (t.closest("select, button, input, a")) return;
      }
      openPedidoForm(e.data.id);
    },
  });
  return historialGridApi;
}

async function showHistorialFiles(v) {
  const panel = $("#historial-detail-body");
  if (!panel) return;
  panel.innerHTML = el("p", { class: "muted", text: "Cargando archivos…" }).outerHTML;
  try {
    const res = await fetch(`/api/trips/${encodeURIComponent(v.id)}/files`);
    const data = await res.json();
    const archivos = data?.archivos || [];
    panel.innerHTML = "";
    if (!archivos.length) {
      panel.append(el("p", { class: "muted", text: "Sin archivos del conductor todavía." }));
      return;
    }
    archivos.forEach((f) => {
      const wrap = el("div", { class: "file-item" });
      const name = f.name || "archivo";
      const mime = mimeFor(name);
      const src = `data:${mime};base64,${f.content_b64 || ""}`;
      if (mime.startsWith("image/")) {
        const lnk = el("a", { href: src, download: name, title: "Clic para descargar" });
        lnk.append(el("img", { class: "file-thumb", src, alt: name }));
        wrap.append(lnk);
        wrap.append(el("a", { class: "file-link", href: src, download: name, text: `⬇ ${name}` }));
      } else if (mime === "application/pdf") {
        wrap.append(el("embed", { class: "qp-pdf", src, type: "application/pdf" }));
        wrap.append(el("a", { class: "file-link", href: src, download: name, text: `⬇ ${name}` }));
      } else {
        wrap.append(el("a", { class: "file-link", href: src, download: name, text: `⬇ ${name}` }));
      }
      wrap.append(el("span", { class: "muted", text: `${name} · ${f.ftime || ""}` }));
      panel.append(wrap);
    });
  } catch (err) {
    panel.innerHTML = el("div", { class: "alert alert-error" },
      el("span", { text: "No se pudo cargar: " + err.message })).outerHTML;
  }
}

function openHistorialDetail(v, tipo) {
  const wrap = $("#historial-detail");
  const panel = $("#historial-detail-body");
  if (!wrap || !panel) return;
  wrap.hidden = false;
  chatsAbiertos.delete(panel);
  const titulo = $("#historial-detail-title");
  if (tipo === "chat") {
    renderChatPanel(v, panel);
    chatsAbiertos.set(panel, v);
    if (titulo) titulo.textContent = `${v.referencia || v.id || "Viaje"} · chat`;
  } else if (tipo === "files") {
    if (titulo) titulo.textContent = `${v.referencia || v.id || "Viaje"} · archivos`;
    showHistorialFiles(v);
  } else {
    if (titulo) titulo.textContent = `${v.referencia || v.id || "Viaje"} · información`;
    renderMensajesPanel(v, panel);
  }
  wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

document.addEventListener("click", (e) => {
  if (e.target.closest && e.target.closest("#historial-detail-close")) {
    const wrap = $("#historial-detail");
    const panel = $("#historial-detail-body");
    if (wrap) wrap.hidden = true;
    if (panel) { panel.innerHTML = ""; chatsAbiertos.delete(panel); }
  }
});

document.addEventListener("change", (e) => {
  if (e.target.classList && e.target.classList.contains("cobro-sel")) {
    const tripId = e.target.getAttribute("data-trip");
    const value = e.target.value;
    updateTripPago(tripId, value);
    if (historialGridApi) historialGridApi.applyTransaction({ update: [{ id: tripId, estado_pago: value }] });
  }
});

function parseQpReport(xml) {
  const ans = [];
  const re = /<Answer question="([^"]*)"[^>]*>([\s\S]*?)<\/Answer>/g;
  let m;
  while ((m = re.exec(xml)) !== null) {
    const values = [];
    const vre = /<Value option="([^"]*)" value="([^"]*)"/g;
    let vm;
    while ((vm = vre.exec(m[2])) !== null) values.push({ option: vm[1], value: vm[2] });
    ans.push({ question: m[1], values });
  }
  return ans;
}

function formatQpValue(values) {
  if (!values.length) return "";
  if (values.length === 1 && (values[0].option === "IN" || values[0].option === "MSG")) {
    return values[0].value;
  }
  const opts = values.filter((v) => v.value === "true").map((v) => v.option);
  if (opts.length) return opts.join(", ");
  return values.map((v) => (v.option ? `${v.option}=${v.value}` : v.value)).join(", ");
}

function renderQpAnswer(a, filesMap) {
  const row = el("div", { class: "qp-row" });
  row.append(el("span", { class: "qp-q", text: a.question }));
  const fileVals = a.values.filter((v) => v.option === "IN" && filesMap[v.value]);
  if (fileVals.length) {
    const wrap = el("span", { class: "qp-a" });
    fileVals.forEach((fv) => {
      const f = filesMap[fv.value];
      const mime = mimeFor(fv.value);
      const src = `data:${mime};base64,${f.content_b64 || ""}`;
      if (mime.startsWith("image/")) {
        const lnk = el("a", { href: src, download: fv.value, title: "Clic para descargar" });
        lnk.append(el("img", { class: "file-thumb qp-img", src, alt: fv.value }));
        wrap.append(lnk);
        wrap.append(el("a", { class: "file-link", href: src, download: fv.value, text: `⬇ ${fv.value}` }));
      } else if (mime === "application/pdf") {
        wrap.append(el("embed", { class: "qp-pdf", src, type: "application/pdf" }));
        wrap.append(el("a", { class: "file-link", href: src, download: fv.value, text: `⬇ ${fv.value}` }));
      } else {
        wrap.append(el("a", { class: "file-link", href: src, download: fv.value, text: `⬇ ${fv.value}` }));
      }
    });
    row.append(wrap);
  } else {
    row.append(el("span", { class: "qp-a", text: formatQpValue(a.values) }));
  }
  return row;
}

async function renderMensajesPanel(v, panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando información…" }).outerHTML;
  try {
    const [res, fres] = await Promise.all([
      fetch(`/api/trips/${encodeURIComponent(v.id)}/mensajes`),
      fetch(`/api/trips/${encodeURIComponent(v.id)}/files`),
    ]);
    const data = await res.json();
    const fdata = await fres.json();
    const mensajes = (data?.mensajes || []).filter((m) => m.tipo === "cuestionario");
    const archivos = fdata?.archivos || [];
    const filesMap = {};
    archivos.forEach((f) => { filesMap[f.name] = f; });
    panel.innerHTML = "";
    if (!mensajes.length) {
      panel.append(el("p", { class: "muted", text: "Sin informes del viaje todavía." }));
    } else {
      mensajes.forEach((m) => {
        const item = el("div", { class: "file-item" });
        const cabecera = [
          "📝 cuestionario",
          m.messagetype ? `tipo: ${m.messagetype}` : null,
          m.time ? formatDate(m.time) : null,
        ].filter(Boolean).join(" · ");
        item.append(el("div", { class: "muted", text: cabecera }));
        if (m.body) {
          const ans = parseQpReport(m.body);
          if (ans.length) {
            const rep = /<Report id="([^"]+)" version="(\d+)"/.exec(m.body);
            if (rep) item.append(el("div", { class: "qp-title", text: `${rep[1]} · v${rep[2]}` }));
            const list = el("div", { class: "qp-list" });
            ans.forEach((a) => { list.append(renderQpAnswer(a, filesMap)); });
            item.append(list);
          } else {
            item.append(el("div", { text: m.body }));
          }
        }
        panel.append(item);
      });
    }
  } catch (err) {
    panel.innerHTML = el("div", { class: "alert alert-error" },
      el("span", { text: "No se pudo cargar la información: " + err.message })).outerHTML;
  }
}

async function duplicarTrip(tripId) {
  if (!confirm("¿Duplicar este viaje?\nSe creará una copia SIN asignar (sin vehículo ni conductor) para asignarla desde el planificador.")) return;
  try {
    const res = await fetch(`/api/trips/${encodeURIComponent(tripId)}/duplicar`, { method: "POST" });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showError(`Viaje duplicado (${data.trip_id}). Asígnalo desde el planificador.`);
      loadHistory();
    } else {
      showError(data?.detail?.error || data?.detail || `No se pudo duplicar (HTTP ${res.status}).`);
    }
  } catch (err) {
    showError("No se pudo duplicar: " + err.message);
  }
}

async function deleteTrip(tripId) {
  if (!confirm(`¿Eliminar el viaje ${tripId}?\nSe borrará del terminal y del historial.`)) return;
  try {
    const res = await fetch(`/api/trips/${encodeURIComponent(tripId)}`, { method: "DELETE" });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      loadHistory();
      if (data.terminal_eliminado === false) {
        showError(`Viaje borrado del historial, pero el terminal respondió: ${data.error_terminal || "error desconocido"}.`);
      }
    } else {
      showError(data?.detail?.error || data?.detail || `No se pudo eliminar (HTTP ${res.status}).`);
    }
  } catch (err) {
    showError("No se pudo eliminar: " + err.message);
  }
}

function mimeFor(name) {
  const ext = String(name || "").split(".").pop().toLowerCase();
  if (ext === "png") return "image/png";
  if (ext === "jpg" || ext === "jpeg") return "image/jpeg";
  if (ext === "gif") return "image/gif";
  if (ext === "pdf") return "application/pdf";
  return "application/octet-stream";
}

async function updateTripPago(tripId, estadoPago) {
  try {
    const res = await fetch(`/api/trips/${encodeURIComponent(tripId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ estado_pago: estadoPago }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  } catch (err) {
    showError("No se pudo actualizar el cobro: " + err.message);
  }
}

function estadoClass(estado) {
  const s = String(estado).toLowerCase();
  if (/^(finalizado|completado|entregado|ok|finished)$/.test(s)) return "ok";
  if (/^(en curso|busy|activo)$/.test(s)) return "info";
  if (/^(recibido|aceptado|received|accepted|asignado)$/.test(s)) return "info";
  if (/^(sin_asignar|sin asignar|pedido)$/.test(s)) return "pedido";
  if (/^(enviado|nuevo|new|pendiente|creado)$/.test(s)) return "warn";
  if (/^(error|fallido|rechazado|cancelado|refused)$/.test(s)) return "error";
  return "neutral";
}

/* ---------- Estado del terminal ---------- */

async function loadHealth() {
  try {
    const res = await fetch("/api/health");
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.terminal) {
      $("#terminal-name").textContent = `${data.terminal} · ${data.customer || ""}`;
      $("#terminal-status").hidden = false;
    }
  } catch (_) {
    /* sin conexión: se mantiene oculto */
  }
}

$("#btn-refresh")?.addEventListener("click", loadHistory);

/* ---------- Distancia estimada (km) ---------- */

function collectPuntos() {
  const puntos = [];
  const add = (container, label) => {
    const lat = parseFloat($(".f-lat", container)?.value);
    const lng = parseFloat($(".f-lng", container)?.value);
    const ciudad = $(".f-ciudad", container)?.value.trim()
      || $(".f-nombre", container)?.value.trim() || label;
    if (!isNaN(lat) && !isNaN(lng)) puntos.push({ lat, lng, nombre: ciudad });
  };
  add($('[data-address="origen"]'), "Origen");
  for (const item of $("#paradas-list").children) add(item, "Parada");
  add($('[data-address="destino"]'), "Destino");
  return puntos;
}

let lastKmResult = null;

let map = null;
let routeLayer = null;

function decodePolyline(str) {
  const coords = [];
  let lat = 0, lng = 0, i = 0;
  while (i < str.length) {
    let result = 0, shift = 0, b;
    do { b = str.charCodeAt(i++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    const dlat = (result & 1) ? ~(result >> 1) : (result >> 1);
    lat += dlat;
    result = 0; shift = 0;
    do { b = str.charCodeAt(i++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
    const dlng = (result & 1) ? ~(result >> 1) : (result >> 1);
    lng += dlng;
    coords.push([lat / 1e5, lng / 1e5]);
  }
  return coords;
}

/* ---------- Selección de puntos en el mapa ---------- */

let mapTarget = null;     // contenedor de dirección a rellenar con el clic
let mapTargetMode = "destino";
let mapMarker = null;     // marcador del punto fijado

function clearMapMarker() {
  if (mapMarker && map) { map.removeLayer(mapMarker); mapMarker = null; }
}

function setMapTarget(mode, paradaEl) {
  mapTargetMode = mode;
  if (mode === "origen") mapTarget = $('[data-address="origen"]');
  else if (mode === "destino") mapTarget = $('[data-address="destino"]');
  else if (mode === "parada") mapTarget = paradaEl || ($("#paradas-list")?.lastElementChild || null);
  document.querySelectorAll(".map-target").forEach((b) =>
    b.classList.toggle("active", b.dataset.target === mode));
  clearMapMarker();
}

async function onMapClick(e) {
  if (!mapTarget) { showError("Selecciona Origen, Parada o Destino en la barra del mapa."); return; }
  const lat = e.latlng.lat, lng = e.latlng.lng;
  const setF = (cls, val) => { const f = $(`.${cls}`, mapTarget); if (f) f.value = val ?? ""; };
  setF("f-lat", Number(lat.toFixed(6)));
  setF("f-lng", Number(lng.toFixed(6)));
  clearMapMarker();
  mapMarker = L.marker([lat, lng]).addTo(map);
  try {
    const res = await fetch(`/api/reverse-geocode?lat=${lat}&lng=${lng}`);
    const d = await res.json();
    const r = d?.resultado;
    if (r) {
      setF("f-nombre", r.nombre || "");
      setF("f-calle", r.calle || "");
      setF("f-numero", r.numero || "");
      setF("f-ciudad", r.ciudad || "");
      setF("f-cp", r.cp || "");
      setF("f-pais", r.pais || "ES");
    }
  } catch (_) { /* las coordenadas ya quedaron fijadas */ }
}

document.querySelectorAll(".map-target").forEach((b) =>
  b.addEventListener("click", () => setMapTarget(b.dataset.target)));
setMapTarget("destino"); // objetivo por defecto

function initMap() {
  if (map) return map;
  const el = document.getElementById("map");
  if (!el || typeof L === "undefined") return null;
  map = L.map(el).setView([40.0, -3.7], 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "© OpenStreetMap",
  }).addTo(map);
  map.on("click", onMapClick);
  return map;
}

/* ---------- Mapa de flota (planificador) ---------- */

let fleetMap = null;
let fleetMarkers = {};
let fleetMapAdjusted = false;

function initFleetMap() {
  if (fleetMap) return fleetMap;
  const el = document.getElementById("fleet-map");
  if (!el || typeof L === "undefined") return null;
  fleetMap = L.map(el).setView([40.0, -3.7], 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "© OpenStreetMap",
  }).addTo(fleetMap);
  return fleetMap;
}

async function loadFleetPositions() {
  if (isSuperadmin()) return;
  const m = initFleetMap();
  if (!m) return;
  let data = [];
  try {
    const res = await fetch("/api/vehiculos/posiciones", { headers: { Authorization: "Bearer " + getToken() } });
    if (!res.ok) return;
    const d = await res.json();
    data = d.vehiculos || [];
  } catch (_) { return; }

  const activos = new Set();
  data.forEach((v) => {
    if (v.last_lat == null || v.last_lng == null) return;
    activos.add(v.id);
    const latlng = [Number(v.last_lat), Number(v.last_lng)];
    const label = v.matricula && v.matricula !== v.id ? `${v.id} · ${v.matricula}` : v.id;
    const html = `<strong>${label}</strong><br>${[v.marca, v.modelo].filter(Boolean).join(" ") || ""}<br><span class="muted">Actualizado: ${formatDate(v.last_position_time)}</span>`;
    if (fleetMarkers[v.id]) {
      fleetMarkers[v.id].setLatLng(latlng).setPopupContent(html);
    } else {
      fleetMarkers[v.id] = L.marker(latlng).addTo(m).bindPopup(html);
    }
  });
  for (const id of Object.keys(fleetMarkers)) {
    if (!activos.has(id)) { m.removeLayer(fleetMarkers[id]); delete fleetMarkers[id]; }
  }
  if (data.length && !fleetMapAdjusted) {
    const pts = data.filter((v) => v.last_lat != null && v.last_lng != null)
      .map((v) => [Number(v.last_lat), Number(v.last_lng)]);
    if (pts.length) { m.fitBounds(pts); fleetMapAdjusted = true; }
  }
}

setInterval(loadFleetPositions, 60000);

/* ---------- Replay de ruta (telemetría histórica) ---------- */

let replayLayer = null;
let replayMarker = null;
let replayPoints = [];
let replayIndex = 0;
let replayTimer = null;
let replayPlaying = false;

function fmtDTLocal(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function headingToCompass(h) {
  if (h == null || isNaN(h)) return "—";
  const dirs = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"];
  return dirs[Math.round((((h % 360) + 360) % 360) / 45) % 8];
}

function populateReplayVehicles() {
  const sel = $("#replay-vehiculo");
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = "";
  const opts = (vehiculosCache || []).map((v) => ({
    id: v.id,
    label: v.matricula && v.matricula !== v.id ? `${v.id} · ${v.matricula}` : v.id,
  }));
  if (!opts.length) {
    sel.append(el("option", { value: "", text: "Sin vehículos" }));
    return;
  }
  opts.forEach((o) => sel.append(el("option", { value: o.id, text: o.label })));
  if (prev && opts.some((o) => o.id === prev)) sel.value = prev;
  else sel.value = opts[0].id;
}

function setDefaultReplayRange() {
  const hasta = new Date();
  const desde = new Date(hasta.getTime() - 24 * 3600 * 1000);
  $("#replay-desde").value = fmtDTLocal(desde);
  $("#replay-hasta").value = fmtDTLocal(hasta);
}

function replayStop() {
  replayPlaying = false;
  if (replayTimer) { clearInterval(replayTimer); replayTimer = null; }
  const btn = $("#btn-replay-play");
  if (btn) btn.textContent = "▶";
}

function renderReplayFrame(i) {
  if (!replayPoints.length) return;
  i = Math.max(0, Math.min(replayPoints.length - 1, i));
  replayIndex = i;
  const p = replayPoints[i];
  if (replayMarker) replayMarker.setLatLng([p.lat, p.lng]);
  $("#replay-slider").value = i;
  const spd = p.speed != null ? `${Math.round(p.speed)} km/h` : "—";
  const km = p.mileage != null ? `${Math.round(p.mileage).toLocaleString("es-ES")} km` : "—";
  $("#replay-info").innerHTML =
    `<strong>${formatDate(p.time)}</strong> · ${spd} · ${headingToCompass(p.heading)} · ${km} · <span class="muted">${i + 1}/${replayPoints.length}</span>`;
}

async function loadTelemetryReplay() {
  replayStop();
  const m = initFleetMap();
  const veh = $("#replay-vehiculo").value;
  const desdeRaw = $("#replay-desde").value;
  const hastaRaw = $("#replay-hasta").value;
  const msg = $("#replay-msg");
  const player = $("#replay-player");
  if (!m) { msg.hidden = false; msg.textContent = "Mapa no disponible."; return; }
  if (!veh) { msg.hidden = false; msg.textContent = "No hay vehículos para consultar."; return; }

  const params = new URLSearchParams({ vehiculo: veh, limit: "5000" });
  if (desdeRaw) params.set("desde", new Date(desdeRaw).toISOString());
  if (hastaRaw) params.set("hasta", new Date(hastaRaw).toISOString());

  msg.hidden = false;
  msg.textContent = "Cargando traza…";
  try {
    const res = await fetch(`/api/telemetria?${params.toString()}`);
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d?.detail || `HTTP ${res.status}`);
    const pts = (d.puntos || [])
      .map((p) => ({ lat: Number(p.lat), lng: Number(p.lng), time: p.time, speed: p.speed, heading: p.heading, mileage: p.mileage }))
      .filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lng))
      .reverse(); // el endpoint devuelve DESC por tiempo; para el replay queremos ASC
    replayPoints = pts;
    if (!pts.length) {
      msg.textContent = "Sin datos de telemetría en ese rango.";
      player.hidden = true;
      return;
    }
    if (replayLayer) m.removeLayer(replayLayer);
    replayLayer = L.layerGroup().addTo(m);
    const coords = pts.map((p) => [p.lat, p.lng]);
    L.polyline(coords, { color: "#f59e0b", weight: 4, opacity: 0.85 }).addTo(replayLayer);
    L.circleMarker(coords[0], { radius: 7, color: "#16a34a", fillColor: "#16a34a", fillOpacity: 1 }).addTo(replayLayer);
    L.circleMarker(coords[coords.length - 1], { radius: 7, color: "#dc2626", fillColor: "#dc2626", fillOpacity: 1 }).addTo(replayLayer);
    replayMarker = L.circleMarker(coords[0], { radius: 8, color: "#2563eb", fillColor: "#2563eb", fillOpacity: 1, weight: 2 }).addTo(replayLayer);
    m.fitBounds(L.latLngBounds(coords));
    msg.textContent = `${pts.length} puntos cargados.`;
    player.hidden = false;
    $("#replay-slider").max = pts.length - 1;
    renderReplayFrame(0);
  } catch (err) {
    msg.textContent = `No se pudo cargar la traza: ${err.message}`;
    player.hidden = true;
  }
}

function replayPlayPause() {
  const btn = $("#btn-replay-play");
  if (!replayPoints.length) return;
  if (replayPlaying) { replayStop(); return; }
  if (replayIndex >= replayPoints.length - 1) replayIndex = 0;
  replayPlaying = true;
  btn.textContent = "⏸";
  const speed = parseFloat($("#replay-speed").value) || 1;
  const interval = Math.max(40, Math.round(500 / speed));
  replayTimer = setInterval(() => {
    if (replayIndex >= replayPoints.length - 1) { replayStop(); return; }
    renderReplayFrame(replayIndex + 1);
  }, interval);
}

function bindReplay() {
  const btnCargar = $("#btn-replay-cargar");
  const btnPlay = $("#btn-replay-play");
  const slider = $("#replay-slider");
  const speed = $("#replay-speed");
  if (btnCargar) btnCargar.addEventListener("click", loadTelemetryReplay);
  if (btnPlay) btnPlay.addEventListener("click", replayPlayPause);
  if (slider) slider.addEventListener("input", () => { replayStop(); renderReplayFrame(parseInt(slider.value, 10)); });
  if (speed) speed.addEventListener("change", () => { if (replayPlaying) { replayStop(); replayPlayPause(); } });
}

function drawRoute(ptv) {
  const m = initMap();
  if (!m) return;
  if (routeLayer) m.removeLayer(routeLayer);
  routeLayer = L.layerGroup().addTo(m);
  const coords = decodePolyline(ptv.polyline || "");
  if (coords.length) {
    L.polyline(coords, { color: "#2563eb", weight: 5, opacity: 0.8 }).addTo(routeLayer);
    L.circleMarker(coords[0], { radius: 7, color: "#16a34a", fillColor: "#16a34a", fillOpacity: 1 }).addTo(routeLayer);
    L.circleMarker(coords[coords.length - 1], { radius: 7, color: "#dc2626", fillColor: "#dc2626", fillOpacity: 1 }).addTo(routeLayer);
    m.fitBounds(L.latLngBounds(coords));
  }
  (ptv.traffic_events || []).forEach((ev) => {
    if (ev.lat == null || ev.lng == null) return;
    L.marker([ev.lat, ev.lng]).addTo(routeLayer)
      .bindPopup(`<strong>Tráfico</strong><br>${ev.description || ""}${ev.delay ? `<br>Retraso: ${(ev.delay / 60).toFixed(1)} min` : ""}`);
  });
}

function renderKmBox(result) {
  const box = $("#km-result");
  const totalKm = result.total_km || 0;
  const precio = parseFloat($('[name="precio"]').value) || 0;
  const nota = result.metodo === "linea_recta" ? " <span class='muted'>(línea recta)</span>" : "";
  const filas = (result.tramos || []).map((t) =>
    `<div class="km-tramo"><span>${t.de} → ${t.a}</span><strong>${t.km} km${t.toll_km ? ` · ${t.toll_km} km peaje` : ""}</strong></div>`
  ).join("");
  const precioKm = (totalKm > 0 && precio > 0)
    ? `<div class="km-precio">€/km estimado: <strong>${eurKm(precio, totalKm)}</strong></div>`
    : "";
  const tollKm = result.total_toll_km || 0;
  let peajeHtml = "";
  if (tollKm > 0) {
    const cat = "pesado4";
    const rate = tarifasCache[cat] ?? 0.30;
    const peaje = tollKm * rate;
    peajeHtml = `<div class="km-precio">Peaje estimado: <strong>${formatEUR(peaje)}</strong> <span class='muted'>(${tollKm} km de peaje · ${PEAJE_LABELS[cat] || cat} · ${rate.toFixed(2)} €/km)</span></div>`;
  }
  let resumen = "";
  const ptv = result.ptv;
  if (ptv && ptv.distance_km) {
    const sch = ptv.schedule || {};
    const t = [`<strong>${ptv.distance_km} km</strong>`];
    const totalMin = sch.total_min || ptv.travel_time_min || 0;
    t.push(`${Math.round(totalMin)} min`);
    const pausa = (sch.break_min || 0) + (sch.rest_min || 0);
    if (pausa) t.push(`incl. ${Math.round(pausa)} min pausa/descanso`);
    if (ptv.traffic_delay_min) t.push(`+${ptv.traffic_delay_min} min tráfico`);
    if (ptv.toll != null) t.push(`peaje <strong>${formatEUR(ptv.toll)}</strong>`);
    resumen = `<div class="km-precio">Ruta óptima (PTV): ${t.join(" · ")}</div>`;
    if (sch.end_time) resumen += `<div class="km-precio">Llegada estimada: <strong>${formatDate(sch.end_time)}</strong></div>`;
  } else {
    resumen = peajeHtml;
  }
  box.innerHTML = `<div class="km-box">${resumen}${filas}<div class="km-total">Total (OSRM): <strong>${totalKm} km</strong>${nota}</div>${precioKm}</div>`;
}

async function calcularKm() {
  const box = $("#km-result");
  const puntos = collectPuntos();
  if (puntos.length < 2) {
    box.hidden = false;
    box.innerHTML = '<p class="muted">Necesitas coordenadas en al menos 2 puntos (usa «Buscar lugar» en cada dirección).</p>';
    return;
  }
  box.hidden = false;
  box.innerHTML = '<p class="muted">Calculando…</p>';
  try {
    const res = await fetch("/api/ruta", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ puntos, terminal: "", conduccion_acumulada_min: 0 }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d?.detail || `HTTP ${res.status}`);
    lastKmResult = d;
    renderKmBox(d);
    if (d.ptv && d.ptv.polyline) drawRoute(d.ptv);
  } catch (err) {
    lastKmResult = null;
    box.innerHTML = `<div class="alert alert-error"><span>No se pudo calcular: ${err.message}</span></div>`;
  }
}

$("#btn-calcular-km").addEventListener("click", calcularKm);
$('[name="precio"]').addEventListener("input", () => {
  if (lastKmResult && !$("#km-result").hidden) renderKmBox(lastKmResult);
});

/* ---------- Pantallas ---------- */

const SCREENS = ["planificador", "nuevo", "historial", "contabilidad", "maestros", "rrhh", "config", "empresas"];
let currentScreen = "planificador";
let previousScreen = "planificador";
let previousContabTab = "dashboard";

function showScreen(name) {
  currentScreen = name;
  for (const s of SCREENS) {
    const btn = document.querySelector(`.side-item[data-screen="${s}"]`);
    const panel = document.getElementById(`screen-${s}`);
    if (btn) btn.classList.toggle("active", s === name);
    if (panel) panel.hidden = s !== name;
  }
  if (name === "nuevo" && map) setTimeout(() => map.invalidateSize(), 50);
  if (name === "planificador" || name === "historial") loadHistory();
  if (name === "planificador") { initFleetMap(); setTimeout(() => fleetMap?.invalidateSize(), 60); loadFleetPositions(); }
  if (name === "contabilidad") { showContabilidadTab("dashboard"); }
  if (name === "maestros") { loadClientes(); loadConductores(); loadProveedores(); loadVehiculos(); loadTarifasPeaje(); loadTransportistas(); loadMantenimientos(); loadAlertas(); renderCategoriasList(); loadDirecciones(); }
  if (name === "rrhh") { showRrhhTab("resumen"); }
  if (name === "config") { loadConfigScreen(); }
  if (name === "empresas") { loadEmpresas(); }
}

document.querySelectorAll(".side-item").forEach((b) =>
  b.addEventListener("click", () => showScreen(b.dataset.screen))
);

$("#btn-nuevo-pedido").addEventListener("click", () => { editingTripId = null; previousScreen = currentScreen; resetPedidoForm(); showScreen("nuevo"); });
$("#btn-cancelar-pedido").addEventListener("click", () => { editingTripId = null; resetPedidoForm(); goBack(); });

function goBack() {
  if (previousScreen === "contabilidad") {
    showScreen("contabilidad");
    showContabilidadTab(previousContabTab || "dashboard");
  } else {
    showScreen(previousScreen);
  }
}

/* ---------- Ingresos ---------- */

function formatEUR(n) {
  return new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" }).format(n || 0);
}

function eurKm(precio, km) {
  if (!precio || !km) return "—";
  return `${(precio / km).toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €/km`;
}

function ingTable(rows, conCostes) {
  const headers = ["Concepto", "Viajes", "Km", "Total", "Gastos", "Margen"];
  if (conCostes) headers.push("Costes fijos", "Gastos vehículo", "Estructura", "Margen real");
  headers.push("€/km");
  const data = rows.map((r) => {
    const cells = [r.nombre, String(r.viajes ?? 0), r.km ? `${r.km} km` : "—", formatEUR(r.total),
      r.gastos != null ? formatEUR(r.gastos) : "—", r.margen != null ? formatEUR(r.margen) : "—"];
    if (conCostes) cells.push(
      r.costes_fijos != null ? formatEUR(r.costes_fijos) : "—",
      r.gastos_vehiculo != null ? formatEUR(r.gastos_vehiculo) : "—",
      r.costes_estructura != null ? formatEUR(r.costes_estructura) : "—",
      r.margen_real != null ? formatEUR(r.margen_real) : "—");
    cells.push(r.eur_km != null ? `${r.eur_km} €/km` : "—");
    return cells;
  });
  return dataGrid(headers, data, { emptyMsg: "Sin datos todavía." });
}

function mesTable(rows) {
  const headers = ["Mes", "Viajes", "Total", "Gastos", "Margen", "Cobrado", "Pendiente"];
  const data = rows.map((r) => [r.mes, String(r.viajes ?? 0), formatEUR(r.total), formatEUR(r.gastos), formatEUR(r.margen), formatEUR(r.cobrado), formatEUR(r.pendiente)]);
  return dataGrid(headers, data, { emptyMsg: "Sin datos." });
}

function ingDetalleTable(rows) {
  return builderGrid(
    ["Fecha", "Origen", "Destino", "Cliente", "Vehículo", "Precio", "Gastos", "Margen", "Km", "Cobro", "Factura"],
    rows,
    (r) => [formatDate(r.creado), r.origen || "—", r.destino || "—", r.cliente || "—", r.terminal || "—",
      formatEUR(r.precio), formatEUR(r.gastos), formatEUR(r.margen), r.km_total ? `${r.km_total} km` : "—",
      r.estado_pago || "—", r.factura || "—"],
    (r) => openPedidoForm(r.id),
    "Sin viajes en el rango."
  );
}

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const d = await res.json();
    const cfg = d.config || {};
    const inp = $("#cfg-estructura-pct");
    if (inp && cfg.costes_estructura_pct != null) inp.value = cfg.costes_estructura_pct;
  } catch (_) {}
}

$("#btn-cfg-estructura")?.addEventListener("click", async () => {
  const inp = $("#cfg-estructura-pct");
  const val = parseFloat(inp?.value) || 0;
  const res = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ costes_estructura_pct: val }),
  }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar la configuración."); return; }
  loadIngresos();
});

async function loadIngresos() {
  const box = $("#ingresos");
  if (!box) return;
  try {
    const params = new URLSearchParams();
    const desde = $("#ing-desde")?.value || "";
    const hasta = $("#ing-hasta")?.value || "";
    const estado = $("#ing-estado")?.value || "";
    if (desde) params.set("desde", desde);
    if (hasta) params.set("hasta", hasta);
    if (estado) params.set("estado", estado);
    const qs = params.toString();
    const res = await fetch("/api/ingresos" + (qs ? `?${qs}` : ""));
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const d = await res.json();
    box.innerHTML = "";

    const detalle = [
      `${d.viajes || 0} viajes`,
      d.total_km ? `${d.total_km} km` : null,
      d.eur_km != null ? `${d.eur_km} €/km` : null,
    ].filter(Boolean).join(" · ");
    box.append(
      el("div", { class: "ing-total" },
        el("div", { class: "ing-total-main" },
          el("span", { class: "muted", text: "Ingresos totales" }),
          el("strong", { text: formatEUR(d.total) }),
          el("span", { class: "muted", text: `(${detalle})` }),
        ),
        el("div", { class: "ing-margen" },
          el("span", { text: `Gastos: ${formatEUR(d.total_gastos || 0)}` }),
          el("span", { text: `Estructura: ${formatEUR(d.costes_estructura || 0)}` }),
          el("strong", { text: `Margen real: ${formatEUR(d.margen_real != null ? d.margen_real : 0)}` }),
        ),
        el("div", { class: "ing-cobro" },
          el("span", { text: `Cobrado: ${formatEUR(d.cobrado || 0)}` }),
          el("span", { text: `Pendiente: ${formatEUR(d.pendiente || 0)}` }),
        ),
      ),
    );

    box.append(el("h3", { class: "ing-subtitle", text: "Detalle de viajes" }));
    box.append(ingDetalleTable(d.detalle || []));

    box.append(el("h3", { class: "ing-subtitle", text: "Por vehículo" }));
    box.append(ingTable(d.por_vehiculo, true));

    box.append(el("h3", { class: "ing-subtitle", text: "Por cliente" }));
    box.append(ingTable(d.por_cliente, false));

    box.append(el("h3", { class: "ing-subtitle", text: "Por mes" }));
    box.append(mesTable(d.por_mes));
  } catch (err) {
    box.innerHTML = el("div", { class: "alert alert-error" },
      el("span", { text: "No se pudo cargar los ingresos: " + err.message })).outerHTML;
  }
}

async function loadGastoTerminals() {
  const sel = $('[name="gasto-terminal"]');
  const fsel = $("#gasto-filter-terminal");
  try {
    const res = await fetch("/api/terminals");
    const d = await res.json();
    const terminales = d.terminales || [];
    [sel, fsel].forEach((s) => {
      if (!s) return;
      s.innerHTML = "";
      if (s === fsel) s.append(el("option", { value: "", text: "Todos" }));
      else s.append(el("option", { value: "", text: "— General / empresa —" }));
      terminales.forEach((t) => s.append(el("option", { value: t.id, text: t.name || t.id })));
    });
  } catch (_) {
    if (sel) sel.innerHTML = '<option value="">Error</option>';
  }
}

async function loadProveedores() {
  const sel = $('[name="gasto-proveedor"]');
  const list = $("#proveedores-list");
  try {
    const res = await fetch("/api/proveedores");
    const d = await res.json();
    const provs = d.proveedores || [];
    if (sel) {
      sel.innerHTML = '<option value="">— Sin proveedor —</option>';
      provs.forEach((p) => sel.append(el("option", { value: String(p.id), text: p.nombre + (p.cif ? ` (${p.cif})` : "") })));
    }
    if (list) {
      if (!provs.length) {
        list.innerHTML = '<p class="muted">Sin proveedores registrados.</p>';
      } else {
        maestroTable(list, ["Nombre", "CIF", "Dirección", "Población", "Teléfono", "Email", ""], provs, (p) => [
          p.nombre,
          p.cif || "—",
          p.direccion || "—",
          p.poblacion || "—",
          p.telefono || "—",
          p.email || "—",
          delBtn(async () => {
            await fetch(`/api/proveedores/${p.id}`, { method: "DELETE" }).catch(() => {});
            loadProveedores();
            loadGastosResumen();
          }),
        ], "Sin proveedores registrados.", (p) => {
          editState.proveedor = p;
          formFill([["#prov-nombre", p.nombre], ["#prov-cif", p.cif], ["#prov-direccion", p.direccion],
                    ["#prov-poblacion", p.poblacion], ["#prov-telefono", p.telefono], ["#prov-email", p.email]]);
          setEditBtn("#btn-add-proveedor", true, "Añadir");
        });
      }
    }
  } catch (_) {
    if (list) list.innerHTML = '<p class="muted">No se pudieron cargar los proveedores.</p>';
  }
}

$("#btn-add-proveedor")?.addEventListener("click", async () => {
  const nombre = $("#prov-nombre")?.value.trim() || "";
  if (!nombre) { showError("Indica el nombre del proveedor."); return; }
  const rec = editState.proveedor;
  const body = {
    nombre,
    cif: $("#prov-cif")?.value.trim() || "",
    direccion: $("#prov-direccion")?.value.trim() || "",
    poblacion: $("#prov-poblacion")?.value.trim() || "",
    cp: rec?.cp || "",
    telefono: $("#prov-telefono")?.value.trim() || "",
    email: $("#prov-email")?.value.trim() || "",
  };
  const url = rec ? `/api/proveedores/${rec.id}` : "/api/proveedores";
  const res = await fetch(url, {
    method: rec ? "PATCH" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar el proveedor."); return; }
  editState.proveedor = null;
  ["#prov-nombre", "#prov-cif", "#prov-direccion", "#prov-poblacion", "#prov-telefono", "#prov-email"].forEach((s) => { const e = $(s); if (e) e.value = ""; });
  setEditBtn("#btn-add-proveedor", false, "Añadir");
  loadProveedores();
});

let gastosCache = [];

function renderGastos(gastos) {
  const box = $("#gastos-list");
  if (!box) return;
  box.innerHTML = "";
  if (!gastos.length) { box.innerHTML = '<p class="muted">Sin gastos registrados.</p>'; return; }
  box.append(builderGrid(
    ["Fecha", "Vehículo", "Categoría", "Proveedor", "Concepto", "Importe", ""],
    gastos,
    (g) => {
      const b = el("button", { type: "button", class: "btn-remove", text: "✕", title: "Quitar" });
      b.addEventListener("click", async () => {
        await fetch(`/api/gastos/${g.id}`, { method: "DELETE" }).catch(() => {});
        loadGastos(); loadGastosResumen(); loadIngresos();
      });
      return [g.fecha || "—", g.terminal || "General",
        g.categoria ? (g.cuenta ? `${g.categoria} (${g.cuenta})` : g.categoria) : "—",
        g.proveedor ? (g.proveedor_cif ? `${g.proveedor} (${g.proveedor_cif})` : g.proveedor) : "—",
        g.concepto || "—", formatEUR(g.importe), b];
    },
    (g) => {
      editState.gasto = g;
      formFill([
        ['[name="gasto-terminal"]', g.terminal],
        ['[name="gasto-categoria"]', g.categoria],
        ['[name="gasto-cuenta"]', g.cuenta || ""],
        ['[name="gasto-fecha"]', g.fecha],
        ['[name="gasto-importe"]', g.importe],
        ['[name="gasto-iva"]', g.iva ?? 21],
        ['[name="gasto-retencion"]', g.retencion ?? 0],
        ['[name="gasto-concepto"]', g.concepto],
        ['[name="gasto-proveedor"]', g.proveedor_id || ""],
      ]);
      setEditBtn("#btn-add-gasto", true, "Añadir gasto");
    }
  ));
}

async function loadGastos() {
  const box = $("#gastos-list");
  if (!box) return;
  try {
    const res = await fetch("/api/gastos");
    const d = await res.json();
    gastosCache = d.gastos || [];
    renderGastos(gastosCache);
  } catch (err) {
    box.innerHTML = el("div", { class: "alert alert-error" },
      el("span", { text: "No se pudieron cargar los gastos: " + err.message })).outerHTML;
  }
}

async function loadGastosResumen() {
  const box = $("#gastos-resumen");
  if (!box) return;
  try {
    const res = await fetch("/api/gastos/resumen");
    const d = await res.json();
    const resumen = d.resumen || [];
    box.innerHTML = "";
    if (!resumen.length) {
      box.innerHTML = '<p class="muted">Sin datos.</p>';
      return;
    }
    const wrap = el("div", { class: "table-wrap" });
    box.append(dataGrid(["Categoría", "Nº", "Total"], resumen.map((r) => [r.categoria, String(r.n), formatEUR(r.total)])));

    const provBox = $("#gastos-por-proveedor");
    if (provBox) {
      const pp = d.por_proveedor || [];
      provBox.innerHTML = "";
      if (!pp.length) {
        provBox.innerHTML = '<p class="muted">Sin datos.</p>';
      } else {
        provBox.append(dataGrid(["Proveedor", "CIF", "Nº", "Acumulado"], pp.map((r) => [r.nombre, r.cif || "—", String(r.n), formatEUR(r.total)])));
      }
    }
  } catch (_) {
    box.innerHTML = '<p class="muted">No se pudo cargar el resumen.</p>';
  }
}

$("#btn-add-gasto")?.addEventListener("click", async () => {
  const terminal = $('[name="gasto-terminal"]')?.value || "";
  const categoria = $('[name="gasto-categoria"]')?.value || "";
  const cuenta = $('[name="gasto-cuenta"]')?.value || "";
  const fecha = $('[name="gasto-fecha"]')?.value || "";
  const importe = parseFloat($('[name="gasto-importe"]')?.value) || 0;
  const concepto = $('[name="gasto-concepto"]')?.value.trim() || "";
  const prov = $('[name="gasto-proveedor"]')?.value || "";
  const proveedor_id = prov ? parseInt(prov) : null;
  const iva = parseFloat($('[name="gasto-iva"]')?.value) || 21;
  const retencion = parseFloat($('[name="gasto-retencion"]')?.value) || 0;
  if (!importe) { showError("Indica el importe del gasto."); return; }
  const rec = editState.gasto;
  const url = rec ? `/api/gastos/${rec.id}` : "/api/gastos";
  const res = await fetch(url, {
    method: rec ? "PATCH" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ terminal, categoria, cuenta, fecha, importe, concepto, proveedor_id, iva, retencion }),
  }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar el gasto."); return; }
  editState.gasto = null;
  $('[name="gasto-importe"]').value = "";
  $('[name="gasto-concepto"]').value = "";
  $('[name="gasto-proveedor"]').value = "";
  $('[name="gasto-cuenta"]').value = "";
  $("#gasto-ocr-result").textContent = "";
  setEditBtn("#btn-add-gasto", false, "Añadir gasto");
  loadGastos();
  loadGastosResumen();
  loadIngresos();
});

$("#gasto-foto-input")?.addEventListener("change", async () => {
  const file = $("#gasto-foto-input").files[0];
  if (!file) return;
  const box = $("#gasto-ocr-result");
  box.textContent = "Leyendo ticket…";
  const b64 = await fileToBase64(file);
  try {
    const res = await fetch("/api/ocr", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ imagen: b64 }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(d?.detail?.error || `HTTP ${res.status}`);
    if (d.total != null) $('[name="gasto-importe"]').value = d.total;
    if (d.fecha) $('[name="gasto-fecha"]').value = d.fecha;
    if (d.proveedor) $('[name="gasto-concepto"]').value = d.proveedor;
    box.textContent = `OCR leído: ${d.proveedor || ""} · ${d.total != null ? d.total + " €" : "sin total"} · ${d.fecha || "sin fecha"}. Revisa y corrige si hace falta.`;
  } catch (err) {
    box.textContent = "No se pudo leer la imagen: " + err.message;
  }
});

$("#btn-gasto-filtrar")?.addEventListener("click", () => { loadGastos(); loadGastosResumen(); });
["#gasto-filter-terminal", "#gasto-filter-desde", "#gasto-filter-hasta"].forEach((sel) => {
  const elx = $(sel);
  if (elx) elx.addEventListener("change", loadGastos);
});

$("#btn-export-gastos")?.addEventListener("click", () => exportCSV("gastos"));

$("#btn-refresh-ingresos")?.addEventListener("click", loadIngresos);
$("#btn-ing-filtrar")?.addEventListener("click", loadIngresos);
["#ing-desde", "#ing-hasta", "#ing-estado"].forEach((sel) => {
  const elx = $(sel);
  if (elx) elx.addEventListener("change", loadIngresos);
});

function exportCSV(tipo) {
  window.location.href = `/api/export?tipo=${tipo}`;
}
$("#btn-export-trips")?.addEventListener("click", () => exportCSV("trips"));
$("#btn-export-ingresos")?.addEventListener("click", () => exportCSV("ingresos"));

async function loadCostes() {
  const box = $("#costes-fijos");
  if (!box) return;
  try {
    const res = await fetch("/api/costes-fijos");
    const d = await res.json();
    const costes = d.costes || [];
    box.innerHTML = "";
    if (!costes.length) {
      box.append(el("p", { class: "muted", text: "Sin costes fijos registrados." }));
      return;
    }
    costes.forEach((c) => {
      const item = el("div", { class: "costo-item" },
        el("span", { text: `${c.terminal || "—"} · ${c.concepto}: ${formatEUR(c.importe)}` }),
        el("button", { type: "button", class: "btn-remove", text: "✕", title: "Quitar" }),
      );
      $(".btn-remove", item).addEventListener("click", async () => {
        await fetch(`/api/costes-fijos/${c.id}`, { method: "DELETE" }).catch(() => {});
        loadCostes();
        loadIngresos();
      });
      box.append(item);
    });
  } catch (err) {
    box.innerHTML = el("div", { class: "alert alert-error" },
      el("span", { text: "No se pudieron cargar los costes fijos: " + err.message })).outerHTML;
  }
}

$("#btn-add-coste")?.addEventListener("click", async () => {
  const terminal = $("#cf-terminal")?.value.trim() || "";
  const concepto = $("#cf-concepto")?.value.trim() || "";
  const importe = parseFloat($("#cf-importe")?.value) || 0;
  if (!concepto || !importe) { showError("Indica concepto e importe del coste fijo."); return; }
  const res = await fetch("/api/costes-fijos", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ terminal, concepto, importe }),
  }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo añadir el coste fijo."); return; }
  $("#cf-concepto").value = "";
  $("#cf-importe").value = "";
  loadCostes();
  loadIngresos();
});

/* ---------- Datos maestros: alta ---------- */

$("#btn-add-cliente")?.addEventListener("click", async () => {
  const nombre = $("#cli-nombre")?.value.trim() || "";
  if (!nombre) { showError("Indica el nombre del cliente."); return; }
  const rec = editState.cliente;
  const body = {
    nombre,
    cif: $("#cli-cif")?.value.trim() || "",
    direccion: $("#cli-direccion")?.value.trim() || "",
    poblacion: $("#cli-poblacion")?.value.trim() || "",
    cp: rec?.cp || "",
    telefono: $("#cli-telefono")?.value.trim() || "",
    email: $("#cli-email")?.value.trim() || "",
  };
  const url = rec ? `/api/clientes/${rec.id}` : "/api/clientes";
  const res = await fetch(url, {
    method: rec ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar el cliente."); return; }
  editState.cliente = null;
  ["#cli-nombre", "#cli-cif", "#cli-direccion", "#cli-poblacion", "#cli-telefono", "#cli-email"].forEach((s) => { const e = $(s); if (e) e.value = ""; });
  setEditBtn("#btn-add-cliente", false, "Añadir");
  loadClientes();
});

$("#btn-sync-conductores")?.addEventListener("click", async () => {
  const btn = $("#btn-sync-conductores");
  btn.disabled = true; btn.textContent = "Sincronizando…";
  try {
    const r = await cfetch("/api/conductores/sincronizar-rrhh", { method: "POST" });
    showError(`Conductores: ${r.creados} creados · ${r.actualizados} actualizados (${r.empleados_conductor} empleados conductor en RRHH).`);
    loadConductores();
  } catch (e) { showError(e.message); }
  btn.disabled = false; btn.textContent = "🔄 Nutrir desde RRHH";
});

$("#btn-add-conductor")?.addEventListener("click", async () => {
  const nombre = $("#con-nombre")?.value.trim() || "";
  if (!nombre) { showError("Indica el nombre del conductor."); return; }
  const rec = editState.conductor;
  const body = {
    nombre,
    dni: $("#con-dni")?.value.trim() || "",
    telefono: $("#con-telefono")?.value.trim() || "",
    email: $("#con-email")?.value.trim() || "",
  };
  const url = rec ? `/api/conductores/${rec.id}` : "/api/conductores";
  const res = await fetch(url, {
    method: rec ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar el conductor."); return; }
  editState.conductor = null;
  ["#con-nombre", "#con-dni", "#con-telefono", "#con-email"].forEach((s) => { const e = $(s); if (e) e.value = ""; });
  setEditBtn("#btn-add-conductor", false, "Añadir");
  loadConductores();
});

$("#btn-add-vehiculo")?.addEventListener("click", async () => {
  const id = $("#veh-id")?.value.trim() || "";
  if (!id) { showError("Indica el ID del vehículo (terminal o matrícula)."); return; }
  const body = {
    id,
    categoria: $("#veh-categoria")?.value || "tractora",
    matricula: $("#veh-matricula")?.value.trim() || "",
    marca: $("#veh-marca")?.value.trim() || "",
    modelo: $("#veh-modelo")?.value.trim() || "",
    anno: parseInt($("#veh-anno")?.value) || 0,
    itv: $("#veh-itv")?.value || "",
    seguro: $("#veh-seguro")?.value || "",
    peaje_categoria: $("#veh-peaje")?.value || "pesado4",
    ptv_profile: $("#veh-ptv-profile")?.value || "EUR_TRAILER_TRUCK",
    ejes: parseInt($("#veh-ejes")?.value) || 0,
    mma: parseInt($("#veh-mma")?.value) || 0,
    clase_euro: $("#veh-clase-euro")?.value || "",
    capacidad_peso: parseFloat($("#veh-cap-peso")?.value) || 0,
    capacidad_palets: parseInt($("#veh-cap-palets")?.value) || 0,
  };
  const res = await fetch("/api/vehiculos", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar el vehículo."); return; }
  ["#veh-matricula", "#veh-marca", "#veh-modelo", "#veh-anno", "#veh-itv", "#veh-seguro"].forEach((s) => { const e = $(s); if (e) e.value = ""; });
  loadVehiculos();
});

/* ==================== Contabilidad (doble partida) ==================== */

async function cfetch(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try { const j = await res.json(); msg = j?.detail?.error || j?.detail || msg; } catch (_) {}
    throw new Error(msg);
  }
  return res.json();
}

function showContabilidadTab(tab) {
  contabilidadTab = tab;
  document.querySelectorAll("#contabilidad-tabs .sub-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.ctab === tab)
  );
  const panel = document.getElementById("contabilidad-panel");
  const esEstatico = (tab === "gastos" || tab === "ingresos" || tab === "configuracion");
  document.querySelectorAll(".ctab-panel").forEach((p) => p.hidden = p.dataset.ctab !== tab);
  panel.hidden = esEstatico;
  if (esEstatico) {
    if (tab === "gastos") { loadGastoTerminals(); loadProveedores(); loadCategorias(); loadCuentasGasto(); loadGastos(); loadGastosResumen(); }
    if (tab === "ingresos") { loadConfig(); loadIngresos(); loadCostes(); }
    if (tab === "configuracion") { loadEmpresa(); loadSmtpConfig(); }
    return;
  }
  const fn = {
    dashboard: renderContabilidadDashboard,
    facturacion: renderContabilidadFacturacion,
    diario: renderContabilidadDiario,
    cuentas: renderContabilidadCuentas,
    balance: renderContabilidadBalance,
    pyg: renderContabilidadPyg,
    tesoreria: renderContabilidadTesoreria,
    impuestos: renderContabilidadImpuestos,
    inmovilizado: renderContabilidadInmovilizado,
    explotacion: renderContabilidadExplotacion,
  }[tab];
  if (fn) fn(panel);
}

document.querySelectorAll("#contabilidad-tabs .sub-item").forEach((b) =>
  b.addEventListener("click", () => showContabilidadTab(b.dataset.ctab))
);

/* ==================== Recursos Humanos (RRHH) ==================== */

let rrhhTab = "resumen";
const RRHH_EXPORT = { empleados: "empleados", nominas: "nominas", ausencias: "ausencias" };

function fillRrhhSelect(sel, values, labels) {
  const s = $(sel); if (!s) return;
  s.innerHTML = "";
  values.forEach((v) => s.append(el("option", { value: v, text: (labels && labels[v]) || v })));
}

function showRrhhTab(tab) {
  rrhhTab = tab;
  document.querySelectorAll("#rrhh-tabs .sub-item").forEach((b) => b.classList.toggle("active", b.dataset.rtab === tab));
  document.querySelectorAll(".rtab-panel").forEach((p) => p.hidden = p.dataset.rtab !== tab);
  const panel = document.getElementById("rrhh-panel");
  const esEstatico = tab !== "resumen";
  panel.hidden = esEstatico;
  if (!esEstatico) { renderRrhhResumen(panel); return; }
  if (tab === "empleados") { loadEmpCategorias(); loadEmpleados(); }
  if (tab === "nominas") { loadNominaEmpleados(); loadNominas(); }
  if (tab === "ausencias") { loadNominaEmpleados(); loadAusencias(); }
}

document.querySelectorAll("#rrhh-tabs .sub-item").forEach((b) => b.addEventListener("click", () => showRrhhTab(b.dataset.rtab)));
document.querySelectorAll(".rrhh-export").forEach((b) => b.addEventListener("click", () => exportCSV(b.dataset.export)));
$("#btn-export-rrhh")?.addEventListener("click", () => { const t = RRHH_EXPORT[rrhhTab]; if (t) exportCSV(t); });

async function renderRrhhResumen(panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando…" }).outerHTML;
  try {
    const d = await cfetch("/api/rrhh/resumen");
    const wrap = el("div", { class: "c-wrap" });
    const row = el("div", { class: "kpi-row" });
    row.append(el("div", { class: "kpi-card pos" }, el("div", { class: "kpi-label", text: "Empleados activos" }), el("div", { class: "kpi-val", text: d.activos })));
    row.append(el("div", { class: "kpi-card" }, el("div", { class: "kpi-label", text: "Bajas" }), el("div", { class: "kpi-val", text: d.bajas })));
    row.append(el("div", { class: "kpi-card neg" }, el("div", { class: "kpi-label", text: `Coste nómina ${d.nomina_mes}` }), el("div", { class: "kpi-val", text: formatEUR(d.coste_nomina_mes) })));
    wrap.append(row);

    wrap.append(el("h3", { text: "Plantilla por categoría" }));
    wrap.append(dataGrid(["Categoría", "Empleados"], d.por_categoria.map((c) => [c.categoria, String(c.c)]), { emptyMsg: "Sin empleados registrados." }));

    wrap.append(el("h3", { text: "Próximas ausencias" }));
    wrap.append(dataGrid(["Empleado", "Tipo", "Inicio", "Fin"], d.prox_ausencias.map((a) => [
      `${a.nombre || ""} ${a.apellidos || ""}`.trim(), a.tipo, formatDate(a.fecha_inicio), formatDate(a.fecha_fin)
    ]), { emptyMsg: "Sin ausencias programadas." }));
    panel.innerHTML = ""; panel.append(wrap);
  } catch (e) { contabilidadError(panel, e); }
}

function loadEmpCategorias() {
  fillRrhhSelect("#emp-categoria", ["Conductor", "Administrativo", "Director", "Mecánico", "Comercial", "Mozo", "Otro"]);
  fillRrhhSelect("#emp-contrato", ["Indefinido", "Temporal", "Prácticas", "Obra y servicio", "Fijo discontinuo", "Autónomo"]);
  fillRrhhSelect("#emp-jornada", ["Completa", "Parcial"]);
}

const EMP_FIELDS = ["nombre", "apellidos", "dni", "nss", "email", "telefono", "direccion", "ciudad", "cp", "alta", "baja", "puesto", "salario", "motivo", "banco", "iban", "titular", "convenio", "obs"];

function resetEmpForm() {
  EMP_FIELDS.forEach((f) => { const x = $("#emp-" + f); if (x) x.value = ""; });
  $("#emp-irpf").value = "15";
  $("#emp-categoria").value = "Conductor";
  $("#emp-contrato").value = "Indefinido";
  $("#emp-jornada").value = "Completa";
  $("#emp-dispo").value = "disponible";
}

async function loadEmpleados() {
  const box = $("#empleados-list"); if (!box) return;
  try {
    const res = await fetch("/api/empleados");
    const d = await res.json();
    const emps = d.empleados || [];
    maestroTable(box, ["Nombre", "Categoría", "Puesto", "Contrato", "Salario", "Estado", "Disp.", ""], emps, (e) => {
      const del = el("button", { type: "button", class: "btn-remove", text: "✕", title: "Eliminar" });
      del.addEventListener("click", async () => {
        if (!confirm(`¿Eliminar a ${e.nombre}?`)) return;
        await fetch(`/api/empleados/${e.id}`, { method: "DELETE" }).catch(() => {});
        loadEmpleados();
      });
      return [
        `${e.nombre} ${e.apellidos || ""}`.trim(),
        e.categoria || "—",
        e.puesto || "—",
        e.tipo_contrato || "—",
        e.salario_bruto ? formatEUR(e.salario_bruto) : "—",
        e.activo ? "Activo" : "Baja",
        e.disponibilidad === "disponible" ? "✓" : "✗",
        del,
      ];
    }, "Sin empleados.", (e) => {
      editState.empleado = e;
      formFill([
        ["#emp-nombre", e.nombre], ["#emp-apellidos", e.apellidos], ["#emp-dni", e.dni], ["#emp-nss", e.nss],
        ["#emp-categoria", e.categoria], ["#emp-puesto", e.puesto], ["#emp-contrato", e.tipo_contrato], ["#emp-jornada", e.jornada],
        ["#emp-email", e.email], ["#emp-telefono", e.telefono], ["#emp-direccion", e.direccion], ["#emp-ciudad", e.ciudad], ["#emp-cp", e.cp],
        ["#emp-alta", e.fecha_alta], ["#emp-baja", e.fecha_baja], ["#emp-salario", e.salario_bruto], ["#emp-irpf", e.irpf],
        ["#emp-dispo", e.disponibilidad], ["#emp-motivo", e.motivo_no_dispo], ["#emp-banco", e.banco], ["#emp-iban", e.iban],
        ["#emp-titular", e.titular], ["#emp-convenio", e.convenio], ["#emp-obs", e.observaciones],
      ]);
      setEditBtn("#btn-add-emp", true, "Añadir empleado");
    });
  } catch (_) { box.innerHTML = '<p class="muted">Error al cargar.</p>'; }
}

$("#btn-add-emp")?.addEventListener("click", async () => {
  const g = (s) => $(s)?.value || "";
  const rec = editState.empleado;
  const body = {
    nombre: g("#emp-nombre"), apellidos: g("#emp-apellidos"), dni: g("#emp-dni"), nss: g("#emp-nss"),
    email: g("#emp-email"), telefono: g("#emp-telefono"), direccion: g("#emp-direccion"), ciudad: g("#emp-ciudad"), cp: g("#emp-cp"),
    fecha_alta: g("#emp-alta"), fecha_baja: g("#emp-baja"), categoria: g("#emp-categoria"), puesto: g("#emp-puesto"),
    tipo_contrato: g("#emp-contrato"), jornada: g("#emp-jornada"), banco: g("#emp-banco"), iban: g("#emp-iban"), titular: g("#emp-titular"),
    salario_bruto: parseFloat(g("#emp-salario")) || 0, irpf: parseFloat(g("#emp-irpf")) || 15,
    disponibilidad: g("#emp-dispo"), motivo_no_dispo: g("#emp-motivo"), convenio: g("#emp-convenio"), observaciones: g("#emp-obs"),
  };
  if (!body.nombre) { showError("Indica el nombre del empleado."); return; }
  const url = rec ? `/api/empleados/${rec.id}` : "/api/empleados";
  const res = await fetch(url, { method: rec ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar el empleado."); return; }
  editState.empleado = null;
  resetEmpForm();
  setEditBtn("#btn-add-emp", false, "Añadir empleado");
  loadEmpleados();
});

async function loadNominaEmpleados() {
  const sels = [$("#nomina-emp"), $("#aus-empleado")];
  try {
    const res = await fetch("/api/empleados");
    const d = await res.json();
    sels.forEach((sel) => {
      if (!sel) return;
      sel.innerHTML = "";
      sel.append(el("option", { value: "", text: "— Empleado —" }));
      (d.empleados || []).filter((e) => e.activo).forEach((e) => sel.append(el("option", { value: e.id, text: `${e.nombre} ${e.apellidos || ""}`.trim() })));
    });
  } catch (_) {}
}

async function loadNominas() {
  const box = $("#nominas-list"); if (!box) return;
  try {
    const res = await fetch("/api/nominas");
    const d = await res.json();
    const noms = d.nominas || [];
    maestroTable(box, ["Periodo", "Empleado", "Bruto", "Neto", "Coste", "Estado", ""], noms, (n) => {
      const del = el("button", { type: "button", class: "btn-remove", text: "✕", title: "Eliminar" });
      del.addEventListener("click", async () => {
        if (!confirm("¿Eliminar la nómina?")) return;
        await fetch(`/api/nominas/${n.id}`, { method: "DELETE" }).catch(() => {});
        loadNominas();
      });
      const actions = el("span", { class: "row-actions" });
      if (!n.contabilizado) {
        const bc = el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Contabilizar" });
        bc.addEventListener("click", async () => {
          bc.disabled = true;
          try { await cfetch(`/api/nominas/${n.id}/contabilizar`, { method: "POST" }); showError("Nómina contabilizada."); loadNominas(); }
          catch (e) { showError(e.message); bc.disabled = false; }
        });
        actions.append(bc);
      } else if (!n.pagado) {
        const bp = el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Pagar" });
        bp.addEventListener("click", async () => {
          bp.disabled = true;
          try { await cfetch(`/api/nominas/${n.id}/pagar`, { method: "POST" }); showError("Nómina pagada."); loadNominas(); }
          catch (e) { showError(e.message); bp.disabled = false; }
        });
        actions.append(bp);
      } else {
        actions.append(el("span", { class: "muted", text: "✓ pagada" }));
      }
      actions.append(del);
      return [
        n.periodo || "—",
        `${n.nombre || ""} ${n.apellidos || ""}`.trim(),
        formatEUR(n.salario_bruto),
        formatEUR(n.neto),
        formatEUR(n.coste_empresa),
        n.estado === "pagada" ? "Pagada" : (n.contabilizado ? "Contabilizada" : "Borrador"),
        actions,
      ];
    }, "Sin nóminas.", (n) => {
      editState.nomina = n;
      formFill([
        ["#nomina-emp", n.empleado_id], ["#nomina-per-edit", n.periodo], ["#nomina-bruto", n.salario_bruto],
        ["#nomina-irpf", n.irpf_pct], ["#nomina-ss-t", n.ss_trabajador_pct], ["#nomina-ss-e", n.ss_empresa_pct], ["#nomina-notas", n.notas],
      ]);
      $("#nomina-edit-form").hidden = false;
    });
  } catch (_) { box.innerHTML = '<p class="muted">Error al cargar.</p>'; }
}

$("#btn-generar-nominas")?.addEventListener("click", async () => {
  const periodo = $("#nomina-periodo")?.value || "";
  if (!periodo) { showError("Indica el periodo."); return; }
  const btn = $("#btn-generar-nominas");
  btn.disabled = true; btn.textContent = "Generando…";
  try {
    const r = await cfetch("/api/nominas/generar", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ periodo }) });
    showError(`Generadas ${r.creadas} nómina(s)${r.saltadas ? ` · ${r.saltadas} ya existían` : ""}.`);
    loadNominas();
  } catch (e) { showError(e.message); }
  btn.disabled = false; btn.textContent = "Generar nóminas del periodo";
});

$("#btn-save-nomina")?.addEventListener("click", async () => {
  const g = (s) => $(s)?.value || "";
  const rec = editState.nomina;
  const body = {
    empleado_id: g("#nomina-emp"), periodo: g("#nomina-per-edit"),
    salario_bruto: parseFloat(g("#nomina-bruto")) || 0, irpf_pct: parseFloat(g("#nomina-irpf")) || 15,
    ss_trabajador_pct: parseFloat(g("#nomina-ss-t")) || 6.35, ss_empresa_pct: parseFloat(g("#nomina-ss-e")) || 30,
    notas: g("#nomina-notas"),
  };
  if (!body.empleado_id || !body.periodo) { showError("Indica empleado y periodo."); return; }
  const url = rec ? `/api/nominas/${rec.id}` : "/api/nominas";
  const res = await fetch(url, { method: rec ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar la nómina."); return; }
  editState.nomina = null;
  $("#nomina-edit-form").hidden = true;
  ["#nomina-bruto", "#nomina-notas"].forEach((s) => { const x = $(s); if (x) x.value = ""; });
  loadNominas();
});

async function loadAusencias() {
  const box = $("#ausencias-list"); if (!box) return;
  try {
    const res = await fetch("/api/ausencias");
    const d = await res.json();
    const aus = d.ausencias || [];
    maestroTable(box, ["Empleado", "Tipo", "Inicio", "Fin", "Días", "Estado", ""], aus, (a) => {
      const del = el("button", { type: "button", class: "btn-remove", text: "✕", title: "Eliminar" });
      del.addEventListener("click", async () => {
        await fetch(`/api/ausencias/${a.id}`, { method: "DELETE" }).catch(() => {});
        loadAusencias();
      });
      return [
        `${a.nombre || ""} ${a.apellidos || ""}`.trim(),
        a.tipo || "—",
        formatDate(a.fecha_inicio),
        formatDate(a.fecha_fin),
        a.dias || "—",
        a.estado || "—",
        del,
      ];
    }, "Sin ausencias.", (a) => {
      editState.ausencia = a;
      formFill([
        ["#aus-empleado", a.empleado_id], ["#aus-tipo", a.tipo], ["#aus-inicio", a.fecha_inicio],
        ["#aus-fin", a.fecha_fin], ["#aus-dias", a.dias], ["#aus-estado", a.estado], ["#aus-nota", a.nota],
      ]);
      setEditBtn("#btn-add-ausencia", true, "Añadir ausencia");
    });
  } catch (_) { box.innerHTML = '<p class="muted">Error al cargar.</p>'; }
}

$("#btn-add-ausencia")?.addEventListener("click", async () => {
  const g = (s) => $(s)?.value || "";
  const rec = editState.ausencia;
  const body = {
    empleado_id: g("#aus-empleado"), tipo: g("#aus-tipo"), fecha_inicio: g("#aus-inicio"),
    fecha_fin: g("#aus-fin"), dias: parseFloat(g("#aus-dias")) || 0, estado: g("#aus-estado"), nota: g("#aus-nota"),
  };
  if (!body.empleado_id) { showError("Selecciona el empleado."); return; }
  const url = rec ? `/api/ausencias/${rec.id}` : "/api/ausencias";
  const res = await fetch(url, { method: rec ? "PATCH" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar la ausencia."); return; }
  editState.ausencia = null;
  ["#aus-inicio", "#aus-fin", "#aus-dias", "#aus-nota"].forEach((s) => { const x = $(s); if (x) x.value = ""; });
  setEditBtn("#btn-add-ausencia", false, "Añadir ausencia");
  loadAusencias();
});

async function loadEmpresa() {
  try {
    const res = await fetch("/api/empresa");
    const d = await res.json();
    const e = d.empresa || {};
    formFill([
      ["#emp-nombre", e.nombre], ["#emp-cif", e.cif], ["#emp-direccion", e.direccion],
      ["#emp-poblacion", e.poblacion], ["#emp-cp", e.cp], ["#emp-pais", e.pais || "ES"],
      ["#emp-telefono", e.telefono], ["#emp-email", e.email], ["#emp-web", e.web],
      ["#emp-iva", e.iva ?? 21], ["#emp-iban", e.iban],
    ]);
  } catch (_) {}
}

async function loadSmtpConfig() {
  try {
    const res = await fetch("/api/config");
    const d = await res.json();
    const c = d.config || {};
    formFill([
      ["#smtp-host", c.smtp_host], ["#smtp-port", c.smtp_port || "587"],
      ["#smtp-user", c.smtp_user], ["#smtp-password", c.smtp_password],
      ["#smtp-from", c.smtp_from],
    ]);
  } catch (_) {}
}

function flashBtn(sel, msg) {
  const b = $(sel);
  if (!b) return;
  const orig = b.textContent;
  b.textContent = msg;
  setTimeout(() => { b.textContent = orig; }, 1500);
}

$("#btn-save-empresa")?.addEventListener("click", async () => {
  const body = {
    nombre: $("#emp-nombre")?.value.trim() || "",
    cif: $("#emp-cif")?.value.trim() || "",
    direccion: $("#emp-direccion")?.value.trim() || "",
    poblacion: $("#emp-poblacion")?.value.trim() || "",
    cp: $("#emp-cp")?.value.trim() || "",
    pais: $("#emp-pais")?.value.trim() || "ES",
    telefono: $("#emp-telefono")?.value.trim() || "",
    email: $("#emp-email")?.value.trim() || "",
    web: $("#emp-web")?.value.trim() || "",
    iva: parseFloat($("#emp-iva")?.value) || 21,
    iban: $("#emp-iban")?.value.trim() || "",
  };
  const res = await fetch("/api/empresa", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar la empresa."); return; }
  flashBtn("#btn-save-empresa", "Guardado ✓");
});

$("#btn-save-smtp")?.addEventListener("click", async () => {
  const body = {
    smtp_host: $("#smtp-host")?.value.trim() || "",
    smtp_port: $("#smtp-port")?.value.trim() || "587",
    smtp_user: $("#smtp-user")?.value.trim() || "",
    smtp_password: $("#smtp-password")?.value || "",
    smtp_from: $("#smtp-from")?.value.trim() || "",
  };
  const res = await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar la configuración de correo."); return; }
  flashBtn("#btn-save-smtp", "Guardado ✓");
});

function contabilidadError(panel, e) {
  panel.innerHTML = el("div", { class: "alert alert-error" },
    el("span", { text: "Error: " + e.message })).outerHTML;
}

async function renderContabilidadDashboard(panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando…" }).outerHTML;
  try {
    const [d, s, f, c] = await Promise.all([
      cfetch("/api/contabilidad/dashboard"),
      cfetch("/api/contabilidad/situacion"),
      cfetch("/api/contabilidad/facturas"),
      cfetch("/api/contabilidad/cierre"),
    ]);
    const wrap = el("div", { class: "c-wrap" });
    const kpis = [
      ["Ingresos", d.ingresos, "pos"],
      ["Gastos", d.gastos, "neg"],
      ["Margen", d.margen, d.margen >= 0 ? "pos" : "neg"],
      ["IVA neto", d.iva_neto, ""],
      ["Tesorería", d.tesoreria, "pos"],
    ];
    const row = el("div", { class: "kpi-row" });
    kpis.forEach(([label, val, cls]) =>
      row.append(el("div", { class: `kpi-card ${cls}` },
        el("div", { class: "kpi-label", text: label }),
        el("div", { class: "kpi-val", text: formatEUR(val) }))));
    wrap.append(row);

    wrap.append(el("h3", { text: "Balance de situación" }));
    const sit = el("div", { class: "sit-grid" });
    [["Activo", s.activo], ["Pasivo", s.pasivo], ["Patrimonio neto", s.patrimonio_neto],
     ["Resultado del ejercicio", s.resultado]].forEach(([l, v]) =>
      sit.append(el("div", { class: "sit-row" }, el("span", { text: l }), el("strong", { text: formatEUR(v) }))));
    sit.append(el("div", { class: `sit-row ${s.cuadra ? "ok" : "bad"}` },
      el("span", { text: s.cuadra ? "✓ Cuadra (activo = pasivo + neto)" : "⚠ No cuadra" })));
    wrap.append(sit);

    wrap.append(el("h3", { text: "Facturas" }));
    const pend = (f.facturas || []).filter((x) => x.estado !== "cobrada").length;
    wrap.append(el("p", { class: "muted", text: `${(f.facturas || []).length} facturas · ${pend} pendientes de cobro · ${formatEUR(d.facturas?.cobrada || 0)} cobradas` }));

    wrap.append(el("h3", { text: "Cierre contable" }));
    const hoy = new Date();
    const finMes = new Date(hoy.getFullYear(), hoy.getMonth() + 1, 0).toISOString().slice(0, 10);
    const cierreRow = el("div", { class: "head-row" });
    cierreRow.append(el("span", { class: "muted", text: c.cierre_fecha ? `Periodo cerrado hasta ${formatDate(c.cierre_fecha)}. Los asientos anteriores están bloqueados.` : "Sin cierre: los asientos se registran libremente." }));
    const btnCierre = el("button", { type: "button", class: "btn btn-ghost btn-sm", text: `Cerrar mes (${finMes})` });
    btnCierre.addEventListener("click", async () => {
      if (!confirm(`¿Cerrar la contabilidad hasta ${finMes}? Se bloquearán los asientos de fecha anterior.`)) return;
      btnCierre.disabled = true; btnCierre.textContent = "Cerrando…";
      try { await cfetch("/api/contabilidad/cierre", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fecha: finMes }) }); renderContabilidadDashboard(panel); }
      catch (e) { showError(e.message); btnCierre.disabled = false; btnCierre.textContent = `Cerrar mes (${finMes})`; }
    });
    cierreRow.append(btnCierre);
    wrap.append(cierreRow);
    panel.innerHTML = "";
    panel.append(wrap);
  } catch (e) { contabilidadError(panel, e); }
}

async function renderContabilidadFacturacion(panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando…" }).outerHTML;
  try {
    const [vb, fact] = await Promise.all([
      cfetch("/api/contabilidad/facturables"),
      cfetch("/api/contabilidad/facturas"),
    ]);
    const wrap = el("div", { class: "c-wrap" });

    wrap.append(el("h3", { text: "Viajes pendientes de facturar" }));
    const seleccion = new Set();
    if (!vb.viajes.length) {
      wrap.append(el("p", { class: "muted", text: "No hay viajes sin factura con precio." }));
    } else {
      const btnAgrupar = el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Facturar selección" });
      btnAgrupar.addEventListener("click", async () => {
        const ids = [...seleccion];
        if (!ids.length) { showError("Marca al menos un viaje para facturar."); return; }
        btnAgrupar.disabled = true; btnAgrupar.textContent = "Generando…";
        try {
          const r = await cfetch("/api/contabilidad/facturas/agrupada", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ trip_ids: ids }) });
          showError(`Factura ${r.factura} generada con ${r.viajes} viajes (${formatEUR(r.total)}).`);
          renderContabilidadFacturacion(panel);
        } catch (e) { showError("No se pudo facturar: " + e.message); btnAgrupar.disabled = false; btnAgrupar.textContent = "Facturar selección"; }
      });
      const cab = el("div", { class: "head-row" });
      cab.append(el("span", { class: "muted", text: "Marca varios viajes del mismo cliente para agruparlos en una sola factura." }));
      cab.append(btnAgrupar);
      wrap.append(cab);

      wrap.append(builderGrid(
        ["", "Ref", "Cliente", "Origen", "Destino", "Base", "IVA", "Total", ""],
        vb.viajes,
        (v) => {
          const iva = v.iva || 21;
          const base = parseFloat(v.precio) || 0;
          const total = base * (1 + iva / 100);
          const cb = el("input", { type: "checkbox" });
          cb.checked = seleccion.has(v.id);
          cb.addEventListener("change", () => { if (cb.checked) seleccion.add(v.id); else seleccion.delete(v.id); });
          const btn = el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Facturar" });
          btn.addEventListener("click", async () => {
            btn.disabled = true; btn.textContent = "Generando…";
            try {
              const r = await cfetch(`/api/contabilidad/facturas/${encodeURIComponent(v.id)}`, { method: "POST" });
              showError(`Factura ${r.factura} generada (${formatEUR(r.total)}).`);
              renderContabilidadFacturacion(panel);
            } catch (e) { showError("No se pudo facturar: " + e.message); btn.disabled = false; btn.textContent = "Facturar"; }
          });
          return [cb, v.referencia || v.id, v.cliente || "—", formatAddress(v.origen), formatAddress(v.destino), formatEUR(base), `${iva}%`, formatEUR(total), btn];
        },
        (v) => openPedidoForm(v.id)
      ));
    }

    wrap.append(el("h3", { text: "Facturas emitidas" }));
    if (!fact.facturas.length) {
      wrap.append(el("p", { class: "muted", text: "Aún no hay facturas." }));
    } else {
      wrap.append(builderGrid(
        ["Nº", "Fecha", "Cliente", "Base", "IVA", "Total", "Estado", ""],
        fact.facturas,
        (f) => {
          let btn = null;
          if (f.estado !== "cobrada") {
            btn = el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Marcar cobrada" });
            btn.addEventListener("click", async () => {
              btn.disabled = true; btn.textContent = "Cobrando…";
              try { await cfetch(`/api/contabilidad/facturas/${f.id}/cobrar`, { method: "POST" }); renderContabilidadFacturacion(panel); }
              catch (e) { showError("Error: " + e.message); btn.disabled = false; btn.textContent = "Marcar cobrada"; }
            });
          }
          const pdfBtn = el("a", { class: "btn btn-ghost btn-sm", text: "📄 PDF", href: `/api/contabilidad/facturas/${f.id}/pdf`, target: "_blank", rel: "noopener" });
          const emailBtn = el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "✉️", title: "Enviar por email" });
          emailBtn.addEventListener("click", async () => {
            const email = prompt("Email del destinatario:", "");
            if (!email) return;
            emailBtn.disabled = true;
            try {
              await cfetch(`/api/contabilidad/facturas/${f.id}/enviar`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email }) });
              showError(`Factura ${f.numero} enviada a ${email}.`);
            } catch (e) { showError("No se pudo enviar: " + e.message); }
            emailBtn.disabled = false;
          });
          const acciones = el("span", { class: "ag-actions" });
          acciones.append(pdfBtn, emailBtn, btn || el("span", { class: "muted", text: "✓" }));
          return [f.numero, formatDate(f.fecha), f.cliente_nombre || "—", formatEUR(f.base), formatEUR(f.cuota_iva), formatEUR(f.total), el("span", { class: "badge", text: f.estado }), acciones];
        },
        (f) => { if (f.trip_id) openPedidoForm(f.trip_id); }
      ));
    }
    panel.innerHTML = "";
    panel.append(wrap);
  } catch (e) { contabilidadError(panel, e); }
}

async function renderContabilidadDiario(panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando…" }).outerHTML;
  try {
    const d = await cfetch("/api/contabilidad/asientos");
    const wrap = el("div", { class: "c-wrap" });
    const head = el("div", { class: "head-row" });
    head.append(el("h3", { text: "Libro diario" }));
    const btnNuevo = el("button", { type: "button", class: "btn btn-primary btn-sm", text: "＋ Nuevo asiento" });
    btnNuevo.addEventListener("click", () => renderAsientoForm(panel));
    head.append(btnNuevo);
    wrap.append(head);
    if (!d.asientos.length) {
      wrap.append(el("p", { class: "muted", text: "Sin asientos." }));
    } else {
      const filas = [];
      d.asientos.forEach((a) => {
        const apuntes = d.apuntes[a.id] || [];
        apuntes.forEach((p, idx) => filas.push({ a, p, mostrarBorrar: a.origen === "manual" && idx === 0 }));
      });
      wrap.append(builderGrid(
        ["Nº", "Fecha", "Concepto", "Origen", "Cuenta", "Descripción", "Debe", "Haber", ""],
        filas,
        (r) => {
          let del = "";
          if (r.mostrarBorrar) {
            del = el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Borrar" });
            del.addEventListener("click", async () => {
              if (!confirm("¿Borrar este asiento?")) return;
              try { await cfetch(`/api/contabilidad/asientos/${r.a.id}`, { method: "DELETE" }); renderContabilidadDiario(panel); }
              catch (e) { showError(e.message); }
            });
          }
          return [String(r.a.numero), r.a.fecha, r.a.concepto, r.a.origen, r.p.cuenta, r.p.cuenta_nombre || "",
            r.p.debe ? formatEUR(r.p.debe) : "", r.p.haber ? formatEUR(r.p.haber) : "", del];
        }
      ));
    }
    panel.innerHTML = "";
    panel.append(wrap);
  } catch (e) { contabilidadError(panel, e); }
}

async function renderAsientoForm(panel) {
  let cuentas = [];
  try { cuentas = (await cfetch("/api/contabilidad/cuentas")).cuentas; } catch (_) {}
  const wrap = el("div", { class: "card c-wrap" });
  wrap.append(el("h3", { text: "Nuevo asiento manual" }));
  const fecha = el("input", { type: "date", value: new Date().toISOString().slice(0, 10) });
  const concepto = el("input", { type: "text", placeholder: "Concepto", class: "full" });
  const doc = el("input", { type: "text", placeholder: "Documento (opcional)", class: "full" });
  const lineas = el("div", {});
  const addLinea = () => {
    const sel = el("select", {});
    sel.append(el("option", { value: "", text: "— cuenta —" }));
    cuentas.forEach((cu) => sel.append(el("option", { value: cu.codigo, text: `${cu.codigo} ${cu.nombre}` })));
    const iDebe = el("input", { type: "number", step: "0.01", placeholder: "Debe" });
    const iHaber = el("input", { type: "number", step: "0.01", placeholder: "Haber" });
    const rm = el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "✕" });
    const row = el("div", { class: "linea" }, sel, iDebe, iHaber, rm);
    rm.addEventListener("click", () => row.remove());
    lineas.append(row);
  };
  const btnAdd = el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "＋ Línea" });
  btnAdd.addEventListener("click", () => addLinea());
  const btnSave = el("button", { type: "button", class: "btn btn-primary", text: "Guardar asiento" });
  btnSave.addEventListener("click", async () => {
    const ls = [...lineas.children].map((row) => ({
      cuenta: row.querySelector("select").value,
      debe: parseFloat(row.querySelector('input[placeholder="Debe"]').value) || 0,
      haber: parseFloat(row.querySelector('input[placeholder="Haber"]').value) || 0,
      concepto: "",
    })).filter((l) => l.cuenta && (l.debe || l.haber));
    if (!ls.length) { showError("Añade al menos una línea."); return; }
    btnSave.disabled = true;
    try {
      await cfetch("/api/contabilidad/asientos", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fecha: fecha.value, concepto: concepto.value, documento: doc.value, lineas: ls }) });
      showError("Asiento guardado.");
      renderContabilidadDiario(panel);
    } catch (e) { showError("Error: " + e.message); btnSave.disabled = false; }
  });
  wrap.append(el("label", { text: "Fecha" }, fecha),
    el("label", { text: "Concepto" }, concepto),
    el("label", { text: "Documento" }, doc),
    el("label", { text: "Líneas (debe = haber)" }), lineas,
    btnAdd, el("div", { class: "gap" }, btnSave));
  addLinea(); addLinea();
  panel.innerHTML = "";
  panel.append(wrap);
}

async function renderContabilidadCuentas(panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando…" }).outerHTML;
  try {
    const d = await cfetch("/api/contabilidad/cuentas");
    const wrap = el("div", { class: "c-wrap" });
    wrap.append(el("h3", { text: `Plan contable (${d.cuentas.length} cuentas)` }));
    const tipoLabel = { activo: "Activo", pasivo: "Pasivo", patrimonio: "Patrimonio", gasto: "Gasto", ingreso: "Ingreso" };
    wrap.append(dataGrid(["Código", "Nombre", "Grupo", "Tipo"],
      d.cuentas.map((c) => [c.codigo, c.nombre, `G${c.grupo}`, tipoLabel[c.tipo] || c.tipo])));
    panel.innerHTML = "";
    panel.append(wrap);
  } catch (e) { contabilidadError(panel, e); }
}

async function renderContabilidadBalance(panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando…" }).outerHTML;
  try {
    const d = await cfetch("/api/contabilidad/balance");
    const wrap = el("div", { class: "c-wrap" });
    wrap.append(el("h3", { text: "Balance de sumas y saldos" }));
    const rows = d.cuentas.map((c) => [c.cuenta, c.nombre, c.debe ? formatEUR(c.debe) : "", c.haber ? formatEUR(c.haber) : "", formatEUR(c.saldo)]);
    rows.push(["TOTAL", "", formatEUR(d.total_debe), formatEUR(d.total_haber), ""]);
    wrap.append(dataGrid(["Cuenta", "Nombre", "Debe", "Haber", "Saldo"], rows));
    panel.innerHTML = "";
    panel.append(wrap);
  } catch (e) { contabilidadError(panel, e); }
}

async function renderContabilidadPyg(panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando…" }).outerHTML;
  try {
    const d = await cfetch("/api/contabilidad/pyg");
    const wrap = el("div", { class: "c-wrap" });
    wrap.append(el("h3", { text: "Cuenta de resultados (Pérdidas y Ganancias)" }));

    const rows = [];
    rows.push(["", "GASTOS", ""]);
    d.gastos.forEach((g) => rows.push([g.cuenta, g.nombre, formatEUR(g.importe)]));
    rows.push(["", "Total gastos", formatEUR(d.total_gastos)]);
    rows.push(["", "INGRESOS", ""]);
    d.ingresos.forEach((i) => rows.push([i.cuenta, i.nombre, formatEUR(i.importe)]));
    rows.push(["", "Total ingresos", formatEUR(d.total_ingresos)]);
    rows.push(["", "RESULTADO", formatEUR(d.resultado)]);
    wrap.append(dataGrid(["Cuenta", "Concepto", "Importe"], rows));
    panel.innerHTML = "";
    panel.append(wrap);
  } catch (e) { contabilidadError(panel, e); }
}

async function renderContabilidadTesoreria(panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando…" }).outerHTML;
  try {
    const [d, p] = await Promise.all([
      cfetch("/api/contabilidad/tesoreria"),
      cfetch("/api/contabilidad/tesoreria-prevision"),
    ]);
    const wrap = el("div", { class: "c-wrap" });
    wrap.append(el("div", { class: "kpi-row" },
      el("div", { class: "kpi-card pos" },
        el("div", { class: "kpi-label", text: "Saldo en bancos + caja" }),
        el("div", { class: "kpi-val", text: formatEUR(d.saldo) }))));

    wrap.append(el("h3", { text: "Previsión de tesorería (90 días)" }));
    const prevRows = p.prevision.map((m) => [m.mes, formatEUR(m.entradas), formatEUR(m.salidas), formatEUR(m.saldo_proyectado)]);
    prevRows.push(["Saldo actual", formatEUR(p.saldo), "", ""]);
    wrap.append(dataGrid(["Mes", "Entradas (cobros)", "Salidas (pagos)", "Saldo proyectado"], prevRows));
    wrap.append(el("p", { class: "muted", text: `Pendiente total: ${formatEUR(p.entradas_pendientes)} por cobrar · ${formatEUR(p.salidas_pendientes)} por pagar${(p.sin_fecha.entradas || p.sin_fecha.salidas) ? " (parte sin fecha clara)" : ""}.` }));

    wrap.append(el("h3", { text: "Facturas pendientes de cobro" }));
    if (!d.facturas_pendientes.length) {
      wrap.append(el("p", { class: "muted", text: "No hay facturas pendientes." }));
    } else {
      wrap.append(builderGrid(
        ["Nº", "Fecha", "Cliente", "Total", ""],
        d.facturas_pendientes,
        (f) => {
          const btn = el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Cobrar" });
          btn.addEventListener("click", async () => {
            btn.disabled = true; btn.textContent = "…";
            try { await cfetch(`/api/contabilidad/facturas/${f.id}/cobrar`, { method: "POST" }); renderContabilidadTesoreria(panel); }
            catch (e) { showError(e.message); btn.disabled = false; btn.textContent = "Cobrar"; }
          });
          return [f.numero, formatDate(f.fecha), f.cliente_nombre || "—", formatEUR(f.total), btn];
        }
      ));
    }

    wrap.append(el("h3", { text: "Gastos pendientes de pago" }));
    if (!d.gastos_pendientes.length) {
      wrap.append(el("p", { class: "muted", text: "No hay gastos pendientes." }));
    } else {
      wrap.append(builderGrid(
        ["Fecha", "Categoría", "Concepto", "Proveedor", "Importe", ""],
        d.gastos_pendientes,
        (g) => {
          const btn = el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Pagar" });
          btn.addEventListener("click", async () => {
            btn.disabled = true; btn.textContent = "…";
            try { await cfetch(`/api/gastos/${g.id}/pagar`, { method: "POST" }); renderContabilidadTesoreria(panel); }
            catch (e) { showError(e.message); btn.disabled = false; btn.textContent = "Pagar"; }
          });
          return [formatDate(g.fecha), g.categoria || "—", g.concepto || "—", g.proveedor || "—", formatEUR(g.importe), btn];
        }
      ));
    }

    wrap.append(el("h3", { text: "Movimientos recientes" }));
    if (!d.movimientos.length) {
      wrap.append(el("p", { class: "muted", text: "Sin movimientos." }));
    } else {
      wrap.append(dataGrid(["Fecha", "Concepto", "Tipo", "Importe"],
        d.movimientos.map((m) => [formatDate(m.fecha), m.concepto, m.origen === "cobro" ? "Cobro" : "Pago", formatEUR(m.importe)])));
    }
    panel.innerHTML = "";
    panel.append(wrap);
  } catch (e) { contabilidadError(panel, e); }
}

async function renderContabilidadImpuestos(panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando…" }).outerHTML;
  try {
    const [d, m347] = await Promise.all([
      cfetch("/api/contabilidad/impuestos"),
      cfetch("/api/contabilidad/modelo347"),
    ]);
    const wrap = el("div", { class: "c-wrap" });
    wrap.append(el("h3", { text: "IVA — liquidación trimestral (mod. 303)" }));
    const ivaRows = d.trimestres.map((tr) => [tr.trimestre, formatEUR(tr.repercutido), formatEUR(tr.soportado), formatEUR(tr.a_ingresar)]);
    ivaRows.push(["TOTAL", formatEUR(d.total_repercutido), formatEUR(d.total_soportado), formatEUR(d.a_ingresar_total)]);
    wrap.append(dataGrid(["Trimestre", "IVA repercutido", "IVA soportado", "A ingresar / devolver"], ivaRows));
    wrap.append(el("h3", { text: "Retenciones practicadas (IRPF)" }));
    wrap.append(el("div", { class: "kpi-row" },
      el("div", { class: "kpi-card" },
        el("div", { class: "kpi-label", text: "Retenciones a ingresar (mod. 111)" }),
        el("div", { class: "kpi-val", text: formatEUR(d.retenciones) }))));

    wrap.append(el("h3", { text: `Modelo 347 — operaciones > ${formatEUR(m347.limite)} (${m347.anio})` }));
    const tabla347 = (titulo, rows) => {
      const box = el("div", {});
      box.append(el("p", { class: "muted", text: titulo }));
      box.append(dataGrid(["Tercero", "Total"], rows.map((r) => [r.tercero, formatEUR(r.total)]), { emptyMsg: "Ningún tercero supera el límite." }));
      return box;
    };
    wrap.append(tabla347(`Clientes (ventas) — ${formatEUR(m347.total_clientes)}`, m347.clientes));
    wrap.append(tabla347(`Proveedores (compras) — ${formatEUR(m347.total_proveedores)}`, m347.proveedores));
    panel.innerHTML = "";
    panel.append(wrap);
  } catch (e) { contabilidadError(panel, e); }
}

async function renderContabilidadInmovilizado(panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando…" }).outerHTML;
  try {
    const [inv, veh] = await Promise.all([
      cfetch("/api/contabilidad/inmovilizado"),
      cfetch("/api/vehiculos"),
    ]);
    const wrap = el("div", { class: "c-wrap" });
    const hoy = new Date();
    const mesActual = `${hoy.getFullYear()}-${String(hoy.getMonth() + 1).padStart(2, "0")}`;
    const head = el("div", { class: "head-row" });
    head.append(el("h3", { text: "Inmovilizado · amortización mensual" }));
    const btnAmort = el("button", { type: "button", class: "btn btn-primary btn-sm", text: `Generar amortización ${mesActual}` });
    btnAmort.addEventListener("click", async () => {
      if (!confirm(`¿Generar asiento de amortización mensual ${mesActual}?`)) return;
      btnAmort.disabled = true; btnAmort.textContent = "Generando…";
      try { const r = await cfetch(`/api/contabilidad/amortizar?periodo=${mesActual}`, { method: "POST" }); showError(`Amortización generada (${formatEUR(r.total)}).`); renderContabilidadInmovilizado(panel); }
      catch (e) { showError(e.message); btnAmort.disabled = false; btnAmort.textContent = `Generar amortización ${mesActual}`; }
    });
    head.append(btnAmort);
    wrap.append(head);

    if (!inv.vehiculos.length) {
      wrap.append(el("p", { class: "muted", text: "Aún no hay vehículos con datos de amortización. Configúralos abajo." }));
    } else {
      wrap.append(builderGrid(
        ["Vehículo", "Coste", "Vida útil", "Amort. anual", "Amort. acumulada", "VNC", ""],
        inv.vehiculos,
        (v) => {
          let btnAdq = "";
          if (!v.tiene_adquisicion) {
            btnAdq = el("button", { type: "button", class: "btn btn-ghost btn-sm", text: "Registrar adquisición" });
            btnAdq.addEventListener("click", async () => {
              if (!confirm(`¿Registrar adquisición de ${v.matricula} (${formatEUR(v.coste)})?`)) return;
              btnAdq.disabled = true;
              try { await cfetch(`/api/contabilidad/inmovilizado/${encodeURIComponent(v.id)}/adquisicion`, { method: "POST" }); showError("Adquisición registrada (218/572)."); renderContabilidadInmovilizado(panel); }
              catch (e) { showError(e.message); btnAdq.disabled = false; }
            });
          }
          return [`${v.matricula} ${(v.marca || "")} ${(v.modelo || "")}`.trim(), formatEUR(v.coste), `${v.vida_util} años`, formatEUR(v.anual), formatEUR(v.acumulado), formatEUR(v.vnc), btnAdq || el("span", { class: "muted", text: "✓" })];
        }
      ));
      wrap.append(el("p", { class: "muted", text: `Amort. acumulada contable (281): ${formatEUR(inv.amort_acumulada_contable)}` }));
    }

    wrap.append(el("h3", { text: "Configurar amortización de un vehículo" }));
    const vehs = (veh.vehiculos || []).filter((x) => x.categoria === "tractora" || x.categoria === "rigido");
    const sel = el("select", {});
    sel.append(el("option", { value: "", text: "— vehículo —" }));
    vehs.forEach((v) => sel.append(el("option", { value: v.id, text: `${v.matricula || v.id} ${v.marca || ""}` })));
    const iCoste = el("input", { type: "number", step: "0.01", placeholder: "Coste adquisición (€)" });
    const iFecha = el("input", { type: "date" });
    const iVida = el("input", { type: "number", step: "1", placeholder: "Vida útil (años)", value: "5" });
    const iResidual = el("input", { type: "number", step: "0.01", placeholder: "Valor residual (€)", value: "0" });
    const btnSave = el("button", { type: "button", class: "btn btn-primary btn-sm", text: "Guardar" });
    btnSave.addEventListener("click", async () => {
      if (!sel.value) { showError("Selecciona un vehículo."); return; }
      try {
        await cfetch(`/api/contabilidad/inmovilizado/${encodeURIComponent(sel.value)}`, { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ coste_adquisicion: parseFloat(iCoste.value) || 0, fecha_adquisicion: iFecha.value, vida_util: parseInt(iVida.value) || 5, valor_residual: parseFloat(iResidual.value) || 0 }) });
        showError("Amortización guardada.");
        renderContabilidadInmovilizado(panel);
      } catch (e) { showError(e.message); }
    });
    wrap.append(el("div", { class: "linea" }, sel, iCoste, iFecha, iVida, iResidual, btnSave));
    panel.innerHTML = "";
    panel.append(wrap);
  } catch (e) { contabilidadError(panel, e); }
}

async function renderContabilidadExplotacion(panel) {
  panel.innerHTML = el("p", { class: "muted", text: "Cargando…" }).outerHTML;
  try {
    const [d, dv] = await Promise.all([
      cfetch("/api/contabilidad/explotacion"),
      cfetch("/api/contabilidad/explotacion-vehiculos"),
    ]);
    const wrap = el("div", { class: "c-wrap" });
    wrap.append(el("h3", { text: "Explotación mensual (ingresos − gastos)" }));
    const explRows = d.meses.map((m) => [m.mes, formatEUR(m.ingresos), formatEUR(m.gastos), formatEUR(m.resultado)]);
    explRows.push(["TOTAL", formatEUR(d.total_ingresos), formatEUR(d.total_gastos), formatEUR(d.total_resultado)]);
    wrap.append(dataGrid(["Mes", "Ingresos", "Gastos", "Resultado"], explRows, { emptyMsg: "Sin datos todavía." }));

    // Explotación por vehículo con reparto de gastos generales
    wrap.append(el("h3", { text: "Explotación por vehículo" }));
    const repartirCb = el("input", { type: "checkbox" });
    const repartirLabel = el("label", { class: "muted" });
    repartirLabel.append(repartirCb, ` Repartir gastos generales (${formatEUR(dv.gastos_generales)}) por km`);
    wrap.append(repartirLabel);

    const vbox = el("div", {});
    const renderVehiculos = () => {
      vbox.innerHTML = "";
      const repartir = repartirCb.checked;
      const cols = ["Vehículo", "Km", "Ingresos", "Gastos directos"];
      if (repartir) cols.push("Gastos imputados");
      cols.push("Resultado");
      const kmTotal = dv.total_km || 0;
      const rows = dv.vehiculos.map((v) => {
        const imputados = repartir && kmTotal > 0 ? v.gastos + dv.gastos_generales * (v.km / kmTotal) : v.gastos;
        const resultado = v.ingresos - imputados;
        const cells = [v.vehiculo, v.km ? `${v.km} km` : "—", formatEUR(v.ingresos), formatEUR(v.gastos)];
        if (repartir) cells.push(formatEUR(imputados));
        cells.push(formatEUR(resultado));
        return cells;
      });
      vbox.append(dataGrid(cols, rows, { emptyMsg: "Sin vehículos con datos." }));
    };
    renderVehiculos();
    repartirCb.addEventListener("change", renderVehiculos);
    wrap.append(vbox);

    panel.innerHTML = "";
    panel.append(wrap);
  } catch (e) { contabilidadError(panel, e); }
}

/* ==================== Maestros: pestañas + export + categorías ==================== */

function showMtab(tab) {
  document.querySelectorAll("#maestros-tabs .sub-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.mtab === tab));
  document.querySelectorAll("#maestros-panel .mtab-panel").forEach((p) =>
    p.hidden = p.dataset.mtab !== tab);
}

document.querySelectorAll("#maestros-tabs .sub-item").forEach((b) =>
  b.addEventListener("click", () => showMtab(b.dataset.mtab)));

document.querySelectorAll(".mtab-export").forEach((b) =>
  b.addEventListener("click", () => exportCSV(b.dataset.export)));

async function renderCategoriasList() {
  const box = $("#categorias-list");
  if (!box) return;
  try {
    const res = await fetch("/api/categorias");
    const d = await res.json();
    const cats = d.categorias || [];
    maestroTable(box, ["Categoría", "Cuenta", ""], cats, (c) => {
      const del = el("button", { type: "button", class: "btn-remove", text: "✕", title: "Quitar" });
      del.addEventListener("click", async () => {
        await fetch(`/api/categorias/${c.id}`, { method: "DELETE" }).catch(() => {});
        renderCategoriasList(); loadCategorias();
      });
      return [c.nombre, c.cuenta || "—", del];
    }, "Sin categorías.", (c) => {
      editState.categoria = c;
      formFill([["#cat-nombre", c.nombre], ["#cat-cuenta", c.cuenta]]);
      setEditBtn("#btn-add-categoria", true, "Añadir");
    });
  } catch (_) { box.innerHTML = '<p class="muted">Error al cargar.</p>'; }
}

$("#btn-add-categoria")?.addEventListener("click", async () => {
  const nombre = $("#cat-nombre")?.value?.trim();
  if (!nombre) return;
  const cuenta = $("#cat-cuenta")?.value?.trim() || "";
  await fetch("/api/categorias", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ nombre, cuenta }) });
  $("#cat-nombre").value = "";
  $("#cat-cuenta").value = "";
  setEditBtn("#btn-add-categoria", false, "Añadir");
  renderCategoriasList(); loadCategorias();
});

async function loadDirecciones() {
  const box = $("#direcciones-list");
  if (!box) return;
  try {
    const res = await fetch("/api/direcciones");
    const d = await res.json();
    const dirs = d.direcciones || [];
    maestroTable(box, ["Nombre", "Empresa", "Dirección", "Ciudad", "CP", "Comentario", ""], dirs, (dir) => {
      const calle = [dir.calle, dir.numero].filter(Boolean).join(" ");
      return [
        dir.nombre || "—",
        dir.empresa || "—",
        calle || "—",
        dir.ciudad || "—",
        dir.cp || "—",
        dir.comentario || "—",
        delBtn(async () => {
          await fetch(`/api/direcciones/${dir.id}`, { method: "DELETE" }).catch(() => {});
          loadDirecciones();
        }),
      ];
    }, "Sin direcciones guardadas.", (dir) => {
      editState.direccion = dir;
      formFill([
        ["#dir-nombre", dir.nombre], ["#dir-empresa", dir.empresa], ["#dir-calle", dir.calle],
        ["#dir-numero", dir.numero], ["#dir-ciudad", dir.ciudad], ["#dir-cp", dir.cp],
        ["#dir-pais", dir.pais], ["#dir-lat", dir.lat], ["#dir-lng", dir.lng],
        ["#dir-comentario", dir.comentario],
      ]);
      setEditBtn("#btn-add-direccion", true, "Añadir");
    });
  } catch (_) {
    box.innerHTML = '<p class="muted">No se pudieron cargar las direcciones.</p>';
  }
}

$("#btn-add-direccion")?.addEventListener("click", async () => {
  const rec = editState.direccion;
  const body = {
    nombre: $("#dir-nombre")?.value.trim() || "",
    empresa: $("#dir-empresa")?.value.trim() || "",
    calle: $("#dir-calle")?.value.trim() || "",
    numero: $("#dir-numero")?.value.trim() || "",
    ciudad: $("#dir-ciudad")?.value.trim() || "",
    cp: $("#dir-cp")?.value.trim() || "",
    pais: $("#dir-pais")?.value.trim() || "ES",
    lat: parseFloat($("#dir-lat")?.value) || null,
    lng: parseFloat($("#dir-lng")?.value) || null,
    comentario: $("#dir-comentario")?.value.trim() || "",
  };
  const url = rec ? `/api/direcciones/${rec.id}` : "/api/direcciones";
  const res = await fetch(url, {
    method: rec ? "PATCH" : "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).catch(() => null);
  if (!res || !res.ok) { showError("No se pudo guardar la dirección."); return; }
  editState.direccion = null;
  ["#dir-nombre", "#dir-empresa", "#dir-calle", "#dir-numero", "#dir-ciudad", "#dir-cp", "#dir-pais", "#dir-lat", "#dir-lng", "#dir-comentario"].forEach((s) => { const e = $(s); if (e) e.value = ""; });
  setEditBtn("#btn-add-direccion", false, "Añadir");
  loadDirecciones();
});

// Exportación de la pestaña activa de Contabilidad
let contabilidadTab = "dashboard";
const CONTAB_EXPORT = { gastos: "gastos", ingresos: "ingresos", facturacion: "facturas", diario: "asientos", balance: "balance", pyg: "pyg", tesoreria: "facturas", impuestos: "pyg", inmovilizado: "vehiculos", explotacion: "pyg" };
$("#btn-export-contabilidad")?.addEventListener("click", () => {
  const tipo = CONTAB_EXPORT[contabilidadTab];
  if (tipo) exportCSV(tipo); else showError("Esta pestaña no tiene exportación Excel.");
});

/* ---------- Configuración de integraciones (por cliente) ---------- */

async function loadConfigScreen() {
  try {
    const res = await fetch("/api/config");
    const d = await res.json();
    const c = d.config || {};
    const set = (id, key) => { const e = document.getElementById(id); if (e) e.value = c[key] || ""; };
    set("cfg-trimble-username", "trimble_username");
    set("cfg-trimble-password", "trimble_password");
    set("cfg-trimble-customer", "trimble_customer");
    set("cfg-trimble-terminal", "trimble_terminal");
    set("cfg-ptv-key", "ptv_api_key");
    set("cfg-auth-users", "auth_users");
    set("cfg-auth-password", "auth_password");
  } catch (_) {}
}

document.getElementById("btn-save-integraciones")?.addEventListener("click", async () => {
  const body = {
    trimble_username: document.getElementById("cfg-trimble-username").value.trim(),
    trimble_password: document.getElementById("cfg-trimble-password").value,
    trimble_customer: document.getElementById("cfg-trimble-customer").value.trim(),
    trimble_terminal: document.getElementById("cfg-trimble-terminal").value.trim(),
    ptv_api_key: document.getElementById("cfg-ptv-key").value.trim(),
    auth_users: document.getElementById("cfg-auth-users").value.trim(),
    auth_password: document.getElementById("cfg-auth-password").value,
  };
  const res = await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => null);
  const msg = document.getElementById("cfg-msg");
  if (res && res.ok) {
    msg.textContent = "✓ Configuración guardada.";
    msg.hidden = false;
    setTimeout(() => (msg.hidden = true), 3000);
  } else {
    alert("No se pudo guardar la configuración.");
  }
});

/* ---------- Empresas (super-admin) ---------- */

async function loadEmpresas() {
  const box = document.getElementById("empresas-list");
  if (!box) return;
  try {
    const res = await fetch("/api/empresas");
    const d = await res.json();
    const emps = d.empresas || [];
    box.innerHTML = "";
    box.append(builderGrid(
      ["Identificador", "Nombre", "Base de datos", ""],
      emps,
      (e) => {
        const entrar = el("button", { type: "button", class: "btn-primary", text: "Entrar", title: "Entrar como esta empresa" });
        entrar.addEventListener("click", async () => {
          try {
            const r = await fetch(`/api/empresas/${encodeURIComponent(e.slug)}/entrar`, { method: "POST" });
            const d = await r.json();
            if (r.ok && d.token) {
              window.TMS_SESSION = { token: d.token, user: d.usuario + " · " + d.empresa, superadmin: false };
              try {
                localStorage.setItem("tms_token", d.token);
                localStorage.setItem("tms_user", d.usuario + " · " + d.empresa);
                localStorage.setItem("tms_superadmin", "0");
              } catch (_) {}
              restoreSession();
            } else {
              alert((d && d.detail && d.detail.error) || (d && d.error) || "No se pudo entrar en la empresa.");
            }
          } catch (e2) {
            alert("Error al entrar: " + (e2 && e2.message ? e2.message : e2));
          }
        });
        const del = el("button", { type: "button", class: "btn-remove", text: "✕", title: "Eliminar empresa" });
        del.addEventListener("click", async () => {
          if (!confirm(`¿Eliminar la empresa «${e.slug}» y su base de datos?`)) return;
          const r = await fetch(`/api/empresas/${encodeURIComponent(e.slug)}`, { method: "DELETE" }).catch(() => null);
          if (r && r.ok) loadEmpresas(); else alert("No se pudo eliminar la empresa.");
        });
        const acciones = el("span", { class: "ag-actions" });
        acciones.append(entrar, del);
        return [e.slug, e.nombre, e.db_name, acciones];
      },
      null,
      "No hay empresas."
    ));
  } catch (_) {
    box.innerHTML = '<p class="muted">No se pudieron cargar las empresas.</p>';
  }
}

document.getElementById("btn-crear-empresa")?.addEventListener("click", async () => {
  const slug = document.getElementById("emp-slug").value.trim().toLowerCase();
  const nombre = document.getElementById("emp-nombre").value.trim();
  const usuario = document.getElementById("emp-usuario").value.trim();
  const password = document.getElementById("emp-password").value;
  if (!slug || !nombre) { alert("Indica identificador y nombre."); return; }
  let res = null, data = {};
  try {
    res = await fetch("/api/empresas", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug, nombre, auth_users: usuario, auth_password: password }),
    });
    data = await res.json();
  } catch (_) {}
  if (res && res.ok) {
    ["emp-slug", "emp-nombre", "emp-usuario", "emp-password"].forEach((id) => { const e = document.getElementById(id); if (e) e.value = ""; });
    loadEmpresas();
  } else {
    alert((data && (data.error || data.detail)) || "No se pudo crear la empresa.");
  }
});

/* ---------- Arranque ---------- */

restoreSession();

/* ---------- Service worker (PWA instalable) ---------- */

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/sw.js")
      .catch((err) => console.warn("SW no registrado:", err));
  });
}
