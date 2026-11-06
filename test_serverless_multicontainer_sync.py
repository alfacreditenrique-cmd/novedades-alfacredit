"""
Prueba de Validación de Aislamiento y Persistencia Serverless Multi-Contenedor:
Simula contenedores Lambda efímeros independientes (cada uno con su propio /tmp limpio)
para verificar que las 8 sucursales se acumulan permanentemente (1/8, 2/8... 8/8)
sin sobreescribir ni borrar ninguna sucursal previa.
Utiliza un Cloud Store aislado para no contaminar la base de datos de producción.
"""
import os
import sys
import tempfile
import shutil
import unittest
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from app.cloud_sync import pull_latest_db_from_cloud, push_db_to_cloud
from app.database import get_db_connection, init_db
from app.main import app, obtener_estado_completo_pipeline
from app.auth import create_session_token, COOKIE_NAME

class TestServerlessMultiContainerSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Crear almacén cloud simulado aislado
        cls.mock_cloud_dir = tempfile.mkdtemp(prefix="mock_cloud_store_")
        os.environ["CLOUDSYNC_LOCAL_STORE_DIR"] = cls.mock_cloud_dir
        os.environ["USE_CLOUDSYNC"] = "1"
        os.environ["VERCEL"] = "1"
        os.environ["ENABLE_CLOUDSYNC_IN_TEST"] = "1"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.mock_cloud_dir, ignore_errors=True)
        os.environ.pop("CLOUDSYNC_LOCAL_STORE_DIR", None)
        os.environ.pop("USE_CLOUDSYNC", None)
        os.environ.pop("VERCEL", None)
        os.environ.pop("ENABLE_CLOUDSYNC_IN_TEST", None)

    def test_multicontainer_cumulative_branches(self):
        # 1. Preparar contenedor inicial con el baseline limpio del seed DB (Masaya 1/8)
        tmp_base = tempfile.mkdtemp(prefix="alfacredit_cont_init_")
        os.environ["TMPDIR"] = tmp_base
        db_file = os.path.join(tmp_base, "alfacredit_storage", "alfacredit_novedades.db")
        os.makedirs(os.path.dirname(db_file), exist_ok=True)

        seed_db = os.path.join(BASE_DIR, "storage", "database", "alfacredit_novedades.db")
        shutil.copy2(seed_db, db_file)

        # Confirmar baseline: Masaya ENVIADO, resto PENDIENTE
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT id FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
        ciclo_id = c.fetchone()["id"]
        c.execute("UPDATE reportes_sucursales SET estado = 'PENDIENTE', enviado_en = NULL WHERE ciclo_id = ? AND sucursal_id != 2", (ciclo_id,))
        c.execute("UPDATE reportes_sucursales SET estado = 'ENVIADO', enviado_en = '2026-11-06 15:54:21' WHERE ciclo_id = ? AND sucursal_id = 2", (ciclo_id,))
        conn.commit()
        conn.close()

        # Subir baseline al cloud store simulado
        push_db_to_cloud(db_file, "init_baseline")
        shutil.rmtree(tmp_base, ignore_errors=True)

        # 2. Sucursales restantes a enviar en contenedores Lambda independientes
        sucursales_a_enviar = [
            (1, "Estelí", "gerente.esteli@alfacreditsa.com"),
            (3, "Managua", "gerente.managua@alfacreditsa.com"),
            (5, "Sébaco", "gerente.sebaco@alfacreditsa.com"),
            (6, "Jinotepe", "gerente.jinotepe@alfacreditsa.com"),
            (7, "Juigalpa", "gerente.juigalpa@alfacreditsa.com"),
            (8, "Rivas", "gerente.rivas@alfacreditsa.com"),
            (9, "León", "gerente.leon@alfacreditsa.com")
        ]

        conteo_esperado = 1  # Iniciamos con Masaya
        enviadas_acumuladas = ["Masaya"]

        for suc_id, suc_nombre, suc_email in sucursales_a_enviar:
            # CREAR UN CONTENEDOR TOTALMENTE NUEVO Y AISLADO (Simulando nueva instancia Lambda)
            container_tmp = tempfile.mkdtemp(prefix=f"lambda_container_{suc_nombre}_")
            os.environ["TMPDIR"] = container_tmp

            client = TestClient(app)

            # Verificar que el contenedor nuevo ve todas las enviadas acumuladas antes del submit
            st_pre = client.get(f"/api/status?ciclo_id={ciclo_id}").json()
            enviadas_pre = [s["sucursal_nombre"] for s in st_pre["sucursales_enviadas"]]
            for ya_enviada in enviadas_acumuladas:
                self.assertIn(ya_enviada, enviadas_pre, f"La sucursal previa {ya_enviada} desapareció en el contenedor de {suc_nombre}")

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
            self.assertEqual(res.status_code, 200, f"Error saving {suc_nombre}: {res.text}")

            conteo_esperado += 1
            enviadas_acumuladas.append(suc_nombre)

            # Destruir contenedor inmediatamente (simulando destrucción de Lambda)
            shutil.rmtree(container_tmp, ignore_errors=True)

            # ABRIR OTRO CONTENEDOR NUEVO PARA DASHBOARD / BRANDON Y VERIFICAR PERSISTENCIA
            dashboard_tmp = tempfile.mkdtemp(prefix="lambda_dashboard_view_")
            os.environ["TMPDIR"] = dashboard_tmp
            client_dash = TestClient(app)

            st_post = client_dash.get(f"/api/status?ciclo_id={ciclo_id}").json()
            enviadas_post = [s["sucursal_nombre"] for s in st_post["sucursales_enviadas"]]

            self.assertEqual(st_post["conteo_enviadas"], conteo_esperado, f"Esperado {conteo_esperado}/8, obtenido {st_post['conteo_enviadas']}")
            for esperada in enviadas_acumuladas:
                self.assertIn(esperada, enviadas_post, f"La sucursal {esperada} se borró tras enviar {suc_nombre}!")

            shutil.rmtree(dashboard_tmp, ignore_errors=True)

        self.assertEqual(conteo_esperado, 8)
        self.assertEqual(len(enviadas_acumuladas), 8)

if __name__ == '__main__':
    unittest.main()
