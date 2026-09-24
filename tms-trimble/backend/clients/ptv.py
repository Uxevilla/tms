"""Ruteo: PTV (ruta completa con tráfico/peaje) + OSRM (distancia por carretera)."""
import datetime
import json
import math
import urllib.request

import config
from core import _is_toll_step
from db import _db, _tenant_ctx, _valores_proveedor


def _ptv_api_key() -> str:
    """API key de PTV del tenant actual (fallback a .env para el caso sin tenant)."""
    t = _tenant_ctx.get()
    if not t:
        return config.DEFAULT_PTV_API_KEY
    with _db() as conn:
        return _valores_proveedor(conn, "ptv").get("api_key", "") or config.DEFAULT_PTV_API_KEY


def _ptv_route(puntos, veh, conduccion_acumulada_min=0.0):
    """Ruta completa vía PTV: distancia, tiempo, tráfico, peaje, polyline y eventos de tráfico.

    conduccion_acumulada_min: minutos de conducción ya realizados desde la última pausa
    (del tacógrafo); alimenta el workLogbook para que PTV aplique la pausa restante correcta.

    Devuelve dict {'distance_km', 'travel_time_min', 'traffic_delay_min', 'toll',
    'currency', 'polyline', 'traffic_events', 'schedule'} o None si falla.
    """
    if not _ptv_api_key() or len(puntos) < 2:
        return None
    waypoints = [{"onRoad": {"latitude": p["lat"], "longitude": p["lng"]}} for p in puntos]
    profile = veh.get("ptv_profile") or "EUR_TRAILER_TRUCK"
    url = f"{config.PTV_BASE_URL}/routes?profile={profile}&results=TOLL_COSTS,MONETARY_COSTS,POLYLINE,TRAFFIC_EVENTS,SCHEDULE_EVENTS,SCHEDULE_REPORT"
    url += "&options%5BtrafficMode%5D=REALISTIC&options%5BpolylineFormat%5D=GOOGLE_ENCODED_POLYLINE"
    extra = {}
    if veh.get("ejes"):
        extra["numberOfAxles"] = int(veh["ejes"])
    if veh.get("mma"):
        extra["totalPermittedWeight"] = int(veh["mma"])
    if veh.get("clase_euro"):
        extra["emissionStandard"] = veh["clase_euro"]
    if extra:
        url += "&" + "&".join(f"vehicle%5B{k}%5D={v}" for k, v in extra.items())
    body = {"waypoints": waypoints}
    driver = {"workingHoursPreset": "EU_DRIVING_TIME_REGULATION_FOR_MULTIPLE_DAYS"}
    if conduccion_acumulada_min and conduccion_acumulada_min > 0:
        driver["workLogbook"] = {
            "lastTimeTheDriverWorked": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "accumulatedDrivingTimeSinceLastBreak": int(conduccion_acumulada_min * 60),
        }
    body["driver"] = driver
    try:
        req = urllib.request.Request(
            url, method="POST",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "apiKey": _ptv_api_key()},
        )
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read().decode())
        prices = data.get("toll", {}).get("costs", {}).get("prices", [])
        toll = round(float(prices[0]["price"]), 2) if prices and prices[0].get("price") is not None else None
        traffic_events = []
        for ev in data.get("events", []) or []:
            t = ev.get("traffic")
            if not t:
                continue
            traffic_events.append({
                "lat": ev.get("latitude"),
                "lng": ev.get("longitude"),
                "delay": t.get("delay"),
                "accessType": t.get("accessType"),
                "description": t.get("description", ""),
            })
        sr = data.get("scheduleReport") or {}
        breaks = []
        for ev in data.get("events", []) or []:
            sch = ev.get("schedule") or {}
            types = sch.get("scheduleTypes") or []
            if "BREAK" in types or "DAILY_REST" in types:
                breaks.append({
                    "type": "BREAK" if "BREAK" in types else "DAILY_REST",
                    "duration_min": round(sch.get("duration", 0) / 60, 1),
                    "at": ev.get("startsAt"),
                })
        driving_min = round(sr.get("drivingTime", 0) / 60, 1)
        break_min = round(sr.get("breakTime", 0) / 60, 1)
        rest_min = round(sr.get("restTime", 0) / 60, 1)
        return {
            "distance_km": round(data.get("distance", 0) / 1000, 1),
            "travel_time_min": round(data.get("travelTime", 0) / 60, 1),
            "traffic_delay_min": round(data.get("trafficDelay", 0) / 60, 1),
            "toll": toll,
            "currency": (prices[0].get("currency", "EUR") if prices else "EUR"),
            "polyline": data.get("polyline", ""),
            "traffic_events": traffic_events,
            "schedule": {
                "driving_min": driving_min,
                "break_min": break_min,
                "rest_min": rest_min,
                "total_min": round(driving_min + break_min + rest_min, 1),
                "end_time": sr.get("endTime"),
                "breaks": breaks,
            },
        }
    except Exception:
        return None


def _haversine_km(lat1, lng1, lat2, lng2):
    """Distancia en línea recta (km) entre dos coordenadas."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _calc_ruta(puntos):
    """Distancia (km) entre puntos consecutivos por carretera (OSRM) con fallback línea recta.

    Devuelve (tramos, total_km, metodo, total_toll_km) donde metodo es
    'carretera' | 'linea_recta' | 'sin_ruta' y cada tramo lleva 'km' y 'toll_km'.
    """
    if len(puntos) < 2:
        return [], 0.0, "sin_ruta", 0.0
    coords = ";".join(f"{p['lng']},{p['lat']}" for p in puntos)
    url = f"https://router.project-osrm.org/route/v1/driving/{coords}?overview=false&steps=true"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "TMS-Trimble/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
        if data.get("code") == "Ok" and data.get("routes"):
            legs = data["routes"][0].get("legs", [])
            tramos, total, total_toll = [], 0.0, 0.0
            for i, leg in enumerate(legs):
                km = round(leg["distance"] / 1000, 1)
                toll_m = sum(s.get("distance", 0) for s in leg.get("steps", []) if _is_toll_step(s))
                toll_km = round(toll_m / 1000, 1)
                tramos.append({
                    "de": puntos[i].get("nombre") or f"Punto {i + 1}",
                    "a": puntos[i + 1].get("nombre") or f"Punto {i + 2}",
                    "km": km,
                    "toll_km": toll_km,
                })
                total += km
                total_toll += toll_km
            return tramos, round(total, 1), "carretera", round(total_toll, 1)
    except Exception:
        pass
    tramos, total = [], 0.0
    for i in range(len(puntos) - 1):
        km = round(_haversine_km(puntos[i]["lat"], puntos[i]["lng"], puntos[i + 1]["lat"], puntos[i + 1]["lng"]), 1)
        tramos.append({
            "de": puntos[i].get("nombre") or f"Punto {i + 1}",
            "a": puntos[i + 1].get("nombre") or f"Punto {i + 2}",
            "km": km,
            "toll_km": 0.0,
        })
        total += km
    return tramos, round(total, 1), "linea_recta", 0.0
