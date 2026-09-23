"""Cliente REST de TransFollow eCMR (Freight Documents). Solo stdlib.

Auth simple por tenant: cabecera `X-API-Key`. La clave vive en la tabla `config`
del cliente (`transfollow_api_key`); el base URL en `transfollow_base_url`.

Referencia de la API: skill `transfollow-ecmr-api`.
"""
import json
import urllib.error
import urllib.request

PROD_BASE_URL = "https://api.transfollow.com/api/"
PARTNER_BASE_URL = "https://partner.transfollow.com/api/"


class TransFollowError(RuntimeError):
    """Error de la API de TransFollow (400/401/403/404/409...)."""

    def __init__(self, status, code, description, field=None):
        self.status = status
        self.code = code
        self.description = description
        self.field = field
        msg = f"TransFollow {status}: {code} — {description}" + (f" (campo {field})" if field else "")
        super().__init__(msg)


class TransFollowClient:
    def __init__(self, api_key: str, base_url: str = PROD_BASE_URL):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/") + "/"

    def _request(self, method: str, path: str, payload: dict | None = None,
                 accept: str = "application/json") -> dict:
        url = self._base_url + path.lstrip("/")
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("X-API-Key", self._api_key)
        req.add_header("Accept", accept)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read()
                if "pdf" in (r.headers.get("Content-Type") or ""):
                    return {"ok": True, "status": r.status, "pdf": body}
                return {"ok": True, "status": r.status,
                        "json": json.loads(body.decode("utf-8")) if body else {}}
        except urllib.error.HTTPError as e:
            return self._parse_error(e.code, e.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "status": 0, "error": str(e)}

    @staticmethod
    def _parse_error(status: int, body: str) -> dict:
        try:
            d = json.loads(body)
            first = (d.get("errors") or [{}])[0]
            return {"ok": False, "status": status,
                    "code": first.get("code"), "description": first.get("description"),
                    "field": first.get("field")}
        except Exception:  # noqa: BLE001
            return {"ok": False, "status": status, "error": body[:300]}

    # ------------------------------------------------------------------ #
    # Freight Documents
    # ------------------------------------------------------------------ #
    def create_freight_document(self, payload: dict) -> dict:
        """Crea un Freight Document (WAYBILL). Devuelve {ok, freightDocumentId}."""
        r = self._request("POST", "freightdocuments", payload)
        if not r.get("ok"):
            return r
        return {"ok": True, "status": r["status"],
                "freightDocumentId": (r.get("json") or {}).get("freightDocumentId")}

    def get_freight_document(self, fd_id, as_pdf: bool = False) -> dict:
        accept = "application/pdf" if as_pdf else "application/json"
        return self._request("GET", f"freightdocuments/{fd_id}", accept=accept)

    def issue_freight_document(self, fd_id) -> dict:
        """Publica el FD a todas las partes."""
        return self._request("POST", f"freightdocuments/{fd_id}/issue")

    def cancel_freight_document(self, fd_id) -> dict:
        return self._request("POST", f"freightdocuments/{fd_id}/cancel")


def build_waybill(trip: dict, *, carrier_email: str, consignor: dict, consignee: dict,
                  goods: list, configuration: dict | None = None) -> dict:
    """Construye el payload de un Freight Document (type=WAYBILL) desde datos del TMS.

    ATENCIÓN: la estructura/nombres exactos de `POST /freightdocuments` deben
    contrastarse con la referencia vigente de TransFollow. Los nombres de campo
    de primer nivel (configuration, carrier, consignor, consignee,
    placesOfTakingOver/Delivery, structuredGoods, transportConditions) siguen la
    skill; las subestructuras son aproximadas y se ajustan aquí.
    """
    return {
        "type": "WAYBILL",
        "status": "ISSUED",
        "configuration": configuration or {},
        "carrier": {"email": carrier_email},
        "consignor": {"email": consignor.get("email", ""), "name": consignor.get("nombre", "")},
        "consignee": {"email": consignee.get("email", ""), "name": consignee.get("nombre", "")},
        "placesOfTakingOver": [{"name": trip.get("origen", "")}],
        "placesOfDelivery": [{"name": trip.get("destino", "")}],
        "structuredGoods": goods,
        "transportConditions": {},
    }
