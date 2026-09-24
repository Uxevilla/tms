import { useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import type { ColDef, ValueFormatterParams, CellValueChangedEvent } from "ag-grid-community";
import { Truck, Wrench, X, Save, Plus, UploadCloud, FileText, Download, Trash2, RotateCcw } from "lucide-react";
import { REST_VEHICULOS, REST_MANTENIMIENTOS, REST_MANTENIMIENTO_ALERTAS, REST_MANTENIMIENTO_CONVERTIR, PATCH_VEHICULO, REST_PROVEEDORES } from "../config";
import { api, ApiError } from "../api";
import { useAgGridState } from "../hooks/useAgGridState";
import { TallerCalendario } from "./TallerCalendario";
import { CaducidadRenderer } from "./CaducidadRenderer";
import { panelCell } from "./panelCell";

import { gridTheme, GRID_ROW_HEIGHT, GRID_HEADER_HEIGHT } from "../gridConfig";

// ------------------------------------------------------------------ tipos
interface Vehiculo {
  id: string;
  categoria: string;
  matricula: string;
  marca: string;
  modelo: string;
  anno: number;
  itv: string;
  seguro: string;
  clase_euro: string;
  capacidad_peso: number;
  capacidad_palets: number;
  coste_adquisicion: number;
  valor_residual: number;
  vida_util: number;
  disponible: boolean;
  ejes?: number;
  mma?: number;
  fecha_caducidad_itv?: string;
  seguro_compania?: string;
  fecha_caducidad_seguro?: string;
  tipo_tenencia?: string;
  proveedor_id?: number | null;
  proveedor_nombre?: string;
  fecha_alta?: string;
  cuota_mensual?: number;
  km_actuales?: number;
}

interface Mantenimiento {
  id: number;
  vehiculo_id: string;
  tipo: string;
  fecha: string;
  fecha_fin?: string;
  km: number;
  coste: number;
  notas: string;
  hecho: boolean;
  matricula?: string;
  categoria?: string;
  proveedor_id?: number | null;
  base_imponible?: number;
  iva?: number;
}

interface Alerta {
  id: number;
  vehiculo_id: string;
  codigo: string;
  severidad: string;
  mensaje: string;
  estado: string;
  creado_en: string;
  matricula?: string;
  marca?: string;
  modelo?: string;
  categoria?: string;
}

const eur = (v: number) =>
  v.toLocaleString("es-ES", { style: "currency", currency: "EUR" });

const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");

// Celdas editables: cursor de texto + fondo gris sutil al hover.
const editableCell = "cursor-text hover:bg-slate-100";

export function VehiculosDashboard() {
  const [tab, setTab] = useState<"flota" | "mantenimientos">("flota");
  const [vistaMant, setVistaMant] = useState<"lista" | "calendario">("lista");
  const [vehiculos, setVehiculos] = useState<Vehiculo[]>([]);
  const [mantenimientos, setMantenimientos] = useState<Mantenimiento[]>([]);
  const [alertas, setAlertas] = useState<Alerta[]>([]);
  const [selected, setSelected] = useState<Vehiculo | null>(null);

  // formulario drawer
  const [itv, setItv] = useState("");
  const [seguro, setSeguro] = useState("");
  const [coste, setCoste] = useState("");
  const [valorResidual, setValorResidual] = useState("");
  const [vidaUtil, setVidaUtil] = useState("");
  const [mTipo, setMTipo] = useState("");
  const [mFecha, setMFecha] = useState("");
  const [mFechaFin, setMFechaFin] = useState("");
  const [mKm, setMKm] = useState("");
  const [mCoste, setMCoste] = useState("");
  const [guardando, setGuardando] = useState(false);

  // ---- Alta de vehículo ----
  const [creando, setCreando] = useState(false);
  const [vIdTrimble, setVIdTrimble] = useState("");
  const [vMatricula, setVMatricula] = useState("");
  const [vCategoria, setVCategoria] = useState("tractora");
  const [vMarca, setVMarca] = useState("");
  const [vModelo, setVModelo] = useState("");
  const [vAnno, setVAnno] = useState("");
  const [vClaseEuro, setVClaseEuro] = useState("EURO 6");
  const [vPeso, setVPeso] = useState("");
  const [vPalets, setVPalets] = useState("");
  const [msgAlta, setMsgAlta] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const [guardandoAlta, setGuardandoAlta] = useState(false);
  const [editando, setEditando] = useState(false);
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);

  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 4000);
    return () => clearTimeout(t);
  }, [banner]);
  const [tabAlta, setTabAlta] = useState<"tecnico" | "legal" | "finanzas" | "documentos">("tecnico");
  const [vEjes, setVEjes] = useState("");
  const [vFechaCaducidadItv, setVFechaCaducidadItv] = useState("");
  const [vSeguroCompania, setVSeguroCompania] = useState("");
  const [vFechaCaducidadSeguro, setVFechaCaducidadSeguro] = useState("");
  const [vTipoTenencia, setVTipoTenencia] = useState("Propiedad");
  const [vFechaAlta, setVFechaAlta] = useState("");
  const [vProveedorId, setVProveedorId] = useState("");
  const [vCuotaMensual, setVCuotaMensual] = useState("");
  const [proveedores, setProveedores] = useState<Record<string, any>[]>([]);
  const proveedorNombres = useMemo<string[]>(() => proveedores.map((p) => String(p.nombre ?? "")).filter(Boolean), [proveedores]);
  const idProveedor = (nombre: string) => proveedores.find((p) => p.nombre === nombre)?.id ?? null;

  // ---- Drawer "Nueva Revisión/Taller" ----
  const [drawerRevision, setDrawerRevision] = useState(false);
  const [rVehiculo, setRVehiculo] = useState("");
  const [rTipo, setRTipo] = useState("");
  const [rFecha, setRFecha] = useState("");
  const [rFechaFin, setRFechaFin] = useState("");
  const [rKm, setRKm] = useState("");
  const [rCoste, setRCoste] = useState("");
  const [rNotas, setRNotas] = useState("");
  const [rEstado, setREstado] = useState<"pendiente" | "completado">("pendiente");
  const [rGenerarGasto, setRGenerarGasto] = useState(false);
  const [rProveedor, setRProveedor] = useState("");
  const [rBase, setRBase] = useState("");
  const [rIva, setRIva] = useState("21");
  const [guardandoRevision, setGuardandoRevision] = useState(false);
  const revertiendoRef = useRef(false);

  // Tono de alerta para fechas de caducidad: rojo si ya venció, ámbar si < 30 días.
  function claseCaducidad(fecha: string): string {
    if (!fecha) return "";
    const hoy = new Date();
    hoy.setHours(0, 0, 0, 0);
    const d = new Date(fecha + "T00:00:00");
    if (isNaN(d.getTime())) return "";
    const dias = Math.round((d.getTime() - hoy.getTime()) / 86400000);
    if (dias < 0) return "border-red-400 bg-red-50 text-red-700";
    if (dias <= 30) return "border-amber-400 bg-amber-50 text-amber-700";
    return "";
  }

  function abrirAlta() {
    setMsgAlta(null);
    setEditando(false);
    setTabAlta("tecnico");
    setCreando(true);
    setVIdTrimble(""); setVMatricula(""); setVCategoria("tractora"); setVMarca(""); setVModelo("");
    setVAnno(""); setVEjes(""); setVClaseEuro("EURO 6"); setVPeso(""); setVPalets("");
    setVFechaCaducidadItv(""); setVSeguroCompania(""); setVFechaCaducidadSeguro("");
    setVTipoTenencia("Propiedad"); setVFechaAlta(""); setVProveedorId(""); setVCuotaMensual("");
    setDocs([]);
    if (proveedores.length === 0) {
      api<{ proveedores?: Record<string, any>[] }>(REST_PROVEEDORES)
        .then((d) => setProveedores(d.proveedores ?? []))
        .catch(() => {});
    }
  }

  // ---- Documentos (tab) ----
  const [docs, setDocs] = useState<{ id: number; nombre: string; contenido: string; size: number }[]>([]);
  const [arrastrando, setArrastrando] = useState(false);
  const [subiendoDocs, setSubiendoDocs] = useState(false);
  const [cargandoDocs, setCargandoDocs] = useState(false);

  function cargarDocs() {
    if (!vIdTrimble.trim()) return;
    setCargandoDocs(true);
    api<{ documentos?: { id: number; nombre: string; contenido: string; size: number }[] }>(`${REST_VEHICULOS}/${vIdTrimble.trim()}/documentos`)
      .then((d) => setDocs(d.documentos ?? []))
      .catch(() => {})
      .finally(() => setCargandoDocs(false));
  }

  useEffect(() => {
    if (tabAlta === "documentos" && vIdTrimble.trim()) cargarDocs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tabAlta, vIdTrimble]);

  async function subirArchivos(fileList: FileList | File[]) {
    const files = Array.from(fileList).filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    if (!files.length || !vIdTrimble.trim()) return;
    setSubiendoDocs(true);
    const docsPayload: { nombre: string; contenido: string }[] = [];
    for (const f of files) {
      const contenido = await new Promise<string>((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => resolve(String(fr.result).split(",")[1] || "");
        fr.onerror = () => reject(fr.error);
        fr.readAsDataURL(f);
      });
      docsPayload.push({ nombre: f.name, contenido });
    }
    try {
      await api(`${REST_VEHICULOS}/${vIdTrimble.trim()}/documentos`, {
        method: "POST",
        body: JSON.stringify({ documentos: docsPayload }),
      });
      cargarDocs();
    } catch (err) {
      console.error("Error subiendo documentos:", err);
    } finally {
      setSubiendoDocs(false);
    }
  }

  function descargarDoc(d: { nombre: string; contenido: string }) {
    const byteChars = atob(d.contenido);
    const bytes = new Uint8Array(byteChars.length);
    for (let i = 0; i < byteChars.length; i++) bytes[i] = byteChars.charCodeAt(i);
    const blob = new Blob([bytes], { type: "application/pdf" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = d.nombre;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function eliminarDoc(id: number) {
    try {
      await api(`${REST_VEHICULOS}/${vIdTrimble.trim()}/documentos/${id}`, {
        method: "DELETE",
      });
      setDocs((prev) => prev.filter((d) => d.id !== id));
    } catch (err) {
      console.error("Error eliminando documento:", err);
    }
  }

  function cargarFormVehiculo(v: Vehiculo) {
    setVIdTrimble(v.id || "");
    setVMatricula(v.matricula || "");
    setVCategoria(v.categoria || "tractora");
    setVMarca(v.marca || "");
    setVModelo(v.modelo || "");
    setVAnno(v.anno ? String(v.anno) : "");
    setVEjes(v.ejes ? String(v.ejes) : "");
    setVClaseEuro(v.clase_euro || "EURO 6");
    setVPeso(v.capacidad_peso ? String(v.capacidad_peso) : "");
    setVPalets(v.capacidad_palets ? String(v.capacidad_palets) : "");
    setVFechaCaducidadItv(v.fecha_caducidad_itv || "");
    setVSeguroCompania(v.seguro_compania || "");
    setVFechaCaducidadSeguro(v.fecha_caducidad_seguro || "");
    setVTipoTenencia(v.tipo_tenencia || "Propiedad");
    setVFechaAlta(v.fecha_alta || "");
    setVProveedorId(v.proveedor_id ? String(v.proveedor_id) : "");
    setVCuotaMensual(v.cuota_mensual ? String(v.cuota_mensual) : "");
  }

  function editarFicha(v: Vehiculo) {
    setMsgAlta(null);
    setEditando(true);
    cargarFormVehiculo(v);
    setDocs([]);
    setTabAlta("tecnico");
    setCreando(true);
    setSelected(null);
  }

  function abrirDocumentos(v: Vehiculo) {
    setMsgAlta(null);
    setEditando(true);
    cargarFormVehiculo(v);
    setDocs([]);
    setTabAlta("documentos");
    setCreando(true);
    setSelected(null);
  }

  async function altaVehiculo() {
    if (!vIdTrimble.trim()) {
      setMsgAlta({ tipo: "error", texto: "Indica el ID / Referencia Trimble (obligatorio para asociar telemetría)." });
      return;
    }
    if (!vMatricula.trim()) {
      setMsgAlta({ tipo: "error", texto: "Indica la matrícula." });
      return;
    }
    setGuardandoAlta(true);
    setMsgAlta(null);
    try {
      await api(REST_VEHICULOS, {
        method: "POST",
        body: JSON.stringify({
          id: vIdTrimble.trim(),
          categoria: vCategoria,
          matricula: vMatricula.trim(),
          marca: vMarca.trim(),
          modelo: vModelo.trim(),
          anno: Number(vAnno) || 0,
          ejes: Number(vEjes) || 0,
          clase_euro: vClaseEuro,
          capacidad_peso: Number(vPeso) || 0,
          capacidad_palets: Number(vPalets) || 0,
          fecha_caducidad_itv: vFechaCaducidadItv,
          seguro_compania: vSeguroCompania.trim(),
          fecha_caducidad_seguro: vFechaCaducidadSeguro,
          tipo_tenencia: vTipoTenencia,
          proveedor_id: vProveedorId ? Number(vProveedorId) : null,
          fecha_alta: vFechaAlta,
          cuota_mensual: Number(vCuotaMensual) || 0,
        }),
      });
      setBanner({ tipo: "ok", texto: editando ? "Ficha del vehículo actualizada correctamente." : "Vehículo dado de alta correctamente." });
      setCreando(false);
      const v = await api<{ vehiculos?: Vehiculo[] }>(REST_VEHICULOS);
      setVehiculos(v.vehiculos ?? []);
      setEditando(false);
      setVIdTrimble(""); setVMatricula(""); setVMarca(""); setVModelo(""); setVAnno(""); setVEjes(""); setVPeso(""); setVPalets("");
      setVFechaCaducidadItv(""); setVSeguroCompania(""); setVFechaCaducidadSeguro("");
      setVTipoTenencia("Propiedad"); setVFechaAlta(""); setVProveedorId(""); setVCuotaMensual("");
    } catch (err) {
      if (err instanceof ApiError) {
        const d = err.detail as { detail?: { error?: string }; error?: string } | null;
        setMsgAlta({ tipo: "error", texto: d?.detail?.error || d?.error || err.message });
      } else {
        console.error("Error dando de alta vehículo:", err);
        setMsgAlta({ tipo: "error", texto: "Error de red al dar de alta el vehículo." });
      }
    } finally {
      setGuardandoAlta(false);
    }
  }

  // Carga de datos vivos.
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const [v, m, a, p] = await Promise.all([
          api<{ vehiculos?: Vehiculo[] }>(REST_VEHICULOS),
          api<{ mantenimientos?: Mantenimiento[] }>(REST_MANTENIMIENTOS),
          api<{ alertas?: Alerta[] }>(REST_MANTENIMIENTO_ALERTAS),
          api<{ proveedores?: Record<string, any>[] }>(REST_PROVEEDORES),
        ]);
        if (!cancel) {
          const provs = p.proveedores ?? [];
          setProveedores(provs);
          const nombreProveedor = (id: number | null | undefined) =>
            id == null ? "" : provs.find((x: any) => x.id === id)?.nombre ?? "";
          setVehiculos((v.vehiculos ?? []).map((x: Vehiculo) => ({ ...x, proveedor_nombre: nombreProveedor(x.proveedor_id) })));
          setMantenimientos(m.mantenimientos ?? []);
          setAlertas(a.alertas ?? []);
        }
      } catch (err) {
        console.error("Error cargando flota:", err);
      }
    })();
    return () => { cancel = true; };
  }, []);

  const flotaCols = useMemo<ColDef<Vehiculo>[]>(
    () => [
      { field: "id", headerName: "ID Trimble", width: 150, pinned: "left" },
      { field: "matricula", headerName: "Matrícula", width: 110, pinned: "left", cellRenderer: panelCell("vehiculo") },
      { field: "categoria", headerName: "Categoría", width: 110 },
      { field: "marca", headerName: "Marca", flex: 1, minWidth: 110 },
      { field: "modelo", headerName: "Modelo", flex: 1, minWidth: 110 },
      {
        field: "km_actuales",
        headerName: "Kilómetros",
        width: 120,
        type: "rightAligned",
        valueFormatter: (p) => `${Math.round(Number(p.value) || 0).toLocaleString("es-ES")} km`,
      },
      { field: "anno", headerName: "Año", width: 70 },
      { field: "fecha_caducidad_itv", headerName: "Caducidad ITV", width: 120, editable: true, cellClass: editableCell, cellEditor: "agDateStringCellEditor", cellRenderer: CaducidadRenderer },
      { field: "seguro_compania", headerName: "Cía. Seguro", width: 130, editable: true, cellClass: editableCell },
      { field: "fecha_caducidad_seguro", headerName: "Caducidad Seguro", width: 125, editable: true, cellClass: editableCell, cellEditor: "agDateStringCellEditor", cellRenderer: CaducidadRenderer },
      {
        field: "tipo_tenencia",
        headerName: "Tenencia",
        width: 110,
        editable: true,
        cellClass: editableCell,
        cellEditor: "agSelectCellEditor",
        cellEditorParams: { values: ["Propiedad", "Renting", "Leasing"] },
      },
      {
        field: "proveedor_nombre",
        headerName: "Proveedor",
        width: 150,
        editable: true,
        cellClass: editableCell,
        cellEditor: "agSelectCellEditor",
        cellEditorParams: { values: proveedorNombres },
      },
      { field: "cuota_mensual", headerName: "Cuota (€/mes)", width: 110, editable: true, cellClass: editableCell, type: "rightAligned", cellEditor: "agNumberCellEditor" },
      { field: "clase_euro", headerName: "Clase Euro", width: 100, editable: true, cellClass: editableCell },
      { field: "capacidad_peso", headerName: "Carga (t)", width: 90, editable: true, cellClass: editableCell, type: "rightAligned", cellEditor: "agNumberCellEditor" },
      { field: "capacidad_palets", headerName: "Palets", width: 80, editable: true, cellClass: editableCell, type: "rightAligned", cellEditor: "agNumberCellEditor" },
      { field: "mma", headerName: "MMA (kg)", width: 95, editable: true, cellClass: editableCell, type: "rightAligned", cellEditor: "agNumberCellEditor" },
      { field: "ejes", headerName: "Ejes", width: 70, editable: true, cellClass: editableCell, type: "rightAligned", cellEditor: "agNumberCellEditor" },
      { field: "coste_adquisicion", headerName: "Coste (€)", width: 105, editable: true, cellClass: editableCell, type: "rightAligned", cellEditor: "agNumberCellEditor", valueFormatter: (p) => eur(Number(p.value) || 0) },
      { field: "valor_residual", headerName: "V. Residual (€)", width: 115, editable: true, cellClass: editableCell, type: "rightAligned", cellEditor: "agNumberCellEditor", valueFormatter: (p) => eur(Number(p.value) || 0) },
      { field: "vida_util", headerName: "Vida útil", width: 85, editable: true, cellClass: editableCell, type: "rightAligned", cellEditor: "agNumberCellEditor" },
      {
        headerName: "Estado",
        width: 120,
        cellRenderer: (p: { data: Vehiculo }) => (
          <span
            className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-bold ${
              p.data.disponible
                ? "bg-emerald-100 text-emerald-700"
                : "bg-amber-100 text-amber-700"
            }`}
          >
            {p.data.disponible ? "Disponible" : "En curso"}
          </span>
        ),
      },
    ],
    [proveedorNombres]
  );

  const mantCols = useMemo<ColDef<Mantenimiento>[]>(
    () => [
      { field: "matricula", headerName: "Vehículo", width: 120, valueGetter: (p) => p.data?.matricula || p.data?.vehiculo_id, cellRenderer: panelCell<Mantenimiento>("vehiculo", (d) => d.matricula || d.vehiculo_id) },
      { field: "categoria", headerName: "Categoría", width: 130, valueGetter: (p) => cap(p.data?.categoria || "") },
      { field: "tipo", headerName: "Tipo", flex: 1, minWidth: 140 },
      { field: "fecha", headerName: "Fecha", width: 100 },
      { field: "km", headerName: "Km", width: 90, type: "rightAligned", valueFormatter: (p: ValueFormatterParams) => `${Number(p.value) || 0}` },
      { field: "coste", headerName: "Coste", width: 110, type: "rightAligned", editable: true, cellClass: editableCell, cellEditor: "agNumberCellEditor", valueFormatter: (p: ValueFormatterParams) => eur(Number(p.value) || 0) },
      {
        field: "hecho",
        headerName: "Estado",
        width: 120,
        editable: true,
        cellClass: editableCell,
        cellEditor: "agSelectCellEditor",
        cellEditorParams: { values: ["Pendiente", "Completado"] },
        valueGetter: (p) => (p.data?.hecho ? "Completado" : "Pendiente"),
        valueSetter: (p) => { p.data.hecho = p.newValue === "Completado"; return true; },
      },
    ],
    []
  );

  const alertaCols: ColDef<Alerta>[] = [
    { field: "matricula", headerName: "Vehículo", width: 120, valueGetter: (p) => p.data?.matricula || p.data?.vehiculo_id, cellRenderer: panelCell<Alerta>("vehiculo", (d) => d.matricula || d.vehiculo_id) },
    { field: "categoria", headerName: "Categoría", width: 120, valueGetter: (p) => cap(p.data?.categoria || "") },
    { field: "codigo", headerName: "Código", width: 130 },
    {
      field: "severidad",
      headerName: "Severidad",
      width: 100,
      cellRenderer: (p: { value: string }) => {
        const s = (p.value || "").toLowerCase();
        return (
          <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-bold ${s === "alta" ? "bg-red-100 text-red-700" : s === "media" ? "bg-amber-100 text-amber-700" : "bg-sky-100 text-sky-700"}`}>
            {p.value}
          </span>
        );
      },
    },
    { field: "mensaje", headerName: "Mensaje", flex: 1, minWidth: 180 },
    { field: "estado", headerName: "Estado", width: 90 },
    {
      headerName: "Acción",
      width: 150,
      cellRenderer: (p: { data: Alerta }) => (
        <button
          onClick={() => convertirAlerta(p.data.id)}
          className="inline-flex items-center gap-1 rounded-md bg-blue-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-blue-700"
        >
          <Wrench size={12} /> Convertir
        </button>
      ),
    },
  ];

  const defaultColDef = useMemo<ColDef>(() => ({ sortable: true, resizable: true, filter: true }), []);

  const { resetColumnState, exportToCsv, ...gridHandlers } = useAgGridState("tms_vehiculos_column_state");
  const { resetColumnState: resetAlertas, exportToCsv: exportAlertas, ...alertHandlers } = useAgGridState("tms_alertas_grid");
  const { resetColumnState: resetMant, exportToCsv: exportMant, ...mantHandlers } = useAgGridState("tms_historial_grid");

  async function recargarMantYAlertas() {
    try {
      const [m, a] = await Promise.all([
        api<{ mantenimientos?: Mantenimiento[] }>(REST_MANTENIMIENTOS),
        api<{ alertas?: Alerta[] }>(REST_MANTENIMIENTO_ALERTAS),
      ]);
      setMantenimientos(m.mantenimientos ?? []);
      setAlertas(a.alertas ?? []);
    } catch (err) {
      console.error("Error recargando mantenimientos/alertas:", err);
    }
  }

  function abrirRevision() {
    setRVehiculo(""); setRTipo(""); setRFecha(""); setRFechaFin(""); setRKm(""); setRCoste("");
    setRNotas(""); setREstado("pendiente"); setRGenerarGasto(false); setRProveedor(""); setRBase(""); setRIva("21");
    setDrawerRevision(true);
  }

  async function guardarRevision() {
    if (!rVehiculo || !rTipo || !rFecha) {
      setBanner({ tipo: "error", texto: "Indica vehículo, tipo y fecha." });
      return;
    }
    setGuardandoRevision(true);
    try {
      const hecho = rEstado === "completado";
      const body: Record<string, any> = {
        vehiculo_id: rVehiculo, tipo: rTipo, fecha: rFecha, fecha_fin: rFechaFin,
        km: Number(rKm) || 0, coste: Number(rCoste) || 0, notas: rNotas, hecho,
        generar_gasto: hecho && rGenerarGasto,
        proveedor_id: rProveedor ? Number(rProveedor) : null,
        base_imponible: rGenerarGasto ? Number(rBase) || 0 : 0,
        iva: Number(rIva) || 21,
      };
      const d = await api<{ ok?: boolean; detail?: { error?: string }; error?: string }>(REST_MANTENIMIENTOS, {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (d.ok) {
        setBanner({ tipo: "ok", texto: "Revisión registrada." });
        setDrawerRevision(false);
        recargarMantYAlertas();
      } else {
        setBanner({ tipo: "error", texto: d?.detail?.error || d?.error || "Error al guardar la revisión." });
      }
    } catch (err) {
      if (err instanceof ApiError) {
        const d = err.detail as { detail?: { error?: string }; error?: string } | null;
        setBanner({ tipo: "error", texto: d?.detail?.error || d?.error || err.message });
      } else {
        setBanner({ tipo: "error", texto: "Error de red al guardar." });
      }
    } finally {
      setGuardandoRevision(false);
    }
  }

  async function convertirAlerta(id: number) {
    try {
      await api(REST_MANTENIMIENTO_CONVERTIR(id), {
        method: "POST",
      });
      setBanner({ tipo: "ok", texto: "Alerta convertida en orden de taller." });
      recargarMantYAlertas();
    } catch (err) {
      if (err instanceof ApiError) {
        setBanner({ tipo: "error", texto: `No se pudo convertir (${err.status}).` });
      } else {
        setBanner({ tipo: "error", texto: "Error de red al convertir." });
      }
    }
  }

  async function onCellValueChangedMant(event: CellValueChangedEvent<Mantenimiento>) {
    const { colDef, data, newValue, oldValue } = event;
    const field = colDef.field;
    if (!field || !data || revertiendoRef.current) return;
    try {
      await api(`${REST_MANTENIMIENTOS}/${data.id}/campos`, {
        method: "PATCH",
        body: JSON.stringify({ [field]: newValue }),
      });
    } catch {
      revertiendoRef.current = true;
      event.node.setDataValue(field, oldValue);
      revertiendoRef.current = false;
      setBanner({ tipo: "error", texto: `No se pudo guardar «${field}».` });
    }
  }

  // Edición en línea: PATCH del campo modificado; rollback a event.oldValue si falla.
  async function onCellValueChanged(event: CellValueChangedEvent<Vehiculo>) {
    const { colDef, data, newValue, oldValue } = event;
    const field = colDef.field;
    if (!field || !data || revertiendoRef.current) return;

    let campo = field;
    let valor: unknown = newValue;
    // El proveedor se edita por nombre en el grid; aquí se resuelve a su id (FK).
    if (field === "proveedor_nombre") {
      campo = "proveedor_id";
      valor = idProveedor(String(newValue ?? ""));
    }

    try {
      await api(PATCH_VEHICULO(data.id), {
        method: "PATCH",
        body: JSON.stringify({ [campo]: valor }),
      });
      if (field === "proveedor_nombre") {
        setVehiculos((prev) => prev.map((x) => (x.id === data.id ? { ...x, proveedor_id: valor as number, proveedor_nombre: String(newValue ?? "") } : x)));
      }
    } catch {
      revertiendoRef.current = true;
      event.node.setDataValue(field, oldValue); // rollback automático
      revertiendoRef.current = false;
      setBanner({ tipo: "error", texto: `No se pudo guardar «${field}». Cambio revertido.` });
    }
  }

  function selectVehicle(v: Vehiculo) {
    setSelected(v);
    setItv(v.itv || "");
    setSeguro(v.seguro || "");
    setCoste(v.coste_adquisicion ? String(v.coste_adquisicion) : "");
    setValorResidual(v.valor_residual ? String(v.valor_residual) : "");
    setVidaUtil(v.vida_util ? String(v.vida_util) : "");
  }

  async function guardarDatosTecnicos() {
    if (!selected) return;
    setGuardando(true);
    try {
      const body: Record<string, unknown> = { itv, seguro };
      if (coste !== "") body.coste_adquisicion = Number(coste);
      if (valorResidual !== "") body.valor_residual = Number(valorResidual);
      if (vidaUtil !== "") body.vida_util = Number(vidaUtil);
      await api(PATCH_VEHICULO(selected.id), {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setVehiculos((prev) => prev.map((v) => (v.id === selected.id ? { ...v, ...body } : v)));
      setSelected(null);
    } catch (err) {
      console.error("Error guardando vehículo:", err);
    } finally {
      setGuardando(false);
    }
  }

  async function registrarMantenimiento() {
    if (!selected || !mTipo) return;
    setGuardando(true);
    try {
      await api(REST_MANTENIMIENTOS, {
        method: "POST",
        body: JSON.stringify({
          vehiculo_id: selected.id, tipo: mTipo, fecha: mFecha, fecha_fin: mFechaFin,
          km: Number(mKm) || 0, coste: Number(mCoste) || 0, notas: "", hecho: false,
        }),
      });
      setMTipo(""); setMFecha(""); setMFechaFin(""); setMKm(""); setMCoste("");
      const m = await api<{ mantenimientos?: Mantenimiento[] }>(REST_MANTENIMIENTOS);
      setMantenimientos(m.mantenimientos ?? []);
    } catch (err) {
      console.error("Error registrando mantenimiento:", err);
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="flex h-full w-full flex-col p-3">
      <div className="mb-2 flex shrink-0 items-center gap-2">
        <div className="inline-flex rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
          <button onClick={() => setTab("flota")} className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "flota" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}>
            <Truck size={16} /> Flota Activa
          </button>
          <button onClick={() => setTab("mantenimientos")} className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "mantenimientos" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}>
            <Wrench size={16} /> Mantenimientos
          </button>
        </div>
        {tab === "flota" && (
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={resetColumnState}
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
            >
              <RotateCcw size={16} /> Restaurar vista
            </button>
            <button
              onClick={() => exportToCsv("vehiculos.csv")}
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
            >
              <Download size={16} /> Exportar CSV
            </button>
            <button
              onClick={abrirAlta}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white shadow hover:bg-blue-700"
            >
              <Plus size={16} /> Añadir Vehículo
            </button>
          </div>
        )}
        {tab === "mantenimientos" && (
          <div className="ml-auto flex items-center gap-2">
            <button onClick={abrirRevision} className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white shadow hover:bg-blue-700">
              <Plus size={16} /> Nueva Revisión/Taller
            </button>
          </div>
        )}
      </div>

      {tab === "flota" ? (
        <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white">
          <AgGridReact<Vehiculo>
            theme={gridTheme}
            columnDefs={flotaCols}
            defaultColDef={defaultColDef}
            rowData={vehiculos}
            rowHeight={GRID_ROW_HEIGHT}
            headerHeight={GRID_HEADER_HEIGHT}
            rowSelection="single"
            singleClickEdit
            stopEditingWhenCellsLoseFocus
            onCellValueChanged={onCellValueChanged}
            onCellClicked={(e) => {
              if (e.colDef.editable) return;
              if (e.data) selectVehicle(e.data);
            }}

            {...gridHandlers}
          />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 gap-2">
          <div className="flex min-h-0 w-1/2 flex-col">
            <TallerCalendario vehiculos={vehiculos} />
          </div>
          <div className="flex min-h-0 w-1/2 flex-col gap-2">
            <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-slate-200 bg-white">
              <div className="flex items-center justify-between border-b border-slate-100 px-3 py-1.5">
                <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Alertas preventivas</span>
                <button onClick={() => exportAlertas("alertas_preventivas.csv")} className="text-xs text-slate-400 hover:text-slate-600"><Download size={14} /></button>
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">
                <AgGridReact<Alerta> theme={gridTheme} columnDefs={alertaCols} defaultColDef={defaultColDef} rowData={alertas} rowHeight={GRID_ROW_HEIGHT} headerHeight={GRID_HEADER_HEIGHT} {...alertHandlers} />
              </div>
            </div>
            <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-slate-200 bg-white">
              <div className="flex items-center justify-between border-b border-slate-100 px-3 py-1.5">
                <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Historial de reparaciones</span>
                <button onClick={() => exportMant("historial.csv")} className="text-xs text-slate-400 hover:text-slate-600"><Download size={14} /></button>
              </div>
              <div className="min-h-0 flex-1 overflow-hidden">
                <AgGridReact<Mantenimiento> theme={gridTheme} columnDefs={mantCols} defaultColDef={defaultColDef} rowData={mantenimientos} rowHeight={GRID_ROW_HEIGHT} headerHeight={GRID_HEADER_HEIGHT} singleClickEdit stopEditingWhenCellsLoseFocus onCellValueChanged={onCellValueChangedMant} {...mantHandlers} />
              </div>
            </div>
          </div>
        </div>
      )}

      {selected && (
        <div className="fixed inset-y-0 right-0 z-40 flex w-1/4 flex-col border-l border-slate-200 bg-white shadow-2xl">
          <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
            <h3 className="text-sm font-semibold text-slate-800">{selected.matricula} · {selected.marca} {selected.modelo}</h3>
            <div className="flex items-center gap-1">
              <button onClick={() => editarFicha(selected)} className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-200">
                <Save size={14} /> Editar ficha
              </button>
              <button onClick={() => abrirDocumentos(selected)} className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600 hover:bg-slate-200">
                <FileText size={14} /> Documentos
              </button>
              <button onClick={() => setSelected(null)} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </div>
          </header>

          <div className="flex-1 space-y-4 overflow-y-auto p-4 text-sm">
            <div>
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Datos técnicos</div>
              <div className="space-y-2">
                <label className="block">
                  <span className="text-xs text-slate-500">ITV</span>
                  <input type="date" value={itv} onChange={(e) => setItv(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
                <label className="block">
                  <span className="text-xs text-slate-500">Seguro</span>
                  <input type="date" value={seguro} onChange={(e) => setSeguro(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
                <label className="block">
                  <span className="text-xs text-slate-500">Coste adquisición (€)</span>
                  <input type="number" value={coste} onChange={(e) => setCoste(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
                <label className="block">
                  <span className="text-xs text-slate-500">Valor residual (€)</span>
                  <input type="number" value={valorResidual} onChange={(e) => setValorResidual(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
                <label className="block">
                  <span className="text-xs text-slate-500">Vida útil (años)</span>
                  <input type="number" value={vidaUtil} onChange={(e) => setVidaUtil(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
              </div>
              <button onClick={guardarDatosTecnicos} disabled={guardando} className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60">
                <Save size={15} /> {guardando ? "Guardando…" : "Guardar datos técnicos"}
              </button>
            </div>

            <div className="border-t border-slate-100 pt-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Nuevo mantenimiento</div>
              <div className="space-y-2">
                <input type="text" placeholder="Tipo (aceite, neumáticos, ITV…)" value={mTipo} onChange={(e) => setMTipo(e.target.value)} className="w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                <div className="flex gap-2">
                  <input type="date" value={mFecha} onChange={(e) => setMFecha(e.target.value)} className="w-1/2 rounded-md border border-slate-200 px-2 py-1.5 text-sm" title="Inicio" />
                  <input type="date" value={mFechaFin} onChange={(e) => setMFechaFin(e.target.value)} className="w-1/2 rounded-md border border-slate-200 px-2 py-1.5 text-sm" title="Fin (salida estimada)" />
                </div>
                <div className="flex gap-2">
                  <input type="number" placeholder="Km" value={mKm} onChange={(e) => setMKm(e.target.value)} className="w-1/2 rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                  <input type="number" placeholder="Coste €" value={mCoste} onChange={(e) => setMCoste(e.target.value)} className="w-1/2 rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </div>
              </div>
              <button onClick={registrarMantenimiento} disabled={guardando || !mTipo} className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-800 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:opacity-60">
                <Plus size={15} /> Registrar mantenimiento
              </button>
            </div>
          </div>
        </div>
      )}

      {drawerRevision && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/40 backdrop-blur-sm" onClick={() => !guardandoRevision && setDrawerRevision(false)} />
          <div className="fixed inset-y-0 right-0 z-50 flex w-[360px] flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">Nueva revisión / taller</h3>
              <button onClick={() => setDrawerRevision(false)} disabled={guardandoRevision} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"><X size={18} /></button>
            </header>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
              <label className="block">
                <span className="text-xs font-medium text-slate-500">Vehículo *</span>
                <select value={rVehiculo} onChange={(e) => setRVehiculo(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="">— Seleccionar —</option>
                  {vehiculos.map((v) => <option key={v.id} value={v.id}>[{cap(v.categoria)}] {v.matricula} — {v.marca} {v.modelo}</option>)}
                </select>
              </label>
              <label className="block">
                <span className="text-xs font-medium text-slate-500">Tipo *</span>
                <select value={rTipo} onChange={(e) => setRTipo(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="">— Seleccionar —</option>
                  <option value="ITV">ITV</option>
                  <option value="Taller">Taller correctivo</option>
                  <option value="Preventivo">Revisión rutinaria</option>
                  <option value="Aceite">Cambio de aceite</option>
                  <option value="Neumáticos">Neumáticos</option>
                  <option value="Otros">Otros</option>
                </select>
              </label>
              <div className="flex gap-2">
                <label className="block w-1/2">
                  <span className="text-xs font-medium text-slate-500">Fecha *</span>
                  <input type="date" value={rFecha} onChange={(e) => setRFecha(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
                <label className="block w-1/2">
                  <span className="text-xs font-medium text-slate-500">Fin</span>
                  <input type="date" value={rFechaFin} onChange={(e) => setRFechaFin(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
              </div>
              <div className="flex gap-2">
                <label className="block w-1/2">
                  <span className="text-xs font-medium text-slate-500">Km</span>
                  <input type="number" value={rKm} onChange={(e) => setRKm(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
                <label className="block w-1/2">
                  <span className="text-xs font-medium text-slate-500">Coste (€)</span>
                  <input type="number" value={rCoste} onChange={(e) => setRCoste(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
              </div>
              <label className="block">
                <span className="text-xs font-medium text-slate-500">Estado</span>
                <select value={rEstado} onChange={(e) => setREstado(e.target.value as "pendiente" | "completado")} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                  <option value="pendiente">Pendiente</option>
                  <option value="completado">Completado</option>
                </select>
              </label>
              {rEstado === "completado" && (
                <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
                  <label className="flex items-center justify-between">
                    <span className="text-xs font-medium text-slate-600">¿Generar apunte de gasto?</span>
                    <input type="checkbox" checked={rGenerarGasto} onChange={(e) => setRGenerarGasto(e.target.checked)} className="h-4 w-4" />
                  </label>
                  {rGenerarGasto && (
                    <div className="mt-3 space-y-2">
                      <label className="block">
                        <span className="text-xs font-medium text-slate-500">Taller (proveedor)</span>
                        <select value={rProveedor} onChange={(e) => setRProveedor(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                          <option value="">Sin proveedor</option>
                          {proveedores.map((p) => <option key={p.id} value={p.id}>{p.nombre}</option>)}
                        </select>
                      </label>
                      <div className="flex gap-2">
                        <label className="block w-1/2">
                          <span className="text-xs font-medium text-slate-500">Base (€) *</span>
                          <input type="number" value={rBase} onChange={(e) => setRBase(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                        </label>
                        <label className="block w-1/2">
                          <span className="text-xs font-medium text-slate-500">% IVA</span>
                          <input type="number" value={rIva} onChange={(e) => setRIva(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                        </label>
                      </div>
                    </div>
                  )}
                </div>
              )}
              <label className="block">
                <span className="text-xs font-medium text-slate-500">Notas</span>
                <textarea value={rNotas} onChange={(e) => setRNotas(e.target.value)} rows={2} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
              </label>
              <button onClick={guardarRevision} disabled={guardandoRevision} className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-800 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:opacity-60">
                <Save size={15} /> {guardandoRevision ? "Guardando…" : "Guardar revisión"}
              </button>
            </div>
          </div>
        </>
      )}

      {banner && (
        <div className={`fixed right-4 top-4 z-50 rounded-lg px-4 py-3 text-sm font-medium text-white shadow-lg ${banner.tipo === "ok" ? "bg-emerald-600" : "bg-red-600"}`}>
          {banner.texto}
        </div>
      )}

      {creando && (
        <>
          <div className="fixed inset-0 z-30 bg-slate-900/40 backdrop-blur-sm" onClick={() => !guardandoAlta && setCreando(false)} />
          <div className="fixed inset-y-0 right-0 z-40 flex w-1/4 flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">{editando ? "Ficha del vehículo" : "Alta de vehículo"}</h3>
              <button onClick={() => setCreando(false)} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </header>
            <div className="flex min-h-0 flex-1 flex-col text-sm">
              <div className="flex shrink-0 gap-1 border-b border-slate-100 p-2">
                {([
                  ["tecnico", "Datos Técnicos"],
                  ["legal", "Documentación"],
                  ["finanzas", "Finanzas"],
                  ["documentos", "Documentos"],
                ] as const).map(([id, label]) => (
                  <button
                    key={id}
                    onClick={() => setTabAlta(id)}
                    className={`flex-1 rounded-md px-2 py-1.5 text-xs font-medium transition ${tabAlta === id ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
                {tabAlta === "tecnico" && (
                  <>
                    <label className="block">
                      <span className="text-xs text-slate-500">ID / Referencia Trimble *</span>
                      <input value={vIdTrimble} onChange={(e) => setVIdTrimble(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" placeholder="APP_EUSEBIO / CCV-RAUL / 3001-TTL" />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Matrícula *</span>
                      <input value={vMatricula} onChange={(e) => setVMatricula(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" placeholder="1234 KLM" />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Categoría</span>
                      <select value={vCategoria} onChange={(e) => setVCategoria(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                        <option value="tractora">Tractora</option>
                        <option value="semirremolque">Semirremolque</option>
                        <option value="frigorifico">Frigorífico</option>
                        <option value="furgon">Furgón</option>
                        <option value="ligero">Ligero</option>
                        <option value="cisterna">Cisterna</option>
                      </select>
                    </label>
                    <div className="flex gap-2">
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Marca</span>
                        <input value={vMarca} onChange={(e) => setVMarca(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Modelo</span>
                        <input value={vModelo} onChange={(e) => setVModelo(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                    </div>
                    <div className="flex gap-2">
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Año</span>
                        <input type="number" value={vAnno} onChange={(e) => setVAnno(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Ejes</span>
                        <input type="number" value={vEjes} onChange={(e) => setVEjes(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                    </div>
                    <label className="block">
                      <span className="text-xs text-slate-500">Clase Euro</span>
                      <select value={vClaseEuro} onChange={(e) => setVClaseEuro(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                        <option>EURO 6</option>
                        <option>EURO 5</option>
                        <option>EURO 4</option>
                      </select>
                    </label>
                    <div className="flex gap-2">
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Capacidad (t)</span>
                        <input type="number" value={vPeso} onChange={(e) => setVPeso(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                      <label className="block w-1/2">
                        <span className="text-xs text-slate-500">Capacidad (palets)</span>
                        <input type="number" value={vPalets} onChange={(e) => setVPalets(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                      </label>
                    </div>
                  </>
                )}
                {tabAlta === "legal" && (
                  <>
                    <label className="block">
                      <span className="text-xs text-slate-500">Caducidad ITV</span>
                      <input type="date" value={vFechaCaducidadItv} onChange={(e) => setVFechaCaducidadItv(e.target.value)} className={`mt-0.5 w-full rounded-md border px-2 py-1.5 text-sm ${claseCaducidad(vFechaCaducidadItv) || "border-slate-200"}`} />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Compañía de seguro</span>
                      <input value={vSeguroCompania} onChange={(e) => setVSeguroCompania(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Caducidad seguro</span>
                      <input type="date" value={vFechaCaducidadSeguro} onChange={(e) => setVFechaCaducidadSeguro(e.target.value)} className={`mt-0.5 w-full rounded-md border px-2 py-1.5 text-sm ${claseCaducidad(vFechaCaducidadSeguro) || "border-slate-200"}`} />
                    </label>
                    <p className="text-[11px] text-slate-400">Ámbar = vence en &lt; 30 días · Rojo = ya vencida.</p>
                  </>
                )}
                {tabAlta === "finanzas" && (
                  <>
                    <label className="block">
                      <span className="text-xs text-slate-500">Tipo de tenencia</span>
                      <select value={vTipoTenencia} onChange={(e) => setVTipoTenencia(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                        <option>Propiedad</option>
                        <option>Renting</option>
                        <option>Leasing</option>
                      </select>
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Fecha de alta</span>
                      <input type="date" value={vFechaAlta} onChange={(e) => setVFechaAlta(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Proveedor</span>
                      <select value={vProveedorId} onChange={(e) => setVProveedorId(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                        <option value="">— Seleccionar —</option>
                        {proveedores.map((p) => <option key={p.id} value={p.id}>{p.nombre}</option>)}
                      </select>
                    </label>
                    <label className="block">
                      <span className="text-xs text-slate-500">Cuota mensual (€)</span>
                      <input type="number" value={vCuotaMensual} onChange={(e) => setVCuotaMensual(e.target.value)} className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                  </>
                )}
                {tabAlta === "documentos" && (
                  <div className="space-y-3">
                    {!vIdTrimble.trim() ? (
                      <p className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-500">Guarda primero el vehículo (con su ID / Referencia Trimble) para adjuntar documentos.</p>
                    ) : (
                      <>
                        <div
                          onDragOver={(e) => { e.preventDefault(); setArrastrando(true); }}
                          onDragLeave={() => setArrastrando(false)}
                          onDrop={(e) => { e.preventDefault(); setArrastrando(false); subirArchivos(e.dataTransfer.files); }}
                          className={`flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-6 text-center transition ${arrastrando ? "border-blue-400 bg-slate-100" : "border-slate-300 bg-slate-50"}`}
                        >
                          <UploadCloud size={28} className={arrastrando ? "text-blue-500" : "text-slate-400"} />
                          <p className="text-xs text-slate-500">
                            Arrastra aquí tus PDFs o{" "}
                            <label className="cursor-pointer text-blue-600 underline">
                              selecciónalos
                              <input type="file" accept="application/pdf" multiple className="hidden" onChange={(e) => e.target.files && subirArchivos(e.target.files)} />
                            </label>
                          </p>
                          {subiendoDocs && <span className="text-xs text-slate-400">Subiendo…</span>}
                        </div>
                        {cargandoDocs && <p className="text-xs text-slate-400">Cargando documentos…</p>}
                        {!cargandoDocs && docs.length === 0 ? (
                          <p className="text-xs text-slate-400">Sin documentos.</p>
                        ) : (
                          <ul className="space-y-1">
                            {docs.map((d) => (
                              <li key={d.id} className="flex items-center gap-2 rounded-md border border-slate-100 px-2 py-1.5">
                                <FileText size={16} className="shrink-0 text-slate-400" />
                                <span className="min-w-0 flex-1 truncate text-xs text-slate-700">{d.nombre}</span>
                                <span className="shrink-0 text-[10px] text-slate-400">{Math.max(1, Math.round(d.size / 1024))} KB</span>
                                <button onClick={() => descargarDoc(d)} title="Descargar" className="shrink-0 rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600"><Download size={15} /></button>
                                <button onClick={() => eliminarDoc(d.id)} title="Eliminar" className="shrink-0 rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-600"><Trash2 size={15} className="text-red-500" /></button>
                              </li>
                            ))}
                          </ul>
                        )}
                      </>
                    )}
                  </div>
                )}
                {msgAlta && (
                  <div className={`rounded-md px-3 py-2 text-xs ${msgAlta.tipo === "ok" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"}`}>
                    {msgAlta.texto}
                  </div>
                )}
              </div>
            </div>
            <footer className="border-t border-slate-200 p-4">
              <button onClick={altaVehiculo} disabled={guardandoAlta} className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60">
                {guardandoAlta ? "Guardando…" : editando ? "Guardar ficha" : "Añadir vehículo"}
              </button>
            </footer>
          </div>
        </>
      )}
    </div>
  );
}
