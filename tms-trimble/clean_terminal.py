"""Limpia el terminal APP_EUSEBIO: removeTrips de todos los viajes y verifica."""
import re
import soap_client as sc

c = sc.TrimbleClient("[REDACTADO]", "[REDACTADO]", "[REDACTADO]", "APP_EUSEBIO")


def trips_en_terminal():
    r = c.query_terminal("APP_EUSEBIO")
    tasks = re.findall(r"<id>([^<]+)</id>", r["body"])
    # id de tarea = <tripId>_T<nn> -> derivar tripId único
    trips = sorted({re.sub(r"_T\d+$", "", t) for t in tasks})
    return r["status"], tasks, trips


status, tasks, trips = trips_en_terminal()
print(f"Estado queryTerminal: {status} | tareas: {len(tasks)} | viajes: {len(trips)}")
print("Viajes:", trips)

if not trips:
    print("El terminal ya está vacío.")
else:
    body = (
        "<ser:removeTrips>"
        f"<customer>{c.customer}</customer>"
        + "".join(f"<tripId>{t}</tripId>" for t in trips)
        + "</ser:removeTrips>"
    )
    resp = c._call(sc.PLANNING_URL, body)
    fault = sc.TrimbleClient._fault(resp)
    print(f"\nremoveTrips -> status {resp['status']} | fault: {fault}")
    if not fault:
        print(resp["body"][:300])

    print("\n--- Verificación tras removeTrips ---")
    status2, tasks2, trips2 = trips_en_terminal()
    print(f"tareas restantes: {len(tasks2)} | viajes restantes: {len(trips2)}")
    if tasks2:
        print("Quedan:", tasks2)
    else:
        print("✅ Terminal APP_EUSEBIO limpio.")
