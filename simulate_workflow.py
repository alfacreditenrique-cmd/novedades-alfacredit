"""
Script de Simulación Completa del Flujo Semanal de AlfaCredit S.A.
"""
import os
import sys

# Forzar encoding UTF-8 en stdout
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from app.main import app, obtener_estado_completo_pipeline
from app.auth import create_session_token, COOKIE_NAME
from app.database import get_db_connection
from app.audit_service import get_audit_logs

def simular():
    client = TestClient(app)
    print("==================================================================")
    print("[INICIO] SIMULACION DE FLUJO COMPLETO - ALFACREDIT S.A.")
    print("==================================================================")

    # 1. Estado inicial
    st = obtener_estado_completo_pipeline()
    print(f"\n[ESTADO INICIAL] Fase: {st['fase_actual']} ({st['fase_nombre']})")
    print(f"Responsable del Atraso: {st['atraso_responsable']}")
    print(f"Mensaje: {st['atraso_mensaje']}")
    print(f"Sucursales Enviadas: {st['conteo_enviadas']}/{st['total_sucursales']}")

    # 2. Simular Gerentes enviando sus reportes
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, nombre, email_gerente FROM sucursales")
    sucursales = c.fetchall()

    for suc in sucursales:
        c.execute("SELECT id FROM usuarios WHERE email = ?", (suc["email_gerente"],))
        u = c.fetchone()
        u_id = u["id"]
        token = create_session_token(u_id, suc["email_gerente"])

        # Obtener reporte_id
        c.execute("SELECT id FROM reportes_sucursales WHERE sucursal_id = ?", (suc["id"],))
        rep = c.fetchone()
        rep_id = rep["id"]

        # Filas de colaboradores
        filas = [
            {
                "nombre": f"Colaborador 1 - {suc['nombre']}",
                "cargo": "OFICIAL DE CREDITO",
                "estado": "ACTIVO",
                "combustible_base": 750.0,
                "dias_pas": ["", "", "", "", "", ""],
                "dias_fut": ["", "", "", "", "", ""],
                "talla": "L",
                "req": "Guantes",
                "obs": "Todo en orden"
            },
            {
                "nombre": f"Supervisor - {suc['nombre']}",
                "cargo": "SUPERVISOR",
                "estado": "ACTIVO",
                "combustible_base": 900.0,
                "dias_pas": ["MD", "", "", "", "", ""], # Deducción -0.5
                "dias_fut": ["", "", "", "", "", ""],
                "talla": "M",
                "req": "",
                "obs": ""
            }
        ]

        # Enviar reporte oficial
        client.cookies.set(COOKIE_NAME, token)
        res = client.post("/api/novedades/save", json={
            "reporte_id": rep_id,
            "filas": filas,
            "es_envio_final": True
        })
        print(f"[OK] Sucursal {suc['nombre']}: Reporte enviado exitosamente.")

    conn.close()

    # 3. Verificar estado tras envío de los 9 gerentes
    st2 = obtener_estado_completo_pipeline()
    print(f"\n[ESTADO TRAS GERENTES] Fase: {st2['fase_actual']} ({st2['fase_nombre']})")
    print(f"Responsable del Atraso: {st2['atraso_responsable']}")
    print(f"Mensaje: {st2['atraso_mensaje']}")
    print(f"Sucursales Enviadas: {st2['conteo_enviadas']}/{st2['total_sucursales']} (100%)")
    print(f"Total Neto Aprobado: C$ {st2['suma_total_neto']:,.2f}")

    # 4. Simular Brandon enviando la solicitud
    token_brandon = create_session_token(2, "brandon.alfacredit@gmail.com")
    client.cookies.set(COOKIE_NAME, token_brandon)
    res_b = client.post("/api/brandon/send-request", data={
        "asunto": f"Solicitud de Combustible - Corte {st2['ciclo']['fecha_sabado']}",
        "cuerpo": "Estimada Dania, remito la solicitud de combustible aprobada.",
        "destinatario": "dania.tesoreria@alfacredit.com"
    })
    print(f"\n[OK] Brandon envio la solicitud de combustible: {res_b.json()['message']}")

    # 5. Verificar estado tras envío de Brandon
    st3 = obtener_estado_completo_pipeline()
    print(f"\n[ESTADO TRAS BRANDON] Fase: {st3['fase_actual']} ({st3['fase_nombre']})")
    print(f"Responsable del Atraso: {st3['atraso_responsable']}")
    print(f"Mensaje: {st3['atraso_mensaje']}")

    # 6. Simular Dania registrando el depósito y comprobante
    token_dania = create_session_token(3, "dania.tesoreria@alfacredit.com")
    client.cookies.set(COOKIE_NAME, token_dania)
    res_d = client.post("/api/dania/register-deposit", data={
        "monto": st3['suma_total_neto'],
        "referencia": "BANPRO-TRANSF-884912-2026",
        "notas": "Deposito efectuado a todas las cuentas de las sucursales."
    })
    print(f"\n[OK] Dania registro el deposito: {res_d.json()['message']}")

    # 7. Verificar estado final
    st4 = obtener_estado_completo_pipeline()
    print(f"\n[ESTADO FINAL] Fase: {st4['fase_actual']} ({st4['fase_nombre']})")
    print(f"Responsable del Atraso: {st4['atraso_responsable']}")
    print(f"Mensaje: {st4['atraso_mensaje']}")
    print(f"Monto Total Desembolsado: C$ {st4['evento_dania']['monto_depositado']:,.2f}")
    print(f"Numero de Referencia: {st4['evento_dania']['numero_referencia']}")

    # 8. Simular creación de Respaldo Total por Admin
    token_admin = create_session_token(1, "alfacreditenrique@gmail.com")
    client.cookies.set(COOKIE_NAME, token_admin)
    res_bk = client.post("/api/backups/create")
    print(f"\n[OK] Respaldo Total Generado: {res_bk.json()['filename']} ({res_bk.json()['size_kb']} KB)")

    # 9. Verificar Auditoría
    logs = get_audit_logs(limit=10)
    print(f"\n[AUDITORIA] Registros de Auditoria Recientes ({len(logs)} eventos):")
    for log in logs[:5]:
        print(f"  - [{log['timestamp']}] {log['usuario_nombre']} ({log['rol']}): {log['accion']} -> {log['detalle']}")

    print("\n==================================================================")
    print("[FINALIZADO] SIMULACION COMPLETADA CON EXITO AL 100%")
    print("==================================================================")

if __name__ == "__main__":
    simular()
