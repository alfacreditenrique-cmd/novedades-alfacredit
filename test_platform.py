"""
Script de Pruebas Automatizadas para la Plataforma AlfaCredit Novedades y Combustible
"""
import os
import sys
import unittest
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from app.database import init_db, get_db_connection, hash_password
from app.calculations import calcular_fila_novedades, LEYENDA_MAESTRA
from app.excel_engine import generar_excel_sucursal, generar_excel_consolidado_todas_sucursales
from app.backup_service import crear_respaldo_completo, listar_respaldos
from app.auth import create_session_token, verify_session_token
from fastapi.testclient import TestClient
from app.main import app, obtener_estado_completo_pipeline

class TestAlfaCreditPlatform(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)

    def test_01_database_initialization(self):
        """Verifica que las tablas, 8 sucursales y usuarios existan en la BD"""
        conn = get_db_connection()
        c = conn.cursor()
        
        c.execute("SELECT COUNT(*) as cnt FROM sucursales")
        suc_count = c.fetchone()["cnt"]
        self.assertEqual(suc_count, 8, f"Se esperaban 8 sucursales, se encontraron {suc_count}")

        c.execute("SELECT COUNT(*) as cnt FROM usuarios")
        user_count = c.fetchone()["cnt"]
        self.assertGreaterEqual(user_count, 11, "Debe haber al menos 11 usuarios (8 gerentes, admin, brandon, yeser)")

        c.execute("SELECT COUNT(*) as cnt FROM ciclos_semanales")
        ciclos_count = c.fetchone()["cnt"]
        self.assertGreaterEqual(ciclos_count, 1, "Debe haber al menos 1 ciclo semanal activo")

        conn.close()

    def test_02_calculations_logic(self):
        """Verifica la fórmula matemática exacta de deducciones/adiciones de combustible"""
        # Caso 1: Asignación C$ 600, 1 día de falta (-1) y 1 día de vacaciones (-1) = -2 días
        # Ajuste = 600 / 6 * (-2) = -200, Total Neto = 400
        res = calcular_fila_novedades(600.0, ["F", "", "", "", "", ""], ["V", "", "", "", "", ""])
        self.assertEqual(res["factor_total"], -2.0)
        self.assertEqual(res["ajuste_dinero"], -200.0)
        self.assertEqual(res["deduccion_mostrada"], 200.0)
        self.assertEqual(res["total_neto"], 400.0)

        # Caso 2: Asignación C$ 900, 1 Nuevo (+1) y 1 Reposición (+1) = +2 días
        # Ajuste = 900 / 6 * (+2) = +300, Total Neto = 1200
        res2 = calcular_fila_novedades(900.0, ["N", "", "", "", "", ""], ["R", "", "", "", "", ""])
        self.assertEqual(res2["factor_total"], 2.0)
        self.assertEqual(res2["ajuste_dinero"], 300.0)
        self.assertEqual(res2["total_neto"], 1200.0)

        # Caso 3: Asignación C$ 1200, Medio Día (-0.5)
        # Ajuste = 1200 / 6 * (-0.5) = -100, Total Neto = 1100
        res3 = calcular_fila_novedades(1200.0, ["MD", "", "", "", "", ""], ["", "", "", "", "", ""])
        self.assertEqual(res3["factor_total"], -0.5)
        self.assertEqual(res3["ajuste_dinero"], -100.0)
        self.assertEqual(res3["total_neto"], 1100.0)

        # Caso 4: Código 'B' (Baja / Ausencia): debe restar exactamente -1 día (-1.0)
        # Asignación C$ 1200, 1 día B. Ajuste = 1200 / 6 * (-1.0) = -200, Total Neto = 1000
        res4 = calcular_fila_novedades(1200.0, ["B", "", "", "", "", ""], ["", "", "", "", "", ""])
        self.assertEqual(res4["factor_total"], -1.0)
        self.assertEqual(res4["ajuste_dinero"], -200.0)
        self.assertEqual(res4["deduccion_mostrada"], 200.0)
        self.assertEqual(res4["total_neto"], 1000.0)

    def test_03_auth_and_tokens(self):
        """Verifica la generación y validación de tokens de sesión"""
        token = create_session_token(1, "enrique@alfacreditsa.com")
        user_id = verify_session_token(token)
        self.assertEqual(user_id, 1)

        # Token corrupto debe retornar None
        self.assertIsNone(verify_session_token("invalid_token"))

    def test_04_excel_generation(self):
        """Verifica la generación del archivo Excel individual y consolidado"""
        filas = [{
            "nombre": "Juan Perez",
            "cargo": "OFICIAL DE CREDITO",
            "estado": "ACTIVO",
            "combustible_base": 750.0,
            "dias_pas": ["F", "", "", "", "", ""],
            "dias_fut": ["", "", "", "", "", ""],
            "talla": "M",
            "req": "Casco",
            "obs": "Sin novedad"
        }]
        buf = generar_excel_sucursal("Estelí", "2026-09-05", filas)
        self.assertGreater(buf.getbuffer().nbytes, 1000, "El Excel individual debe contener datos válidos")

        buf_con = generar_excel_consolidado_todas_sucursales("2026-09-05", [{
            "sucursal_nombre": "Estelí",
            "email_gerente": "gerenteesteli7@gmail.com",
            "total_empleados": 5,
            "suma_base": 3000.0,
            "suma_neto": 2800.0
        }])
        self.assertGreater(buf_con.getbuffer().nbytes, 1000, "El Excel consolidado debe contener datos válidos")

    def test_05_backup_creation(self):
        """Verifica la creación del archivo de respaldo .ZIP completo"""
        res = crear_respaldo_completo(usuario_email="test@alfacredit.com", tipo="TEST")
        self.assertTrue(res["success"])
        self.assertTrue(os.path.exists(res["filepath"]))
        self.assertGreater(res["size_kb"], 0)

        backups = listar_respaldos()
        self.assertGreaterEqual(len(backups), 1)

    def test_06_pipeline_status_endpoint(self):
        """Verifica la respuesta del pipeline de estado en tiempo real para 8 sucursales"""
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("fase_actual", data)
        self.assertIn("atraso_responsable", data)
        self.assertIn("sucursales", data)
        self.assertEqual(data["total_sucursales"], 8)

    def test_07_dark_mode_and_secondary_email_confirm(self):
        """Verifica el soporte para Modo Oscuro y el botón de confirmación secundaria de correo"""
        # Verificar que el botón de dark mode y script existen en templates
        res_login = self.client.get("/login")
        self.assertEqual(res_login.status_code, 200)
        self.assertIn("alfa_theme", res_login.text)
        self.assertIn("theme-toggle-btn", res_login.text)

        # Asegurar estado limpio antes de la prueba
        conn = get_db_connection()
        conn.execute("DELETE FROM eventos_brandon WHERE ciclo_id = (SELECT id FROM ciclos_semanales ORDER BY id DESC LIMIT 1)")
        conn.execute("DELETE FROM eventos_dania WHERE ciclo_id = (SELECT id FROM ciclos_semanales ORDER BY id DESC LIMIT 1)")
        conn.execute("UPDATE ciclos_semanales SET estado_general = 'FASE_1_GERENTES'")
        conn.commit()
        conn.close()

        try:
            # Probar confirmación secundaria manual de correo (solicitud Brandon -> Fase 3)
            token = create_session_token(2, "adquisicionesylogistica@alfacreditsa.com")
            self.client.cookies.set("alfacredit_session", token)

            res_confirm = self.client.post("/api/email/confirm-secondary", data={
                "tipo": "solicitud_brandon",
                "notas": "Prueba de confirmación manual de recepción de correo"
            })
            self.assertEqual(res_confirm.status_code, 200)
            data = res_confirm.json()
            self.assertTrue(data["success"])
            self.assertIn("solicitud de combustible", data["message"].lower())
            self.assertEqual(data["nuevo_estado"]["fase_actual"], 3, "Debe avanzar a Fase 3 (Dania)")

            # Probar confirmación secundaria manual de comprobante de Dania -> Fase 4
            res_confirm_d = self.client.post("/api/email/confirm-secondary", json={
                "tipo": "comprobante_dania",
                "notas": "Prueba confirmación manual comprobante Dania"
            })
            self.assertEqual(res_confirm_d.status_code, 200)
            data_d = res_confirm_d.json()
            self.assertTrue(data_d["success"])
            self.assertEqual(data_d["nuevo_estado"]["fase_actual"], 4, "Debe avanzar a Fase 4 (Completado)")
        finally:
            # Limpiar datos creados en el test para no ensuciar la base de datos
            conn = get_db_connection()
            conn.execute("DELETE FROM eventos_brandon WHERE ciclo_id = (SELECT id FROM ciclos_semanales ORDER BY id DESC LIMIT 1)")
            conn.execute("DELETE FROM eventos_dania WHERE ciclo_id = (SELECT id FROM ciclos_semanales ORDER BY id DESC LIMIT 1)")
            conn.execute("UPDATE ciclos_semanales SET estado_general = 'FASE_1_GERENTES'")
            conn.commit()
            conn.close()

    def test_08_turso_libsql_compatibility(self):
        """Verifica que el wrapper LibsqlConnection y LibsqlRow funcione correctamente"""
        import tempfile
        import libsql_client
        from app.database import LibsqlConnection

        temp_db = os.path.join(tempfile.gettempdir(), "test_turso_compat.db")
        if os.path.exists(temp_db):
            os.remove(temp_db)

        client = libsql_client.create_client_sync(f"file:{temp_db}")
        conn = LibsqlConnection(client)
        conn.execute("CREATE TABLE test_turso (id INTEGER PRIMARY KEY, nombre TEXT, valor REAL)")
        c = conn.cursor()
        c.execute("INSERT INTO test_turso (nombre, valor) VALUES (?, ?)", ("Managua", 1500.0))
        self.assertGreaterEqual(c.lastrowid, 1)

        row = conn.execute("SELECT * FROM test_turso WHERE nombre = ?", ("Managua",)).fetchone()
        self.assertEqual(row["nombre"], "Managua")
        self.assertEqual(row["NOMBRE"], "Managua")
        self.assertEqual(row["valor"], 1500.0)
        self.assertEqual(dict(row)["nombre"], "Managua")

        # Verificar iterabilidad de cursor (for r in cur)
        all_rows = list(conn.execute("SELECT * FROM test_turso"))
        self.assertEqual(len(all_rows), 1)
        self.assertEqual(all_rows[0]["nombre"], "Managua")

        conn.close()
    def test_09_novedades_locking_when_brandon_submits(self):
        """Verifica que ningún gerente pueda editar novedades una vez que Brandon envió la solicitud a Finanzas"""
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM usuarios WHERE email = 'gerente.esteli@alfacreditsa.com'")
        u_gerente = c.fetchone()
        c.execute("SELECT id, ciclo_id FROM reportes_sucursales WHERE sucursal_id = 1 ORDER BY id DESC LIMIT 1")
        rep = c.fetchone()
        conn.close()

        token_gerente = create_session_token(u_gerente["id"], "gerente.esteli@alfacreditsa.com")
        self.client.cookies.set("alfacredit_session", token_gerente)

        # 1. En Fase 1 el gerente puede guardar normalmente
        res_ok = self.client.post("/api/novedades/save", json={
            "reporte_id": rep["id"],
            "filas": [{
                "colaborador_id": 1,
                "nombre": "Colaborador Test Esteli",
                "cargo": "GERENTE SUCURSAL",
                "estado": "ACTIVO",
                "combustible_base": 1200.0,
                "dias_pas": ["", "", "", "", "", ""],
                "dias_fut": ["", "", "", "", "", ""],
                "talla": "L",
                "req": "Guantes",
                "obs": "Al dia"
            }],
            "es_envio_final": False
        })
        self.assertEqual(res_ok.status_code, 200, f"Error en guardado fase 1: {res_ok.text}")

        # 2. Brandon envía la solicitud de combustible (Avanza a Fase 3)
        token_brandon = create_session_token(2, "adquisicionesylogistica@alfacreditsa.com")
        self.client.cookies.set("alfacredit_session", token_brandon)
        res_conf = self.client.post("/api/email/confirm-secondary", data={"tipo": "solicitud_brandon"})
        self.assertEqual(res_conf.status_code, 200)

        # 3. Gerente intenta guardar después del envío de Brandon -> 403 Forbidden
        self.client.cookies.set("alfacredit_session", token_gerente)
        res_blocked_save = self.client.post("/api/novedades/save", json={
            "reporte_id": rep["id"],
            "filas": [],
            "es_envio_final": False
        })
        self.assertEqual(res_blocked_save.status_code, 403, "Debe rechazar con HTTP 403")
        self.assertIn("bloqueado", res_blocked_save.json()["detail"].lower())

        # 4. Gerente intenta enviar oficialmente -> 403 Forbidden
        res_blocked_submit = self.client.post("/api/novedades/submit", json={
            "reporte_id": rep["id"],
            "filas": []
        })
        self.assertEqual(res_blocked_submit.status_code, 403, "Debe rechazar con HTTP 403")

        # 5. La página /novedades muestra el aviso de solo lectura
        res_page = self.client.get("/novedades")
        self.assertEqual(res_page.status_code, 200)
        self.assertIn("Periodo de Novedades Bloqueado", res_page.text)
        self.assertIn("Solo Lectura", res_page.text)

    def test_10_corte_semanal_validation_and_clean_cycle(self):
        """Verifica la validación de Fase 4 y el corte semanal con novedades limpias y colaboradores preservados"""
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM usuarios WHERE email = 'gerente.esteli@alfacreditsa.com'")
        u_gerente = c.fetchone()
        conn.close()

        token_gerente = create_session_token(u_gerente["id"], "gerente.esteli@alfacreditsa.com")
        token_brandon = create_session_token(2, "adquisicionesylogistica@alfacreditsa.com")

        # 1. Gerente no autorizado para corte semanal -> 403 Forbidden
        self.client.cookies.set("alfacredit_session", token_gerente)
        res_unauth = self.client.post("/api/ciclo/corte-semanal")
        self.assertEqual(res_unauth.status_code, 403)

        # 2. Brandon intenta hacer corte antes de Fase 4 -> 400 Bad Request
        self.client.cookies.set("alfacredit_session", token_brandon)
        res_not_ready = self.client.post("/api/ciclo/corte-semanal")
        self.assertEqual(res_not_ready.status_code, 400)
        self.assertIn("fondos despachados", res_not_ready.json()["detail"].lower())

        # 3. Dania confirma depósito -> Pasa a Fase 4
        res_dania = self.client.post("/api/email/confirm-secondary", data={"tipo": "comprobante_dania"})
        self.assertEqual(res_dania.status_code, 200)

        # 4. Brandon ejecuta el corte semanal exitosamente
        res_corte = self.client.post("/api/ciclo/corte-semanal")
        self.assertEqual(res_corte.status_code, 200)
        data_corte = res_corte.json()
        self.assertTrue(data_corte["success"])
        self.assertIn("Corte semanal completado", data_corte["message"])

        # 5. Verificar estado de la base de datos tras el corte
        conn = get_db_connection()
        c = conn.cursor()
        # El ciclo anterior debe tener cerrado_en y estado FASE_4_COMPLETADO
        c.execute("SELECT id, fecha_sabado, cerrado_en, estado_general FROM ciclos_semanales WHERE id = ?", (data_corte["ciclo_anterior_id"],))
        old_c = c.fetchone()
        self.assertIsNotNone(old_c["cerrado_en"])
        self.assertEqual(old_c["estado_general"], "FASE_4_COMPLETADO")

        # El nuevo ciclo debe estar en FASE_1_GERENTES
        c.execute("SELECT * FROM ciclos_semanales WHERE id = ?", (data_corte["nuevo_ciclo_id"],))
        new_c = c.fetchone()
        self.assertEqual(new_c["estado_general"], "FASE_1_GERENTES")
        self.assertNotEqual(new_c["fecha_sabado"], old_c["fecha_sabado"])
        self.assertNotEqual(new_c["id"], old_c["id"])

        # Todas las 8 sucursales deben tener reporte en PENDIENTE
        c.execute("SELECT COUNT(*) as cnt FROM reportes_sucursales WHERE ciclo_id = ? AND estado = 'PENDIENTE'", (new_c["id"],))
        self.assertEqual(c.fetchone()["cnt"], 8)

        # Los colaboradores deben haberse preservado pero con casillas de novedades limpias
        c.execute('''
        SELECT d.* FROM detalle_novedades d
        JOIN reportes_sucursales r ON d.reporte_id = r.id
        WHERE r.ciclo_id = ?
        ''', (new_c["id"],))
        filas = c.fetchall()
        self.assertGreaterEqual(len(filas), 8, "Debe tener colaboradores preservados")
        for f in filas:
            self.assertEqual(f["factor_total"], 0.0)
            self.assertEqual(f["deduccion_ajuste"], 0.0)
            self.assertEqual(f["total_neto"], f["combustible_base"])
            pas = json.loads(f["dias_pas_json"])
            fut = json.loads(f["dias_fut_json"])
            self.assertEqual(pas, [""] * 6)
            self.assertEqual(fut, [""] * 6)

        conn.close()

        # 6. Gerente ahora vuelve a estar habilitado para la nueva semana
        self.client.cookies.set("alfacredit_session", token_gerente)
        res_new_page = self.client.get("/novedades")
        self.assertEqual(res_new_page.status_code, 200)
        self.assertNotIn("Periodo de Novedades Bloqueado", res_new_page.text)

        # 7. Verificar endpoint de historial de ciclos
        self.client.cookies.set("alfacredit_session", token_brandon)
        res_historial = self.client.get("/api/ciclos/historial")
        self.assertEqual(res_historial.status_code, 200)
        ciclos_list = res_historial.json()["historial"]
        self.assertGreaterEqual(len(ciclos_list), 2, "Debe listar ciclo previo archivado y ciclo actual")

        # 8. Descarga de Excel histórico usando ciclo_id
        res_excel_hist = self.client.get(f"/api/export/excel/consolidado?ciclo_id={old_c['id']}")
        self.assertEqual(res_excel_hist.status_code, 200)
        self.assertTrue(len(res_excel_hist.content) > 1000)

if __name__ == "__main__":
    unittest.main()


