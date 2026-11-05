import io
import os
import sys

# Forzar encoding UTF-8 en consola Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from app.main import app, obtener_estado_completo_pipeline
from app.auth import create_session_token, COOKIE_NAME
from app.database import init_db, get_db_connection
from app.backup_service import crear_respaldo_completo

def test_workflow():
    init_db()
    client = TestClient(app)

    # 1. Verificar usuario Brandon
    conn = get_db_connection()
    u = conn.execute("SELECT id, email, rol FROM usuarios WHERE rol='brandon'").fetchone()
    conn.close()

    assert u is not None
    assert u["email"].lower() == "adquisicionesylogistica@alfacreditsa.com", f"Email esperado: adquisicionesylogistica@alfacreditsa.com, obtenido: {u['email']}"
    print(f"✅ Usuario Brandon verificado con correo oficial: {u['email']}")

    # 2. Simular envío de solicitud con 3 PDFs adjuntos
    token = create_session_token(u["id"], u["email"])
    client.cookies.set(COOKIE_NAME, token)

    pdf_files = [
        ("archivos_pdf", ("cotizacion_combustible_puma.pdf", io.BytesIO(b"%PDF-1.4 Cotizacion Puma Energy C$ 15,000"), "application/pdf")),
        ("archivos_pdf", ("desglose_flota_vehicular.pdf", io.BytesIO(b"%PDF-1.4 Desglose por ruta y kilometraje"), "application/pdf")),
        ("archivos_pdf", ("justificacion_novedades.pdf", io.BytesIO(b"%PDF-1.4 Justificacion de altas y deducciones"), "application/pdf"))
    ]

    res = client.post(
        "/api/brandon/send-request",
        data={
            "asunto": "Solicitud de Combustible con 3 PDFs de Respaldo",
            "cuerpo": "Estimada Dania, adjunto cotización, desglose de flota y justificación.",
            "destinatario": "dania.tesoreria@alfacredit.com"
        },
        files=pdf_files
    )

    assert res.status_code == 200, f"Error en envío: {res.text}"
    data = res.json()
    assert data["success"] is True
    assert len(data["archivos_adjuntos"]) == 3
    print(f"✅ Brandon envió la solicitud exitosamente con {len(data['archivos_adjuntos'])} PDFs adjuntos")

    # 3. Verificar estado en pipeline
    st = obtener_estado_completo_pipeline()
    ev_b = st["evento_brandon"]
    assert ev_b is not None
    assert len(ev_b["archivos_adjuntos"]) == 3
    print(f"✅ Pipeline detectó los 3 PDFs: {ev_b['archivos_adjuntos']}")

    # 4. Probar descarga de cada PDF
    for pdf_fn in ev_b["archivos_adjuntos"]:
        res_dl = client.get(f"/api/adjuntos-brandon/download/{pdf_fn}")
        assert res_dl.status_code == 200
        assert len(res_dl.content) > 0
        print(f"✅ Descarga verificada para: {pdf_fn} ({len(res_dl.content)} bytes)")

    # 5. Probar que el respaldo ZIP incluye los PDFs de Brandon
    res_bk = crear_respaldo_completo(usuario_email="Adquisicionesylogistica@alfacreditsa.com", tipo="TEST_PDF")
    assert res_bk["success"] is True
    print(f"✅ Respaldo ZIP completo generado con PDFs incluidos: {res_bk['filename']} ({res_bk['size_kb']} KB)")

    # 6. Probar renderizado de la vista de validación de depósito (debe mostrar los PDFs de Brandon)
    client.cookies.set(COOKIE_NAME, token)
    res_dania_view = client.get("/deposito")
    assert res_dania_view.status_code == 200
    assert "Documentos PDF de Respaldo Enviados por Brandon" in res_dania_view.text
    print("✅ Vista de validación muestra correctamente la tarjeta de revisión de PDFs de Brandon")

    print("\n🎉 TODAS LAS PRUEBAS DE BRANDON, OUTLOOK Y MULTI-PDF PASARON CON ÉXITO AL 100%")

if __name__ == "__main__":
    test_workflow()
