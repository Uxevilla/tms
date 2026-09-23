"""Configuración del TMS desde variables de entorno (con soporte de .env local)."""
import os


def _load_dotenv(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v


_load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))


# ---------------------------------------------------------------------- #
# Infraestructura (común a todos los clientes)
# ---------------------------------------------------------------------- #
HOST = os.environ.get("TMS_HOST", "0.0.0.0")
PORT = int(os.environ.get("TMS_PORT", "8750"))

DB_HOST = os.environ.get("DB_HOST", "timescaledb")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_USER = os.environ.get("DB_USER", "tms")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
# Base de datos por defecto (la del primer cliente registrado)
DB_NAME = os.environ.get("DB_NAME", "tms")

# Base de datos maestra: registro central de empresas (multi-tenant)
MASTER_DB_NAME = os.environ.get("MASTER_DB_NAME", "tms_master")

# Firma HMAC de los tokens de sesión y credenciales del super-admin
SECRET_KEY = os.environ.get("TMS_SECRET_KEY", "")
SUPERADMIN_USER = os.environ.get("SUPERADMIN_USER", "admin")
SUPERADMIN_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD", "")


# ---------------------------------------------------------------------- #
# Valores POR DEFECTO para el primer cliente.
# Se copian a la tabla `config` de su base de datos en el arranque si no existen;
# a partir de ahí cada cliente los edita desde su pantalla de Configuración.
# ---------------------------------------------------------------------- #
DEFAULT_TRIMBLE_USERNAME = os.environ.get("TRIMBLE_USERNAME", "")
DEFAULT_TRIMBLE_PASSWORD = os.environ.get("TRIMBLE_PASSWORD", "")
DEFAULT_TRIMBLE_CUSTOMER = os.environ.get("TRIMBLE_CUSTOMER", "")
DEFAULT_TRIMBLE_TERMINAL = os.environ.get("TRIMBLE_TERMINAL", "APP_EUSEBIO")

DEFAULT_PTV_API_KEY = os.environ.get("PTV_API_KEY", "")
PTV_BASE_URL = os.environ.get("PTV_BASE_URL", "https://api.myptv.com/routing/v1")

DEFAULT_TRANSFOLLOW_API_KEY = os.environ.get("TRANSFOLLOW_API_KEY", "")
DEFAULT_TRANSFOLLOW_BASE_URL = os.environ.get("TRANSFOLLOW_BASE_URL", "https://api.transfollow.com/api/")

DEFAULT_AUTH_USERS = [u.strip() for u in os.environ.get("TMS_AUTH_USERS", "").split(",") if u.strip()]
DEFAULT_AUTH_PASSWORD = os.environ.get("TMS_AUTH_PASSWORD", "")

# Nombres de clave en la tabla `config` de cada cliente para las integraciones
TRIMBLE_KEYS = ("trimble_username", "trimble_password", "trimble_customer", "trimble_terminal")
PTV_KEYS = ("ptv_api_key",)
SMTP_KEYS = ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from")
TRANSFOLLOW_KEYS = ("transfollow_api_key", "transfollow_base_url")
AUTH_KEYS = ("auth_users", "auth_password")
