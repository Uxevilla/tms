#!/usr/bin/env python3
import urllib.request, base64

USER = "[REDACTADO]"
PASS = "[REDACTADO]"
CUSTOMER = "[REDACTADO]"
AUTH = 'Basic ' + base64.b64encode(f"{USER}:{PASS}".encode()).decode()

def call(endpoint, body):
    xml = ('<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
           'xmlns:ser="http://fleetworks.acunia.com/fleet/service">'
           '<soapenv:Header/><soapenv:Body>' + body + '</soapenv:Body></soapenv:Envelope>')
    req = urllib.request.Request(endpoint, data=xml.encode('utf-8'))
    req.add_header('Content-Type', 'text/xml; charset=utf-8')
    req.add_header('SOAPAction', '')
    req.add_header('Authorization', AUTH)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')

# 1) Poll traces from Tracking service to see real ATY values
print("=== pollTraces (Tracking) ===")
s, r = call("https://soap.box.trimbletl.com/fleet-service/Tracking",
            f'<ser:pollTraces><customer>{CUSTOMER}</customer><mark>2020-01-01T00:00:00.000</mark></ser:pollTraces>')
print("HTTP", s)
print(r[:2000])
print()

# 2) Test createTrips with activity.type = NAME (CARGA / DESCARGA)
print("=== createTrips with type=NAME ===")
body = f'''<ser:createTrips><customer>{CUSTOMER}</customer><tripData>
<id>TRP-NAMETEST</id><name>Name test</name><description>t</description>
<task><id>TRP-NAMETEST_T1</id><name>Recogida</name><description>t</description>
<activity><id>TRP-NAMETEST_T1_1</id><type>CARGA</type><description>carga</description></activity></task>
<task><id>TRP-NAMETEST_T2</id><name>Entrega</name><description>t</description>
<activity><id>TRP-NAMETEST_T2_1</id><type>DESCARGA</type><description>descarga</description></activity></task>
</tripData></ser:createTrips>'''
s, r = call("https://soap.box.trimbletl.com/fleet-service/Planning", body)
print("HTTP", s)
print(r[:2000])
