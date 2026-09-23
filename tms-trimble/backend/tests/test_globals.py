"""Detecta funciones que usan nombres no definidos (NameError latentes tras refactors)."""
import builtins
import dis
import os
import sys
import types

import main  # noqa: F401  (carga todos los routers y servicios)


def test_sin_nombres_indefinidos():
    base = os.path.dirname(os.path.dirname(__file__))
    faltan = set()
    for m in list(sys.modules.values()):
        f = getattr(m, "__file__", "") or ""
        if not f.startswith(base) or "/tests/" in f or ".venv" in f or "site-packages" in f:
            continue

        def walk(code, q):
            for i in dis.get_instructions(code):
                if i.opname == "LOAD_GLOBAL":
                    n = str(i.argval)
                    if n not in m.__dict__ and not hasattr(builtins, n):
                        faltan.add(f"{os.path.relpath(f, base)}:{q} -> {n}")
            for c in code.co_consts:
                if isinstance(c, types.CodeType):
                    walk(c, f"{q}.{c.co_name}")

        for k, v in vars(m).items():
            v = getattr(v, "__wrapped__", v)
            if isinstance(v, types.FunctionType) and v.__module__ == m.__name__:
                walk(v.__code__, k)
    assert not faltan, "\n".join(sorted(faltan))
