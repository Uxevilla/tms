// CellRenderer genérico para las tablas antiguas: pinta la celda con un atributo
// [data-panel="tipo:id"]; el AppShell captura el clic (delegación) y abre el panel.
export function panelCell(tipo: string, getId?: (data: any) => string | number | undefined) {
  return (params: { value?: unknown; data?: any }) => {
    const val = params.value;
    const id = getId ? getId(params.data) : val;
    const txt = val == null || val === "" ? "" : String(val);
    if (id == null || id === "") return <span>{txt}</span>;
    return (
      <span data-panel={`${tipo}:${id}`} className="cursor-pointer text-primary hover:underline">
        {txt}
      </span>
    );
  };
}
