"""Cliente SOAP de Trimble FleetWorks (estilo document/literal). Solo stdlib.

Validado contra el entorno de formacion 'PTX ES Formacion':
- activity.type usa el NOMBRE del tipo de actividad (p.ej. 'CARGA', 'DESCARGA'),
  no la referencia numerica.
"""
import base64
import os
import re
import time
import urllib.error
import urllib.request
from xml.sax.saxutils import escape

PLANNING_URL = "https://soap.box.trimbletl.com/fleet-service/Planning"
CUSTOMER_URL = "https://soap.box.trimbletl.com/fleet-service/Customer"
TRACKING_URL = "https://soap.box.trimbletl.com/fleet-service/Tracking"
DOCUMENT_URL = "https://soap.box.trimbletl.com/fleet-service/Document"
FILES_URL = "https://soap.box.trimbletl.com/fleet-service/Files"
DRIVER_ACTIVITY_URL = "https://soap.box.trimbletl.com/fleet-service/DriverActivity"
MESSAGING_URL = "https://soap.box.trimbletl.com/fleet-service/Messaging"


def _envelope(body: str) -> str:
    return (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ser="http://fleetworks.acunia.com/fleet/service">'
        "<soapenv:Header/><soapenv:Body>" + body + "</soapenv:Body></soapenv:Envelope>"
    )


# ---- Modo falso de Trimble (TMS_TRIMBLE_FAKE=1) para CI y e2e ----
# Registra cada operación SOAP (create/assign/deploy/unassign/remove) y responde OK,
# o fault si algún id de viaje empieza por "E2E-FAIL". Sin red ni credenciales.
_FAKE_CALLS: list = []


def _trimble_fake() -> bool:
    return os.environ.get("TMS_TRIMBLE_FAKE") == "1"


def _fake_calls() -> list:
    return list(_FAKE_CALLS)


def _fake_reset() -> None:
    _FAKE_CALLS.clear()


class TrimbleClient:
    """Cliente fino para los servicios Planning/Customer/Tracking de FleetWorks."""

    def __init__(self, username: str, password: str, customer: str, terminal: str):
        self._auth = "Basic " + base64.b64encode(
            f"{username}:{password}".encode()
        ).decode()
        self.customer = customer
        self.terminal = terminal

    # ------------------------------------------------------------------ #
    # Transporte
    # ------------------------------------------------------------------ #
    def _call(self, url: str, body: str) -> dict:
        if _trimble_fake():
            op_m = re.search(r"<ser:(\w+)>", body)
            op = op_m.group(1) if op_m else "?"
            ids = [i for i in re.findall(r"<tripIds?>(.*?)</tripIds?>", body) if i]
            # Solo el ciclo de vida del viaje interesa a los e2e; el polling de fondo
            # (pollTraces/pollFiles/pollMessages/findAll*) es ruido y no se registra.
            if op in ("createTrips", "assignTrips", "deployTrips", "unAssignTrips", "removeTrips"):
                _FAKE_CALLS.append({"op": op, "ids": ids})
            for i in ids:
                if i.startswith("E2E-FAIL"):
                    return {"ok": False, "status": 500,
                            "body": f"<faultstring>E2E fake fault: {i}</faultstring>"}
            return {"ok": True, "status": 200, "body": "<return/>"}
        xml = _envelope(body).encode("utf-8")
        req = urllib.request.Request(url, data=xml)
        req.add_header("Content-Type", "text/xml; charset=utf-8")
        req.add_header("SOAPAction", "")
        req.add_header("Authorization", self._auth)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return {"ok": True, "status": r.status,
                        "body": r.read().decode("utf-8", "replace")}
        except urllib.error.HTTPError as e:
            return {"ok": False, "status": e.code,
                    "body": e.read().decode("utf-8", "replace")}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "status": 0, "body": str(e)}

    @staticmethod
    def _fault(resp: dict):
        m = re.search(r"<faultstring>([^<]*)</faultstring>", resp.get("body", ""))
        return m.group(1) if m else None

    # ------------------------------------------------------------------ #
    # Construcción de XML de tareas / viajes
    # ------------------------------------------------------------------ #
    @staticmethod
    def _contact_xml(contact: dict) -> str:
        if not contact:
            return ""
        def esc(v):
            return escape(str(v)) if v is not None else ""
        xml = (
            "<contact>"
            f"<name>{esc(contact.get('nombre'))}</name>"
            f"<company>{esc(contact.get('empresa'))}</company>"
            "<address>"
            f"<street>{esc(contact.get('calle'))}</street>"
            f"<number>{esc(contact.get('numero'))}</number>"
            f"<city>{esc(contact.get('ciudad'))}</city>"
            f"<zipcode>{esc(contact.get('cp'))}</zipcode>"
            f"<country>{esc(contact.get('pais'))}</country>"
            "</address>"
        )
        lat = contact.get("lat")
        lng = contact.get("lng")
        if lat is not None and lng is not None and str(lat).strip() != "" and str(lng).strip() != "":
            xml += (
                "<coordinate>"
                f"<latitude>{esc(lat)}</latitude>"
                f"<longitude>{esc(lng)}</longitude>"
                "</coordinate>"
            )
        xml += "</contact>"
        return xml

    @staticmethod
    def _task_xml(task: dict) -> str:
        t = task
        xml = f"<task><id>{escape(t['id'])}</id><name>{escape(t['nombre'])}</name>"
        if t.get("descripcion"):
            xml += f"<description>{escape(t['descripcion'])}</description>"
        xml += TrimbleClient._contact_xml(t.get("contacto") or {})
        tipo = t.get("tipo") or t["actividad"]
        docs_xml = ""
        for n, doc in enumerate(t.get("documentos") or [], start=1):
            docs_xml += (
                f"<property><key>activity.file.{n}.fileKey</key>"
                f"<value>{escape(doc['fileKey'])}</value></property>"
                f"<property><key>activity.file.{n}.fileName</key>"
                f"<value>{escape(doc['fileName'])}</value></property>"
            )
        ecmr_xml = ""
        if t.get("ecmr_provider") and t.get("ecmr_id"):
            ecmr_xml = (
                f"<property><key>cmr.provider</key><value>{escape(t['ecmr_provider'])}</value></property>"
                f"<property><key>cmr.id</key><value>{escape(t['ecmr_id'])}</value></property>"
            )
        tw_xml = ""
        if t.get("timewindowstart"):
            tw_xml += f"<property><key>timewindowstart</key><value>{escape(t['timewindowstart'])}</value></property>"
        if t.get("timewindowend"):
            tw_xml += f"<property><key>timewindowend</key><value>{escape(t['timewindowend'])}</value></property>"
        routing_xml = ""
        if t.get("routing"):
            # Propiedades de navegación del viaje (manual §5.3.6): se envían a CoPilot.
            for k, v in (("routing.fastest", "true"), ("routing.highway", "true"),
                         ("routing.toll", "true"), ("routing.ferry", "true")):
                routing_xml += f"<property><key>{k}</key><value>{v}</value></property>"
        via_xml = ""
        for n, wp in enumerate(t.get("waypoints") or [], start=1):
            via_xml += (
                f"<property><key>waypoint.{n}.name</key><value>{escape(wp['nombre'])}</value></property>"
                f"<property><key>waypoint.{n}.latitude</key><value>{escape(str(wp['lat']))}</value></property>"
                f"<property><key>waypoint.{n}.longitude</key><value>{escape(str(wp['lng']))}</value></property>"
            )
            if wp.get("distance"):
                via_xml += f"<property><key>waypoint.{n}.distance</key><value>{escape(str(wp['distance']))}</value></property>"
        xml += (
            f"<activity><id>{escape(t['id'])}_1</id>"
            f"<type>{escape(tipo)}</type>"
            f"<description>{escape(t.get('actividad', ''))}</description>"
            f"{docs_xml}"
            f"{ecmr_xml}"
            f"{tw_xml}"
            f"{routing_xml}"
            f"{via_xml}"
            "</activity>"
        )
        xml += "</task>"
        return xml

    # ------------------------------------------------------------------ #
    # Operaciones de planificación
    # ------------------------------------------------------------------ #
    def _trip_xml(self, trip: dict) -> str:
        tasks_xml = "".join(self._task_xml(t) for t in trip["tasks"])
        props = "".join(
            f"<property><key>{escape(k)}</key><value>{escape(v)}</value></property>"
            for k, v in (trip.get("propiedades") or {}).items()
        )
        return (
            f"<tripData><id>{escape(trip['id'])}</id><name>{escape(trip['nombre'])}</name>"
            f"<description>{escape(trip.get('descripcion', ''))}</description>"
            f"{tasks_xml}{props}</tripData>"
        )

    def create_trip(self, trip: dict) -> dict:
        body = (
            f"<ser:createTrips><customer>{escape(self.customer)}</customer>"
            f"{self._trip_xml(trip)}</ser:createTrips>"
        )
        return self._call(PLANNING_URL, body)

    def update_terminal_trips(self, trips: list, terminal: str = None) -> dict:
        """Crea + asigna + despliega viajes en UNA llamada. Manual §5.2.3/§5.3.6:
        es la operación que habilita la *trip navigation* en el dispositivo (igual que
        FleetCockpit). OJO: REEMPLAZA la lista completa de viajes del terminal."""
        term = terminal or self.terminal
        trips_xml = "".join(self._trip_xml(t) for t in trips)
        body = (
            f"<ser:updateTerminalTrips><customer>{escape(self.customer)}</customer>"
            f"<terminal>{escape(term)}</terminal>{trips_xml}</ser:updateTerminalTrips>"
        )
        return self._call(PLANNING_URL, body)

    def assign_trip(self, trip_id: str, terminal: str = None) -> dict:
        term = terminal or self.terminal
        body = (
            f"<ser:assignTrips><customer>{escape(self.customer)}</customer>"
            f"<terminal>{escape(term)}</terminal><tripIds>{escape(trip_id)}</tripIds></ser:assignTrips>"
        )
        return self._call(PLANNING_URL, body)

    def deploy_trip(self, trip_id: str) -> dict:
        body = (
            f"<ser:deployTrips><customer>{escape(self.customer)}</customer>"
            f"<tripIds>{escape(trip_id)}</tripIds></ser:deployTrips>"
        )
        return self._call(PLANNING_URL, body)

    def remove_trips(self, trip_ids) -> dict:
        """Elimina viajes del servidor (y los desvincula del terminal).

        Nota: removeTrips usa <tripId> (SINGULAR, repetible), no <tripIds>.
        """
        ids_xml = "".join(f"<tripId>{escape(t)}</tripId>" for t in trip_ids)
        body = (
            f"<ser:removeTrips><customer>{escape(self.customer)}</customer>"
            f"{ids_xml}</ser:removeTrips>"
        )
        resp = self._call(PLANNING_URL, body)
        fault = self._fault(resp)
        return {"ok": resp["ok"] and fault is None, "status": resp["status"], "fault": fault}

    def unassign_trips(self, trip_ids) -> dict:
        """Quita viajes del terminal (siguen en el servidor de Trimble para reasignarlos).

        Manual 1.6.5 §5.2: UnassignTrips desvincula del terminal sin borrar del servidor;
        a diferencia de removeTrips, el viaje queda disponible para volver a asignarlo.
        """
        ids_xml = "".join(f"<tripId>{escape(t)}</tripId>" for t in trip_ids)
        body = (
            f"<ser:unAssignTrips><customer>{escape(self.customer)}</customer>"
            f"{ids_xml}</ser:unAssignTrips>"
        )
        resp = self._call(PLANNING_URL, body)
        fault = self._fault(resp)
        return {"ok": resp["ok"] and fault is None, "status": resp["status"], "fault": fault}

    def query_terminal(self, terminal: str = None) -> dict:
        term = terminal or self.terminal
        body = (
            f"<ser:queryTerminal><customer>{escape(self.customer)}</customer>"
            f"<terminal>{escape(term)}</terminal></ser:queryTerminal>"
        )
        return self._call(PLANNING_URL, body)

    def list_units(self) -> list:
        """Devuelve la lista de unidades {id, name, enabled} del cliente."""
        body = f"<ser:findAllUnits><customer>{escape(self.customer)}</customer></ser:findAllUnits>"
        r = self._call(CUSTOMER_URL, body)
        units = []
        if r.get("ok"):
            for block in re.findall(r"<return>(.*?)</return>", r["body"], re.S):
                i = re.search(r"<id>([^<]*)</id>", block)
                n = re.search(r"<name>([^<]*)</name>", block)
                e = re.search(r"<enabled>([^<]*)</enabled>", block)
                units.append({
                    "id": i.group(1) if i else None,
                    "name": n.group(1) if n else "",
                    "enabled": (e.group(1) == "true") if e else False,
                })
        return units

    def list_drivers(self) -> list:
        """Devuelve la lista de conductores {id, firstName, lastName} del cliente."""
        body = f"<ser:findAllDrivers><customer>{escape(self.customer)}</customer></ser:findAllDrivers>"
        r = self._call(CUSTOMER_URL, body)
        drivers = []
        if r.get("ok"):
            for block in re.findall(r"<return>(.*?)</return>", r["body"], re.S):
                i = re.search(r"<id>([^<]*)</id>", block)
                fn = re.search(r"<firstName>([^<]*)</firstName>", block)
                ln = re.search(r"<lastName>([^<]*)</lastName>", block)
                drivers.append({
                    "id": i.group(1) if i else "",
                    "firstName": fn.group(1) if fn else "",
                    "lastName": ln.group(1) if ln else "",
                })
        return drivers

    # ------------------------------------------------------------------ #
    # Operaciones de documentos (DMS)
    # ------------------------------------------------------------------ #
    def create_document(self, file_name: str, file_content_b64: str,
                        file_type: str = "pdf", is_permanent: bool = True) -> dict:
        """Sube un documento (PDF) al DMS. Devuelve uniqueDocId + status."""
        body = (
            f"<ser:createDocument><customer>{escape(self.customer)}</customer>"
            f"<documentData>"
            f"<fileName>{escape(file_name)}</fileName>"
            f"<fileType>{escape(file_type)}</fileType>"
            f"<fileContent>{file_content_b64}</fileContent>"
            f"<isPermanent>{'true' if is_permanent else 'false'}</isPermanent>"
            f"</documentData></ser:createDocument>"
        )
        resp = self._call(DOCUMENT_URL, body)
        m = re.search(r"<uniqueDocId>([^<]*)</uniqueDocId>", resp.get("body", ""))
        s = re.search(r"<status>([^<]*)</status>", resp.get("body", ""))
        fault = self._fault(resp)
        return {
            "ok": resp["ok"] and fault is None,
            "status_code": resp["status"],
            "uniqueDocId": m.group(1) if m else None,
            "status": s.group(1) if s else None,
            "error": fault,
        }

    def assign_documents(self, document_ids: list, terminal_ids: list = None,
                         group_ids: list = None) -> dict:
        """Asigna documentos (por uniqueDocId) a unidades para que estén disponibles en el dispositivo.
        Manual 9.4: documentId = uniqueDocId; terminalId opcional (sin terminal ni grupo = nivel cliente)."""
        ids = "".join(f"<documentId>{escape(str(d))}</documentId>" for d in document_ids)
        grps = "".join(f"<groupId>{escape(str(g))}</groupId>" for g in (group_ids or []))
        terms = "".join(f"<terminalId>{escape(str(t))}</terminalId>" for t in (terminal_ids or []))
        body = (
            f"<ser:assignDocuments><customerNumber>{escape(self.customer)}</customerNumber>"
            f"{ids}{grps}{terms}</ser:assignDocuments>"
        )
        resp = self._call(DOCUMENT_URL, body)
        fault = self._fault(resp)
        return {"ok": resp["ok"] and fault is None, "status_code": resp["status"], "error": fault}

    # ------------------------------------------------------------------ #
    # Seguimiento (trazas) y archivos
    # ------------------------------------------------------------------ #
    def poll_traces(self, mark: str = None) -> dict:
        mk = f"<mark>{escape(mark)}</mark>" if mark else ""
        body = (f"<ser:pollTraces><customer>{escape(self.customer)}</customer>"
                f"{mk}</ser:pollTraces>")
        return self._call(TRACKING_URL, body)

    def poll_files(self, mark: str = None) -> dict:
        mk = f"<mark>{escape(mark)}</mark>" if mark else ""
        body = (f"<ser:pollFiles><customer>{escape(self.customer)}</customer>"
                f"{mk}</ser:pollFiles>")
        return self._call(FILES_URL, body)

    def download_file(self, file_name: str, file_format: str = None) -> dict:
        fmt = f"<fileFormat>{escape(file_format)}</fileFormat>" if file_format else ""
        body = (f"<ser:downloadFile><customer>{escape(self.customer)}</customer>"
                f"<fileName>{escape(file_name)}</fileName>{fmt}</ser:downloadFile>")
        return self._call(FILES_URL, body)

    def poll_driver_daily_metadata(self, mark: str = None) -> dict:
        """Descarga metadatos diarios del conductor (tiempos de conducción/descanso del tacógrafo).

        Servicio DriverActivity; requiere el rol 'integrator_driveractivity'.
        Devuelve la respuesta SOAP cruda (la parseamos en main.py).
        """
        mk = f"<mark>{escape(mark)}</mark>" if mark else ""
        body = (f"<ser:pollDriverDailyMetadata><customer>{escape(self.customer)}</customer>"
                f"{mk}</ser:pollDriverDailyMetadata>")
        return self._call(DRIVER_ACTIVITY_URL, body)

    def poll_driving_times(self, mark: str = None) -> dict:
        """Descarga los tiempos de conducción/descanso del tacógrafo (estados driving/resting/working).

        Servicio DriverActivity. Cada <drivingTimes> trae <driver>, <coordinate> y <description>
        con <type> (driving/working/resting/waiting), <startTime> y <endTime>. A partir de estos
        bloques se puede calcular la conducción acumulada desde la última pausa/descanso para
        alimentar el workLogbook de PTV.
        """
        mk = f"<mark>{escape(mark)}</mark>" if mark else ""
        body = (f"<ser:pollDrivingTimes><customer>{escape(self.customer)}</customer>"
                f"{mk}</ser:pollDrivingTimes>")
        return self._call(DRIVER_ACTIVITY_URL, body)

    # ------------------------------------------------------------------ #
    # Mensajería (mensajes del conductor / question path)
    # ------------------------------------------------------------------ #
    def poll_messages(self, mark: str = None) -> dict:
        """Mensajes libres del conductor (Messaging.pollMessages)."""
        mk = f"<mark>{escape(mark)}</mark>" if mark else ""
        body = (f"<ser:pollMessages><customer>{escape(self.customer)}</customer>"
                f"{mk}</ser:pollMessages>")
        return self._call(MESSAGING_URL, body)

    def poll_structured_messages(self, mark: str = None) -> dict:
        """Mensajes estructurados (candidato del question path) (Messaging.pollStructuredMessages)."""
        mk = f"<mark>{escape(mark)}</mark>" if mark else ""
        body = (f"<ser:pollStructuredMessages><customer>{escape(self.customer)}</customer>"
                f"{mk}</ser:pollStructuredMessages>")
        return self._call(MESSAGING_URL, body)

    def poll_message_states(self, mark: str = None) -> dict:
        """Estados de entrega/lectura de mensajes (Messaging.pollMessageStates)."""
        mk = f"<mark>{escape(mark)}</mark>" if mark else ""
        body = (f"<ser:pollMessageStates><customer>{escape(self.customer)}</customer>"
                f"{mk}</ser:pollMessageStates>")
        return self._call(MESSAGING_URL, body)

    def send_message(self, terminal_ids: str, subject: str, body: str,
                     needreply: bool = False, message_id: str = None,
                     originid: str = None) -> dict:
        """Envía un mensaje libre a uno o varios terminales (Messaging.sendMessage).

        terminal_ids: ids separados por ';' (uno o varios). originid (opcional) = id del
        mensaje al que responde, para mantener el mismo hilo de conversación.
        """
        mid = message_id or f"TMS{int(time.time() * 1000)}"
        need = "true" if needreply else "false"
        orig = f"<originid>{escape(originid)}</originid>" if originid else ""
        xml = (f"<ser:sendMessage><customer>{escape(self.customer)}</customer>"
               f"<terminalIds>{escape(terminal_ids)}</terminalIds>"
               f"<message><id>{escape(mid)}</id>"
               f"<subject>{escape(subject)}</subject>"
               f"<body>{escape(body)}</body>"
               f"{orig}"
               f"<needreply>{need}</needreply>"
               f"</message></ser:sendMessage>")
        return self._call(MESSAGING_URL, xml)

    def send_structured_message(self, terminal_ids: str, subject: str, body: str,
                                messagetype: str, needreply: bool = False,
                                message_id: str = None) -> dict:
        """Envía un mensaje estructurado (con question path) a terminales
        (Messaging.sendStructuredMessage). messagetype = tipo configurado en FleetWorks.
        """
        mid = message_id or f"TMS{int(time.time() * 1000)}"
        need = "true" if needreply else "false"
        xml = (f"<ser:sendStructuredMessage><customer>{escape(self.customer)}</customer>"
               f"<terminalIds>{escape(terminal_ids)}</terminalIds>"
               f"<structuredmessage><id>{escape(mid)}</id>"
               f"<subject>{escape(subject)}</subject>"
               f"<body>{escape(body)}</body>"
               f"<needreply>{need}</needreply>"
               f"<messagetype>{escape(messagetype)}</messagetype>"
               f"</structuredmessage></ser:sendStructuredMessage>")
        return self._call(MESSAGING_URL, xml)

    # ------------------------------------------------------------------ #
    # Orquestación de alto nivel
    # ------------------------------------------------------------------ #
    def send_trip(self, trip: dict, terminal: str = None) -> list:
        """Crea + asigna + despliega un viaje (cadena ADITIVA: no borra otros viajes del terminal).
        Se usa createTrips→assignTrips→deployTrips en vez de updateTerminalTrips porque esta última
        REEMPLAZA la lista completa de viajes del terminal (borra los viajes en cola)."""
        results = []
        steps = [
            ("create", lambda: self.create_trip(trip)),
            ("assign", lambda: self.assign_trip(trip["id"], terminal)),
            ("deploy", lambda: self.deploy_trip(trip["id"])),
        ]
        for nombre, fn in steps:
            resp = fn()
            fault = self._fault(resp)
            ok = resp["ok"] and fault is None
            results.append({
                "paso": nombre,
                "ok": ok,
                "status": resp["status"],
                "error": fault,
            })
            if not ok:
                break
        return results
