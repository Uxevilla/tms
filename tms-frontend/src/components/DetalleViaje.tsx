import { useEffect, useState } from "react";
import { X, FileText, Download, ClipboardList, Split, Eye, CheckCircle2 } from "lucide-react";
import { api } from "../api";
import type { Viaje } from "../types";

interface Mensaje {
  id: string;
  tipo: string;
  messagetype: string;
  originid: string;
  source: string;
  subject: string;
  body: string;
  time: string;
  needreply: boolean;
}
interface Doc {
  id: string;
  nombre: string;
  contenido: string;
  size: number;
  source: string;
  formato: string;
  mime?: string;
  tipo_documento?: string;
  revisado_at?: string | null;
  revisado_por?: string | null;
}
interface Tramo {
  id: number;
  orden: number;
  origen_nombre: string;
  origen_ciudad: string;
  destino_nombre: string;
  destino_ciudad: string;
  terminal: string;
  conductor: string;
  km_total: number;
  km_real: number | null;
  estado: string;
}

export function DetalleViaje({ trip, onClose }: { trip: Viaje; onClose: () => void }) {
  const [mensajes, setMensajes] = useState<Mensaje[]>([]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [tramos, setTramos] = useState<Tramo[]>([]);
  const [cargando, setCargando] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const [m, d, tr] = await Promise.all([
          api<any>(`/api/trips/${encodeURIComponent(trip.id)}/mensajes`),
          api<any>(`/api/trips/${encodeURIComponent(trip.id)}/documentos`),
          api<any>(`/api/trips/${encodeURIComponent(trip.id)}/tramos`),
        ]);
        setMensajes(m.mensajes ?? []);
        setDocs(d.documentos ?? []);
        setTramos(tr.tramos ?? []);
      } catch {
        /* noop */
      }
      setCargando(false);
    })();
  }, [trip.id]);

  const qps = mensajes.filter((m) => m.tipo === "cuestionario");

  function descargar(d: Doc) {
    const b64 = (d.contenido || "").replace(/^data:[^;]+;base64,/, "");
    if (!b64) return;
    const ext = d.formato || (d.nombre.includes(".") ? d.nombre.split(".").pop() : "pdf");
    const a = document.createElement("a");
    a.href = `data:application/${ext};base64,${b64}`;
    a.download = d.nombre || `archivo.${ext}`;
    a.click();
  }

  function ver(d: Doc) {
    const b64 = (d.contenido || "").replace(/^data:[^;]+;base64,/, "");
    if (!b64) return;
    const mime = d.mime || (d.formato === "pdf" ? "application/pdf" : "image/png");
    window.open(`data:${mime};base64,${b64}`, "_blank");
  }

  return (
    <>
      <div className="fixed inset-0 z-[2099] bg-slate-900/40 backdrop-blur-sm" onClick={onClose} />
      <div className="fixed inset-y-0 right-0 z-[2100] flex w-[44%] flex-col border-l border-slate-200 bg-white shadow-2xl">
        <header className="border-b border-slate-200 px-4 py-3">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="text-sm font-semibold text-slate-800">
                {trip.origen || "—"} → {trip.destino || "—"}
              </h3>
              <p className="mt-0.5 text-xs text-slate-400">
                {trip.id} · {trip.matricula || "sin terminal"} · {trip.conductor || "sin conductor"}
              </p>
              {trip.itinerario && <p className="mt-0.5 text-xs text-slate-500">{trip.itinerario}</p>}
            </div>
            <button onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
              <X size={18} />
            </button>
          </div>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto p-4">
          {cargando ? (
            <p className="text-xs text-slate-400">Cargando…</p>
          ) : (
            <>
              {/* Tramos (segmentos) del viaje */}
              {tramos.length > 0 && (
                <section>
                  <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    <Split size={14} /> Tramos ({tramos.length})
                  </h4>
                  <ul className="space-y-1.5">
                    {tramos.map((t) => (
                      <li key={t.id} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs font-medium text-slate-700">
                            {t.orden}. {t.origen_nombre} → {t.destino_nombre}
                          </span>
                          <span className="shrink-0 text-[11px] text-slate-400">
                            {t.terminal || "sin vehículo"}{t.conductor ? ` · ${t.conductor}` : ""}
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {/* Question paths cumplimentados por el chófer */}
              <section>
                <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <ClipboardList size={14} /> Question paths cumplimentados ({qps.length})
                </h4>
                {qps.length === 0 ? (
                  <p className="text-xs text-slate-400">El chófer aún no ha cumplimentado ningún question path.</p>
                ) : (
                  <div className="space-y-2">
                    {qps.map((m) => (
                      <div key={m.id} className="rounded-lg border border-amber-200 bg-amber-50 p-3">
                        <div className="mb-1.5 flex items-center justify-between gap-2">
                          <span className="truncate text-xs font-semibold text-amber-800">
                            {m.messagetype || m.subject || "Question path"}
                          </span>
                          <span className="shrink-0 text-[11px] text-amber-600">
                            {m.time ? new Date(m.time).toLocaleString("es-ES") : ""}
                          </span>
                        </div>
                        <pre className="whitespace-pre-wrap break-words text-xs leading-relaxed text-amber-900">{m.body}</pre>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              {/* Archivos del viaje */}
              <section>
                <h4 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  <FileText size={14} /> Archivos del viaje ({docs.length})
                </h4>
                {docs.length === 0 ? (
                  <p className="text-xs text-slate-400">Sin archivos adjuntos.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {docs.map((d) => (
                      <li key={d.id} className="flex items-center justify-between gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2">
                        <div className="flex min-w-0 items-center gap-2">
                          <FileText size={15} className="shrink-0 text-slate-400" />
                          <div className="min-w-0">
                            <div className="flex items-center gap-1.5">
                              <span className="truncate text-xs font-medium text-slate-700">{d.nombre}</span>
                              {d.tipo_documento && d.tipo_documento !== "otro" && (
                                <span className="shrink-0 rounded bg-blue-100 px-1.5 py-0.5 text-[10px] font-semibold text-blue-700">{d.tipo_documento}</span>
                              )}
                              {d.revisado_at && <span title={`Revisado por ${d.revisado_por || "?"}`}><CheckCircle2 size={13} className="shrink-0 text-emerald-500" /></span>}
                            </div>
                            <div className="text-[11px] text-slate-400">
                              {d.source || "archivo"} · {(d.size / 1024).toFixed(0)} KB
                            </div>
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-1">
                          <button onClick={() => ver(d)} title="Ver" className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-blue-600"><Eye size={15} /></button>
                          <button onClick={() => descargar(d)} title="Descargar" className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-blue-600"><Download size={15} /></button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </>
          )}
        </div>
      </div>
    </>
  );
}
