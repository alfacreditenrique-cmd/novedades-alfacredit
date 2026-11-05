import os
import sys
import tempfile
import unittest

# Forzar simulación de entorno serverless Vercel
os.environ["VERCEL"] = "1"
os.environ["USE_TMP_DB"] = "1"
os.environ["TMPDIR"] = tempfile.gettempdir()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from api.index import app, handler
from app.database import DB_PATH, get_db_connection, init_db

class TestVercelEntrypoint(unittest.TestCase):
    def setUp(self):
        # Asegurar que init_db funciona en entorno /tmp efímero
        init_db()
        self.client = TestClient(app)

    def test_handler_exists(self):
        self.assertIsNotNone(handler)
        self.assertEqual(handler, app)

    def test_tmp_database_path(self):
        # Verificar que DB_PATH está en TMPDIR y no en storage/database/ local
        temp_dir_norm = os.path.normpath(tempfile.gettempdir()).lower()
        db_path_norm = os.path.normpath(DB_PATH).lower()
        self.assertTrue(
            db_path_norm.startswith(temp_dir_norm) or "/tmp" in db_path_norm,
            f"DB_PATH no está en directorio temporal: {DB_PATH}"
        )

    def test_api_health_or_login(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AlfaCredit", response.text)

    def test_auth_workflow_in_serverless(self):
        # Login de gerente
        res_login = self.client.post(
            "/login",
            data={"email": "gerente.esteli@alfacreditsa.com", "password": "1234"},
            follow_redirects=False
        )
        self.assertEqual(res_login.status_code, 303)
        self.assertIn("/dashboard", res_login.headers.get("location", ""))

    def test_static_assets_serving(self):
        # Probar que los archivos estáticos responden correctamente (CSS, JS, Logo)
        res_css = self.client.get("/static/css/custom.css")
        self.assertEqual(res_css.status_code, 200)
        self.assertIn("--alfa-blue", res_css.text)

        res_logo = self.client.get("/static/img/logo.png")
        self.assertEqual(res_logo.status_code, 200)

    def test_dashboard_and_export_in_serverless(self):
        from app.auth import create_session_token, COOKIE_NAME
        conn = get_db_connection()
        user = conn.execute("SELECT id, email FROM usuarios WHERE email = 'enrique@alfacreditsa.com'").fetchone()
        conn.close()
        token = create_session_token(user["id"], user["email"])
        self.client.cookies.set(COOKIE_NAME, token)

        # Dashboard
        res_dash = self.client.get("/dashboard")
        self.assertEqual(res_dash.status_code, 200)
        self.assertIn("Panel de Control", res_dash.text)

        # Consolidado Excel
        res_excel = self.client.get("/api/export/excel/consolidado")
        self.assertEqual(res_excel.status_code, 200)
        self.assertEqual(res_excel.headers.get("content-type"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    unittest.main()
