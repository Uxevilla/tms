import { useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, ValueFormatterParams, CellValueChangedEvent } from "ag-grid-community";
import { Users, Receipt, CalendarDays, CalendarRange, X, Plus, FilePlus2, Save, UserPlus, RotateCcw, Download, FileDown } from "lucide-react";
import { REST_EMPLEADOS, REST_NOMINAS, REST_AUSENCIAS } from "../config";
import { getToken } from "../auth";
import { api, ApiError } from "../api";
import { useAgGridState } from "../hooks/useAgGridState";
import { PlanningCalendario } from "./PlanningCalendario";
import { CaducidadRenderer } from "./CaducidadRenderer";
import { panelCell } from "./panelCell";

import { gridTheme, GRID_ROW_HEIGHT, GRID_HEADER_HEIGHT } from "../gridConfig";

// Celdas editables: cursor de texto + fondo gris sutil al hover.
const editableCell = "cursor-text hover:bg-slate-100";

// ------------------------------------------------------------------ tipos
interface Empleado {
  id: string;
  nombre: string;
  apellidos: string;
  conductor_id?: number | null;
  dni: string;
  telefono: string;
  categoria: string;
  puesto: string;
  salario_bruto: number;
  irpf: number;
  activo: boolean;
  nss?: string;
  email?: string;
  direccion?: string;
  ciudad?: string;
  cp?: string;
  fecha_alta?: string;
  fecha_baja?: string;
  tipo_contrato?: string;
  jornada?: string;
  banco?: string;
  iban?: string;
  titular?: string;
  disponibilidad?: string;
  motivo_no_dispo?: string;
  convenio?: string;
  observaciones?: string;
  caducidad_carnet?: string;
  caducidad_cap?: string;
  caducidad_medica?: string;
}

interface Nomina {
  id: number;
  empleado_id: string;
  periodo: string;
  salario_bruto: number;
  irpf_importe: number;
  neto: number;
  coste_empresa: number;
  estado: string;
  pagado: boolean;
  nombre: string;
  apellidos: string;
  categoria: string;
}

interface Ausencia {
  id: number;
  empleado_id: string;
  tipo: string;
  fecha_inicio: string;
  fecha_fin: string;
  dias: number;
  estado: string;
  nota: string;
  nombre: string;
  apellidos: string;
}

const eur = (v: number) =>
  v.toLocaleString("es-ES", { style: "currency", currency: "EUR" });

async function verNominaPdf(id: number) {
  try {
    const r = await fetch(`${REST_NOMINAS}/${id}/pdf`, { headers: { Authorization: `Bearer ${getToken() ?? ""}` } });
    if (!r.ok) return;
    const url = URL.createObjectURL(await r.blob());
    window.open(url, "_blank");
  } catch (err) {
    console.error("Error generando PDF de nómina:", err);
  }
}

const EMPLEADO_VACIO = {
  nombre: "", apellidos: "", dni: "", nss: "", email: "", telefono: "",
  direccion: "", ciudad: "", cp: "", categoria: "Conductor", puesto: "",
  tipo_contrato: "Indefinido", jornada: "Completa", salario_bruto: "", irpf: "15",
  fecha_alta: "", fecha_baja: "", convenio: "", banco: "", iban: "", titular: "",
  disponibilidad: "disponible", motivo_no_dispo: "", observaciones: "",
};

export function RrhhDashboard() {
  const [tab, setTab] = useState<"plantilla" | "nominas" | "ausencias" | "planning">("plantilla");
  const [empleados, setEmpleados] = useState<Empleado[]>([]);
  const [nominas, setNominas] = useState<Nomina[]>([]);
  const [ausencias, setAusencias] = useState<Ausencia[]>([]);
  const [selected, setSelected] = useState<Empleado | null>(null);

  // formulario ausencia
  const [aTipo, setATipo] = useState("Vacaciones");
  const [aInicio, setAInicio] = useState("");
  const [aFin, setAFin] = useState("");
  const [aDias, setADias] = useState("");
  const [guardando, setGuardando] = useState(false);
  const [msg, setMsg] = useState("");

  // ---- ficha de empleado (alta/edición) ----
  const [creando, setCreando] = useState(false);
  const [editando, setEditando] = useState(false);
  const [fichaId, setFichaId] = useState("");
  const [tabFicha, setTabFicha] = useState<"personal" | "laboral" | "bancario">("personal");
  const [ficha, setFicha] = useState<Record<string, string>>({ ...EMPLEADO_VACIO });
  const [guardandoFicha, setGuardandoFicha] = useState(false);
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const setF = (k: string, v: string) => setFicha((p) => ({ ...p, [k]: v }));

  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 4000);
    return () => clearTimeout(t);
  }, [banner]);

  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const [e, n, a] = await Promise.all([
          api<{ empleados?: Empleado[] }>(REST_EMPLEADOS),
          api<{ nominas?: Nomina[] }>(REST_NOMINAS),
          api<{ ausencias?: Ausencia[] }>(REST_AUSENCIAS),
        ]);
        if (!cancel) {
          setEmpleados(e.empleados ?? []);
          setNominas(n.nominas ?? []);
          setAusencias(a.ausencias ?? []);
        }
      } catch (err) {
        console.error("Error cargando RRHH:", err);
      }
    })();
    return () => { cancel = true; };
  }, []);

  const plantillaCols = useMemo<ColDef<Empleado>[]>(
    () => [
      { headerName: "Nombre", flex: 1, minWidth: 180, valueGetter: (p) => `${p.data?.nombre ?? ""} ${p.data?.apellidos ?? ""}`.trim(), cellRenderer: panelCell<Empleado>("conductor", (d) => d.conductor_id ?? undefined) },
      { field: "dni", headerName: "DNI", width: 120, editable: true, cellClass: editableCell },
      { field: "telefono", headerName: "Teléfono", width: 130, editable: true, cellClass: editableCell },
      { field: "email", headerName: "Email", width: 180, editable: true, cellClass: editableCell },
      { field: "categoria", headerName: "Categoría", width: 140, editable: true, cellClass: editableCell, cellEditor: "agSelectCellEditor", cellEditorParams: { values: ["Conductor", "Administrativo", "Comercial", "Taller", "Oficina"] } },
      { field: "puesto", headerName: "Puesto", width: 130, editable: true, cellClass: editableCell },
      { field: "jornada", headerName: "Jornada", width: 110, editable: true, cellClass: editableCell, cellEditor: "agSelectCellEditor", cellEditorParams: { values: ["Completa", "Parcial"] } },
      { field: "salario_bruto", headerName: "Salario bruto", width: 120, editable: true, cellClass: editableCell, type: "rightAligned", cellEditor: "agNumberCellEditor", valueFormatter: (p: ValueFormatterParams) => eur(Number(p.value) || 0) },
      { field: "irpf", headerName: "IRPF %", width: 90, editable: true, cellClass: editableCell, type: "rightAligned", cellEditor: "agNumberCellEditor" },
      {
        field: "fecha_baja",
        headerName: "Estado",
        width: 110,
        editable: true,
        cellClass: editableCell,
        cellEditor: "agSelectCellEditor",
        cellEditorParams: { values: ["Alta", "Baja"] },
        valueGetter: (p) => (p.data?.fecha_baja ? "Baja" : "Alta"),
        valueSetter: (p) => {
          p.data.fecha_baja = p.newValue === "Baja" ? new Date().toISOString().slice(0, 10) : "";
          return true;
        },
      },
      {
        field: "caducidad_carnet",
        headerName: "Carnet",
        width: 150,
        editable: true,
        cellClass: editableCell,
        cellEditor: "agDateStringCellEditor",
        cellRenderer: CaducidadRenderer,
      },
      {
        field: "caducidad_cap",
        headerName: "CAP",
        width: 150,
        editable: true,
        cellClass: editableCell,
        cellEditor: "agDateStringCellEditor",
        cellRenderer: CaducidadRenderer,
      },
      {
        field: "caducidad_medica",
        headerName: "Rec. Médico",
        width: 150,
        editable: true,
        cellClass: editableCell,
        cellEditor: "agDateStringCellEditor",
        cellRenderer: CaducidadRenderer,
      },
    ],
    []
  );

  const nominaCols = useMemo<ColDef<Nomina>[]>(
    () => [
      { headerName: "Empleado", flex: 1, minWidth: 170, valueGetter: (p) => `${p.data?.nombre ?? ""} ${p.data?.apellidos ?? ""}`.trim() },
      { field: "periodo", headerName: "Periodo", width: 100 },
      { field: "salario_bruto", headerName: "Salario bruto", width: 130, type: "rightAligned", valueFormatter: (p: ValueFormatterParams) => eur(Number(p.value) || 0) },
      { field: "irpf_importe", headerName: "Retenciones", width: 120, type: "rightAligned", valueFormatter: (p: ValueFormatterParams) => eur(Number(p.value) || 0) },
      { field: "neto", headerName: "Neto", width: 120, type: "rightAligned", valueFormatter: (p: ValueFormatterParams) => eur(Number(p.value) || 0) },
      { field: "coste_empresa", headerName: "Coste empresa", width: 130, type: "rightAligned", valueFormatter: (p: ValueFormatterParams) => eur(Number(p.value) || 0) },
      {
        field: "estado",
        headerName: "Estado",
        width: 110,
        cellRenderer: (p: { value: string }) => {
          const s = (p.value || "").toLowerCase();
          return (
            <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-bold ${
              s === "pagada" ? "bg-emerald-100 text-emerald-700" : s === "borrador" ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-600"
            }`}>
              {p.value}
            </span>
          );
        },
      },
      {
        headerName: "PDF",
        width: 70,
        cellRenderer: (p: { data: Nomina }) => (
          <button
            onClick={() => verNominaPdf(p.data.id)}
            title="Descargar PDF"
            className="rounded-md bg-slate-100 p-1.5 text-slate-600 transition hover:bg-slate-200 hover:text-slate-800"
          >
            <FileDown size={14} />
          </button>
        ),
      },
    ],
    []
  );

  const ausenciaCols = useMemo<ColDef<Ausencia>[]>(
    () => [
      { headerName: "Empleado", flex: 1, minWidth: 170, valueGetter: (p) => `${p.data?.nombre ?? ""} ${p.data?.apellidos ?? ""}`.trim() },
      { field: "tipo", headerName: "Tipo", width: 140 },
      { field: "fecha_inicio", headerName: "Inicio", width: 110 },
      { field: "fecha_fin", headerName: "Fin", width: 110 },
      { field: "dias", headerName: "Días", width: 80, type: "rightAligned" },
      {
        field: "estado",
        headerName: "Estado",
        width: 110,
        cellRenderer: (p: { value: string }) => (
          <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-bold ${p.value === "Aprobada" ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-700"}`}>
            {p.value}
          </span>
        ),
      },
    ],
    []
  );

  const defaultColDef = useMemo<ColDef>(() => ({ sortable: true, resizable: true, filter: true }), []);
  const revertiendoRef = useRef(false);
  const { resetColumnState, exportToCsv, ...gridHandlers } = useAgGridState("tms_empleados_grid");
  // Edición en línea: PATCH del campo modificado; rollback a event.oldValue si falla.
  async function onCellValueChanged(event: CellValueChangedEvent<Empleado>) {
    const { colDef, data, newValue, oldValue } = event;
    const field = colDef.field;
    if (!field || !data || revertiendoRef.current) return;
    // Para columnas con valueSetter (estado → fecha_baja), el valor resuelto está en data[field].
    const valor = field === "fecha_baja" ? data.fecha_baja : newValue;
    try {
      await api(`${REST_EMPLEADOS}/${data.id}`, {
        method: "PATCH",
        body: JSON.stringify({ [field]: valor }),
      });
    } catch {
      revertiendoRef.current = true;
      event.node.setDataValue(field, oldValue); // rollback automático
      revertiendoRef.current = false;
      setBanner({ tipo: "error", texto: `No se pudo guardar «${field}». Cambio revertido.` });
    }
  }

  function selectEmpleado(e: Empleado) {
    setSelected(e);
    setMsg("");
  }

  function cargarFormEmpleado(e: Empleado) {
    setFicha({
      nombre: e.nombre || "", apellidos: e.apellidos || "", dni: e.dni || "", nss: e.nss || "",
      email: e.email || "", telefono: e.telefono || "", direccion: e.direccion || "", ciudad: e.ciudad || "", cp: e.cp || "",
      categoria: e.categoria || "Conductor", puesto: e.puesto || "",
      tipo_contrato: e.tipo_contrato || "Indefinido", jornada: e.jornada || "Completa",
      salario_bruto: e.salario_bruto != null ? String(e.salario_bruto) : "", irpf: e.irpf != null ? String(e.irpf) : "15",
      fecha_alta: e.fecha_alta || "", fecha_baja: e.fecha_baja || "", convenio: e.convenio || "",
      banco: e.banco || "", iban: e.iban || "", titular: e.titular || "",
      disponibilidad: e.disponibilidad || "disponible", motivo_no_dispo: e.motivo_no_dispo || "", observaciones: e.observaciones || "",
    });
  }

  function abrirAlta() {
    setFichaId("");
    setEditando(false);
    setTabFicha("personal");
    setFicha({ ...EMPLEADO_VACIO });
    setCreando(true);
  }

  function editarFicha(e: Empleado) {
    setFichaId(e.id);
    setEditando(true);
    cargarFormEmpleado(e);
    setTabFicha("personal");
    setCreando(true);
    setSelected(null);
  }

  async function guardarFicha() {
    if (!ficha.nombre.trim()) {
      setBanner({ tipo: "error", texto: "Indica el nombre del empleado." });
      return;
    }
    setGuardandoFicha(true);
    const payload = {
      nombre: ficha.nombre.trim(), apellidos: ficha.apellidos.trim(), dni: ficha.dni.trim(),
      nss: ficha.nss.trim(), email: ficha.email.trim(), telefono: ficha.telefono.trim(),
      direccion: ficha.direccion.trim(), ciudad: ficha.ciudad.trim(), cp: ficha.cp.trim(),
      categoria: ficha.categoria, puesto: ficha.puesto.trim(),
      tipo_contrato: ficha.tipo_contrato, jornada: ficha.jornada,
      salario_bruto: Number(ficha.salario_bruto) || 0, irpf: Number(ficha.irpf) || 15,
      fecha_alta: ficha.fecha_alta, fecha_baja: ficha.fecha_baja, convenio: ficha.convenio.trim(),
      banco: ficha.banco.trim(), iban: ficha.iban.trim(), titular: ficha.titular.trim(),
      disponibilidad: ficha.disponibilidad, motivo_no_dispo: ficha.motivo_no_dispo.trim(),
      observaciones: ficha.observaciones.trim(),
    };
    try {
      const url = editando ? `${REST_EMPLEADOS}/${fichaId}` : REST_EMPLEADOS;
      await api(url, { method: editando ? "PATCH" : "POST", body: JSON.stringify(payload) });
      setBanner({ tipo: "ok", texto: editando ? "Ficha del empleado actualizada." : "Empleado dado de alta correctamente." });
      setCreando(false);
      setEditando(false);
      const e = await api<{ empleados?: Empleado[] }>(REST_EMPLEADOS);
      setEmpleados(e.empleados ?? []);
      setFicha({ ...EMPLEADO_VACIO });
    } catch (err) {
      if (err instanceof ApiError) {
        const d = err.detail as { detail?: { error?: string }; error?: string } | null;
        setBanner({ tipo: "error", texto: d?.detail?.error || d?.error || err.message });
      } else {
        console.error("Error guardando empleado:", err);
        setBanner({ tipo: "error", texto: "Error de red al guardar el empleado." });
      }
    } finally {
      setGuardandoFicha(false);
    }
  }

  async function generarNomina() {
    if (!selected) return;
    setGuardando(true);
    setMsg("");
    try {
      const periodo = new Date().toISOString().slice(0, 7);
      await api(REST_NOMINAS, {
        method: "POST",
        body: JSON.stringify({ empleado_id: selected.id, periodo, salario_bruto: 0, irpf_pct: 0 }),
      });
      setMsg(`Nómina de ${periodo} generada (bruto ${eur(selected.salario_bruto)}).`);
      const n = await api<{ nominas?: Nomina[] }>(REST_NOMINAS);
      setNominas(n.nominas ?? []);
    } catch (err) {
      if (err instanceof ApiError) {
        setMsg("No se pudo generar (¿ya existe la nómina del periodo?).");
      } else {
        console.error("Error generando nómina:", err);
      }
    } finally {
      setGuardando(false);
    }
  }

  async function registrarAusencia() {
    if (!selected || !aInicio || !aFin) return;
    setGuardando(true);
    setMsg("");
    try {
      await api(REST_AUSENCIAS, {
        method: "POST",
        body: JSON.stringify({
          empleado_id: selected.id, tipo: aTipo, fecha_inicio: aInicio, fecha_fin: aFin,
          dias: Number(aDias) || 0, estado: "Pendiente", nota: "",
        }),
      });
      setMsg("Ausencia registrada.");
      setAInicio(""); setAFin(""); setADias("");
      const a = await api<{ ausencias?: Ausencia[] }>(REST_AUSENCIAS);
      setAusencias(a.ausencias ?? []);
    } catch (err) {
      console.error("Error registrando ausencia:", err);
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="flex h-full w-full flex-col p-3">
      <div className="mb-2 flex shrink-0 items-center gap-2">
        <div className="inline-flex rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
          <button onClick={() => setTab("plantilla")} className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "plantilla" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}>
            <Users size={16} /> Plantilla
          </button>
          <button onClick={() => setTab("nominas")} className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "nominas" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}>
            <Receipt size={16} /> Nóminas
          </button>
          <button onClick={() => setTab("ausencias")} className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "ausencias" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}>
            <CalendarDays size={16} /> Ausencias
          </button>
          <button onClick={() => setTab("planning")} className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "planning" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}>
            <CalendarRange size={16} /> Planning Ausencias
          </button>
        </div>
        {tab === "plantilla" && (
          <div className="ml-auto flex items-center gap-2">
            <button onClick={resetColumnState} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700">
              <RotateCcw size={16} /> Restaurar vista
            </button>
            <button onClick={() => exportToCsv("empleados.csv")} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700">
              <Download size={16} /> Exportar CSV
            </button>
            <button onClick={abrirAlta} className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white shadow hover:bg-blue-700">
              <UserPlus size={16} /> Añadir Empleado
            </button>
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white">
        {tab === "plantilla" && (
          <AgGridReact<Empleado> theme={gridTheme} columnDefs={plantillaCols} defaultColDef={defaultColDef} rowData={empleados} rowHeight={GRID_ROW_HEIGHT} headerHeight={GRID_HEADER_HEIGHT} rowSelection="single" singleClickEdit stopEditingWhenCellsLoseFocus onCellValueChanged={onCellValueChanged} onCellClicked={(e) => { if (e.colDef.editable) return; if (e.data) selectEmpleado(e.data); }} {...gridHandlers} />
        )}
        {tab === "nominas" && (
          <AgGridReact<Nomina> theme={gridTheme} columnDefs={nominaCols} defaultColDef={defaultColDef} rowData={nominas} rowHeight={GRID_ROW_HEIGHT} headerHeight={GRID_HEADER_HEIGHT} />
        )}
        {tab === "ausencias" && (
          <AgGridReact<Ausencia> theme={gridTheme} columnDefs={ausenciaCols} defaultColDef={defaultColDef} rowData={ausencias} rowHeight={GRID_ROW_HEIGHT} headerHeight={GRID_HEADER_HEIGHT} />
        )}
        {tab === "planning" && (
          <PlanningCalendario empleados={empleados} />
        )}
      </div>

      {selected && (
        <div className="fixed inset-y-0 right-0 z-40 flex w-1/4 flex-col border-l border-slate-200 bg-white shadow-2xl">
          <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
            <h3 className="text-sm font-semibold text-slate-800">{selected.nombre} {selected.apellidos}</h3>
            <div className="flex items-center gap-1">
              <button onClick={() => editarFicha(selected)} className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-200">
                <Save size={14} /> Editar ficha
              </button>
              <button onClick={() => setSelected(null)} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </div>
          </header>

          <div className="flex-1 space-y-4 overflow-y-auto p-4 text-sm">
            <div className="space-y-1">
              <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Empleado</div>
              <div className="font-medium text-slate-700">{selected.categoria} · {selected.puesto}</div>
              <div className="text-slate-500">Salario bruto: <span className="font-medium text-slate-700">{eur(selected.salario_bruto)}</span></div>
            </div>

            <div className="border-t border-slate-100 pt-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Nómina del mes</div>
              <button onClick={generarNomina} disabled={guardando} className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60">
                <FilePlus2 size={15} /> {guardando ? "Generando…" : "Generar nómina del mes"}
              </button>
            </div>

            <div className="border-t border-slate-100 pt-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Nueva ausencia</div>
              <div className="space-y-2">
                <select value={aTipo} onChange={(e) => setATipo(e.target.value)} className="w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option>Vacaciones</option>
                  <option>Baja médica</option>
                  <option>Permiso</option>
                  <option>Otro</option>
                </select>
                <div className="flex gap-2">
                  <input type="date" value={aInicio} onChange={(e) => setAInicio(e.target.value)} className="w-1/2 rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                  <input type="date" value={aFin} onChange={(e) => setAFin(e.target.value)} className="w-1/2 rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </div>
                <input type="number" placeholder="Días" value={aDias} onChange={(e) => setADias(e.target.value)} className="w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
              </div>
              <button onClick={registrarAusencia} disabled={guardando || !aInicio || !aFin} className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-800 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:opacity-60">
                <Plus size={15} /> Registrar ausencia
              </button>
            </div>

            {msg && <div className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">{msg}</div>}
          </div>
        </div>
      )}

      {creando && (
        <>
          <div className="fixed inset-0 z-30 bg-slate-900/40 backdrop-blur-sm" onClick={() => !guardandoFicha && setCreando(false)} />
          <div className="fixed inset-y-0 right-0 z-40 flex w-1/4 flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">{editando ? "Ficha del empleado" : "Alta de empleado"}</h3>
              <button onClick={() => setCreando(false)} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </header>
            <div className="flex min-h-0 flex-1 flex-col text-sm">
              <div className="flex shrink-0 gap-1 border-b border-slate-100 p-2">
                {([
                  ["personal", "Datos personales"],
                  ["laboral", "Laboral"],
                  ["bancario", "Bancario"],
                ] as const).map(([id, label]) => (
                  <button
                    key={id}
                    onClick={() => setTabFicha(id)}
                    className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition ${tabFicha === id ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
                {tabFicha === "personal" && (
                  <>
                    <div className="flex gap-2">
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Nombre *</span>
                        <input value={ficha.nombre} onChange={(e) => setF("nombre", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Apellidos</span>
                        <input value={ficha.apellidos} onChange={(e) => setF("apellidos", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                    </div>
                    <div className="flex gap-2">
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">DNI</span>
                        <input value={ficha.dni} onChange={(e) => setF("dni", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">NSS</span>
                        <input value={ficha.nss} onChange={(e) => setF("nss", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                    </div>
                    <label className="block">
                      <span className="text-xs text-slate-500">Email</span>
                      <input type="email" value={ficha.email} onChange={(e) => setF("email", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Teléfono</span>
                      <input value={ficha.telefono} onChange={(e) => setF("telefono", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Dirección</span>
                      <input value={ficha.direccion} onChange={(e) => setF("direccion", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                    <div className="flex gap-2">
                      <label className="block w-2/3">
                        <span className="text-xs text-slate-500">Ciudad</span>
                        <input value={ficha.ciudad} onChange={(e) => setF("ciudad", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                      <label className="block w-1/3">
                        <span className="text-xs text-slate-500">CP</span>
                        <input value={ficha.cp} onChange={(e) => setF("cp", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                    </div>
                  </>
                )}
                {tabFicha === "laboral" && (
                  <>
                    <div className="flex gap-2">
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Categoría</span>
                        <select value={ficha.categoria} onChange={(e) => setF("categoria", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                          <option>Conductor</option>
                          <option>Administrativo</option>
                          <option>Mecánico</option>
                          <option>Otro</option>
                        </select>
                      </label>
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Puesto</span>
                        <input value={ficha.puesto} onChange={(e) => setF("puesto", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                    </div>
                    <div className="flex gap-2">
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Tipo de contrato</span>
                        <select value={ficha.tipo_contrato} onChange={(e) => setF("tipo_contrato", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                          <option>Indefinido</option>
                          <option>Temporal</option>
                          <option>Prácticas</option>
                        </select>
                      </label>
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Jornada</span>
                        <select value={ficha.jornada} onChange={(e) => setF("jornada", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                          <option>Completa</option>
                          <option>Parcial</option>
                        </select>
                      </label>
                    </div>
                    <div className="flex gap-2">
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Salario bruto (€)</span>
                        <input type="number" value={ficha.salario_bruto} onChange={(e) => setF("salario_bruto", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">IRPF (%)</span>
                        <input type="number" value={ficha.irpf} onChange={(e) => setF("irpf", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                    </div>
                    <div className="flex gap-2">
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Fecha alta</span>
                        <input type="date" value={ficha.fecha_alta} onChange={(e) => setF("fecha_alta", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Fecha baja</span>
                        <input type="date" value={ficha.fecha_baja} onChange={(e) => setF("fecha_baja", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                    </div>
                    <label className="block">
                      <span className="text-xs text-slate-500">Convenio</span>
                      <input value={ficha.convenio} onChange={(e) => setF("convenio", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Disponibilidad</span>
                      <select value={ficha.disponibilidad} onChange={(e) => setF("disponibilidad", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                        <option value="disponible">Disponible</option>
                        <option value="ocupado">Ocupado</option>
                        <option value="baja">De baja</option>
                      </select>
                    </label>
                    {ficha.disponibilidad !== "disponible" && (
                      <label className="block">
                        <span className="text-xs text-slate-500">Motivo no disponibilidad</span>
                        <input value={ficha.motivo_no_dispo} onChange={(e) => setF("motivo_no_dispo", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                    )}
                  </>
                )}
                {tabFicha === "bancario" && (
                  <>
                    <label className="block">
                      <span className="text-xs text-slate-500">Banco</span>
                      <input value={ficha.banco} onChange={(e) => setF("banco", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">IBAN</span>
                      <input value={ficha.iban} onChange={(e) => setF("iban", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Titular</span>
                      <input value={ficha.titular} onChange={(e) => setF("titular", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Observaciones</span>
                      <textarea rows={4} value={ficha.observaciones} onChange={(e) => setF("observaciones", e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                  </>
                )}
              </div>
            </div>
            <footer className="border-t border-slate-200 p-4">
              <button onClick={guardarFicha} disabled={guardandoFicha} className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60">
                {guardandoFicha ? "Guardando…" : editando ? "Guardar ficha" : "Añadir empleado"}
              </button>
            </footer>
          </div>
        </>
      )}

      {banner && (
        <div className={`fixed right-4 top-4 z-50 rounded-lg px-4 py-3 text-sm font-medium text-white shadow-lg ${banner.tipo === "ok" ? "bg-emerald-600" : "bg-red-600"}`}>
          {banner.texto}
        </div>
      )}
    </div>
  );
}
