import { useEffect, useRef, useState } from "react";
import { Search, MapPin, Building2, Loader2, X, Globe } from "lucide-react";
import { REST_DIRECCIONES_BUSCAR, REST_DIRECCIONES } from "../config";
import { api } from "../api";

export interface Sugerencia {
  id: number | null;
  tipo: "direccion" | "cliente" | "externo";
  nombre: string;
  empresa: string;
  calle: string;
  numero: string;
  ciudad: string;
  cp: string;
  pais: string;
  lat: number | null;
  lng: number | null;
}

interface Props {
  placeholder?: string;
  /** Texto inicial/seleccionado para precargar (modo edición). */
  value?: string;
  onSelect: (d: Sugerencia & { id: number }) => void;
  onClear?: () => void;
}

const ETIQUETA_TIPO: Record<Sugerencia["tipo"], { texto: string; icono: JSX.Element; color: string }> = {
  direccion: { texto: "guardado", icono: <MapPin size={14} />, color: "text-slate-400" },
  cliente: { texto: "cliente", icono: <Building2 size={14} />, color: "text-amber-500" },
  externo: { texto: "externo", icono: <Globe size={14} />, color: "text-blue-500" },
};

export function BuscadorDireccion({ placeholder = "Buscar empresa o lugar…", value, onSelect, onClear }: Props) {
  const [texto, setTexto] = useState("");
  const [sug, setSug] = useState<Sugerencia[]>([]);
  const [abierto, setAbierto] = useState(false);
  const [buscando, setBuscando] = useState(false);
  const [seleccionado, setSeleccionado] = useState(value || "");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const caja = useRef<HTMLDivElement>(null);

  async function buscar(q: string) {
    setBuscando(true);
    try {
      const data = await api<{ sugerencias?: Sugerencia[] }>(`${REST_DIRECCIONES_BUSCAR}?q=${encodeURIComponent(q)}`);
      setSug(data.sugerencias ?? []);
    } catch (err) {
      console.error("Error buscando direcciones:", err);
    } finally {
      setBuscando(false);
    }
  }

  function onChange(v: string) {
    setSeleccionado("");
    setTexto(v);
    setAbierto(true);
    if (timer.current) clearTimeout(timer.current);
    if (v.trim()) {
      timer.current = setTimeout(() => buscar(v.trim()), 300);
    } else {
      setSug([]);
      timer.current = setTimeout(() => buscar(""), 100);
    }
  }

  async function elegir(s: Sugerencia) {
    let id = s.id;
    if (id == null) {
      // Persistir el resultado externo/cliente como nueva dirección del maestro
      try {
        const data = await api<{ id: number }>(REST_DIRECCIONES, {
          method: "POST",
          body: JSON.stringify({ nombre: s.nombre, empresa: s.empresa, calle: s.calle, numero: s.numero, ciudad: s.ciudad, cp: s.cp, pais: s.pais, lat: s.lat, lng: s.lng }),
        });
        id = data.id;
      } catch {
        return; // no se pudo persistir → no seleccionar
      }
    }
    setSeleccionado([s.nombre || s.calle, s.ciudad].filter(Boolean).join(" · "));
    setTexto("");
    setSug([]);
    setAbierto(false);
    onSelect({ ...s, id: id as number });
  }

  function limpiar() {
    setSeleccionado("");
    setTexto("");
    setSug([]);
    onClear?.();
  }

  useEffect(() => {
    function fuera(e: MouseEvent) {
      if (caja.current && !caja.current.contains(e.target as Node)) setAbierto(false);
    }
    document.addEventListener("mousedown", fuera);
    return () => document.removeEventListener("mousedown", fuera);
  }, []);

  // Precarga (modo edición): sincroniza el texto seleccionado cuando cambia `value`.
  useEffect(() => {
    setSeleccionado(value || "");
  }, [value]);

  return (
    <div ref={caja} className="relative">
      <div className="relative">
        <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          type="text"
          value={seleccionado || texto}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => { setAbierto(true); if (!texto && !seleccionado) buscar(""); }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && abierto && sug.length > 0) {
              e.preventDefault();
              elegir(sug[0]);
            }
          }}
          placeholder={placeholder}
          className="mt-0.5 w-full rounded-md border border-slate-200 py-1.5 pl-8 pr-7 text-sm focus:border-blue-400 focus:outline-none"
        />
        {(seleccionado || texto) && (
          <button type="button" onClick={limpiar} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
            <X size={14} />
          </button>
        )}
      </div>
      {abierto && (
        <div className="absolute z-50 mt-1 max-h-64 w-full overflow-auto rounded-md border border-slate-200 bg-white shadow-lg">
          {buscando && (
            <div className="flex items-center gap-2 px-3 py-2 text-xs text-slate-400">
              <Loader2 size={13} className="animate-spin" /> Buscando…
            </div>
          )}
          {sug.map((s, i) => {
            const meta = ETIQUETA_TIPO[s.tipo] || ETIQUETA_TIPO.externo;
            return (
              <button key={`${s.tipo}-${i}`} type="button" onClick={() => elegir(s)} className="flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-slate-50">
                <span className={`mt-0.5 shrink-0 ${meta.color}`}>{meta.icono}</span>
                <span className="min-w-0">
                  <span className="block truncate text-sm text-slate-800">{s.nombre || s.calle || s.ciudad || "—"}</span>
                  <span className="block truncate text-[11px] text-slate-400">
                    {[s.calle, s.ciudad, s.cp].filter(Boolean).join(", ")}
                    {s.tipo !== "direccion" && <span className={`ml-1 font-semibold ${meta.color}`}>· {meta.texto}</span>}
                  </span>
                </span>
              </button>
            );
          })}
          {!buscando && sug.length === 0 && texto && (
            <div className="px-3 py-2 text-xs text-slate-400">Sin resultados.</div>
          )}
        </div>
      )}
    </div>
  );
}
