import { useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import type { ColDef, CellValueChangedEvent } from "ag-grid-community";
import { FileUp, X, Save, Loader2, Plus, Download, RotateCcw } from "lucide-react";
import { REST_GASTOS_VEHICULOS, REST_GASTOS_OCR, REST_VEHICULOS, REST_PROVEEDORES } from "../config";
import { getToken } from "../auth";
import { useAgGridState } from "../hooks/useAgGridState";
import { VisorDocumentos } from "./VisorDocumentos";

import { gridTheme, GRID_ROW_HEIGHT, GRID_HEADER_HEIGHT } from "../gridConfig";
ModuleRegistry.registerModules([AllCommunityModule]);

const eur = (v: number) => v.toLocaleString("es-ES", { style: "currency", currency: "EUR" });
const editableCell = "cursor-text hover:bg-slate-100";

const TIPOS = [
  { value: "combustible", label: "Combustible" },
  { value: "peajes", label: "Peajes" },
  { value: "neumaticos", label: "Neumáticos" },
  { value: "reparaciones", label: "Reparaciones" },
  { value: "seguros", label: "Seguros" },
  { value: "dietas", label: "Dietas" },
  { value: "otros", label: "Otros" },
];
const labelTipo = (t: string) => TIPOS.find((x) => x.value === t)?.label || t;

interface GastoVehiculo {
  id: number;
  vehiculo_id: string;
  proveedor_id: number | null;
  fecha: string;
  tipo: string;
  litros: number;
  base_imponible: number;
  iva: number;
  importe_total: number;
  factura_ref: string;
  cuenta_contable_gasto: string;
  estado_pago: string;
  creado: string;
  matricula: string;
  proveedor: string;
}

export function GastosDashboard() {
  const [gastos, setGastos] = useState<GastoVehiculo[]>([]);
  const [vehiculos, setVehiculos] = useState<any[]>([]);
  const [proveedores, setProveedores] = useState<any[]>([]);
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const [procesandoOcr, setProcesandoOcr] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [drawer, setDrawer] = useState<{ base64: string; nombre: string } | null>(null);
  const [drawerManual, setDrawerManual] = useState(false);

  const revertiendoRef = useRef(false);
  const { resetColumnState, exportToCsv, ...gridHandlers } = useAgGridState("tms_gastos_grid");

  // formulario del drawer
  const [fVehiculo, setFVehiculo] = useState("");
  const [fFecha, setFFecha] = useState("");
  const [fLitros, setFLitros] = useState("");
  const [fImporte, setFImporte] = useState("");
  const [fProveedor, setFProveedor] = useState("");
  const [fTipo, setFTipo] = useState("combustible");
  const [fRef, setFRef] = useState("");

  // formulario del drawer manual
  const [mVehiculo, setMVehiculo] = useState("");
  const [mProveedor, setMProveedor] = useState("");
  const [mTipo, setMTipo] = useState("combustible");
  const [mFecha, setMFecha] = useState("");
  const [mBase, setMBase] = useState("");
  const [mIva, setMIva] = useState("21");
  const [mLitros, setMLitros] = useState("");
  const [mRef, setMRef] = useState("");

  const mTotal = useMemo(() => {
    const base = Number(mBase) || 0;
    const iva = Number(mIva) || 0;
    return base * (1 + iva / 100);
  }, [mBase, mIva]);

  const fileRef = useRef<HTMLInputElement>(null);

  const headers = () => ({ Authorization: `Bearer ${getToken() ?? ""}` });

  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 4000);
    return () => clearTimeout(t);
  }, [banner]);

  async function cargar() {
    try {
      const [g, v, p] = await Promise.all([
        fetch(REST_GASTOS_VEHICULOS, { headers: headers() }),
        fetch(REST_VEHICULOS, { headers: headers() }),
        fetch(REST_PROVEEDORES, { headers: headers() }),
      ]);
      const gd = await g.json();
      const vd = await v.json();
      const pd = await p.json();
      setGastos(gd.gastos ?? []);
      setVehiculos(vd.vehiculos ?? []);
      setProveedores(pd.proveedores ?? []);
    } catch (e) {
      console.error("Error cargando gastos:", e);
    }
  }

  useEffect(() => {
    cargar();
  }, []);

  function abrirSelector() {
    fileRef.current?.click();
  }

  async function onArchivo(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // permite re-seleccionar el mismo archivo
    if (!file) return;
    setProcesandoOcr(true);
    try {
      const base64 = await new Promise<string>((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => resolve(String(fr.result).split(",")[1] || "");
        fr.onerror = () => reject(fr.error);
        fr.readAsDataURL(file);
      });
      const r = await fetch(REST_GASTOS_OCR, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: JSON.stringify({ archivo_base64: base64 }),
      });
      if (!r.ok) throw new Error(`OCR ${r.status}`);
      const d = await r.json();
      // auto-relleno del formulario con el borrador extraído por el OCR
      setDrawer({ base64: d.archivo_base64 || base64, nombre: file.name });
      const b = d.borrador ?? {};
      setFFecha(b.fecha || "");
      setFLitros(b.litros != null ? String(b.litros) : "");
      setFImporte(b.importe_total != null ? String(b.importe_total) : "");
      setFRef("");
      setFProveedor("");
      setFTipo("combustible");
      if (b.matricula) {
        const norm = (s: string) => s.toUpperCase().replace(/\s/g, "");
        const v = vehiculos.find((x) => norm(x.matricula || "") === norm(String(b.matricula)));
        setFVehiculo(v ? v.id : "");
      } else {
        setFVehiculo("");
      }
    } catch (err) {
      console.error("Error en OCR:", err);
      setBanner({ tipo: "error", texto: "No se pudo procesar la factura (OCR)." });
    } finally {
      setProcesandoOcr(false);
    }
  }

  async function contabilizar() {
    if (!drawer) return;
    if (!fVehiculo || !fFecha || !fImporte) {
      setBanner({ tipo: "error", texto: "Indica vehículo, fecha e importe." });
      return;
    }
    setGuardando(true);
    try {
      const r = await fetch(REST_GASTOS_VEHICULOS, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: JSON.stringify({
          vehiculo_id: fVehiculo,
          proveedor_id: fProveedor ? Number(fProveedor) : null,
          fecha: fFecha,
          tipo: fTipo,
          litros: Number(fLitros) || 0,
          importe_total: Number(fImporte) || 0,
          factura_ref: fRef,
          archivo_base64: drawer.base64,
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        setBanner({ tipo: "ok", texto: "Gasto contabilizado correctamente." });
        setDrawer(null);
        cargar();
      } else {
        setBanner({ tipo: "error", texto: d?.detail?.error || d?.error || `Error ${r.status}` });
      }
    } catch (err) {
      console.error("Error contabilizando gasto:", err);
      setBanner({ tipo: "error", texto: "Error de red al contabilizar." });
    } finally {
      setGuardando(false);
    }
  }

  function abrirManual() {
    setMVehiculo("");
    setMProveedor("");
    setMTipo("combustible");
    setMFecha("");
    setMBase("");
    setMIva("21");
    setMLitros("");
    setMRef("");
    setDrawerManual(true);
  }

  async function contabilizarManual() {
    if (!mVehiculo || !mFecha || !mBase) {
      setBanner({ tipo: "error", texto: "Indica vehículo, fecha y base imponible." });
      return;
    }
    setGuardando(true);
    try {
      const r = await fetch(REST_GASTOS_VEHICULOS, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: JSON.stringify({
          vehiculo_id: mVehiculo,
          proveedor_id: mProveedor ? Number(mProveedor) : null,
          fecha: mFecha,
          tipo: mTipo,
          litros: mTipo === "combustible" ? Number(mLitros) || 0 : 0,
          base_imponible: Number(mBase) || 0,
          iva: Number(mIva) || 0,
          importe_total: mTotal,
          factura_ref: mRef,
          estado_pago: "Pendiente",
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        setBanner({ tipo: "ok", texto: "Gasto creado y contabilizado." });
        setDrawerManual(false);
        cargar();
      } else {
        setBanner({ tipo: "error", texto: d?.detail?.error || d?.error || `Error ${r.status}` });
      }
    } catch (err) {
      console.error("Error creando gasto:", err);
      setBanner({ tipo: "error", texto: "Error de red al crear el gasto." });
    } finally {
      setGuardando(false);
    }
  }

  async function onCellValueChanged(event: CellValueChangedEvent<GastoVehiculo>) {
    const { colDef, data, newValue, oldValue } = event;
    const field = colDef.field;
    if (!field || !data || revertiendoRef.current) return;
    try {
      const r = await fetch(`${REST_GASTOS_VEHICULOS}/${data.id}`, {
        method: "PATCH",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: newValue }),
      });
      if (!r.ok) throw new Error(String(r.status));
    } catch {
      revertiendoRef.current = true;
      event.node.setDataValue(field, oldValue);
      revertiendoRef.current = false;
      setBanner({ tipo: "error", texto: `No se pudo guardar «${field}». Cambio revertido.` });
    }
  }

  const columnDefs = useMemo<ColDef<GastoVehiculo>[]>(
    () => [
      { field: "fecha", headerName: "Fecha", width: 110 },
      { field: "matricula", headerName: "Vehículo", width: 130 },
      { field: "tipo", headerName: "Tipo", width: 120, valueFormatter: (p) => labelTipo(String(p.value || "")) },
      { field: "litros", headerName: "Litros", width: 90, type: "rightAligned", valueFormatter: (p) => (p.value ? `${Number(p.value).toLocaleString("es-ES")} L` : "—") },
      { field: "base_imponible", headerName: "Base", width: 110, type: "rightAligned", valueFormatter: (p) => eur(Number(p.value) || 0) },
      { field: "iva", headerName: "IVA %", width: 70, type: "rightAligned", valueFormatter: (p) => (p.value != null ? `${Number(p.value)}%` : "—") },
      { field: "importe_total", headerName: "Total", width: 120, type: "rightAligned", valueFormatter: (p) => eur(Number(p.value) || 0) },
      { field: "proveedor", headerName: "Proveedor", flex: 1, minWidth: 140 },
      { field: "factura_ref", headerName: "Factura", width: 130, editable: true, cellClass: editableCell },
      {
        field: "estado_pago",
        headerName: "Estado",
        width: 110,
        editable: true,
        cellClass: editableCell,
        cellEditor: "agSelectCellEditor",
        cellEditorParams: { values: ["Pendiente", "Pagado"] },
        cellStyle: (p) =>
          p.value === "Pagado"
            ? { color: "#047857", fontWeight: 600 }
            : { color: "#b45309", fontWeight: 600 },
      },
      { field: "cuenta_contable_gasto", headerName: "Cta.", width: 70, type: "rightAligned" },
    ],
    []
  );

  return (
    <div className="flex h-full w-full flex-col p-3">
      <div className="mb-2 flex shrink-0 items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-700">Gastos operativos</h2>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={resetColumnState} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700">
            <RotateCcw size={16} /> Restaurar vista
          </button>
          <button onClick={() => exportToCsv("gastos.csv")} className="inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-700">
            <Download size={16} /> Exportar CSV
          </button>
          <button onClick={abrirManual} className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-2 text-sm font-semibold text-white shadow hover:bg-blue-700">
            <Plus size={16} /> Nuevo Gasto
          </button>
          <button onClick={abrirSelector} disabled={procesandoOcr} className="inline-flex items-center gap-1.5 rounded-lg bg-slate-800 px-3 py-2 text-sm font-semibold text-white shadow hover:bg-slate-900 disabled:opacity-60">
            {procesandoOcr ? <Loader2 size={16} className="animate-spin" /> : <FileUp size={16} />}
            Importar Factura
          </button>
        </div>
        <input ref={fileRef} type="file" accept="application/pdf" className="hidden" onChange={onArchivo} />
      </div>

      <div className="min-h-0 flex-1 overflow-hidden rounded-xl border border-slate-200 bg-white">
        <AgGridReact<GastoVehiculo>
          theme={gridTheme}
          columnDefs={columnDefs}
          defaultColDef={{ sortable: true, resizable: true, filter: true }}
          rowData={gastos}
          rowHeight={GRID_ROW_HEIGHT}
          headerHeight={GRID_HEADER_HEIGHT}
          singleClickEdit
          stopEditingWhenCellsLoseFocus
          onCellValueChanged={onCellValueChanged}
          {...gridHandlers}
        />
      </div>

      {drawer && (
        <>
          <div className="fixed inset-0 z-[2000] bg-slate-900/40 backdrop-blur-sm" onClick={() => !guardando && setDrawer(null)} />
          <div className="fixed inset-y-0 right-0 z-[2001] flex w-[62%] flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">Imputación de gasto (OCR)</h3>
              <button onClick={() => setDrawer(null)} disabled={guardando} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </header>

            {/* Split view 50/50: visor PDF + formulario de validación */}
            <div className="flex min-h-0 flex-1">
              <div className="min-h-0 w-1/2 border-r border-slate-200 p-3">
                <VisorDocumentos base64={drawer.base64} nombre={drawer.nombre} className="h-full" />
              </div>
              <div className="min-h-0 w-1/2 overflow-y-auto p-4">
                <div className="space-y-3">
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">Vehículo (tractora) *</span>
                    <select value={fVehiculo} onChange={(e) => setFVehiculo(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                      <option value="">Seleccionar…</option>
                      {vehiculos.map((v) => (
                        <option key={v.id} value={v.id}>{v.matricula} — {v.marca} {v.modelo}</option>
                      ))}
                    </select>
                  </label>
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">Fecha *</span>
                    <input type="date" value={fFecha} onChange={(e) => setFFecha(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                  </label>
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">Tipo *</span>
                    <select value={fTipo} onChange={(e) => setFTipo(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                      {TIPOS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                  </label>
                  <div className="grid grid-cols-2 gap-3">
                    <label className="block">
                      <span className="text-xs font-medium text-slate-500">Litros</span>
                      <input type="number" min="0" step="0.01" value={fLitros} onChange={(e) => setFLitros(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                    <label className="block">
                      <span className="text-xs font-medium text-slate-500">Importe total (€) *</span>
                      <input type="number" min="0" step="0.01" value={fImporte} onChange={(e) => setFImporte(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                    </label>
                  </div>
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">Proveedor</span>
                    <select value={fProveedor} onChange={(e) => setFProveedor(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                      <option value="">Sin proveedor</option>
                      {proveedores.map((p) => (
                        <option key={p.id} value={p.id}>{p.nombre}</option>
                      ))}
                    </select>
                  </label>
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">Referencia de factura</span>
                    <input value={fRef} onChange={(e) => setFRef(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" placeholder="Nº factura" />
                  </label>

                  <button
                    onClick={contabilizar}
                    disabled={guardando}
                    className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-800 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:opacity-60"
                  >
                    {guardando ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                    Contabilizar
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Drawer manual */}
      {drawerManual && (
        <>
          <div className="fixed inset-0 z-[2000] bg-slate-900/40 backdrop-blur-sm" onClick={() => !guardando && setDrawerManual(false)} />
          <div className="fixed inset-y-0 right-0 z-[2001] flex w-[34%] flex-col border-l border-slate-200 bg-white shadow-2xl">
            <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
              <h3 className="text-sm font-semibold text-slate-800">Nuevo gasto</h3>
              <button onClick={() => setDrawerManual(false)} disabled={guardando} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                <X size={18} />
              </button>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <div className="space-y-3">
                <label className="block">
                  <span className="text-xs font-medium text-slate-500">Vehículo (tractora) *</span>
                  <select value={mVehiculo} onChange={(e) => setMVehiculo(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                    <option value="">Seleccionar…</option>
                    {vehiculos.map((v) => (
                      <option key={v.id} value={v.id}>{v.matricula} — {v.marca} {v.modelo}</option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-slate-500">Proveedor</span>
                  <select value={mProveedor} onChange={(e) => setMProveedor(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                    <option value="">Sin proveedor</option>
                    {proveedores.map((p) => (
                      <option key={p.id} value={p.id}>{p.nombre}</option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-slate-500">Tipo de gasto *</span>
                  <select value={mTipo} onChange={(e) => setMTipo(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm">
                    {TIPOS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </label>
                <label className="block">
                  <span className="text-xs font-medium text-slate-500">Fecha *</span>
                  <input type="date" value={mFecha} onChange={(e) => setMFecha(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">Base imponible (€) *</span>
                    <input type="number" min="0" step="0.01" value={mBase} onChange={(e) => setMBase(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                  </label>
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">% IVA</span>
                    <input type="number" min="0" step="0.01" value={mIva} onChange={(e) => setMIva(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                  </label>
                </div>
                <div className="rounded-md bg-slate-50 px-3 py-2 text-sm">
                  <span className="text-slate-500">Total: </span>
                  <span className="font-semibold text-slate-800">{eur(mTotal)}</span>
                </div>
                {mTipo === "combustible" && (
                  <label className="block">
                    <span className="text-xs font-medium text-slate-500">Litros</span>
                    <input type="number" min="0" step="0.01" value={mLitros} onChange={(e) => setMLitros(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" />
                  </label>
                )}
                <label className="block">
                  <span className="text-xs font-medium text-slate-500">Referencia de factura</span>
                  <input value={mRef} onChange={(e) => setMRef(e.target.value)} className="mt-1 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm" placeholder="Nº factura" />
                </label>
                <button onClick={contabilizarManual} disabled={guardando} className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-slate-800 py-2 text-sm font-semibold text-white hover:bg-slate-900 disabled:opacity-60">
                  {guardando ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />} Guardar y contabilizar
                </button>
              </div>
            </div>
          </div>
        </>
      )}

      {banner && (
        <div className={`fixed bottom-4 right-4 z-[3000] rounded-lg px-4 py-2 text-sm font-semibold text-white shadow-lg ${banner.tipo === "ok" ? "bg-emerald-600" : "bg-red-600"}`}>
          {banner.texto}
        </div>
      )}
    </div>
  );
}
