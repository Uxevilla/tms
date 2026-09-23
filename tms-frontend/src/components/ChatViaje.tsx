import { useEffect, useRef, useState } from "react";
import { X, Send, FileQuestion, MessageCircle, Loader2 } from "lucide-react";
import { CREAR_VIAJE } from "../config";
import { getToken } from "../auth";

interface Mensaje {
  id: string;
  tipo: string; // enviado | libre | estructurado | cuestionario
  messagetype: string;
  originid: string;
  source: string;
  subject: string;
  body: string;
  time: string;
  needreply: boolean;
}

function fmtHora(iso?: string) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso || "";
  return d.toLocaleString("es-ES", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function ChatViaje({ tripId, onClose }: { tripId: string; onClose: () => void }) {
  const [mensajes, setMensajes] = useState<Mensaje[]>([]);
  const [asunto, setAsunto] = useState("");
  const [cuerpo, setCuerpo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState("");
  const [mostrarQP, setMostrarQP] = useState(false);
  const [qpSubject, setQpSubject] = useState("");
  const [qpType, setQpType] = useState("");
  const [qpBody, setQpBody] = useState("");
  const [enviandoQP, setEnviandoQP] = useState(false);
  const [messagetypes, setMessagetypes] = useState<string[]>([]);
  const listRef = useRef<HTMLDivElement>(null);

  const headers = () => ({ Authorization: `Bearer ${getToken() ?? ""}` });

  async function cargar() {
    try {
      const r = await fetch(`${CREAR_VIAJE}/${encodeURIComponent(tripId)}/mensajes`, { headers: headers() });
      if (r.ok) setMensajes((await r.json()).mensajes ?? []);
    } catch {
      /* sin conexión: se reintenta en el siguiente poll */
    }
  }

  useEffect(() => {
    cargar();
    const t = setInterval(async () => {
      try { await fetch("/api/sync/mensajes", { method: "POST", headers: headers() }); } catch { /* noop */ }
      cargar();
    }, 8000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tripId]);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch("/api/messagetypes", { headers: headers() });
        if (r.ok) setMessagetypes((await r.json()).messagetypes ?? []);
      } catch { /* noop */ }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [mensajes]);

  async function enviar() {
    const s = asunto.trim();
    const b = cuerpo.trim();
    if (!s && !b) return;
    setEnviando(true);
    setError("");
    try {
      const r = await fetch(`${CREAR_VIAJE}/${encodeURIComponent(tripId)}/mensajes`, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: JSON.stringify({ subject: s, body: b, needreply: false }),
      });
      const d = await r.json();
      if (d.ok) {
        setAsunto("");
        setCuerpo("");
        await cargar();
      } else {
        setError(d.error || "No se pudo enviar el mensaje.");
      }
    } catch {
      setError("Error de red al enviar.");
    }
    setEnviando(false);
  }

  async function enviarQuestionPath() {
    const body = qpBody.trim();
    if (!body) return;
    setEnviandoQP(true);
    setError("");
    try {
      const r = await fetch(`${CREAR_VIAJE}/${encodeURIComponent(tripId)}/questionpath`, {
        method: "POST",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: JSON.stringify({ subject: qpSubject.trim(), body, messagetype: qpType.trim() || "question" }),
      });
      const d = await r.json();
      if (d.ok) {
        setQpSubject("");
        setQpBody("");
        setMostrarQP(false);
        await cargar();
      } else {
        setError(d.error || "No se pudo enviar el question path.");
      }
    } catch {
      setError("Error de red al enviar.");
    }
    setEnviandoQP(false);
  }

  const chat = mensajes.filter((m) => m.tipo !== "cuestionario").slice().reverse();
  const cuestionarios = mensajes.filter((m) => m.tipo === "cuestionario");

  return (
    <>
      <div className="fixed inset-0 z-[2000] bg-slate-900/40 backdrop-blur-sm" onClick={onClose} />
      <div className="fixed inset-y-0 right-0 z-[2001] flex w-[42%] flex-col border-l border-slate-200 bg-white shadow-2xl">
        <header className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div className="flex items-center gap-2">
            <MessageCircle size={16} className="text-blue-600" />
            <h3 className="text-sm font-semibold text-slate-800">Chat con el conductor · {tripId}</h3>
          </div>
          <button onClick={onClose} className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
            <X size={18} />
          </button>
        </header>

        <div ref={listRef} className="flex-1 space-y-2 overflow-y-auto p-4">
          {cuestionarios.length > 0 && (
            <div className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
              <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-700">
                <FileQuestion size={14} /> Cuestionarios (question path)
              </div>
              {cuestionarios.slice().reverse().map((m) => (
                <div key={m.id} className="mb-2 rounded-md bg-white p-2 text-xs shadow-sm">
                  <div className="font-semibold text-slate-700">{m.subject || "Question path"}</div>
                  <div className="mt-1 whitespace-pre-wrap break-words text-slate-600">{m.body || "(sin contenido)"}</div>
                  <div className="mt-1 text-[10px] text-slate-400">{m.source || "—"} · {fmtHora(m.time)}</div>
                </div>
              ))}
            </div>
          )}

          {chat.length === 0 && cuestionarios.length === 0 && (
            <div className="pt-8 text-center text-sm text-slate-400">Sin conversación todavía.</div>
          )}

          {chat.map((m) => {
            const saliente = m.tipo === "enviado";
            return (
              <div key={m.id} className={`flex ${saliente ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[80%] rounded-lg px-3 py-2 text-sm shadow-sm ${saliente ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-800"}`}>
                  <div className={`text-[10px] font-semibold ${saliente ? "text-blue-100" : "text-slate-500"}`}>
                    {saliente ? "Tú" : m.source || "Chofer"}{m.time ? ` · ${fmtHora(m.time)}` : ""}
                  </div>
                  {m.subject && m.subject !== "AFRE" && (
                    <div className="text-[11px] font-semibold opacity-80">{m.subject}</div>
                  )}
                  <div className="mt-0.5 whitespace-pre-wrap break-words">{m.body || ""}</div>
                </div>
              </div>
            );
          })}
        </div>

        <footer className="border-t border-slate-200 p-3">
          {error && <div className="mb-2 rounded-md bg-red-50 px-3 py-1.5 text-xs text-red-700">{error}</div>}

          {mostrarQP && (
            <div className="mb-2 rounded-lg border border-blue-200 bg-blue-50 p-2">
              <div className="mb-1.5 flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-blue-700">
                <FileQuestion size={13} /> Question path
              </div>
              <input value={qpSubject} onChange={(e) => setQpSubject(e.target.value)} placeholder="Asunto" className="mb-1 w-full rounded-md border border-slate-200 px-2 py-1 text-xs" />
              <select value={qpType} onChange={(e) => setQpType(e.target.value)} className="mb-1 w-full rounded-md border border-slate-200 bg-white px-2 py-1 text-xs">
                <option value="">— messagetype —</option>
                {messagetypes.map((mt) => <option key={mt} value={mt}>{mt}</option>)}
              </select>
              <textarea value={qpBody} onChange={(e) => setQpBody(e.target.value)} placeholder="Cuerpo del question path (XML/CDATA)…" rows={4} className="mb-1.5 w-full resize-none rounded-md border border-slate-200 px-2 py-1 font-mono text-xs" />
              <div className="flex justify-end">
                <button onClick={enviarQuestionPath} disabled={enviandoQP} className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-2.5 py-1 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-60">
                  {enviandoQP ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />} Enviar
                </button>
              </div>
            </div>
          )}

          <button onClick={() => setMostrarQP((v) => !v)} className="mb-1.5 inline-flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-700">
            <FileQuestion size={13} /> {mostrarQP ? "Ocultar question path" : "Enviar question path"}
          </button>

          <input
            value={asunto}
            onChange={(e) => setAsunto(e.target.value)}
            placeholder="Asunto (opcional)"
            className="mb-1.5 w-full rounded-md border border-slate-200 px-2 py-1.5 text-sm focus:border-blue-400 focus:outline-none"
          />
          <div className="flex gap-2">
            <textarea
              value={cuerpo}
              onChange={(e) => setCuerpo(e.target.value)}
              placeholder="Escribe un mensaje…"
              rows={1}
              className="flex-1 resize-none rounded-md border border-slate-200 px-2 py-1.5 text-sm focus:border-blue-400 focus:outline-none"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  enviar();
                }
              }}
            />
            <button
              onClick={enviar}
              disabled={enviando}
              className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
            >
              {enviando ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />} Enviar
            </button>
          </div>
        </footer>
      </div>
    </>
  );
}
