import os
import sys
from pathlib import Path

# El backend exige TMS_SECRET_KEY (>=32 chars) para arrancar; sin esto los tests no importan main.
os.environ.setdefault("TMS_SECRET_KEY", "t" * 40)

# Hace importable `main` y sus módulos hermanos (config, soap_client, ...) desde tests/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
