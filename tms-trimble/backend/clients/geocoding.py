"""Geocodificación: Nominatim (inversa) + Photon (búsqueda)."""
import json
import urllib.parse
import urllib.request


def _buscar_photon(q, limit=5):
    """Geocodifica con Photon (OpenStreetMap) — gratis, sin API key."""
    url = "https://photon.komoot.io/api/?q=" + urllib.parse.quote(q) + f"&limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return []
    out = []
    for f in data.get("features", []):
        p = f.get("properties", {}) or {}
        coords = (f.get("geometry", {}) or {}).get("coordinates") or [None, None]  # [lng, lat]
        out.append({
            "id": None, "tipo": "externo",
            "nombre": p.get("name") or "",
            "empresa": p.get("name") or "",
            "calle": " ".join(x for x in [p.get("street"), p.get("housenumber")] if x),
            "numero": p.get("housenumber") or "",
            "ciudad": p.get("city") or "",
            "cp": p.get("postcode") or "",
            "pais": (p.get("countrycode") or "ES").upper(),
            "lat": coords[1], "lng": coords[0],
        })
    return out


def reverse_geocode(lat: float = 0, lng: float = 0):
    """Geocodificación inversa vía Nominatim: lat/lng -> dirección."""
    if not lat or not lng:
        return {"resultado": None}
    url = (
        "https://nominatim.openstreetmap.org/reverse?"
        + urllib.parse.urlencode({
            "lat": lat, "lon": lng, "format": "json", "addressdetails": 1,
            "accept-language": "es", "zoom": 18,
        })
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "tms-trimble/0.1 (contacto: uxevilla@gmail.com)",
        "Accept-Language": "es",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"resultado": None, "error": "No se pudo consultar el geocodificador inverso."}
    a = data.get("address", {}) or {}
    return {"resultado": {
        "display_name": data.get("display_name", ""),
        "lat": lat, "lng": lng,
        "nombre": a.get("name") or a.get("amenity") or a.get("shop") or a.get("tourism") or a.get("building") or "",
        "calle": a.get("road") or a.get("pedestrian") or "",
        "numero": a.get("house_number", ""),
        "ciudad": a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or a.get("county") or "",
        "cp": a.get("postcode", ""),
        "pais": (a.get("country_code") or "").upper(),
    }}
