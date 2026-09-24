import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/api";
import { REST_ENTIDAD } from "@/config";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { components } from "@/api/schema";

type PanelTipo = "vehiculo" | "viaje" | "conductor";
type EntidadVehiculo = components["schemas"]["EntidadVehiculo"];
type EntidadViaje = components["schemas"]["EntidadViaje"];
type EntidadConductor = components["schemas"]["EntidadConductor"];
type EntidadData = EntidadVehiculo | EntidadViaje | EntidadConductor;
type Dict = Record<string, unknown>;

interface EntityPanelProps {
  panel: string; // "vehiculo:1111-KKK"
  onClose: () => void;
}

function parsePanel(panel: string): { tipo: PanelTipo; id: string } | null {
  const m = /^(vehiculo|viaje|conductor):(.+)$/.exec(panel);
  if (!m || !m[2]) return null;
  return { tipo: m[1] as PanelTipo, id: m[2] };
}

const s = (x: unknown, fb = "—") => (x == null || x === "" ? fb : String(x));
const n = (x: unknown) => (x == null || x === "" ? null : Number(x));

const fmtEuro = (v: number | null | undefined) =>
  v == null ? "—" : v.toLocaleString("es-ES", { style: "currency", currency: "EUR" });

const fmtMin = (m: number | null | undefined) => {
  if (m == null) return "—";
  const t = Math.round(m);
  const h = Math.floor(t / 60);
  const min = t % 60;
  return h > 0 ? `${h}h ${min}m` : `${min}m`;
};

const fmtFecha = (f: string | null | undefined) =>
  f ? new Date(f).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" }) : "—";

function Fila({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 py-1">
      <span className="shrink-0 text-muted-foreground">{k}</span>
      <span className="truncate text-right font-medium">{v}</span>
    </div>
  );
}

function Caducidad({ c }: { c: { tipo: string; fecha: string | null; dias: number | null } }) {
  const dias = c.dias ?? 0;
  const color = dias < 0 ? "text-red-500" : dias <= 30 ? "text-amber-500" : "text-muted-foreground";
  const txt = dias < 0 ? `Vencido hace ${-dias} d` : dias === 0 ? "Vence hoy" : `En ${dias} d`;
  return (
    <div className="flex justify-between gap-3 py-1 text-sm">
      <span>{c.tipo}</span>
      <span className={`${color} font-medium`}>{txt}</span>
    </div>
  );
}

export function EntityPanel({ panel, onClose }: EntityPanelProps) {
  const parsed = parsePanel(panel);
  const [pestana, setPestana] = useState(0);

  const query = useQuery({
    queryKey: ["entidad", parsed?.tipo ?? "", parsed?.id ?? ""],
    queryFn: () => api<EntidadData>(REST_ENTIDAD(parsed!.tipo, parsed!.id)),
    enabled: !!parsed,
  });

  if (!parsed) return null;
  const data = query.data;
  const error = query.error;

  const titulo =
    parsed.tipo === "vehiculo"
      ? (data as EntidadVehiculo | undefined)?.vehiculo?.matricula || parsed.id
      : parsed.tipo === "viaje"
        ? s((data as EntidadViaje | undefined)?.viaje?.codigo, "") || parsed.id
        : s((data as EntidadConductor | undefined)?.conductor?.nombre, "") || parsed.id;

  const pestanas: string[] =
    parsed.tipo === "vehiculo"
      ? ["Datos", "Viajes", "Tacógrafo", "Mantenimientos", "Documentos"]
      : parsed.tipo === "viaje"
        ? ["Datos", "Paradas", "Documentos", "Mensajes"]
        : ["Datos", "Viajes", "Tacógrafo", "Ausencias"];

  return (
    <Sheet open onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="flex w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-[480px]">
        <SheetHeader className="shrink-0">
          <SheetTitle>{titulo}</SheetTitle>
          <SheetDescription className="capitalize">{parsed.tipo}</SheetDescription>
          <div className="flex gap-1 pt-1">
            {pestanas.map((p, i) => (
              <button
                key={p}
                onClick={() => setPestana(i)}
                className={`rounded px-2.5 py-1 text-xs font-medium transition ${
                  pestana === i ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-secondary"
                }`}
              >
                {p}
              </button>
            ))}
          </div>
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-y-auto p-6 text-sm">
          {query.isLoading && <div className="text-muted-foreground">Cargando…</div>}
          {error && (
            <div className="text-red-500">
              {error instanceof Error && (error as { status?: number }).status === 404
                ? "No encontrado."
                : "Error al cargar."}
            </div>
          )}
          {data && <Contenido tipo={parsed.tipo} pestana={pestana} data={data} />}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function Contenido({ tipo, pestana, data }: { tipo: PanelTipo; pestana: number; data: EntidadData }) {
  if (tipo === "vehiculo") return <Vehiculo pestana={pestana} data={data as EntidadVehiculo} />;
  if (tipo === "viaje") return <Viaje pestana={pestana} data={data as EntidadViaje} />;
  return <Conductor pestana={pestana} data={data as EntidadConductor} />;
}

function ViajeCard({ label, viaje }: { label: string; viaje: Dict }) {
  return (
    <div className="rounded border p-2">
      <div className="font-medium">{label}</div>
      <div className="text-xs text-muted-foreground">
        {s(viaje.origen)} → {s(viaje.destino)}
      </div>
      <div className="text-xs">{s(viaje.estado)}</div>
    </div>
  );
}

function Vehiculo({ pestana, data }: { pestana: number; data: EntidadVehiculo }) {
  const v = data.vehiculo ?? {};
  const pos = data.posicion;
  const taco = data.tacografo;
  if (pestana === 0)
    return (
      <div className="space-y-1">
        <Fila k="Matrícula" v={v.matricula || "—"} />
        <Fila k="Código" v={v.codigo || "—"} />
        <Fila k="Marca/modelo" v={[v.marca, v.modelo].filter(Boolean).join(" ") || "—"} />
        <Fila k="Categoría" v={v.categoria || "—"} />
        <Fila k="Odómetro" v={v.km_actuales != null ? `${v.km_actuales.toLocaleString("es-ES")} km` : "—"} />
        <Fila k="Terminal Trimble" v={v.terminal_trimble || "—"} />
        {pos && (
          <div className="mt-3 border-t pt-2">
            <div className="mb-1 text-xs font-semibold text-muted-foreground">Posición</div>
            <Fila k="Velocidad" v={pos.velocidad != null ? `${Math.round(pos.velocidad)} km/h` : "—"} />
            <Fila k="Rumbo" v={pos.heading != null ? `${Math.round(pos.heading)}°` : "—"} />
            <Fila k="Actualizada" v={fmtFecha(pos.time ?? null)} />
          </div>
        )}
        {data.caducidades?.length > 0 && (
          <div className="mt-3 border-t pt-2">
            <div className="mb-1 text-xs font-semibold text-muted-foreground">Caducidades</div>
            {data.caducidades.map((c) => (
              <Caducidad key={c.tipo} c={{ tipo: c.tipo, fecha: c.fecha ?? null, dias: c.dias ?? null }} />
            ))}
          </div>
        )}
        {data.coste_margen_mes && (
          <div className="mt-3 border-t pt-2">
            <div className="mb-1 text-xs font-semibold text-muted-foreground">Mes en curso</div>
            <Fila k="Ingresos" v={fmtEuro(data.coste_margen_mes.ingresos)} />
            <Fila k="Costes" v={fmtEuro(data.coste_margen_mes.costes)} />
            <Fila k="Margen" v={fmtEuro(data.coste_margen_mes.margen)} />
          </div>
        )}
      </div>
    );
  if (pestana === 1)
    return (
      <div className="space-y-2">
        {data.viaje_actual ? (
          <ViajeCard label="Viaje actual" viaje={data.viaje_actual as Dict} />
        ) : (
          <div className="text-muted-foreground">Sin viaje en curso.</div>
        )}
        {data.proximos?.length > 0 && (
          <div className="mt-3">
            <div className="mb-1 text-xs font-semibold text-muted-foreground">Próximos</div>
            {data.proximos.map((t, i) => (
              <ViajeCard key={i} label={s(t.codigo)} viaje={t as Dict} />
            ))}
          </div>
        )}
      </div>
    );
  if (pestana === 2)
    return taco ? (
      <div className="space-y-1">
        <Fila k="Conductor" v={taco.conductor || "—"} />
        <Fila k="Conducción hoy" v={fmtMin(taco.day_driving_min)} />
        <Fila k="Continua" v={fmtMin(taco.driving_coupure_min)} />
        <Fila k="Semana restante" v={fmtMin(taco.remaining_week_available_min)} />
        <Fila k="Descanso" v={taco.next_rest_due_ts ? fmtFecha(new Date(taco.next_rest_due_ts * 1000).toISOString()) : "—"} />
      </div>
    ) : (
      <div className="text-muted-foreground">Sin datos de tacógrafo.</div>
    );
  if (pestana === 3)
    return data.mantenimientos?.length > 0 ? (
      <div className="space-y-2">
        {data.mantenimientos.map((m, i) => (
          <div key={i} className="rounded border p-2">
            <div className="font-medium">{m.tipo ?? "—"}</div>
            <div className="text-xs text-muted-foreground">
              {m.fecha ?? ""} · {m.km != null ? `${m.km.toLocaleString("es-ES")} km` : ""} · {fmtEuro(m.coste)}
            </div>
          </div>
        ))}
      </div>
    ) : (
      <div className="text-muted-foreground">Sin mantenimientos.</div>
    );
  return (
    <div className="space-y-1">
      {data.documentos?.length > 0 ? (
        data.documentos.map((d, i) => (
          <div key={i} className="flex justify-between py-1">
            <span className="truncate">{d.nombre ?? ""}</span>
            <span className="text-xs text-muted-foreground">{d.formato ?? ""}</span>
          </div>
        ))
      ) : (
        <div className="text-muted-foreground">Sin documentos.</div>
      )}
    </div>
  );
}

function Viaje({ pestana, data }: { pestana: number; data: EntidadViaje }) {
  const v = data.viaje ?? {};
  if (pestana === 0)
    return (
      <div className="space-y-1">
        <Fila k="Código" v={s(v.codigo)} />
        <Fila k="Referencia" v={s(v.referencia)} />
        <Fila k="Estado" v={s(v.estado)} />
        <Fila k="Cliente" v={s(v.cliente)} />
        <Fila k="Ruta" v={`${s(v.origen, "?")} → ${s(v.destino, "?")}`} />
        <Fila k="Vehículo" v={s(v.matricula, "") || s(v.terminal)} />
        <Fila k="Conductor" v={s(v.conductor)} />
        <Fila k="Precio" v={fmtEuro(n(v.precio))} />
        <Fila k="Km totales" v={n(v.km_total) != null ? `${n(v.km_total)!.toLocaleString("es-ES")} km` : "—"} />
        {data.rentabilidad && (
          <div className="mt-3 border-t pt-2">
            <div className="mb-1 text-xs font-semibold text-muted-foreground">Rentabilidad</div>
            <Fila k="Ingresos" v={fmtEuro(data.rentabilidad.ingresos)} />
            <Fila k="Costes" v={fmtEuro(data.rentabilidad.costes)} />
            <Fila k="Margen" v={fmtEuro(data.rentabilidad.margen)} />
          </div>
        )}
      </div>
    );
  if (pestana === 1)
    return data.paradas?.length > 0 ? (
      <div className="space-y-2">
        {data.paradas.map((p, i) => (
          <div key={i} className="rounded border p-2">
            <div className="font-medium">
              {s(p.orden, "")}. {s(p.nombre, "") || s(p.ciudad)}
            </div>
            <div className="text-xs text-muted-foreground">{s(p.actividad, "")}</div>
          </div>
        ))}
      </div>
    ) : (
      <div className="text-muted-foreground">Sin paradas.</div>
    );
  if (pestana === 2)
    return (
      <div className="space-y-1">
        {data.documentos?.length > 0 ? (
          data.documentos.map((d, i) => (
            <div key={i} className="flex justify-between py-1">
              <span className="truncate">{d.nombre ?? ""}</span>
              <span className="text-xs text-muted-foreground">{d.formato ?? ""}</span>
            </div>
          ))
        ) : (
          <div className="text-muted-foreground">Sin documentos.</div>
        )}
      </div>
    );
  return (
    <div className="space-y-2">
      {data.mensajes?.length > 0 ? (
        data.mensajes.map((m, i) => (
          <div key={i} className="rounded border p-2">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{s(m.messagetype, "") || s(m.tipo)}</span>
              <span>{s(m.source, "")}</span>
            </div>
            <div className="text-xs">{s(m.subject, "") || s(m.body)}</div>
          </div>
        ))
      ) : (
        <div className="text-muted-foreground">Sin mensajes.</div>
      )}
    </div>
  );
}

function Conductor({ pestana, data }: { pestana: number; data: EntidadConductor }) {
  const c = data.conductor ?? {};
  const taco = data.tacografo;
  if (pestana === 0)
    return (
      <div className="space-y-1">
        <Fila k="Nombre" v={s(c.nombre)} />
        {c.dni ? <Fila k="DNI" v={s(c.dni)} /> : null}
        <Fila k="Teléfono" v={s(c.telefono)} />
        <Fila k="Email" v={s(c.email)} />
        {data.caducidades?.length > 0 && (
          <div className="mt-3 border-t pt-2">
            <div className="mb-1 text-xs font-semibold text-muted-foreground">Caducidades</div>
            {data.caducidades.map((cd) => (
              <Caducidad key={cd.tipo} c={{ tipo: cd.tipo, fecha: cd.fecha ?? null, dias: cd.dias ?? null }} />
            ))}
          </div>
        )}
      </div>
    );
  if (pestana === 1)
    return (
      <div className="space-y-2">
        {data.viaje_actual ? (
          <ViajeCard label="Viaje actual" viaje={data.viaje_actual as Dict} />
        ) : (
          <div className="text-muted-foreground">Sin viaje en curso.</div>
        )}
        {data.proximos?.length > 0 && (
          <div className="mt-3">
            <div className="mb-1 text-xs font-semibold text-muted-foreground">Próximos</div>
            {data.proximos.map((t, i) => (
              <ViajeCard key={i} label={s(t.codigo)} viaje={t as Dict} />
            ))}
          </div>
        )}
      </div>
    );
  if (pestana === 2)
    return taco ? (
      <div className="space-y-1">
        <Fila k="Conducción hoy" v={fmtMin(taco.day_driving_min)} />
        <Fila k="Continua" v={fmtMin(taco.driving_coupure_min)} />
        <Fila k="Semana restante" v={fmtMin(taco.remaining_week_available_min)} />
      </div>
    ) : (
      <div className="text-muted-foreground">Sin datos de tacógrafo.</div>
    );
  return (
    <div className="space-y-2">
      {data.ausencias?.length > 0 ? (
        data.ausencias.map((a, i) => (
          <div key={i} className="rounded border p-2">
            <div className="font-medium">{s(a.tipo)}</div>
            <div className="text-xs text-muted-foreground">
              {s(a.fecha_inicio)} → {s(a.fecha_fin)}
            </div>
          </div>
        ))
      ) : (
        <div className="text-muted-foreground">Sin ausencias próximas.</div>
      )}
    </div>
  );
}
