import { useEffect, useRef, useState } from "react";
import { Send, Paperclip, MessageCircle, Loader2 } from "lucide-react";
import { api } from "../api";

interface Terminal {
  id: string;
}

interface Mensaje {
  id: string;
  tipo: string;
  source: string;
  subject: string;
  body: string;
  time: string;
}

export function MensajeriaDashboard() {
  const [terminales, setTerminales] = useState<Terminal[]>([]);
  const [terminal, setTerminal] = useState<string>("");
  const [mensajes, setMensajes] = useState<Mensaje[]>([]);
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [banner, setBanner] = useState<{ tipo: "ok" | "error"; texto: string } | null>(null);
  const finRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Terminales APP disponibles.
  useEffect(() => {
    api<any>("/api/mensajeria/terminales")
      .then((d) => {
        if (d.ok) {
          setTerminales(d.terminales ?? []);
          setTerminal((t) => t || d.terminales?.[0]?.id || "");
        }
      })
      .catch(() => {});
  }, []);

  // Cargar mensajes del terminal seleccionado.
  useEffect(() => {
    if (!terminal) return;
    let cancel = false;
    const cargar = () =>
      api<any>(`/api/mensajeria/${encodeURIComponent(terminal)}/mensajes`)
        .then((d) => { if (!cancel && d.ok) setMensajes(d.mensajes ?? []); })
        .catch(() => {});
    cargar();
    const t = setInterval(cargar, 2500);
    return () => { cancel = true; clearInterval(t); };
  }, [terminal]);

  // Scroll al final al cambiar los mensajes.
  useEffect(() => {
    finRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [mensajes, terminal]);

  async function recargar() {
    if (!terminal) return;
    const d = await api<any>(`/api/mensajeria/${encodeURIComponent(terminal)}/mensajes`);
    if (d.ok) setMensajes(d.mensajes ?? []);
  }

  async function enviar() {
    if (!terminal || !texto.trim()) return;
    setEnviando(true);
    try {
      const d = await api<any>(`/api/mensajeria/${encodeURIComponent(terminal)}/mensajes`, {
        method: "POST",
        body: JSON.stringify({ subject: "", body: texto.trim(), needreply: false }),
      });
      if (d.ok) { setTexto(""); await recargar(); }
      else setBanner({ tipo: "error", texto: d.error || "Error" });
    } catch {
      setBanner({ tipo: "error", texto: "Error de red al enviar." });
    } finally {
      setEnviando(false);
    }
  }

  async function adjuntar(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !terminal) return;
    if (file.size > 2 * 1024 * 1024) {
      setBanner({ tipo: "error", texto: "Máximo 2 MB por archivo." });
      return;
    }
    try {
      const b64 = await new Promise<string>((res, rej) => {
        const r = new FileReader();
        r.onload = () => res(String(r.result).split(",")[1] || "");
        r.onerror = () => rej(new Error("lectura"));
        r.readAsDataURL(file);
      });
      const d = await api<any>(`/api/mensajeria/${encodeURIComponent(terminal)}/adjuntos`, {
        method: "POST",
        body: JSON.stringify({ nombre: file.name, contenido: b64 }),
      });
      setBanner({ tipo: d.ok ? "ok" : "error", texto: d.ok ? `«${file.name}» enviado.` : (d.error || "Error al adjuntar") });
    } catch {
      setBanner({ tipo: "error", texto: "Error al adjuntar el archivo." });
    }
  }

  return (
    <div className="flex h-full min-h-0 gap-3">
      {/* Lista de terminales */}
      <aside className="w-56 shrink-0 overflow-y-auto rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
          Terminales
        </div>
        {terminales.length === 0 && (
          <div className="px-3 py-3 text-xs text-slate-400">Sin terminales APP configurados.</div>
        )}
        {terminales.map((t) => (
          <button
            key={t.id}
            onClick={() => setTerminal(t.id)}
            className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition ${
              terminal === t.id ? "bg-blue-50 font-semibold text-blue-700" : "text-slate-600 hover:bg-slate-50"
            }`}
          >
            <MessageCircle size={14} />
            <span className="truncate">{t.id}</span>
          </button>
        ))}
      </aside>

      {/* Chat estilo Telegram */}
      <div className="flex min-w-0 flex-1 flex-col rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-100 px-4 py-2.5 text-sm font-semibold text-slate-700">
          {terminal ? `Chat — ${terminal}` : "Selecciona un terminal"}
        </div>

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto bg-slate-50/50 p-4">
          {mensajes.length === 0 && (
            <div className="pt-10 text-center text-sm text-slate-400">Sin mensajes todavía.</div>
          )}
          {mensajes.map((m) => {
            const propio = m.tipo === "enviado";
            return (
              <div key={m.id} className={`flex ${propio ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[75%] rounded-xl px-3 py-1.5 text-sm shadow-sm ${
                    propio ? "bg-blue-600 text-white" : "bg-white text-slate-700 ring-1 ring-slate-200"
                  }`}
                >
                  {m.subject && <div className={`text-xs font-semibold ${propio ? "text-blue-100" : "text-slate-500"}`}>{m.subject}</div>}
                  <div className="whitespace-pre-wrap break-words">{m.body}</div>
                  <div className={`mt-0.5 text-[10px] ${propio ? "text-blue-100" : "text-slate-400"}`}>
                    {new Date(m.time).toLocaleString()}
                  </div>
                </div>
              </div>
            );
          })}
          <div ref={finRef} />
        </div>

        {/* Entrada */}
        <div className="flex items-center gap-2 border-t border-slate-100 p-3">
          <input ref={fileRef} type="file" className="hidden" onChange={adjuntar} />
          <span title="Adjuntos requieren DMS (no contratado)">
            <button
              type="button"
              disabled
              className="cursor-not-allowed rounded-md p-2 text-slate-300"
            >
              <Paperclip size={18} />
            </button>
          </span>
          <input
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") enviar(); }}
            disabled={!terminal}
            placeholder="Escribe un mensaje…"
            className="min-w-0 flex-1 rounded-md border border-slate-200 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none disabled:bg-slate-50"
          />
          <button
            onClick={enviar}
            disabled={enviando || !texto.trim() || !terminal}
            className="flex items-center gap-1 rounded-md bg-blue-600 px-3 py-2 text-white transition hover:bg-blue-700 disabled:opacity-50"
          >
            {enviando ? <Loader2 size={17} className="animate-spin" /> : <Send size={17} />}
          </button>
        </div>
      </div>

      {banner && (
        <div
          className={`fixed right-4 top-20 z-50 rounded-lg px-4 py-3 text-sm font-medium text-white shadow-lg ${
            banner.tipo === "ok" ? "bg-emerald-600" : "bg-red-600"
          }`}
        >
          {banner.texto}
        </div>
      )}
    </div>
  );
}
