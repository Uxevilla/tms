import { useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import type { ColDef, ValueFormatterParams, CellValueChangedEvent } from "ag-grid-community";
import { FileText, HandCoins, X, Check, Users, Truck, BookOpen, Plus, Trash2, TrendingUp, RotateCcw, Download, ShieldCheck, Package, Tags } from "lucide-react";
import { EMITIR_BORRADOR, REST_BORRADORES, REST_LIQUIDACIONES, REST_CLIENTES, REST_PROVEEDORES, REST_TARIFAS, REST_ASIENTOS, REST_PYG, REST_BALANCE, REST_RECONCILIACION, REST_AUDITORIA } from "../config";
import { getRol } from "../auth";
import { api, ApiError } from "../api";
import { useAgGridState } from "../hooks/useAgGridState";

// Registro único de los módulos Community (master/detail incluido).
import { gridTheme, GRID_ROW_HEIGHT, GRID_HEADER_HEIGHT } from "../gridConfig";
ModuleRegistry.registerModules([AllCommunityModule]);

// Tema Alpine en modo compacto (alta densidad para monitores ultrawide).

// Celdas editables: cursor de texto + fondo gris sutil al hover.
const editableCell = "cursor-text hover:bg-slate-100";

// ------------------------------------------------------------------ tipos

interface CosteDesglose {
  concepto: string;
  importe: number;
}

interface BorradorFactura {
  id: number;
  trip_id: string;
  fecha: string;
  cliente: string;
  origen: string;
  destino: string;
  base: number;
  total: number;
  costes: number;
  margen: number; // base - costes (absoluto)
  desglose: CosteDesglose[];
}

interface Liquidacion {
  id: number;
  conductor: string;
  viaje_id: string;
  fecha: string;
  km_total: number;
  tarifa: number;
  importe: number;
  estado: string;
}

interface Cliente {
  id: number;
  nombre: string;
  cif: string;
  direccion: string;
  poblacion: string;
  cp: string;
  telefono: string;
  email: string;
  cuenta_contable_defecto: string;
}

interface Proveedor {
  id: number;
  nombre: string;
  cif: string;
  direccion: string;
  poblacion: string;
  cp: string;
  telefono: string;
  email: string;
  cuenta_contable_defecto: string;
}

interface Tarifa {
  id: number;
  nombre: string;
  tipo: string;
  precio: number;
  cliente_id: number | null;
  cliente_nombre: string;
  activo: boolean;
}

interface Asiento {
  id: number;
  numero: number;
  fecha: string;
  concepto: string;
  origen: string;
  debe: number;
  haber: number;
}

interface PygRow {
  seccion: "Ingresos" | "Gastos" | "Resultado";
  cuenta: string;
  nombre: string;
  importe: number;
}

interface BalanceRow {
  grupo: "Activo" | "Pasivo" | "Patrimonio Neto" | "Total";
  cuenta: string;
  nombre: string;
  importe: number;
}

// ---- shape real del backend (snake_case) + mapeo a los tipos de UI ----

interface BorradorBackend {
  id: number;
  numero: string;
  fecha: string;
  trip_id: string;
  cliente_nombre: string;
  base: number;
  iva: number;
  cuota_iva: number;
  total: number;
  coste: number;
  margen: number;
  creado: string;
  origen?: string | null;
  destino?: string | null;
  desglose?: CosteDesglose[];
}

interface LiquidacionBackend {
  id: number;
  conductor: string;
  viaje_id: string;
  fecha: string;
  km_total: number;
  tarifa: number;
  importe: number;
  estado: string;
}

const mapBorrador = (b: BorradorBackend): BorradorFactura => ({
  id: b.id,
  trip_id: b.trip_id ?? "—",
  fecha: b.fecha ?? "",
  cliente: b.cliente_nombre || "—",
  origen: b.origen || "",
  destino: b.destino || "",
  base: Number(b.base) || 0,
  total: Number(b.total) || 0,
  costes: Number(b.coste) || 0,
  margen: Number(b.margen) || 0,
  desglose: b.desglose ?? [],
});

const mapLiquidacion = (l: LiquidacionBackend): Liquidacion => ({
  id: l.id,
  conductor: l.conductor || "—",
  viaje_id: l.viaje_id ?? "—",
  fecha: l.fecha ?? "",
  km_total: Number(l.km_total) || 0,
  tarifa: Number(l.tarifa) || 0,
  importe: Number(l.importe) || 0,
  estado: l.estado || "Pendiente",
});

const mapAsientos = (
  asientos: { id: number; numero: number; fecha: string; concepto: string; origen: string }[],
  apuntes: Record<number, { debe: number; haber: number }[]>
): Asiento[] =>
  asientos.map((a) => {
    const lines = apuntes[a.id] ?? [];
    const debe = lines.reduce((s, l) => s + Number(l.debe || 0), 0);
    const haber = lines.reduce((s, l) => s + Number(l.haber || 0), 0);
    return { id: a.id, numero: a.numero, fecha: a.fecha, concepto: a.concepto, origen: a.origen, debe, haber };
  });

const mapPyg = (d: { ingresos?: { cuenta: string; nombre: string; importe: number }[]; gastos?: { cuenta: string; nombre: string; importe: number }[]; resultado?: number }): { rows: PygRow[]; resultado: number } => {
  const rows: PygRow[] = [];
  for (const r of d.ingresos ?? []) rows.push({ seccion: "Ingresos", cuenta: r.cuenta, nombre: r.nombre, importe: Number(r.importe) || 0 });
  for (const r of d.gastos ?? []) rows.push({ seccion: "Gastos", cuenta: r.cuenta, nombre: r.nombre, importe: Number(r.importe) || 0 });
  return { rows, resultado: Number(d.resultado) || 0 };
};

const mapBalance = (d: { cuentas?: { cuenta: string; nombre: string; tipo: string; saldo: number }[] }): { rows: BalanceRow[]; totalActivo: number; totalPasivoPatrimonio: number } => {
  const rows: BalanceRow[] = [];
  let activo = 0, pasivo = 0, patrimonio = 0, resultado = 0;
  for (const c of d.cuentas ?? []) {
    const saldo = Number(c.saldo) || 0;
    if (c.tipo === "activo") { rows.push({ grupo: "Activo", cuenta: c.cuenta, nombre: c.nombre, importe: saldo }); activo += saldo; }
    else if (c.tipo === "pasivo") { const v = -saldo; rows.push({ grupo: "Pasivo", cuenta: c.cuenta, nombre: c.nombre, importe: v }); pasivo += v; }
    else if (c.tipo === "patrimonio") { const v = -saldo; rows.push({ grupo: "Patrimonio Neto", cuenta: c.cuenta, nombre: c.nombre, importe: v }); patrimonio += v; }
    else if (c.tipo === "ingreso") resultado += -saldo;
    else if (c.tipo === "gasto") resultado += -saldo;
  }
  patrimonio += resultado;
  if (Math.abs(resultado) > 0.005) rows.push({ grupo: "Patrimonio Neto", cuenta: "129", nombre: "Resultado del ejercicio", importe: resultado });
  return { rows, totalActivo: activo, totalPasivoPatrimonio: pasivo + patrimonio };
};

// ------------------------------------------------------------- helpers

const eur = (v: number) =>
  v.toLocaleString("es-ES", { style: "currency", currency: "EUR" });

const margenPct = (b: BorradorFactura) =>
  b.base > 0 ? (b.margen / b.base) * 100 : 0;

// -------------------------------------------------------------- componente

interface ReconciliacionRow {
  vehiculo: string;
  odo_inicio: number | null;
  odo_fin: number | null;
  km_odometro: number;
  km_viajes: number;
  km_vacio: number;
  pct_vacio: number;
}

interface AuditoriaRow {
  id: number;
  tabla: string;
  registro_id: string | null;
  accion: string;
  usuario: string;
  antes: string | null;
  despues: string | null;
  ts: string;
}

export function ContabilidadDashboard() {
  const [tab, setTab] = useState<"borradores" | "liquidaciones" | "clientes" | "proveedores" | "tarifas" | "diario" | "informes" | "auditoria">("borradores");
  const [borradores, setBorradores] = useState<BorradorFactura[]>([]);
  const [liquidaciones, setLiquidaciones] = useState<Liquidacion[]>([]);
  const [clientes, setClientes] = useState<Cliente[]>([]);
  const [proveedores, setProveedores] = useState<Proveedor[]>([]);
  const [tarifas, setTarifas] = useState<Tarifa[]>([]);
  const [asientos, setAsientos] = useState<Asiento[]>([]);
  // informes financieros
  const [informesSub, setInformesSub] = useState<"pyg" | "balance" | "reconciliacion">("pyg");
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [pyg, setPyg] = useState<{ rows: PygRow[]; resultado: number }>({ rows: [], resultado: 0 });
  const [balance, setBalance] = useState<{ rows: BalanceRow[]; totalActivo: number; totalPasivoPatrimonio: number }>({ rows: [], totalActivo: 0, totalPasivoPatrimonio: 0 });
  const [reconciliacion, setReconciliacion] = useState<ReconciliacionRow[]>([]);
  const [reconcTotal, setReconcTotal] = useState<{ km_odometro: number; km_viajes: number; km_vacio: number }>({ km_odometro: 0, km_viajes: 0, km_vacio: 0 });
  const [auditoria, setAuditoria] = useState<AuditoriaRow[]>([]);
  const [rol, setRol] = useState<string | null>(null);
  const [selected, setSelected] = useState<BorradorFactura | null>(null);
  const [emitiendo, setEmitiendo] = useState(false);
  // alta de cliente/proveedor
  const [altaTipo, setAltaTipo] = useState<"cliente" | "proveedor" | null>(null);
  const [nForm, setNForm] = useState({ nombre: "", cif: "", direccion: "", poblacion: "", cp: "", telefono: "", email: "" });
  const [altaTarifa, setAltaTarifa] = useState(false);
  const [tForm, setTForm] = useState({ nombre: "", tipo: "viaje", precio: "", cliente_id: "" });
  const [guardandoN, setGuardandoN] = useState(false);
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const [palesCliente, setPalesCliente] = useState<Cliente | null>(null);
  const [palesData, setPalesData] = useState<{ saldo: number; movimientos: any[] } | null>(null);

  async function verPales(c: Cliente) {
    setPalesCliente(c);
    setPalesData(null);
    try {
      const d = await api<any>(`/api/clientes/${c.id}/pales`);
      setPalesData(d.ok ? d : { saldo: 0, movimientos: [] });
    } catch {
      setPalesData({ saldo: 0, movimientos: [] });
    }
  }
  const setN = (k: string, v: string) => setNForm((p) => ({ ...p, [k]: v }));

  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 4000);
    return () => clearTimeout(t);
  }, [banner]);

  // Carga inicial: borradores y liquidaciones en paralelo.
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const d = await api<any>(REST_BORRADORES);
        const data = (d.borradores ?? []).map(mapBorrador);
        if (!cancel) setBorradores(data);
      } catch (err) {
        console.error("Error cargando borradores:", err);
      }
    })();
    (async () => {
      try {
        const d = await api<any>(REST_LIQUIDACIONES);
        const data = (d.liquidaciones ?? []).map(mapLiquidacion);
        if (!cancel) setLiquidaciones(data);
      } catch (err) {
        console.error("Error cargando liquidaciones:", err);
      }
    })();
    return () => {
      cancel = true;
    };
  }, []);

  // Carga inicial: clientes, proveedores y asientos en paralelo.
  useEffect(() => {
    let cancel = false;
    (async () => {
      try {
        const [cli, prov, tar, asientosRes] = await Promise.all([
          api<any>(REST_CLIENTES),
          api<any>(REST_PROVEEDORES),
          api<any>(REST_TARIFAS),
          api<any>(REST_ASIENTOS),
        ]);
        if (cancel) return;
        setClientes(cli.clientes ?? []);
        setProveedores(prov.proveedores ?? []);
        setTarifas(tar.tarifas ?? []);
        setAsientos(mapAsientos(asientosRes.asientos ?? [], asientosRes.apuntes ?? {}));
      } catch (err) {
        console.error("Error cargando contabilidad:", err);
      }
    })();
    return () => {
      cancel = true;
    };
  }, []);

  // Informes financieros: se recargan cuando cambian las fechas.
  useEffect(() => {
    let cancel = false;
    const params = new URLSearchParams();
    if (desde) params.set("desde", desde);
    if (hasta) params.set("hasta", hasta);
    const qs = params.toString() ? `?${params.toString()}` : "";
    (async () => {
      try {
        const [pygRes, balanceRes, reconcRes] = await Promise.all([
          api<any>(`${REST_PYG}${qs}`),
          api<any>(`${REST_BALANCE}${qs}`),
          api<any>(`${REST_RECONCILIACION}${qs}`),
        ]);
        if (cancel) return;
        setPyg(mapPyg(pygRes));
        setBalance(mapBalance(balanceRes));
        setReconciliacion(reconcRes.vehiculos ?? []);
        setReconcTotal(reconcRes.total ?? { km_odometro: 0, km_viajes: 0, km_vacio: 0 });
      } catch (err) {
        console.error("Error cargando informes:", err);
      }
    })();
    return () => {
      cancel = true;
    };
  }, [desde, hasta]);

  // Columnas del grid de borradores (con master/detail para el desglose de costes).
  const borradoresCols = useMemo<ColDef<BorradorFactura>[]>(
    () => [
      { field: "trip_id", headerName: "ID Viaje", width: 130, pinned: "left" },
      { field: "fecha", headerName: "Fecha", width: 110 },
      { field: "cliente", headerName: "Cliente", flex: 1, minWidth: 180 },
      {
        headerName: "Ruta",
        flex: 1,
        minWidth: 150,
        valueGetter: (p) => `${p.data?.origen ?? ""} → ${p.data?.destino ?? ""}`,
      },
      { field: "base", headerName: "Base Imponible", width: 130, type: "rightAligned", valueFormatter: (p) => eur(Number(p.value) || 0) },
      { field: "total", headerName: "Total", width: 120, type: "rightAligned", valueFormatter: (p) => eur(Number(p.value) || 0) },
      { field: "costes", headerName: "Costes Reales", width: 130, type: "rightAligned", valueFormatter: (p) => eur(Number(p.value) || 0) },
      {
        headerName: "Margen (%)",
        width: 120,
        cellRenderer: (p: { data: BorradorFactura }) => {
          const pct = margenPct(p.data);
          const low = pct < 12;
          return (
            <span
              className={
                low
                  ? "inline-flex rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-bold text-red-700"
                  : "inline-flex rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-bold text-emerald-700"
              }
            >
              {pct.toFixed(1)}%
            </span>
          );
        },
      },
    ],
    [],
  );

  const liquidacionesCols = useMemo<ColDef<Liquidacion>[]>(
    () => [
      { field: "conductor", headerName: "Conductor", flex: 1, minWidth: 150 },
      { field: "viaje_id", headerName: "ID Viaje", width: 130 },
      { field: "fecha", headerName: "Fecha", width: 110 },
      { field: "km_total", headerName: "Km", width: 100, type: "rightAligned", valueFormatter: (p) => `${Number(p.value) || 0} km` },
      { field: "importe", headerName: "Importe", width: 120, type: "rightAligned", valueFormatter: (p) => eur(Number(p.value) || 0) },
      {
        field: "estado",
        headerName: "Estado",
        width: 110,
        cellRenderer: (p: { value: string }) => (
          <span className="inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-700">{p.value}</span>
        ),
      },
    ],
    [],
  );

  const clientesCols = useMemo<ColDef<Cliente>[]>(
    () => [
      { field: "nombre", headerName: "Cliente", flex: 1, minWidth: 180, editable: true, cellClass: editableCell },
      { field: "cif", headerName: "CIF", width: 120, editable: true, cellClass: editableCell },
      { field: "poblacion", headerName: "Población", width: 150, editable: true, cellClass: editableCell },
      { field: "direccion", headerName: "Dirección", width: 180, editable: true, cellClass: editableCell },
      { field: "cp", headerName: "CP", width: 90, editable: true, cellClass: editableCell },
      { field: "telefono", headerName: "Teléfono", width: 130, editable: true, cellClass: editableCell },
      { field: "email", headerName: "Email", flex: 1, minWidth: 170, editable: true, cellClass: editableCell },
      { field: "cuenta_contable_defecto", headerName: "Cta. contable", width: 120, editable: true, cellClass: editableCell },
      {
        headerName: "Palés",
        width: 70,
        sortable: false,
        filter: false,
        cellRenderer: (p: { data: Cliente }) => (
          <button
            onClick={() => verPales(p.data)}
            className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-slate-500 hover:bg-slate-100"
            title="Cuenta de palés"
          >
            <Package size={14} />
          </button>
        ),
      },
      {
        headerName: "",
        width: 60,
        sortable: false,
        filter: false,
        cellRenderer: (p: { data: Cliente }) => (
          <button onClick={() => eliminar("cliente", p.data.id)} className="rounded p-1 text-red-500 hover:bg-red-50" title="Eliminar">
            <Trash2 size={15} />
          </button>
        ),
      },
    ],
    [],
  );

  const proveedoresCols = useMemo<ColDef<Proveedor>[]>(
    () => [
      { field: "nombre", headerName: "Proveedor", flex: 1, minWidth: 180, editable: true, cellClass: editableCell },
      { field: "cif", headerName: "CIF", width: 120, editable: true, cellClass: editableCell },
      { field: "poblacion", headerName: "Población", width: 150, editable: true, cellClass: editableCell },
      { field: "direccion", headerName: "Dirección", width: 180, editable: true, cellClass: editableCell },
      { field: "cp", headerName: "CP", width: 90, editable: true, cellClass: editableCell },
      { field: "telefono", headerName: "Teléfono", width: 130, editable: true, cellClass: editableCell },
      { field: "email", headerName: "Email", flex: 1, minWidth: 170, editable: true, cellClass: editableCell },
      { field: "cuenta_contable_defecto", headerName: "Cta. contable", width: 120, editable: true, cellClass: editableCell },
      {
        headerName: "",
        width: 60,
        sortable: false,
        filter: false,
        cellRenderer: (p: { data: Proveedor }) => (
          <button onClick={() => eliminar("proveedor", p.data.id)} className="rounded p-1 text-red-500 hover:bg-red-50" title="Eliminar">
            <Trash2 size={15} />
          </button>
        ),
      },
    ],
    [],
  );

  const tarifasCols = useMemo<ColDef<Tarifa>[]>(
    () => [
      { field: "nombre", headerName: "Tarifa", flex: 1, minWidth: 180, editable: true, cellClass: editableCell },
      {
        field: "tipo",
        headerName: "Tipo",
        width: 120,
        editable: true,
        cellClass: editableCell,
        cellEditor: "agSelectCellEditor",
        cellEditorParams: { values: ["km", "viaje", "kilos"] },
        valueFormatter: (p) => ({ km: "Por km", viaje: "Por viaje", kilos: "Por kilos" }[p.value as string] ?? p.value ?? ""),
      },
      { field: "precio", headerName: "Precio", width: 120, editable: true, cellClass: editableCell, type: "rightAligned", valueFormatter: (p) => eur(Number(p.value) || 0) },
      { field: "cliente_nombre", headerName: "Cliente", width: 170, valueFormatter: (p) => (p.value ? p.value : "— General") },
      { field: "activo", headerName: "Activa", width: 90, editable: true, cellClass: editableCell, cellRenderer: "agCheckboxCellRenderer", cellEditor: "agCheckboxCellEditor" },
      {
        headerName: "",
        width: 60,
        sortable: false,
        filter: false,
        cellRenderer: (p: { data: Tarifa }) => (
          <button onClick={() => eliminarTarifa(p.data.id)} className="rounded p-1 text-red-500 hover:bg-red-50" title="Eliminar">
            <Trash2 size={15} />
          </button>
        ),
      },
    ],
    [],
  );

  const asientosCols = useMemo<ColDef<Asiento>[]>(
    () => [
      { field: "numero", headerName: "Nº", width: 70, type: "rightAligned" },
      { field: "fecha", headerName: "Fecha", width: 110 },
      { field: "concepto", headerName: "Concepto", flex: 1, minWidth: 240 },
      { field: "origen", headerName: "Origen", width: 140 },
      { field: "debe", headerName: "Debe", width: 120, type: "rightAligned", valueFormatter: (p) => eur(Number(p.value) || 0) },
      { field: "haber", headerName: "Haber", width: 120, type: "rightAligned", valueFormatter: (p) => eur(Number(p.value) || 0) },
    ],
    [],
  );

  const pygCols = useMemo<ColDef<PygRow>[]>(
    () => [
      {
        field: "seccion",
        headerName: "Sección",
        width: 130,
        cellRenderer: (p: { value: string }) => (
          <span
            className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-bold ${
              p.value === "Ingresos"
                ? "bg-emerald-100 text-emerald-700"
                : p.value === "Gastos"
                  ? "bg-rose-100 text-rose-700"
                  : "bg-slate-800 text-white"
            }`}
          >
            {p.value}
          </span>
        ),
      },
      { field: "cuenta", headerName: "Cuenta", width: 100 },
      { field: "nombre", headerName: "Concepto", flex: 1, minWidth: 220 },
      {
        field: "importe",
        headerName: "Importe",
        width: 140,
        type: "rightAligned",
        cellRenderer: (p: { value: number; data: PygRow }) => {
          const isResultado = p.data?.seccion === "Resultado";
          const color = isResultado
            ? Number(p.value) >= 0
              ? "text-emerald-600"
              : "text-red-600"
            : "text-slate-800";
          return (
            <span className={`tabular-nums font-medium ${color} ${isResultado ? "font-bold" : ""}`}>
              {eur(Number(p.value) || 0)}
            </span>
          );
        },
      },
    ],
    [],
  );

  const balanceCols = useMemo<ColDef<BalanceRow>[]>(
    () => [
      {
        field: "grupo",
        headerName: "Grupo",
        width: 150,
        cellRenderer: (p: { value: string }) => (
          <span
            className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-bold ${
              p.value === "Activo"
                ? "bg-blue-100 text-blue-700"
                : p.value === "Pasivo"
                  ? "bg-amber-100 text-amber-700"
                  : p.value === "Patrimonio Neto"
                    ? "bg-violet-100 text-violet-700"
                    : "bg-slate-800 text-white"
            }`}
          >
            {p.value}
          </span>
        ),
      },
      { field: "cuenta", headerName: "Cuenta", width: 100 },
      { field: "nombre", headerName: "Concepto", flex: 1, minWidth: 220 },
      {
        field: "importe",
        headerName: "Importe",
        width: 160,
        type: "rightAligned",
        cellRenderer: (p: { value: number; data: BalanceRow }) => {
          const total = p.data?.grupo === "Total";
          return (
            <span className={`tabular-nums font-medium ${total ? "font-bold text-slate-900" : "text-slate-800"}`}>
              {eur(Number(p.value) || 0)}
            </span>
          );
        },
      },
    ],
    [],
  );

  const reconciliacionCols = useMemo<ColDef<ReconciliacionRow>[]>(
    () => [
      { field: "vehiculo", headerName: "Vehículo", flex: 1, minWidth: 140 },
      {
        field: "odo_inicio",
        headerName: "Odómetro inicio",
        width: 130,
        type: "rightAligned",
        valueFormatter: (p) => (p.value != null ? Number(p.value).toLocaleString("es-ES") : "—"),
      },
      {
        field: "odo_fin",
        headerName: "Odómetro fin",
        width: 130,
        type: "rightAligned",
        valueFormatter: (p) => (p.value != null ? Number(p.value).toLocaleString("es-ES") : "—"),
      },
      { field: "km_odometro", headerName: "Km odómetro", width: 120, type: "rightAligned", valueFormatter: (p) => `${Number(p.value).toLocaleString("es-ES")} km` },
      { field: "km_viajes", headerName: "Km viajes", width: 110, type: "rightAligned", valueFormatter: (p) => `${Number(p.value).toLocaleString("es-ES")} km` },
      {
        field: "km_vacio",
        headerName: "Km vacío",
        width: 110,
        type: "rightAligned",
        valueFormatter: (p) => `${Number(p.value).toLocaleString("es-ES")} km`,
        cellStyle: (p) => (Number(p.value) < 0 ? { color: "#dc2626" } : null),
      },
      {
        field: "pct_vacio",
        headerName: "% vacío",
        width: 90,
        type: "rightAligned",
        valueFormatter: (p) => `${Number(p.value).toFixed(1)} %`,
        cellStyle: (p) =>
          Number(p.value) < 0
            ? { color: "#dc2626" }
            : Number(p.value) > 20
              ? { color: "#d97706" }
              : { color: "#16a34a" },
      },
    ],
    [],
  );

  // Auditoría: solo se carga para administradores.
  useEffect(() => {
    const r = getRol();
    setRol(r);
    if (r !== "admin" && r !== "superadmin") return;
    (async () => {
      try {
        const data = await api<any>(`${REST_AUDITORIA}?limite=500`);
        setAuditoria(data.auditoria ?? []);
      } catch (err) {
        console.error("Error cargando auditoría:", err);
      }
    })();
  }, []);

  const auditoriaCols = useMemo<ColDef<AuditoriaRow>[]>(
    () => [
      {
        field: "ts",
        headerName: "Fecha",
        width: 180,
        valueFormatter: (p) => (p.value ? String(p.value).replace("T", " ").slice(0, 19) : "—"),
      },
      { field: "tabla", headerName: "Tabla", width: 110 },
      { field: "accion", headerName: "Acción", width: 110 },
      { field: "usuario", headerName: "Usuario", width: 140 },
      { field: "registro_id", headerName: "Registro", width: 90 },
      {
        field: "despues",
        headerName: "Detalle",
        flex: 1,
        minWidth: 260,
        valueFormatter: (p) => (p.value ? String(p.value).slice(0, 140) : "—"),
      },
    ],
    [],
  );

  // Parámetros del panel master/detail para el desglose de costes del borrador
  // (eliminado: masterDetail es de AG Grid Enterprise; el desglose se muestra en el drawer).
  const defaultColDef = useMemo<ColDef>(() => ({ sortable: true, resizable: true, filter: true }), []);
  const revertGuard = useRef(false);
  const { resetColumnState: resetClientes, exportToCsv: exportClientes, ...clientesGrid } = useAgGridState("tms_clientes_grid");
  const { resetColumnState: resetProveedores, exportToCsv: exportProveedores, ...proveedoresGrid } = useAgGridState("tms_proveedores_grid");
  const { resetColumnState: resetTarifas, exportToCsv: exportTarifas, ...tarifasGrid } = useAgGridState("tms_tarifas_grid");

  // Handler genérico de edición inline para clientes/proveedores (PATCH + revert en error).
  const guardarCelda =
    (base: string) =>
    async (event: CellValueChangedEvent<Cliente | Proveedor>) => {
      const { colDef, data, newValue, oldValue } = event;
      const field = colDef.field;
      if (!field || !data || revertGuard.current) return;
      try {
        await api(`${base}/${data.id}`, {
          method: "PATCH",
          body: JSON.stringify({ [field]: newValue }),
        });
      } catch {
        revertGuard.current = true;
        event.node.setDataValue(field, oldValue);
        revertGuard.current = false;
        setBanner({
          tipo: "error",
          texto: `No se pudo guardar «${field}». Cambio revertido.`,
        });
      }
    };

  async function emitirFactura() {
    if (selected) {
      setEmitiendo(true);
      try {
        await api(EMITIR_BORRADOR(selected.id), { method: "POST" });
        setBorradores((prev) => prev.filter((b) => b.id !== selected.id));
        setSelected(null);
      } catch (err) {
        console.error("Error emitiendo factura:", err);
      } finally {
        setEmitiendo(false);
      }
    }
  }

  async function eliminar(tipo: "cliente" | "proveedor", id: number) {
    try {
      const url = tipo === "cliente" ? `${REST_CLIENTES}/${id}` : `${REST_PROVEEDORES}/${id}`;
      await api(url, { method: "DELETE" });
      setBanner({ tipo: "ok", texto: `${tipo === "cliente" ? "Cliente" : "Proveedor"} eliminado.` });
      if (tipo === "cliente") setClientes((prev) => prev.filter((c) => c.id !== id));
      else setProveedores((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      console.error("Error eliminando:", err);
      const detail = err instanceof ApiError ? (err.detail as { detail?: { error?: string } } | null)?.detail?.error : undefined;
      setBanner({
        tipo: "error",
        texto: detail || "No se pudo eliminar.",
      });
    }
  }

  async function eliminarTarifa(id: number) {
    try {
      await api(`${REST_TARIFAS}/${id}`, { method: "DELETE" });
      setBanner({ tipo: "ok", texto: "Tarifa eliminada." });
      setTarifas((prev) => prev.filter((t) => t.id !== id));
    } catch (err) {
      console.error("Error eliminando tarifa:", err);
      setBanner({ tipo: "error", texto: "No se pudo eliminar la tarifa." });
    }
  }

  async function guardarTarifaCelda(event: CellValueChangedEvent) {
    const { data } = event;
    if (!data || revertGuard.current) return;
    try {
      await api(`${REST_TARIFAS}/${data.id}`, {
        method: "PUT",
        body: JSON.stringify({
          nombre: data.nombre,
          tipo: data.tipo,
          precio: Number(data.precio) || 0,
          cliente_id: data.cliente_id ?? null,
          activo: data.activo !== false,
        }),
      });
    } catch {
      revertGuard.current = true;
      event.node.setDataValue(event.colDef.field!, event.oldValue);
      revertGuard.current = false;
      setBanner({ tipo: "error", texto: "No se pudo guardar la tarifa. Cambio revertido." });
    }
  }

  async function darAltaTarifa() {
    if (!tForm.nombre.trim()) return;
    setGuardandoN(true);
    try {
      await api(REST_TARIFAS, {
        method: "POST",
        body: JSON.stringify({
          nombre: tForm.nombre.trim(),
          tipo: tForm.tipo,
          precio: Number(tForm.precio) || 0,
          cliente_id: tForm.cliente_id ? Number(tForm.cliente_id) : null,
          activo: true,
        }),
      });
      setBanner({ tipo: "ok", texto: "Tarifa creada." });
      setAltaTarifa(false);
      setTForm({ nombre: "", tipo: "viaje", precio: "", cliente_id: "" });
      const d = await api<any>(REST_TARIFAS);
      setTarifas(d.tarifas ?? []);
    } catch (err) {
      console.error("Error creando tarifa:", err);
      if (err instanceof ApiError) {
        const d = err.detail as { detail?: { error?: string } } | null;
        setBanner({ tipo: "error", texto: d?.detail?.error || `Error ${err.status}` });
      } else {
        setBanner({ tipo: "error", texto: "Error de red." });
      }
    } finally {
      setGuardandoN(false);
    }
  }

  async function darAlta() {
    if (!altaTipo || !nForm.nombre.trim()) return;
    setGuardandoN(true);
    const url = altaTipo === "cliente" ? REST_CLIENTES : REST_PROVEEDORES;
    try {
      await api(url, {
        method: "POST",
        body: JSON.stringify({
          nombre: nForm.nombre.trim(),
          cif: nForm.cif.trim(),
          direccion: nForm.direccion.trim(),
          poblacion: nForm.poblacion.trim(),
          cp: nForm.cp.trim(),
          telefono: nForm.telefono.trim(),
          email: nForm.email.trim(),
        }),
      });
      setBanner({ tipo: "ok", texto: `${altaTipo === "cliente" ? "Cliente" : "Proveedor"} dado de alta.` });
      setAltaTipo(null);
      setNForm({ nombre: "", cif: "", direccion: "", poblacion: "", cp: "", telefono: "", email: "" });
      const d = await api<any>(url);
      if (altaTipo === "cliente") setClientes(d.clientes ?? []);
      else setProveedores(d.proveedores ?? []);
    } catch (err) {
      console.error("Error dando de alta:", err);
      if (err instanceof ApiError) {
        const d = err.detail as { detail?: { error?: string } } | null;
        setBanner({ tipo: "error", texto: d?.detail?.error || `Error ${err.status}` });
      } else {
        setBanner({ tipo: "error", texto: "Error de red." });
      }
    } finally {
      setGuardandoN(false);
    }
  }

  return (
    <div className="flex h-full w-full flex-col p-3">
      <div className="mb-2 flex shrink-0 items-center gap-2">
        <div className="inline-flex rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
          <button
            onClick={() => setTab("borradores")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "borradores" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}
          >
            <FileText size={16} /> Borradores
          </button>
          <button
            onClick={() => setTab("liquidaciones")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "liquidaciones" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}
          >
            <HandCoins size={16} /> Liquidaciones
          </button>
          <button
            onClick={() => setTab("clientes")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "clientes" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}
          >
            <Users size={16} /> Clientes
          </button>
          <button
            onClick={() => setTab("proveedores")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "proveedores" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}
          >
            <Truck size={16} /> Proveedores
          </button>
          <button
            onClick={() => setTab("tarifas")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "tarifas" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}
          >
            <Tags size={16} /> Tarifas
          </button>
          <button
            onClick={() => setTab("diario")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "diario" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}
          >
            <BookOpen size={16} /> Libro Diario
          </button>
          <button
            onClick={() => setTab("informes")}
            className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "informes" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}
          >
            <TrendingUp size={16} /> Informes
          </button>
          {rol === "admin" && (
            <button
              onClick={() => setTab("auditoria")}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${tab === "auditoria" ? "bg-blue-600 text-white shadow" : "text-slate-600 hover:bg-slate-100"}`}
            >
              <ShieldCheck size={16} /> Auditoría
            </button>
          )}
        </div>
        {(tab === "clientes" || tab === "proveedores" || tab === "tarifas") && (
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={() => (tab === "clientes" ? resetClientes() : tab === "proveedores" ? resetProveedores() : resetTarifas())}
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
            >
              <RotateCcw size={16} /> Restaurar vista
            </button>
            <button
              onClick={() => (tab === "clientes" ? exportClientes("clientes.csv") : tab === "proveedores" ? exportProveedores("proveedores.csv") : exportTarifas("tarifas.csv"))}
              className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700"
            >
              <Download size={16} /> Exportar CSV
            </button>
            {tab === "tarifas" ? (
              <button
                onClick={() => setAltaTarifa(true)}
                className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white shadow hover:bg-blue-700"
              >
                <Plus size={16} /> Añadir Tarifa
              </button>
            ) : (
              <button
                onClick={() => {
                  setAltaTipo(tab === "clientes" ? "cliente" : "proveedor");
                  setNForm({ nombre: "", cif: "", direccion: "", poblacion: "", cp: "", telefono: "", email: "" });
                }}
                className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white shadow hover:bg-blue-700"
              >
                <Plus size={16} /> {tab === "clientes" ? "Añadir Cliente" : "Añadir Proveedor"}
              </button>
            )}
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white">
        {tab === "borradores" && (
          <AgGridReact
            theme={gridTheme}
            columnDefs={borradoresCols}
            defaultColDef={defaultColDef}
            rowData={borradores}
            rowHeight={GRID_ROW_HEIGHT}
            headerHeight={GRID_HEADER_HEIGHT}
            rowSelection="single"
            onRowClicked={(p) => setSelected(p.data ?? null)}
          />
        )}
        {tab === "liquidaciones" && (
          <AgGridReact
            theme={gridTheme}
            columnDefs={liquidacionesCols}
            defaultColDef={defaultColDef}
            rowData={liquidaciones}
            rowHeight={GRID_ROW_HEIGHT}
            headerHeight={GRID_HEADER_HEIGHT}
            rowSelection="single"
          />
        )}
        {tab === "clientes" && (
          <AgGridReact
            theme={gridTheme}
            columnDefs={clientesCols}
            defaultColDef={defaultColDef}
            rowData={clientes}
            rowHeight={GRID_ROW_HEIGHT}
            headerHeight={GRID_HEADER_HEIGHT}
            rowSelection="single"
            singleClickEdit
            stopEditingWhenCellsLoseFocus
            onCellValueChanged={guardarCelda(REST_CLIENTES)}

            {...clientesGrid}
          />
        )}
        {tab === "proveedores" && (
          <AgGridReact
            theme={gridTheme}
            columnDefs={proveedoresCols}
            defaultColDef={defaultColDef}
            rowData={proveedores}
            rowHeight={GRID_ROW_HEIGHT}
            headerHeight={GRID_HEADER_HEIGHT}
            rowSelection="single"
            singleClickEdit
            stopEditingWhenCellsLoseFocus
            onCellValueChanged={guardarCelda(REST_PROVEEDORES)}

            {...proveedoresGrid}
          />
        )}
        {tab === "tarifas" && (
          <AgGridReact
            theme={gridTheme}
            columnDefs={tarifasCols}
            defaultColDef={defaultColDef}
            rowData={tarifas}
            rowHeight={GRID_ROW_HEIGHT}
            headerHeight={GRID_HEADER_HEIGHT}
            rowSelection="single"
            singleClickEdit
            stopEditingWhenCellsLoseFocus
            onCellValueChanged={guardarTarifaCelda}

            {...tarifasGrid}
          />
        )}
        {tab === "diario" && (
          <AgGridReact
            theme={gridTheme}
            columnDefs={asientosCols}
            defaultColDef={defaultColDef}
            rowData={asientos}
            rowHeight={GRID_ROW_HEIGHT}
            headerHeight={GRID_HEADER_HEIGHT}
            rowSelection="single"
          />
        )}
        {tab === "informes" && (
          <div className="flex h-full w-full flex-col">
            <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-slate-100 px-3 py-2">
              <div className="inline-flex rounded-lg border border-slate-200 bg-white p-0.5 shadow-sm">
                <button
                  onClick={() => setInformesSub("pyg")}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${informesSub === "pyg" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
                >
                  PyG
                </button>
                <button
                  onClick={() => setInformesSub("balance")}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${informesSub === "balance" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
                >
                  Balance de Situación
                </button>
                <button
                  onClick={() => setInformesSub("reconciliacion")}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${informesSub === "reconciliacion" ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
                >
                  Reconciliación KM
                </button>
              </div>
              <label className="flex items-center gap-1.5 text-xs text-slate-500">
                Desde
                <input
                  type="date"
                  value={desde}
                  onChange={(e) => setDesde(e.target.value)}
                  className="rounded-md border border-slate-200 px-2 py-1 text-xs"
                />
              </label>
              <label className="flex items-center gap-1.5 text-xs text-slate-500">
                Hasta
                <input
                  type="date"
                  value={hasta}
                  onChange={(e) => setHasta(e.target.value)}
                  className="rounded-md border border-slate-200 px-2 py-1 text-xs"
                />
              </label>
            </div>
            <div className="min-h-0 flex-1">
              {informesSub === "pyg" ? (
                <AgGridReact
                  theme={gridTheme}
                  columnDefs={pygCols}
                  defaultColDef={defaultColDef}
                  rowData={pyg.rows}
                  rowHeight={GRID_ROW_HEIGHT}
                  headerHeight={GRID_HEADER_HEIGHT}
                  pinnedBottomRowData={[
                    { seccion: "Resultado", cuenta: "", nombre: "Resultado del Ejercicio", importe: pyg.resultado },
                  ]}
                />
              ) : informesSub === "balance" ? (
                <AgGridReact
                  theme={gridTheme}
                  columnDefs={balanceCols}
                  defaultColDef={defaultColDef}
                  rowData={balance.rows}
                  rowHeight={GRID_ROW_HEIGHT}
                  headerHeight={GRID_HEADER_HEIGHT}
                  pinnedBottomRowData={[
                    { grupo: "Total", cuenta: "", nombre: "Total Activo", importe: balance.totalActivo },
                    { grupo: "Total", cuenta: "", nombre: "Total Pasivo + Patrimonio Neto", importe: balance.totalPasivoPatrimonio },
                  ]}
                />
              ) : (
                <AgGridReact
                  theme={gridTheme}
                  columnDefs={reconciliacionCols}
                  defaultColDef={defaultColDef}
                  rowData={reconciliacion}
                  rowHeight={GRID_ROW_HEIGHT}
                  headerHeight={GRID_HEADER_HEIGHT}
                  pinnedBottomRowData={[
                    {
                      vehiculo: "TOTAL",
                      odo_inicio: null,
                      odo_fin: null,
                      km_odometro: reconcTotal.km_odometro,
                      km_viajes: reconcTotal.km_viajes,
                      km_vacio: reconcTotal.km_vacio,
                      pct_vacio:
                        reconcTotal.km_odometro > 0
                          ? Number(((reconcTotal.km_vacio / reconcTotal.km_odometro) * 100).toFixed(1))
                          : 0,
                    },
                  ]}
                />
              )}
            </div>
          </div>
        )}
        {tab === "auditoria" && (
          <AgGridReact
            theme={gridTheme}
            columnDefs={auditoriaCols}
            defaultColDef={defaultColDef}
            rowData={auditoria}
            rowHeight={GRID_ROW_HEIGHT}
            headerHeight={GRID_HEADER_HEIGHT}

          />
        )}
      </div>

      {selected && (
        <div className="fixed inset-y-0 right-0 z-40 flex w-1/4 flex-col border-l border-slate-200 bg-white shadow-2xl">
          <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
            <h3 className="text-sm font-semibold text-slate-800">Resumen financiero</h3>
            <button onClick={() => setSelected(null)} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
              <X size={18} />
            </button>
          </header>
          <div className="flex-1 space-y-3 overflow-y-auto p-4 text-sm">
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Viaje</div>
              <div className="font-semibold text-slate-800">{selected.trip_id}</div>
            </div>
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Cliente</div>
              <div className="font-medium text-slate-700">{selected.cliente}</div>
            </div>
            <div>
              <div className="text-xs font-medium uppercase tracking-wide text-slate-400">Ruta</div>
              <div className="font-medium text-slate-700">
                {selected.origen} → {selected.destino}
              </div>
            </div>
            <div className="border-t border-slate-100 pt-3">
              <dl className="space-y-1.5">
                <div className="flex justify-between">
                  <dt className="text-slate-500">Base imponible</dt>
                  <dd className="tabular-nums font-medium text-slate-800">{eur(selected.base)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">IVA (21%)</dt>
                  <dd className="tabular-nums text-slate-600">{eur(selected.total - selected.base)}</dd>
                </div>
                <div className="flex justify-between border-t border-slate-100 pt-1.5">
                  <dt className="font-semibold text-slate-800">Total</dt>
                  <dd className="tabular-nums font-bold text-slate-900">{eur(selected.total)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-slate-500">Costes reales</dt>
                  <dd className="tabular-nums text-slate-600">−{eur(selected.costes)}</dd>
                </div>
                <div className="flex justify-between border-t border-slate-100 pt-1.5">
                  <dt className="font-semibold text-slate-800">Margen</dt>
                  <dd className={`tabular-nums font-bold ${margenPct(selected) < 12 ? "text-red-600" : "text-emerald-600"}`}>
                    {eur(selected.margen)} ({margenPct(selected).toFixed(1)}%)
                  </dd>
                </div>
              </dl>
            </div>
            {(selected.desglose?.length ?? 0) > 0 && (
              <div className="border-t border-slate-100 pt-3">
                <div className="mb-1.5 text-xs font-medium uppercase tracking-wide text-slate-400">Desglose de costes</div>
                <ul className="space-y-1">
                  {selected.desglose!.map((c, i) => (
                    <li key={i} className="flex justify-between">
                      <span className="text-slate-600">{c.concepto}</span>
                      <span className="tabular-nums font-medium text-slate-800">{eur(c.importe)}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          <footer className="border-t border-slate-200 p-4">
            <button
              onClick={emitirFactura}
              disabled={emitiendo}
              className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 py-3 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:opacity-60"
            >
              <Check size={16} />
              {emitiendo ? "Emitiendo…" : "Aprobar y Emitir Factura"}
            </button>
          </footer>
        </div>
      )}

      {altaTipo && (
        <>
          <div className="fixed inset-0 z-30 bg-slate-900/40 backdrop-blur-sm" onClick={() => !guardandoN && setAltaTipo(null)} />
          <div className="fixed inset-y-0 right-0 z-40 flex w-1/4 flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">{altaTipo === "cliente" ? "Alta de cliente" : "Alta de proveedor"}</h3>
              <button onClick={() => setAltaTipo(null)} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </header>
            <div className="flex-1 space-y-3 overflow-y-auto p-4 text-sm">
              <label className="block">
                <span className="text-xs text-slate-500">Nombre / Razón social *</span>
                <input
                  value={nForm.nombre}
                  onChange={(e) => setN("nombre", e.target.value)}
                  className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
                />
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">CIF / NIF</span>
                <input
                  value={nForm.cif}
                  onChange={(e) => setN("cif", e.target.value)}
                  className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
                />
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">Dirección</span>
                <input
                  value={nForm.direccion}
                  onChange={(e) => setN("direccion", e.target.value)}
                  className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
                />
              </label>
              <div className="flex gap-2">
                <label className="block w-2/3">
                  <span className="text-xs text-slate-500">Población</span>
                  <input
                    value={nForm.poblacion}
                    onChange={(e) => setN("poblacion", e.target.value)}
                    className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
                  />
                </label>
                <label className="block w-1/3">
                  <span className="text-xs text-slate-500">CP</span>
                  <input
                    value={nForm.cp}
                    onChange={(e) => setN("cp", e.target.value)}
                    className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
                  />
                </label>
              </div>
              <label className="block">
                <span className="text-xs text-slate-500">Teléfono</span>
                <input
                  value={nForm.telefono}
                  onChange={(e) => setN("telefono", e.target.value)}
                  className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
                />
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">Email</span>
                <input
                  type="email"
                  value={nForm.email}
                  onChange={(e) => setN("email", e.target.value)}
                  className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
                />
              </label>
            </div>
            <footer className="border-t border-slate-200 p-4">
              <button
                onClick={darAlta}
                disabled={guardandoN || !nForm.nombre.trim()}
                className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
              >
                {guardandoN ? "Guardando…" : altaTipo === "cliente" ? "Añadir cliente" : "Añadir proveedor"}
              </button>
            </footer>
          </div>
        </>
      )}

      {altaTarifa && (
        <>
          <div className="fixed inset-0 z-30 bg-slate-900/40 backdrop-blur-sm" onClick={() => !guardandoN && setAltaTarifa(false)} />
          <div className="fixed inset-y-0 right-0 z-40 flex w-1/4 flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">Nueva tarifa</h3>
              <button onClick={() => setAltaTarifa(false)} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </header>
            <div className="flex-1 space-y-3 overflow-y-auto p-4 text-sm">
              <label className="block">
                <span className="text-xs text-slate-500">Nombre *</span>
                <input
                  value={tForm.nombre}
                  onChange={(e) => setTForm((p) => ({ ...p, nombre: e.target.value }))}
                  className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
                />
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">Tipo de valoración</span>
                <select
                  value={tForm.tipo}
                  onChange={(e) => setTForm((p) => ({ ...p, tipo: e.target.value }))}
                  className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
                >
                  <option value="km">Por km</option>
                  <option value="viaje">Por viaje</option>
                  <option value="kilos">Por kilos</option>
                </select>
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">Precio (€)</span>
                <input
                  type="number"
                  step="0.001"
                  value={tForm.precio}
                  onChange={(e) => setTForm((p) => ({ ...p, precio: e.target.value }))}
                  className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
                />
              </label>
              <label className="block">
                <span className="text-xs text-slate-500">Cliente (opcional)</span>
                <select
                  value={tForm.cliente_id}
                  onChange={(e) => setTForm((p) => ({ ...p, cliente_id: e.target.value }))}
                  className="mt-0.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm"
                >
                  <option value="">— General (todos los clientes)</option>
                  {clientes.map((c) => (
                    <option key={c.id} value={c.id}>{c.nombre}</option>
                  ))}
                </select>
              </label>
            </div>
            <footer className="border-t border-slate-200 p-4">
              <button
                onClick={darAltaTarifa}
                disabled={guardandoN || !tForm.nombre.trim()}
                className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
              >
                {guardandoN ? "Guardando…" : "Añadir tarifa"}
              </button>
            </footer>
          </div>
        </>
      )}

      {palesCliente && (
        <>
          <div className="fixed inset-0 z-30 bg-slate-900/40 backdrop-blur-sm" onClick={() => setPalesCliente(null)} />
          <div className="fixed inset-y-0 right-0 z-40 flex w-1/3 flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">Cuenta de palés — {palesCliente.nombre}</h3>
              <button onClick={() => setPalesCliente(null)} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </header>
            <div className="flex-1 space-y-3 overflow-y-auto p-4 text-sm">
              <div className="flex items-baseline justify-between rounded-lg bg-slate-50 px-4 py-3">
                <span className="text-xs font-medium uppercase tracking-wide text-slate-400">Saldo actual</span>
                <span
                  className={`text-2xl font-bold tabular-nums ${(palesData?.saldo ?? 0) < 0 ? "text-red-600" : (palesData?.saldo ?? 0) > 0 ? "text-emerald-600" : "text-slate-700"}`}
                >
                  {(palesData?.saldo ?? 0) > 0 ? "+" : ""}
                  {palesData?.saldo ?? "—"}
                </span>
              </div>
              {palesData ? (
                palesData.movimientos.length === 0 ? (
                  <div className="text-center text-slate-400">Sin movimientos de palés registrados.</div>
                ) : (
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-slate-200 text-left text-slate-400">
                        <th className="py-1.5 font-medium">Fecha</th>
                        <th className="py-1.5 font-medium">Viaje</th>
                        <th className="py-1.5 text-right font-medium">Entreg.</th>
                        <th className="py-1.5 text-right font-medium">Recup.</th>
                        <th className="py-1.5 text-right font-medium">Balance</th>
                      </tr>
                    </thead>
                    <tbody>
                      {palesData.movimientos.map((m) => (
                        <tr key={m.id} className="border-b border-slate-100">
                          <td className="py-1.5 text-slate-500">{m.fecha || "—"}</td>
                          <td className="py-1.5 font-mono text-slate-600">{m.viaje_id || "—"}</td>
                          <td className="py-1.5 text-right tabular-nums text-emerald-600">+{m.entregados}</td>
                          <td className="py-1.5 text-right tabular-nums text-red-500">−{m.recuperados}</td>
                          <td className="py-1.5 text-right font-semibold tabular-nums text-slate-700">{m.balance}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )
              ) : (
                <div className="text-center text-slate-400">Cargando…</div>
              )}
            </div>
          </div>
        </>
      )}

      {banner && (
        <div
          className={`fixed right-4 top-4 z-50 rounded-lg px-4 py-3 text-sm font-medium text-white shadow-lg ${banner.tipo === "ok" ? "bg-emerald-600" : "bg-red-600"}`}
        >
          {banner.texto}
        </div>
      )}
    </div>
  );
}
