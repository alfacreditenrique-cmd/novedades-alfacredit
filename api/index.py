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

@app.middleware("http")
async def vercel_routing_middleware(request, call_next):
    """
    Middleware para normalizar rutas en Vercel Serverless.
    Si Vercel reescribe la petición a /api/index.py o /api,
    restaura la ruta original solicitada por el cliente desde x-matched-path.
    """
    matched_path = (
        request.headers.get("x-matched-path") or 
        request.headers.get("x-forwarded-uri") or 
        request.headers.get("x-original-url") or 
        request.headers.get("x-real-path") or 
        ""
    )
    current_path = request.scope.get("path", "")

    if current_path in ("/api/index.py", "/api/index", "/api", "/api/"):
        if matched_path and matched_path not in ("/api/index.py", "/api/index", "/api", "/api/"):
            request.scope["path"] = matched_path
            request.scope["raw_path"] = matched_path.encode("utf-8")
        else:
            request.scope["path"] = "/"
            request.scope["raw_path"] = b"/"

    return await call_next(request)

handler = app
