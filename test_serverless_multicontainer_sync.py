"""
Prueba de Validación de Aislamiento y Persistencia Serverless Multi-Contenedor:
Simula contenedores Lambda efímeros independientes (cada uno con su propio /tmp limpio)
para verificar que las 8 sucursales se acumulan permanentemente (1/8, 2/8... 8/8)
sin sobreescribir ni borrar ninguna sucursal previa.
"""
import os
import sys
import tempfile
import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Habilitar modo CloudSync para simular Vercel
os.environ["USE_CLOUDSYNC"] = "1"
os.environ["VERCEL"] = "1"

from fastapi.testclient import TestClient
from app.cloud_sync import pull_latest_db_from_cloud, push_db_to_cloud
from app.database import get_db_connection, init_db, asegurar_ciclo_actual
from app.main import app, obtener_estado_completo_pipeline
from app.auth import create_session_token, COOKIE_NAME

def simular_contenedores_independientes():
    print("======================================================================")
    print("INICIANDO PRUEBA MULTI-CONTENEDOR SERVERLESS (CLOUDSYNC)")
    print("======================================================================")

    # 1. Preparar contenedor inicial para asegurar baseline
    tmp_base = tempfile.mkdtemp(prefix="alfacredit_cont_init_")
    os.environ["TMPDIR"] = tmp_base
    db_file = os.path.join(tmp_base, "alfacredit_storage", "alfacredit_novedades.db")

    # Pull inicial de la nube
    pull_latest_db_from_cloud(db_file, force=True)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, fecha_sabado FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    ciclo = c.fetchone()
    ciclo_id = ciclo["id"]
    print(f"Ciclo Activo ID: {ciclo_id} (Fecha Sábado: {ciclo['fecha_sabado']})")

    # Reiniciar estado a: Masaya=ENVIADO, resto=PENDIENTE
    c.execute("UPDATE reportes_sucursales SET estado = 'PENDIENTE', enviado_en = NULL WHERE ciclo_id = ? AND sucursal_id != 2", (ciclo_id,))
    c.execute("UPDATE reportes_sucursales SET estado = 'ENVIADO', enviado_en = '2026-11-06 15:54:21' WHERE ciclo_id = ? AND sucursal_id = 2", (ciclo_id,))
    conn.commit()
    conn.close()

    # Subir baseline a la nube
    push_db_to_cloud(db_file, "init_test")
    shutil.rmtree(tmp_base, ignore_errors=True)
    print("[BASELINE OK] Masaya inicializado como ENVIADO (1/8)")

    # 2. Sucursales restantes a enviar en contenedores separados
    sucursales_a_enviar = [
        (1, "Estelí", "gerente.esteli@alfacreditsa.com"),
        (3, "Managua", "gerente.managua@alfacreditsa.com"),
        (5, "Sébaco", "gerente.sebaco@alfacreditsa.com"),
        (6, "Jinotepe", "gerente.jinotepe@alfacreditsa.com"),
        (7, "Juigalpa", "gerente.juigalpa@alfacreditsa.com"),
        (8, "Rivas", "gerente.rivas@alfacreditsa.com"),
        (9, "León", "gerente.leon@alfacreditsa.com")
    ]

    conteo_esperado = 1 # Iniciamos con Masaya
    enviadas_acumuladas = ["Masaya"]

    for suc_id, suc_nombre, suc_email in sucursales_a_enviar:
        # CREAR UN CONTENEDOR TOTALMENTE NUEVO Y AISLADO (Simulando nueva Lambda)
        container_tmp = tempfile.mkdtemp(prefix=f"lambda_container_{suc_nombre}_")
        os.environ["TMPDIR"] = container_tmp
        print(f"\n--- [SPIN UP LAMBDA] Contenedor para {suc_nombre} en {container_tmp} ---")

        client = TestClient(app)

        # Verificar que el contenedor nuevo ve las enviadas hasta ahora antes de hacer submit
        st_pre = client.get(f"/api/status?ciclo_id={ciclo_id}").json()
        enviadas_pre = [s["sucursal_nombre"] for s in st_pre["sucursales_enviadas"]]
        print(f"   Estado al conectar contenedor: {st_pre['conteo_enviadas']}/8 enviadas -> {enviadas_pre}")
        for ya_enviada in enviadas_acumuladas:
            assert ya_enviada in enviadas_pre, f"ERROR CRITICO: La sucursal {ya_enviada} desapareció en el nuevo contenedor!"

        # Obtener usuario y reporte_id de esta sucursal en este contenedor
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM usuarios WHERE email = ?", (suc_email,))
        u = c.fetchone()
        u_id = u["id"]

        c.execute("SELECT id FROM reportes_sucursales WHERE ciclo_id = ? AND sucursal_id = ?", (ciclo_id, suc_id))
        rep = c.fetchone()
        rep_id = rep["id"]
        conn.close()

        # Enviar reporte de esta sucursal
        token = create_session_token(u_id, suc_email)
        client.cookies.set(COOKIE_NAME, token)

        res = client.post("/api/novedades/save", json={
            "reporte_id": rep_id,
            "filas": [{
                "nombre": f"Colaborador {suc_nombre}",
                "cargo": "OFICIAL DE CREDITO",
                "estado": "ACTIVO",
                "combustible_base": 750.0,
                "dias_pas": ["", "", "", "", "", ""],
                "dias_fut": ["", "", "", "", "", ""]
            }],
            "es_envio_final": True
        })
        assert res.status_code == 200, f"Error saving: {res.text}"

        conteo_esperado += 1
        enviadas_acumuladas.append(suc_nombre)

        # Destruir contenedor inmediatamente (simulando reciclaje de Lambda)
        shutil.rmtree(container_tmp, ignore_errors=True)

        # AHORA ABRIR OTRO CONTENEDOR NUEVO PARA BRANDON / DASHBOARD Y VERIFICAR PERSISTENCIA
        dashboard_tmp = tempfile.mkdtemp(prefix="lambda_dashboard_view_")
        os.environ["TMPDIR"] = dashboard_tmp
        client_dash = TestClient(app)

        st_post = client_dash.get(f"/api/status?ciclo_id={ciclo_id}").json()
        enviadas_post = [s["sucursal_nombre"] for s in st_post["sucursales_enviadas"]]
        print(f"   [VERIFICADO EN CONTENEDOR INDEPENDIENTE]: {st_post['conteo_enviadas']}/8 enviadas -> {enviadas_post}")

        assert st_post["conteo_enviadas"] == conteo_esperado, f"Esperado {conteo_esperado}, obtenido {st_post['conteo_enviadas']}"
        for esperada in enviadas_acumuladas:
            assert esperada in enviadas_post, f"La sucursal {esperada} se borró tras enviar {suc_nombre}!"

        shutil.rmtree(dashboard_tmp, ignore_errors=True)

    print("\n======================================================================")
    print(f"¡EXITO TOTAL! Se completaron las 8/8 sucursales sin que ninguna se borrara:")
    print(f"Enviadas: {enviadas_acumuladas}")
    print("======================================================================")

if __name__ == '__main__':
    simular_contenedores_independientes()
