import { useCallback, useEffect, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { AllCommunityModule, ModuleRegistry } from "ag-grid-community";
import type { ColDef } from "ag-grid-community";
import {
  Plug,
  Users,
  Shield,
  Plus,
  Save,
  Trash2,
  Settings2,
  Loader2,
} from "lucide-react";
import {
  REST_CONFIG_PROVEEDORES,
  REST_CONFIG_PROVEEDOR,
  REST_CONFIG_VALORES,
  REST_CONFIG_ACTIVIDADES,
  REST_CONFIG_ACTIVIDAD,
  REST_CONFIG_USUARIOS,
  REST_CONFIG_USUARIO,
  REST_CONFIG_ROLES,
} from "../config";
import { getToken } from "../auth";
import { Modal } from "./Modal";
import { gridTheme, GRID_ROW_HEIGHT, GRID_HEADER_HEIGHT } from "../gridConfig";

ModuleRegistry.registerModules([AllCommunityModule]);

const headers = () => ({ Authorization: `Bearer ${getToken() ?? ""}` });

// ------------------------------------------------------------------ tipos
interface Campo {
  id: number;
  clave: string;
  etiqueta: string;
  tipo: string;
  requerido: boolean;
  orden: number;
  valor: string | null;
}
interface Proveedor {
  id: number;
  codigo: string;
  nombre: string;
  categoria: string;
  icono: string;
  activo: boolean;
  orden: number;
  campos: Campo[];
}
interface Actividad {
  id: number;
  nombre: string;
  referencia: string;
  activo: boolean;
}
interface Usuario {
  id: number;
  usuario: string;
  rol: string;
  nombre: string | null;
  activo: boolean;
  creado_en: string;
}
interface Rol {
  id: number;
  nombre: string;
  descripcion: string | null;
}

type Tab = "integraciones" | "usuarios" | "roles";

const CATEGORIAS: Record<string, string> = {
  telemetria: "Telemetría",
  rutas: "Rutas",
  ecmr: "eCMR",
  correo: "Correo",
  notificaciones: "Notificaciones",
};

// ------------------------------------------------------------ cell renderers
function ActivoToggle({ data, onCambio }: { data: Proveedor; onCambio: () => void }) {
  return (
    <input
      type="checkbox"
      checked={data.activo}
      onChange={async () => {
        await fetch(REST_CONFIG_PROVEEDOR(data.codigo), {
          method: "PUT",
          headers: { ...headers(), "Content-Type": "application/json" },
          body: JSON.stringify({ activo: !data.activo }),
        });
        onCambio();
      }}
      className="h-4 w-4 cursor-pointer accent-blue-600"
    />
  );
}

function ConfigurarBtn({ data, onConfigurar }: { data: Proveedor; onConfigurar: (p: Proveedor) => void }) {
  return (
    <button
      type="button"
      onClick={() => onConfigurar(data)}
      className="rounded-md bg-blue-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-blue-700"
    >
      Configurar
    </button>
  );
}

// ------------------------------------------------------------------ main
export function ConfiguracionDashboard() {
  const [tab, setTab] = useState<Tab>("integraciones");
  const [cargando, setCargando] = useState(true);
  const [proveedores, setProveedores] = useState<Proveedor[]>([]);
  const [usuarios, setUsuarios] = useState<Usuario[]>([]);
  const [roles, setRoles] = useState<Rol[]>([]);
  const [configurando, setConfigurando] = useState<Proveedor | null>(null);
  const [usuarioEdit, setUsuarioEdit] = useState<Usuario | null>(null);
  const [nuevoUsuario, setNuevoUsuario] = useState(false);

  const cargar = useCallback(async () => {
    try {
      const [pr, us, ro] = await Promise.all([
        fetch(REST_CONFIG_PROVEEDORES, { headers: headers() }).then((r) => r.json()),
        fetch(REST_CONFIG_USUARIOS, { headers: headers() }).then((r) => r.json()),
        fetch(REST_CONFIG_ROLES, { headers: headers() }).then((r) => r.json()),
      ]);
      setProveedores(pr.proveedores ?? []);
      setUsuarios(us.usuarios ?? []);
      setRoles(ro.roles ?? []);
    } finally {
      setCargando(false);
    }
  }, []);

  useEffect(() => {
    cargar();
  }, [cargar]);

  const provCols: ColDef<Proveedor>[] = [
    { headerName: "Proveedor", field: "nombre", flex: 1 },
    {
      headerName: "Categoría",
      field: "categoria",
      width: 130,
      valueFormatter: (p) => CATEGORIAS[p.value as string] ?? p.value,
    },
    {
      headerName: "Campos",
      width: 90,
      valueGetter: (p) => p.data?.campos?.length ?? 0,
    },
    {
      headerName: "Activo",
      width: 80,
      cellRenderer: ActivoToggle,
      cellRendererParams: { onCambio: cargar },
    },
    {
      headerName: "",
      width: 120,
      cellRenderer: ConfigurarBtn,
      cellRendererParams: { onConfigurar: setConfigurando },
    },
  ];

  const usrCols: ColDef<Usuario>[] = [
    { headerName: "Usuario", field: "usuario", flex: 1 },
    { headerName: "Nombre", field: "nombre", flex: 1, valueFormatter: (p) => p.value ?? "—" },
    { headerName: "Rol", field: "rol", width: 130 },
    {
      headerName: "Activo",
      field: "activo",
      width: 90,
      valueFormatter: (p) => (p.value ? "Sí" : "No"),
    },
    {
      headerName: "",
      width: 110,
      cellRenderer: (p: { data: Usuario }) => (
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => setUsuarioEdit(p.data)}
            className="rounded-md px-2 py-1 text-[11px] font-semibold text-blue-700 hover:bg-blue-50"
          >
            Editar
          </button>
          <button
            type="button"
            onClick={async () => {
              if (!window.confirm(`¿Borrar al usuario "${p.data.usuario}"?`)) return;
              await fetch(REST_CONFIG_USUARIO(p.data.id), { method: "DELETE", headers: headers() });
              cargar();
            }}
            className="rounded-md px-2 py-1 text-[11px] font-semibold text-red-600 hover:bg-red-50"
          >
            Borrar
          </button>
        </div>
      ),
    },
  ];

  if (cargando) {
    return (
      <div className="flex h-full items-center justify-center text-slate-400">
        <Loader2 size={20} className="animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <TabBar tab={tab} onTab={setTab} />

      {tab === "integraciones" && (
        <div className="min-h-0 flex-1 p-4">
          <div className="flex h-full flex-col overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-slate-200">
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5">
              <h3 className="text-sm font-bold text-slate-800">Integraciones</h3>
              <span className="text-[11px] text-slate-400">
                Activa un proveedor y pulsa “Configurar” para sus credenciales y tipos de actividad
              </span>
            </div>
            <div className="min-h-0 flex-1">
              <AgGridReact<Proveedor>
                rowData={proveedores}
                columnDefs={provCols}
                theme={gridTheme}
                rowHeight={GRID_ROW_HEIGHT}
                headerHeight={GRID_HEADER_HEIGHT}
                getRowId={(p) => p.data.codigo}
              />
            </div>
          </div>
        </div>
      )}

      {tab === "usuarios" && (
        <div className="min-h-0 flex-1 p-4">
          <div className="flex h-full flex-col overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-slate-200">
            <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5">
              <h3 className="text-sm font-bold text-slate-800">Usuarios</h3>
              <button
                type="button"
                onClick={() => setNuevoUsuario(true)}
                className="inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-blue-700"
              >
                <Plus size={14} /> Nuevo usuario
              </button>
            </div>
            <div className="min-h-0 flex-1">
              <AgGridReact<Usuario>
                rowData={usuarios}
                columnDefs={usrCols}
                theme={gridTheme}
                rowHeight={GRID_ROW_HEIGHT}
                headerHeight={GRID_HEADER_HEIGHT}
                getRowId={(p) => String(p.data.id)}
              />
            </div>
          </div>
        </div>
      )}

      {tab === "roles" && (
        <div className="min-h-0 flex-1 p-4">
          <div className="overflow-hidden rounded-lg bg-white shadow-sm ring-1 ring-slate-200">
            <div className="border-b border-slate-200 px-4 py-2.5">
              <h3 className="text-sm font-bold text-slate-800">Roles</h3>
            </div>
            <table className="w-full text-[12px]">
              <thead>
                <tr className="border-b border-slate-200 text-left text-[11px] uppercase tracking-wide text-slate-400">
                  <th className="px-4 py-2 font-semibold">Rol</th>
                  <th className="px-4 py-2 font-semibold">Descripción</th>
                </tr>
              </thead>
              <tbody>
                {roles.map((r) => (
                  <tr key={r.id} className="border-b border-slate-100 last:border-0">
                    <td className="px-4 py-2 font-semibold text-slate-800">{r.nombre}</td>
                    <td className="px-4 py-2 text-slate-500">{r.descripcion ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {configurando && (
        <ProviderModal
          proveedor={configurando}
          onClose={() => setConfigurando(null)}
          onGuardado={cargar}
        />
      )}
      {(nuevoUsuario || usuarioEdit) && (
        <UsuarioModal
          usuario={usuarioEdit}
          roles={roles}
          onClose={() => {
            setNuevoUsuario(false);
            setUsuarioEdit(null);
          }}
          onGuardado={cargar}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ tabs
function TabBar({ tab, onTab }: { tab: Tab; onTab: (t: Tab) => void }) {
  const items: { id: Tab; label: string; icon: typeof Plug }[] = [
    { id: "integraciones", label: "Integraciones", icon: Plug },
    { id: "usuarios", label: "Usuarios", icon: Users },
    { id: "roles", label: "Roles", icon: Shield },
  ];
  return (
    <div className="flex shrink-0 gap-1 border-b border-slate-200 bg-white px-4 pt-3">
      {items.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          onClick={() => onTab(id)}
          className={`inline-flex items-center gap-1.5 rounded-t-md px-4 py-2 text-[12px] font-semibold transition-colors ${
            tab === id
              ? "border border-b-0 border-slate-200 bg-slate-50 text-blue-700"
              : "text-slate-500 hover:text-slate-800"
          }`}
        >
          <Icon size={15} />
          {label}
        </button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ provider modal
function ProviderModal({
  proveedor,
  onClose,
  onGuardado,
}: {
  proveedor: Proveedor;
  onClose: () => void;
  onGuardado: () => void;
}) {
  const [valores, setValores] = useState<Record<string, string>>(() =>
    Object.fromEntries(proveedor.campos.map((c) => [c.clave, c.valor ?? ""])),
  );
  const [actividades, setActividades] = useState<Actividad[]>([]);
  const [nueva, setNueva] = useState({ nombre: "", referencia: "" });
  const [guardando, setGuardando] = useState(false);

  const cargarAct = useCallback(async () => {
    const r = await fetch(REST_CONFIG_ACTIVIDADES(proveedor.codigo), { headers: headers() });
    const d = await r.json();
    setActividades(d.actividades ?? []);
  }, [proveedor.codigo]);

  useEffect(() => {
    cargarAct();
  }, [cargarAct]);

  const guardarValores = async () => {
    setGuardando(true);
    try {
      await fetch(REST_CONFIG_VALORES(proveedor.codigo), {
        method: "PUT",
        headers: { ...headers(), "Content-Type": "application/json" },
        body: JSON.stringify(valores),
      });
      onGuardado();
    } finally {
      setGuardando(false);
    }
  };

  const crearActividad = async () => {
    if (!nueva.nombre || !nueva.referencia) return;
    await fetch(REST_CONFIG_ACTIVIDADES(proveedor.codigo), {
      method: "POST",
      headers: { ...headers(), "Content-Type": "application/json" },
      body: JSON.stringify(nueva),
    });
    setNueva({ nombre: "", referencia: "" });
    cargarAct();
  };

  const esTelemetria = proveedor.categoria === "telemetria";

  return (
    <Modal open onClose={onClose} titulo={proveedor.nombre} ancho="max-w-3xl">
      <div className="space-y-5">
        {proveedor.campos.length === 0 ? (
          <p className="text-[12px] text-slate-500">Este proveedor aún no tiene campos definidos.</p>
        ) : (
          <div>
            <h4 className="mb-2 text-[11px] font-bold uppercase tracking-wide text-slate-400">
              Credenciales
            </h4>
            <div className="grid grid-cols-2 gap-3">
              {proveedor.campos.map((c) => (
                <label key={c.id} className="block">
                  <span className="mb-1 block text-[11px] font-semibold text-slate-600">
                    {c.etiqueta}
                    {c.requerido && <span className="text-red-500"> *</span>}
                  </span>
                  <input
                    type={c.tipo === "password" ? "password" : "text"}
                    value={valores[c.clave] ?? ""}
                    placeholder={c.tipo === "password" ? "vacío = no cambiar" : undefined}
                    onChange={(e) => setValores((v) => ({ ...v, [c.clave]: e.target.value }))}
                    className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-[12px] focus:border-blue-500 focus:outline-none"
                  />
                </label>
              ))}
            </div>
            <button
              type="button"
              onClick={guardarValores}
              disabled={guardando}
              className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
            >
              <Save size={14} /> {guardando ? "Guardando…" : "Guardar credenciales"}
            </button>
          </div>
        )}

        {esTelemetria && (
          <div className="border-t border-slate-200 pt-4">
            <h4 className="mb-2 text-[11px] font-bold uppercase tracking-wide text-slate-400">
              Tipos de actividad · nombre ↔ referencia
            </h4>
            <div className="space-y-1.5">
              {actividades.map((a) => (
                <ActividadRow key={a.id} codigo={proveedor.codigo} act={a} onCambio={cargarAct} />
              ))}
              {actividades.length === 0 && (
                <p className="text-[12px] text-slate-400">Sin actividades definidas.</p>
              )}
            </div>
            <div className="mt-3 flex items-end gap-2 rounded-md bg-slate-50 p-2">
              <label className="flex-1">
                <span className="mb-1 block text-[11px] font-semibold text-slate-600">Nombre</span>
                <input
                  value={nueva.nombre}
                  onChange={(e) => setNueva((n) => ({ ...n, nombre: e.target.value }))}
                  placeholder="CARGA"
                  className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-[12px] focus:border-blue-500 focus:outline-none"
                />
              </label>
              <label className="flex-1">
                <span className="mb-1 block text-[11px] font-semibold text-slate-600">Referencia</span>
                <input
                  value={nueva.referencia}
                  onChange={(e) => setNueva((n) => ({ ...n, referencia: e.target.value }))}
                  placeholder="040"
                  className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-[12px] focus:border-blue-500 focus:outline-none"
                />
              </label>
              <button
                type="button"
                onClick={crearActividad}
                disabled={!nueva.nombre || !nueva.referencia}
                className="inline-flex items-center gap-1 rounded-md bg-slate-700 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-slate-800 disabled:opacity-40"
              >
                <Plus size={14} /> Añadir
              </button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}

function ActividadRow({
  codigo,
  act,
  onCambio,
}: {
  codigo: string;
  act: Actividad;
  onCambio: () => void;
}) {
  const [nombre, setNombre] = useState(act.nombre);
  const [referencia, setReferencia] = useState(act.referencia);

  const guardar = async () => {
    await fetch(REST_CONFIG_ACTIVIDAD(codigo, act.id), {
      method: "PUT",
      headers: { ...headers(), "Content-Type": "application/json" },
      body: JSON.stringify({ nombre, referencia }),
    });
    onCambio();
  };

  const borrar = async () => {
    if (!window.confirm(`¿Borrar la actividad "${act.nombre}"?`)) return;
    await fetch(REST_CONFIG_ACTIVIDAD(codigo, act.id), { method: "DELETE", headers: headers() });
    onCambio();
  };

  return (
    <div className="flex items-center gap-2">
      <input
        value={nombre}
        onChange={(e) => setNombre(e.target.value)}
        className="flex-1 rounded-md border border-slate-300 px-2.5 py-1.5 text-[12px] focus:border-blue-500 focus:outline-none"
      />
      <span className="text-slate-300">↔</span>
      <input
        value={referencia}
        onChange={(e) => setReferencia(e.target.value)}
        className="flex-1 rounded-md border border-slate-300 px-2.5 py-1.5 text-[12px] focus:border-blue-500 focus:outline-none"
      />
      <button
        type="button"
        onClick={guardar}
        title="Guardar"
        className="rounded-md p-1.5 text-blue-600 hover:bg-blue-50"
      >
        <Save size={15} />
      </button>
      <button
        type="button"
        onClick={borrar}
        title="Borrar"
        className="rounded-md p-1.5 text-red-500 hover:bg-red-50"
      >
        <Trash2 size={15} />
      </button>
    </div>
  );
}

// ------------------------------------------------------------------ usuario modal
function UsuarioModal({
  usuario,
  roles,
  onClose,
  onGuardado,
}: {
  usuario: Usuario | null;
  roles: Rol[];
  onClose: () => void;
  onGuardado: () => void;
}) {
  const esNuevo = usuario === null;
  const [form, setForm] = useState({
    usuario: usuario?.usuario ?? "",
    password: "",
    rol: usuario?.rol ?? roles[0]?.nombre ?? "",
    nombre: usuario?.nombre ?? "",
    activo: usuario?.activo ?? true,
  });
  const [guardando, setGuardando] = useState(false);

  const guardar = async () => {
    setGuardando(true);
    try {
      if (esNuevo) {
        await fetch(REST_CONFIG_USUARIOS, {
          method: "POST",
          headers: { ...headers(), "Content-Type": "application/json" },
          body: JSON.stringify(form),
        });
      } else {
        const body: Record<string, unknown> = {
          rol: form.rol,
          nombre: form.nombre,
          activo: form.activo,
        };
        if (form.password) body.password = form.password;
        await fetch(REST_CONFIG_USUARIO(usuario!.id), {
          method: "PUT",
          headers: { ...headers(), "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      }
      onGuardado();
      onClose();
    } finally {
      setGuardando(false);
    }
  };

  return (
    <Modal open onClose={onClose} titulo={esNuevo ? "Nuevo usuario" : "Editar usuario"} ancho="max-w-md">
      <div className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-[11px] font-semibold text-slate-600">Usuario</span>
          <input
            value={form.usuario}
            disabled={!esNuevo}
            onChange={(e) => setForm((f) => ({ ...f, usuario: e.target.value }))}
            className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-[12px] focus:border-blue-500 focus:outline-none disabled:bg-slate-100"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] font-semibold text-slate-600">
            Contraseña {esNuevo ? "" : "(dejar vacío para no cambiarla)"}
          </span>
          <input
            type="password"
            value={form.password}
            onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
            className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-[12px] focus:border-blue-500 focus:outline-none"
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] font-semibold text-slate-600">Rol</span>
          <select
            value={form.rol}
            onChange={(e) => setForm((f) => ({ ...f, rol: e.target.value }))}
            className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-[12px] focus:border-blue-500 focus:outline-none"
          >
            {roles.map((r) => (
              <option key={r.id} value={r.nombre}>
                {r.nombre}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] font-semibold text-slate-600">Nombre completo</span>
          <input
            value={form.nombre}
            onChange={(e) => setForm((f) => ({ ...f, nombre: e.target.value }))}
            className="w-full rounded-md border border-slate-300 px-2.5 py-1.5 text-[12px] focus:border-blue-500 focus:outline-none"
          />
        </label>
        {!esNuevo && (
          <label className="flex items-center gap-2 text-[12px] text-slate-700">
            <input
              type="checkbox"
              checked={form.activo}
              onChange={(e) => setForm((f) => ({ ...f, activo: e.target.checked }))}
              className="h-4 w-4 accent-blue-600"
            />
            Activo
          </label>
        )}
        <button
          type="button"
          onClick={guardar}
          disabled={guardando || !form.usuario || (esNuevo && !form.password)}
          className="inline-flex w-full items-center justify-center gap-1.5 rounded-md bg-blue-600 px-3 py-2 text-[12px] font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          <Save size={14} /> {guardando ? "Guardando…" : "Guardar"}
        </button>
      </div>
    </Modal>
  );
}
