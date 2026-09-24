import os, sys, psycopg2
sys.path.insert(0, "/app/backend")
TARGET = sys.argv[1] if len(sys.argv) > 1 else "tms_mig"

raw = psycopg2.connect(host=os.environ["DB_HOST"], port=5432,
                       user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], dbname=TARGET)
raw.autocommit = True
c = raw.cursor()

def pk_name(tbl):
    c.execute("SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass AND contype='p'", (tbl,))
    r = c.fetchone()
    return r[0] if r else None

def col_exists(schema, table, col):
    c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name=%s",
              (schema, table, col))
    return c.fetchone() is not None

def add_unique(tbl, conname, cols):
    c.execute("SELECT 1 FROM pg_constraint WHERE conname=%s", (conname,))
    if not c.fetchone():
        c.execute(f"ALTER TABLE {tbl} ADD CONSTRAINT {conname} UNIQUE ({cols})")

# --- vehiculos ---
if not col_exists("flota", "vehiculos", "codigo"):
    pkn = pk_name("flota.vehiculos")
    c.execute("ALTER TABLE flota.vehiculos RENAME COLUMN id TO codigo")
    if pkn:
        c.execute("ALTER TABLE flota.vehiculos DROP CONSTRAINT %s" % pkn)
if not col_exists("flota", "vehiculos", "id"):
    c.execute("ALTER TABLE flota.vehiculos ADD COLUMN id BIGINT GENERATED ALWAYS AS IDENTITY")
if not pk_name("flota.vehiculos"):
    c.execute("ALTER TABLE flota.vehiculos ADD PRIMARY KEY (id)")
add_unique("flota.vehiculos", "uq_vehiculos_codigo", "codigo")
if not col_exists("flota", "vehiculos", "terminal_trimble"):
    c.execute("ALTER TABLE flota.vehiculos ADD COLUMN terminal_trimble TEXT")
c.execute("UPDATE flota.vehiculos SET terminal_trimble = codigo WHERE terminal_trimble IS NULL")
add_unique("flota.vehiculos", "uq_vehiculos_terminal", "terminal_trimble")
print(f"[{TARGET}] vehiculos remap OK")

# --- trips ---
if not col_exists("operaciones", "trips", "codigo"):
    c.execute("ALTER TABLE operaciones.paradas DROP CONSTRAINT IF EXISTS paradas_trip_id_fkey")
    c.execute("ALTER TABLE operaciones.tramos DROP CONSTRAINT IF EXISTS tramos_trip_id_fkey")
    pkn = pk_name("operaciones.trips")
    c.execute("ALTER TABLE operaciones.trips RENAME COLUMN id TO codigo")
    if pkn:
        c.execute("ALTER TABLE operaciones.trips DROP CONSTRAINT %s" % pkn)
if not col_exists("operaciones", "trips", "id"):
    c.execute("ALTER TABLE operaciones.trips ADD COLUMN id BIGINT GENERATED ALWAYS AS IDENTITY")
if not pk_name("operaciones.trips"):
    c.execute("ALTER TABLE operaciones.trips ADD PRIMARY KEY (id)")
add_unique("operaciones.trips", "uq_trips_codigo", "codigo")
# re-add FKs (a codigo, la columna TEXT UNIQUE)
c.execute("SELECT 1 FROM pg_constraint WHERE conname='paradas_trip_id_fkey'")
if not c.fetchone():
    c.execute("ALTER TABLE operaciones.paradas ADD CONSTRAINT paradas_trip_id_fkey FOREIGN KEY (trip_id) REFERENCES operaciones.trips(codigo) ON DELETE CASCADE")
c.execute("SELECT 1 FROM pg_constraint WHERE conname='tramos_trip_id_fkey'")
if not c.fetchone():
    c.execute("ALTER TABLE operaciones.tramos ADD CONSTRAINT tramos_trip_id_fkey FOREIGN KEY (trip_id) REFERENCES operaciones.trips(codigo) ON DELETE CASCADE")
print(f"[{TARGET}] trips remap OK")

# --- _db() recrea vistas compat ---
import db as dbm
dbm._tenant_ctx.set({"db_name": TARGET, "empresa": "eusebio", "superadmin": False})
dbm._db()
print(f"[{TARGET}] _db() OK")

# --- verificación ---
c = raw.cursor()
c.execute("SELECT count(*), count(codigo), count(terminal_trimble), count(id) FROM flota.vehiculos")
print(f"[{TARGET}] vehiculos (total,codigo,terminal,surrogate):", c.fetchone())
c.execute("SELECT count(*), count(codigo), count(id) FROM operaciones.trips")
print(f"[{TARGET}] trips (total,codigo,surrogate):", c.fetchone())
c.execute("SELECT id, codigo FROM flota.vehiculos ORDER BY id LIMIT 2")
print(f"[{TARGET}] vehiculos muestra:", c.fetchall())
c.execute("SELECT id, matricula FROM vehiculos ORDER BY 1 LIMIT 2")
print(f"[{TARGET}] vista vehiculos (id=codigo):", c.fetchall())
c.execute("SELECT id FROM trips ORDER BY 1 LIMIT 3")
print(f"[{TARGET}] vista trips (id=codigo):", c.fetchall())
c.execute("SELECT conname FROM pg_constraint WHERE conrelid='operaciones.paradas'::regclass AND contype='f'")
print(f"[{TARGET}] FKs paradas:", c.fetchall())
try:
    c.execute("INSERT INTO flota.vehiculos (codigo, categoria, matricula) VALUES ('TEST-RM','tractora','TEST-RM') ON CONFLICT (codigo) DO UPDATE SET matricula=EXCLUDED.matricula")
    c.execute("DELETE FROM flota.vehiculos WHERE codigo='TEST-RM'")
    print(f"[{TARGET}] upsert vehiculos por codigo: OK")
except Exception as e:
    print(f"[{TARGET}] upsert vehiculos ERROR: {e}")
print("DONE")
