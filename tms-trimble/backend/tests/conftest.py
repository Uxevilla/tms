import sys
from pathlib import Path

# Hace importable `main` y sus módulos hermanos (config, soap_client, ...) desde tests/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
