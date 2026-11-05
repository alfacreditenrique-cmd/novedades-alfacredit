import uvicorn
import os
import sys

# Asegurar que el directorio raíz esté en sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from app.database import init_db

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 ALFACREDIT S.A. - PLATAFORMA DE NOVEDADES & COMBUSTIBLE")
    print("=" * 60)
    print("Inicializando base de datos y esquemas...")
    init_db()
    print("Servidor listo en: http://localhost:8000")
    print("Presiona Ctrl+C para detener el servidor.")
    print("=" * 60)

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )
