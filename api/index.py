import os
import sys

# Asegurar que el directorio raíz del proyecto esté en el PYTHONPATH
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Configurar entorno Serverless / Vercel
os.environ["VERCEL"] = "1"

# Importar la aplicación FastAPI principal
from app.main import app

# Exponer app como handler para compatibilidad total con ASGI/WSGI en Vercel
handler = app
