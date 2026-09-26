import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileText, CheckCircle2, Send, Loader2, Eye, AlertTriangle } from "lucide-react";

import { api, ApiError } from "../api";
import { getToken } from "../auth";
import {
  REST_FACTURABLES,
  REST_FACTURAS,
  REST_FACTURA_AGRUPADA,
  REST_FACTURA_COBRAR,
  REST_FACTURA_PDF,
  REST_FACTURA_ENVIAR,
  REST_BORRADORES,
  EMITIR_BORRADOR,
  REST_CLIENTES,
} from "../config";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "./ui/dialog";

// ------------------------------------------------------------------ tipos
interface Documentacion { ok: boolean; faltan: string[] }

interface Borrador {
  id: number;
  numero: string;
  fecha: string;
  trip_id: string | null;
  cliente_nombre: string;
  base: number;
  iva: number;
  cuota_iva: number;
  total: number;
  creado: string;
  origen: string;
  destino: string;
  desglose: { concepto: string; importe: number }[];
  documentacion: Documentacion;
}

interface Facturable {
  id: string;
  referencia: string;
  cliente: string;
  cliente_id: number | null;
  precio: number;
  iva: number;
  origen: string;
  destino: string;
  creado: string;
  estado: string;
  documentacion: Documentacion;
}

interface Factura {
  id: number;
  numero: string;
  fecha: string;
  trip_id: string | null;
  cliente_id: number | null;
  cliente_nombre: string;
  base: number;
  iva: number;
  cuota_iva: number;
  total: number;
  estado: string;
}

interface Cliente { id: number; nombre: string; email?: string }

// ------------------------------------------------------------------ helpers
const fmtEUR = (n: number | null | undefined) =>
  new Intl.NumberFormat("es-ES", { style: "currency", currency: "EUR" }).format(Number(n ?? 0));

const fmtFecha = (iso?: string | null) => {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (isNaN(d.getTime())) return iso || "";
  return d.toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit", year: "numeric" });
};

const ESTADO: Record<string, { label: string; clase: string }> = {
  borrador: { label: "Borrador", clase: "bg-amber-100 text-amber-700" },
  emitida: { label: "Emitida", clase: "bg-blue-100 text-blue-700" },
  cobrada: { label: "Cobrada", clase: "bg-emerald-100 text-emerald-700" },
  pagada: { label: "Pagada", clase: "bg-emerald-100 text-emerald-700" },
  anulada: { label: "Anulada", clase: "bg-slate-100 text-slate-600" },
};

function EstadoBadge({ estado }: { estado: string }) {
  const e = ESTADO[estado] ?? { label: estado || "—", clase: "bg-slate-100 text-slate-600" };
  return <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${e.clase}`}>{e.label}</span>;
}

function DocBadge({ doc }: { doc?: Documentacion }) {
  if (!doc || doc.ok) return null;
  return (
    <span title={`Falta: ${doc.faltan.join(", ")}`} className="inline-flex items-center gap-1 rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-medium text-red-700">
      <AlertTriangle size={11} />
      {doc.faltan.join(", ")}
    </span>
  );
}

type Pestaña = "pendientes" | "emitidas" | "cobradas";

// ------------------------------------------------------------------ página
export function FacturacionPage() {
  const queryClient = useQueryClient();
  const [pestana, setPestana] = useState<Pestaña>("pendientes");
  const [selBorradores, setSelBorradores] = useState<Set<number>>(new Set());
  const [selViajes, setSelViajes] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<{ tipo: "ok" | "error" | "aviso"; texto: string } | null>(null);

  const [confirmar, setConfirmar] = useState<{ titulo: string; detalle: string; faltan?: string[]; tipo?: "emitir" | "facturar" | "cobrar"; accion: () => void } | null>(null);
  const [fechaCobro, setFechaCobro] = useState(() => new Date().toISOString().slice(0, 10));

  const [enviando, setEnviando] = useState<Factura | null>(null);
  const [fEmail, setFEmail] = useState("");
  const [fAsunto, setFAsunto] = useState("");
  const [fCuerpo, setFCuerpo] = useState("");
  const [fAdjDocs, setFAdjDocs] = useState(false);

  const [filtroCliente, setFiltroCliente] = useState("");
  const [fDesde, setFDesde] = useState("");
  const [fHasta, setFHasta] = useState("");
  const [limite, setLimite] = useState(50);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  const { data: borradores = [] } = useQuery({
    queryKey: ["borradores"],
    queryFn: async () => (await api<{ borradores: Borrador[] }>(REST_BORRADORES)).borradores ?? [],
  });

  const { data: facturables = [] } = useQuery({
    queryKey: ["facturables"],
    queryFn: async () => (await api<{ viajes: Facturable[] }>(REST_FACTURABLES)).viajes ?? [],
  });

  const { data: facturas = [] } = useQuery({
    queryKey: ["facturas"],
    queryFn: async () => (await api<{ facturas: Factura[] }>(REST_FACTURAS)).facturas ?? [],
  });

  const { data: clientes = [] } = useQuery({
    queryKey: ["clientes"],
    queryFn: async () => (await api<{ clientes: Cliente[] }>(REST_CLIENTES)).clientes ?? [],
  });

  function ok(texto: string) { setToast({ tipo: "ok", texto }); }
  function aviso(texto: string) { setToast({ tipo: "aviso", texto }); }
  function err(e: unknown) {
    // ApiError.message trae el texto del error (detail.error); .detail es el cuerpo completo.
    setToast({ tipo: "error", texto: e instanceof ApiError ? e.message : "Error" });
  }
  function refrescar() {
    queryClient.invalidateQueries({ queryKey: ["borradores"] });
    queryClient.invalidateQueries({ queryKey: ["facturables"] });
    queryClient.invalidateQueries({ queryKey: ["facturas"] });
  }

  const emitirMut = useMutation({
    mutationFn: async ({ ids, force }: { ids: number[]; force: boolean }) => {
      const okIds: number[] = [];
      const fallidos: { id: number; error: string }[] = [];
      for (const id of ids) {
        try {
          await api(EMITIR_BORRADOR(id), { method: "POST", body: JSON.stringify({ force }) });
          okIds.push(id);
        } catch (e) {
          fallidos.push({ id, error: e instanceof ApiError ? e.message : "Error" });
        }
      }
      return { ok: okIds.length, fallidos };
    },
    onSuccess: (r) => {
      if (r.fallidos.length === 0) ok(`Emitida(s) ${r.ok} factura(s).`);
      else aviso(`Emitidas ${r.ok}, fallidas ${r.fallidos.length}: ${r.fallidos.map((f) => f.error).join("; ")}`);
      setSelBorradores(new Set());
      refrescar();
    },
    onError: err,
  });

  function pedirEmitir(b: Borrador) {
    const force = b.documentacion?.ok === false;
    setConfirmar({
      titulo: "Emitir factura",
      detalle: `${b.cliente_nombre || "Cliente"} — 1 viaje · Base ${fmtEUR(b.base)} · IVA ${b.iva}% · Total ${fmtEUR(b.total)} · Fecha ${fmtFecha(new Date().toISOString())}`,
      faltan: b.documentacion?.ok === false ? b.documentacion.faltan : undefined,
      accion: () => emitirMut.mutate({ ids: [b.id], force }),
    });
  }

  function pedirEmitirLote() {
    const lista = borradores.filter((b) => selBorradores.has(b.id));
    if (!lista.length) return;
    const base = lista.reduce((s, b) => s + Number(b.base || 0), 0);
    const total = lista.reduce((s, b) => s + Number(b.total || 0), 0);
    const faltan = [...new Set(lista.flatMap((b) => (b.documentacion?.ok === false ? b.documentacion.faltan : [])))];
    const force = lista.some((b) => b.documentacion?.ok === false);
    setConfirmar({
      titulo: `Emitir ${lista.length} borrador(es)`,
      detalle: `${[...new Set(lista.map((b) => b.cliente_nombre))].join(", ") || "varios"} — Base ${fmtEUR(base)} · Total ${fmtEUR(total)}`,
      faltan: faltan.length ? faltan : undefined,
      accion: () => emitirMut.mutate({ ids: lista.map((b) => b.id), force }),
    });
  }

  const facturarMut = useMutation({
    mutationFn: async (tripIds: string[]) =>
      await api<{ factura: string; total: number }>(REST_FACTURA_AGRUPADA, {
        method: "POST",
        body: JSON.stringify({ trip_ids: tripIds, force: true }),
      }),
    onSuccess: (d) => { ok(`Factura ${d.factura} creada (${fmtEUR(d.total)}).`); setSelViajes(new Set()); refrescar(); },
    onError: err,
  });

  function pedirFacturar(viajes: Facturable[]) {
    const base = viajes.reduce((s, v) => s + Number(v.precio || 0), 0);
    const iva = viajes[0]?.iva ?? 21;
    const total = base * (1 + Number(iva) / 100);
    const faltan = [...new Set(viajes.flatMap((v) => (v.documentacion?.ok === false ? v.documentacion.faltan : [])))];
    setConfirmar({
      titulo: `Facturar ${viajes.length} viaje(s)`,
      detalle: `${[...new Set(viajes.map((v) => v.cliente || "—"))].join(", ") || "varios"} — Base ${fmtEUR(base)} · IVA ${iva}% · Total ${fmtEUR(total)} · Fecha ${fmtFecha(new Date().toISOString())}`,
      faltan: faltan.length ? faltan : undefined,
      accion: () => facturarMut.mutate(viajes.map((v) => v.id)),
    });
  }

  const cobrarMut = useMutation({
    mutationFn: (id: number) => api(REST_FACTURA_COBRAR(id), { method: "POST", body: JSON.stringify({ fecha_cobro: fechaCobro }) }),
    onSuccess: () => { ok("Factura marcada como cobrada."); refrescar(); },
    onError: err,
  });

  function pedirCobrar(f: Factura) {
    setFechaCobro(new Date().toISOString().slice(0, 10));
    setConfirmar({
      titulo: `Marcar cobrada ${f.numero}`,
      detalle: `${f.cliente_nombre || "Cliente"} — Total ${fmtEUR(f.total)}`,
      tipo: "cobrar",
      accion: () => cobrarMut.mutate(f.id),
    });
  }

  const enviarMut = useMutation({
    mutationFn: async () => {
      if (!enviando) return;
      await api(REST_FACTURA_ENVIAR(enviando.id), {
        method: "POST",
        body: JSON.stringify({ email: fEmail, asunto: fAsunto, cuerpo: fCuerpo, adjuntar_docs: fAdjDocs }),
      });
    },
    onSuccess: () => { ok("Factura enviada."); setEnviando(null); },
    onError: err,
  });

  function abrirEnviar(f: Factura) {
    const c = clientes.find((x) => x.id === f.cliente_id);
    setEnviando(f);
    setFEmail(c?.email ?? "");
    setFAsunto(`Factura ${f.numero}`);
    setFCuerpo(`Adjuntamos la factura ${f.numero}.`);
    setFAdjDocs(false);
  }

  async function pdfBlob(id: number): Promise<Blob | null> {
    try {
      const r = await fetch(REST_FACTURA_PDF(id), { headers: { Authorization: `Bearer ${getToken() ?? ""}` } });
      if (!r.ok) { err(new ApiError("Error al generar el PDF.", r.status)); return null; }
      return await r.blob();
    } catch {
      err(new ApiError("Error al descargar el PDF.", 0));
      return null;
    }
  }

  async function descargarPdf(id: number) {
    const blob = await pdfBlob(id);
    if (!blob) return;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `factura-${id}.pdf`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function verPdf(id: number) {
    const blob = await pdfBlob(id);
    if (!blob) return;
    window.open(URL.createObjectURL(blob), "_blank");
  }

  const facturasFiltradas = useMemo(() => {
    const q = filtroCliente.trim().toLowerCase();
    return facturas.filter((f) => {
      if (q && !(f.cliente_nombre || "").toLowerCase().includes(q)) return false;
      if (fDesde && (f.fecha || "") < fDesde) return false;
      if (fHasta && (f.fecha || "") > fHasta) return false;
      return true;
    });
  }, [facturas, filtroCliente, fDesde, fHasta]);

  const emitidas = facturasFiltradas.filter((f) => f.estado === "emitida").slice(0, limite);
  const cobradas = facturasFiltradas.filter((f) => f.estado === "cobrada" || f.estado === "pagada").slice(0, limite);
  const totalEmitidas = facturasFiltradas.filter((f) => f.estado === "emitida").length;
  const totalCobradas = facturasFiltradas.filter((f) => f.estado === "cobrada" || f.estado === "pagada").length;

  function toggleSet<T>(s: Set<T>, v: T): Set<T> {
    const n = new Set(s);
    n.has(v) ? n.delete(v) : n.add(v);
    return n;
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center rounded-md border border-input p-0.5">
          {(["pendientes", "emitidas", "cobradas"] as Pestaña[]).map((p) => (
            <button
              key={p}
              type="button"
              onClick={() => setPestana(p)}
              className={`rounded px-2.5 py-1 text-xs font-medium capitalize transition ${pestana === p ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
            >
              {p === "pendientes" ? `Pendientes (${borradores.length + facturables.length})` : p === "emitidas" ? `Emitidas (${totalEmitidas})` : `Cobradas (${totalCobradas})`}
            </button>
          ))}
        </div>
        {pestana === "pendientes" && (selBorradores.size > 0 || selViajes.size > 0) && (
          <div className="flex items-center gap-2">
            {selBorradores.size > 0 && (
              <Button onClick={pedirEmitirLote} disabled={emitirMut.isPending}>
                <FileText size={16} className="mr-1.5" /> Emitir {selBorradores.size}
              </Button>
            )}
            {selViajes.size > 0 && (
              <Button onClick={() => pedirFacturar(facturables.filter((v) => selViajes.has(v.id)))} disabled={facturarMut.isPending}>
                <FileText size={16} className="mr-1.5" /> Facturar {selViajes.size}
              </Button>
            )}
          </div>
        )}
        {(pestana === "emitidas" || pestana === "cobradas") && (
          <div className="flex items-center gap-2">
            <Input value={filtroCliente} onChange={(e) => setFiltroCliente(e.target.value)} placeholder="Cliente…" className="h-8 w-40" />
            <Input type="date" value={fDesde} onChange={(e) => setFDesde(e.target.value)} className="h-8 w-36" />
            <Input type="date" value={fHasta} onChange={(e) => setFHasta(e.target.value)} className="h-8 w-36" />
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="h-full overflow-auto">
          {pestana === "pendientes" && (
            <div className="space-y-6 p-3">
              <section>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Borradores</h3>
                {borradores.length === 0 ? (
                  <p className="py-3 text-sm text-slate-400">Sin borradores.</p>
                ) : (
                  <table className="w-full border-collapse text-sm">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className="w-8 border-b border-slate-200 px-2 py-2">
                          <input type="checkbox" className="h-3.5 w-3.5"
                            checked={borradores.length > 0 && selBorradores.size === borradores.length}
                            onChange={(e) => setSelBorradores(e.target.checked ? new Set(borradores.map((b) => b.id)) : new Set())} />
                        </th>
                        <th className="border-b border-slate-200 px-2 py-2 text-left text-xs font-semibold text-slate-500">Viaje</th>
                        <th className="border-b border-slate-200 px-2 py-2 text-left text-xs font-semibold text-slate-500">Cliente</th>
                        <th className="border-b border-slate-200 px-2 py-2 text-right text-xs font-semibold text-slate-500">Total</th>
                        <th className="border-b border-slate-200 px-2 py-2 text-left text-xs font-semibold text-slate-500">Documentación</th>
                        <th className="border-b border-slate-200 px-2 py-2 text-right text-xs font-semibold text-slate-500">Acción</th>
                      </tr>
                    </thead>
                    <tbody>
                      {borradores.map((b) => (
                        <tr key={b.id} className="border-b border-slate-100 hover:bg-slate-50">
                          <td className="px-2 py-2">
                            <input type="checkbox" className="h-3.5 w-3.5" checked={selBorradores.has(b.id)}
                              onChange={() => setSelBorradores((s) => toggleSet(s, b.id))} />
                          </td>
                          <td className="px-2 py-2 text-xs text-slate-500">{b.trip_id || "—"}</td>
                          <td className="px-2 py-2 font-medium text-slate-700">{b.cliente_nombre || "—"}</td>
                          <td className="px-2 py-2 text-right tabular-nums font-semibold text-slate-800">{fmtEUR(b.total)}</td>
                          <td className="px-2 py-2"><DocBadge doc={b.documentacion} /></td>
                          <td className="px-2 py-2 text-right">
                            <Button size="sm" onClick={() => pedirEmitir(b)} disabled={emitirMut.isPending}>Emitir</Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>

              <section>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Viajes pendientes de facturar</h3>
                {facturables.length === 0 ? (
                  <p className="py-3 text-sm text-slate-400">No hay viajes entregados pendientes.</p>
                ) : (
                  <table className="w-full border-collapse text-sm">
                    <thead className="bg-slate-50">
                      <tr>
                        <th className="w-8 border-b border-slate-200 px-2 py-2">
                          <input type="checkbox" className="h-3.5 w-3.5"
                            checked={facturables.length > 0 && selViajes.size === facturables.length}
                            onChange={(e) => setSelViajes(e.target.checked ? new Set(facturables.map((v) => v.id)) : new Set())} />
                        </th>
                        <th className="border-b border-slate-200 px-2 py-2 text-left text-xs font-semibold text-slate-500">Viaje</th>
                        <th className="border-b border-slate-200 px-2 py-2 text-left text-xs font-semibold text-slate-500">Cliente</th>
                        <th className="border-b border-slate-200 px-2 py-2 text-left text-xs font-semibold text-slate-500">Ruta</th>
                        <th className="border-b border-slate-200 px-2 py-2 text-right text-xs font-semibold text-slate-500">Precio</th>
                        <th className="border-b border-slate-200 px-2 py-2 text-left text-xs font-semibold text-slate-500">Documentación</th>
                        <th className="border-b border-slate-200 px-2 py-2 text-right text-xs font-semibold text-slate-500">Acción</th>
                      </tr>
                    </thead>
                    <tbody>
                      {facturables.map((v) => (
                        <tr key={v.id} className="border-b border-slate-100 hover:bg-slate-50">
                          <td className="px-2 py-2">
                            <input type="checkbox" className="h-3.5 w-3.5" checked={selViajes.has(v.id)}
                              onChange={() => setSelViajes((s) => toggleSet(s, v.id))} />
                          </td>
                          <td className="px-2 py-2 font-medium text-slate-700">{v.referencia || v.id}</td>
                          <td className="px-2 py-2 text-slate-600">{v.cliente || "—"}</td>
                          <td className="px-2 py-2 text-xs text-slate-500">{v.origen || "—"} → {v.destino || "—"}</td>
                          <td className="px-2 py-2 text-right tabular-nums text-slate-800">{fmtEUR(v.precio)}</td>
                          <td className="px-2 py-2"><DocBadge doc={v.documentacion} /></td>
                          <td className="px-2 py-2 text-right">
                            <Button size="sm" variant="outline" onClick={() => pedirFacturar([v])} disabled={facturarMut.isPending}>Facturar</Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </section>
            </div>
          )}

          {pestana !== "pendientes" && (
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 bg-slate-50">
                <tr>
                  <th className="border-b border-slate-200 px-3 py-2 text-left text-xs font-semibold text-slate-500">Número</th>
                  <th className="border-b border-slate-200 px-3 py-2 text-left text-xs font-semibold text-slate-500">Fecha</th>
                  <th className="border-b border-slate-200 px-3 py-2 text-left text-xs font-semibold text-slate-500">Cliente</th>
                  <th className="border-b border-slate-200 px-3 py-2 text-right text-xs font-semibold text-slate-500">Total</th>
                  <th className="border-b border-slate-200 px-3 py-2 text-left text-xs font-semibold text-slate-500">Estado</th>
                  <th className="border-b border-slate-200 px-3 py-2 text-right text-xs font-semibold text-slate-500">Acciones</th>
                </tr>
              </thead>
              <tbody>
                {(pestana === "emitidas" ? emitidas : cobradas).length === 0 ? (
                  <tr><td colSpan={6} className="px-3 py-8 text-center text-sm text-slate-400">Sin facturas.</td></tr>
                ) : (
                  (pestana === "emitidas" ? emitidas : cobradas).map((f) => (
                    <tr key={f.id} className="border-b border-slate-100 hover:bg-slate-50">
                      <td className="px-3 py-2 font-medium text-slate-700">{f.numero}</td>
                      <td className="px-3 py-2 text-slate-600">{fmtFecha(f.fecha)}</td>
                      <td className="px-3 py-2 text-slate-600">{f.cliente_nombre || "—"}</td>
                      <td className="px-3 py-2 text-right tabular-nums font-semibold text-slate-800">{fmtEUR(f.total)}</td>
                      <td className="px-3 py-2"><EstadoBadge estado={f.estado} /></td>
                      <td className="px-3 py-2">
                        <div className="flex items-center justify-end gap-1">
                          <Button size="sm" variant="ghost" onClick={() => verPdf(f.id)} title="Ver PDF"><Eye size={14} /></Button>
                          <Button size="sm" variant="ghost" onClick={() => descargarPdf(f.id)} title="Descargar"><Download size={14} /></Button>
                          <Button size="sm" variant="ghost" onClick={() => abrirEnviar(f)} disabled={enviarMut.isPending} title="Enviar"><Send size={14} /></Button>
                          {pestana === "emitidas" && (
                            <Button size="sm" variant="ghost" onClick={() => pedirCobrar(f)} disabled={cobrarMut.isPending} title="Marcar cobrada"><CheckCircle2 size={14} /></Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          )}
          {pestana !== "pendientes" && ((pestana === "emitidas" ? totalEmitidas : totalCobradas) > limite) && (
            <div className="p-2 text-center">
              <Button variant="ghost" size="sm" onClick={() => setLimite((l) => l + 50)}>Mostrar más</Button>
            </div>
          )}
        </div>
      </div>

      {/* Confirmación */}
      <Dialog open={!!confirmar} onOpenChange={(o) => !o && setConfirmar(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirmar?.titulo}</DialogTitle>
            <DialogDescription>{confirmar?.detalle}</DialogDescription>
          </DialogHeader>
          {confirmar?.faltan && confirmar.faltan.length > 0 && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              <div className="font-semibold">Falta documentación:</div>
              <div>{confirmar.faltan.join(", ")}</div>
              <div className="mt-1 text-xs text-red-600">Se facturará igualmente (forzado).</div>
            </div>
          )}
          {confirmar?.tipo === "cobrar" && (
            <div className="space-y-1">
              <Label htmlFor="f-cobro">Fecha de cobro</Label>
              <Input id="f-cobro" type="date" value={fechaCobro} onChange={(e) => setFechaCobro(e.target.value)} />
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmar(null)}>Cancelar</Button>
            <Button
              onClick={() => { const a = confirmar?.accion; setConfirmar(null); a?.(); }}
              disabled={emitirMut.isPending || facturarMut.isPending || cobrarMut.isPending}
            >
              {(emitirMut.isPending || facturarMut.isPending || cobrarMut.isPending) && <Loader2 size={14} className="mr-1.5 animate-spin" />}
              Confirmar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Envío */}
      <Dialog open={!!enviando} onOpenChange={(o) => !o && setEnviando(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Enviar factura {enviando?.numero}</DialogTitle>
            <DialogDescription>Email con la factura en PDF y, opcionalmente, los documentos del viaje.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="f-email">Destinatario</Label>
              <Input id="f-email" type="email" value={fEmail} onChange={(e) => setFEmail(e.target.value)} placeholder="cliente@ejemplo.com" />
            </div>
            <div className="space-y-1">
              <Label htmlFor="f-asunto">Asunto</Label>
              <Input id="f-asunto" value={fAsunto} onChange={(e) => setFAsunto(e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="f-cuerpo">Cuerpo</Label>
              <textarea id="f-cuerpo" rows={4} value={fCuerpo} onChange={(e) => setFCuerpo(e.target.value)}
                className="w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm focus:border-blue-400 focus:outline-none" />
            </div>
            <label className="flex items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" className="h-3.5 w-3.5" checked={fAdjDocs} onChange={(e) => setFAdjDocs(e.target.checked)} />
              Adjuntar documentos del viaje (CMR / carta de porte)
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEnviando(null)}>Cancelar</Button>
            <Button onClick={() => enviarMut.mutate()} disabled={enviarMut.isPending || !fEmail.trim()}>
              {enviarMut.isPending ? <Loader2 size={14} className="mr-1.5 animate-spin" /> : <Send size={14} className="mr-1.5" />}
              Enviar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {toast && (
        <div className={`fixed right-4 top-20 z-50 rounded-lg px-4 py-3 text-sm font-medium text-white shadow-lg ${toast.tipo === "ok" ? "bg-emerald-600" : toast.tipo === "aviso" ? "bg-amber-600" : "bg-red-600"}`}>
          {toast.texto}
        </div>
      )}
    </div>
  );
}
