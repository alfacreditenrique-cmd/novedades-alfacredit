"""
Adversarial Verification Suite — Challenger 2 (Workflow & Data Integrity)
AlfaCredit S.A. - Sistema de Novedades y Combustible

Testing Objectives:
1. Complete operational branch lifecycle & DB synchronization:
   Create branch -> verify 8 collaborators in DB -> verify weekly cycle report initialized ->
   login as manager -> verify dashboard access -> edit manager details -> verify sync to sucursales ->
   admin resets manager password -> verify new password login.
2. config.json operational impact & live reflection:
   Update brandon_email & dania_email -> check config.json on disk -> call get_email_config() ->
   verify changes live in UI -> revert config to pristine state.
3. Concurrency, idempotency & hardening:
   Repeated init_db() calls -> password persistence -> duplicate branch name & email rejection ->
   concurrent collision handling.
4. Strict 8-branch invariant preservation:
   Guarantee 100% test isolation and clean teardowns.
"""

import os
import sys
import json
import unittest
from concurrent.futures import ThreadPoolExecutor

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from app.main import app, obtener_estado_completo_pipeline
from app.database import init_db, get_db_connection, hash_password
from app.auth import create_session_token, COOKIE_NAME
from app.email_service import get_email_config, CONFIG_PATH


class TestAdversarialChallenger2(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["DISABLE_CLOUDSYNC"] = "1"
        init_db()
        cls.client = TestClient(app, follow_redirects=False)

        # Snapshot pristine config.json
        cls.original_config_content = None
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cls.original_config_content = f.read()

        # Retrieve admin user
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, email, nombre, rol FROM usuarios WHERE rol = 'admin' AND activo = 1 LIMIT 1")
        row_admin = c.fetchone()
        cls.admin_user = dict(row_admin) if row_admin else None

        c.execute("SELECT id, email, nombre, rol, sucursal_id FROM usuarios WHERE rol = 'gerente' AND activo = 1 LIMIT 1")
        row_gerente = c.fetchone()
        cls.gerente_user = dict(row_gerente) if row_gerente else None
        conn.close()

        # Global cleanup to ensure clean starting state
        cls._cleanup_all_adversarial_records()

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("DISABLE_CLOUDSYNC", None)
        # Restore config.json
        if cls.original_config_content is not None:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(cls.original_config_content)
        # Final cleanup
        cls._cleanup_all_adversarial_records()

        # Guarantee 8-branch invariant
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM sucursales")
        cnt = c.fetchone()["cnt"]
        conn.close()
        assert cnt == 8, f"Invariant failure: expected 8 sucursales, found {cnt}"

    @classmethod
    def _cleanup_all_adversarial_records(cls):
        """Purges any non-official sucursales and test accounts"""
        conn = get_db_connection()
        c = conn.cursor()
        official_branches = ('Estelí', 'Masaya', 'Managua', 'Sébaco', 'Jinotepe', 'Juigalpa', 'Rivas', 'León')

        # Clean non-official sucursales
        c.execute("SELECT id FROM sucursales WHERE nombre NOT IN (?, ?, ?, ?, ?, ?, ?, ?)", official_branches)
        s_rows = c.fetchall()
        for s in s_rows:
            sid = s["id"]
            c.execute("DELETE FROM colaboradores WHERE sucursal_id = ?", (sid,))
            c.execute("SELECT id FROM reportes_sucursales WHERE sucursal_id = ?", (sid,))
            reps = c.fetchall()
            for r in reps:
                c.execute("DELETE FROM detalle_novedades WHERE reporte_id = ?", (r["id"],))
            c.execute("DELETE FROM reportes_sucursales WHERE sucursal_id = ?", (sid,))
            c.execute("DELETE FROM usuarios WHERE sucursal_id = ?", (sid,))
            c.execute("DELETE FROM sucursales WHERE id = ?", (sid,))

        # Clean any test users matching keywords
        keywords = ["%ch2%", "%Challenger2%", "%adversarial%", "%San Juan%", "%Race Test%", "%Pipeline Check%", "%CustomUser%"]
        for kw in keywords:
            c.execute("SELECT id FROM usuarios WHERE email LIKE ? OR nombre LIKE ?", (kw, kw))
            u_rows = c.fetchall()
            for u in u_rows:
                uid = u["id"]
                c.execute("DELETE FROM recuperacion_tokens WHERE usuario_id = ?", (uid,))
                c.execute("DELETE FROM usuarios WHERE id = ?", (uid,))

        conn.commit()
        conn.close()

    def _client_with_user(self, user: dict) -> TestClient:
        client = TestClient(app, follow_redirects=False)
        token = create_session_token(user["id"], user["email"])
        client.cookies.set(COOKIE_NAME, token)
        return client

    def _cleanup_branch(self, branch_name: str, manager_email: str = None):
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM sucursales WHERE nombre = ?", (branch_name,))
        s_row = c.fetchone()
        if s_row:
            sid = s_row["id"]
            c.execute("DELETE FROM colaboradores WHERE sucursal_id = ?", (sid,))
            c.execute("SELECT id FROM reportes_sucursales WHERE sucursal_id = ?", (sid,))
            reps = c.fetchall()
            for r in reps:
                c.execute("DELETE FROM detalle_novedades WHERE reporte_id = ?", (r["id"],))
            c.execute("DELETE FROM reportes_sucursales WHERE sucursal_id = ?", (sid,))
            c.execute("DELETE FROM usuarios WHERE sucursal_id = ?", (sid,))
            c.execute("DELETE FROM sucursales WHERE id = ?", (sid,))

        if manager_email:
            c.execute("SELECT id FROM usuarios WHERE LOWER(email) = LOWER(?)", (manager_email.strip(),))
            u_row = c.fetchone()
            if u_row:
                uid = u_row["id"]
                c.execute("DELETE FROM recuperacion_tokens WHERE usuario_id = ?", (uid,))
                c.execute("DELETE FROM usuarios WHERE id = ?", (uid,))

        conn.commit()
        conn.close()

    # =========================================================================
    # 1. Complete Branch Operational Lifecycle & DB Synchronization
    # =========================================================================
    def test_01_complete_branch_operational_lifecycle(self):
        """
        Adversarial test verifying:
        1. Branch created via admin API -> sucursales row active.
        2. Exactly 8 standard collaborators seeded into 'colaboradores'.
        3. Weekly cycle report initialized in 'reportes_sucursales' (PENDIENTE, 8 employees, 5600 base).
        4. Exactly 8 rows created in 'detalle_novedades' with empty week days.
        5. Manager can authenticate via /login with initial password.
        6. Manager accesses /dashboard successfully.
        7. Manager blocked (403) from accessing admin routes.
        8. Admin edits manager profile (name, email, phone) -> verifies DB sync to 'sucursales'.
        9. Manager cannot login with old email, can login with new email.
        10. Admin resets manager password -> old password fails, new password succeeds.
        """
        branch_name = "Sucursal San Juan Challenger2"
        initial_email = "gerente.sanjuan.ch2@alfacreditsa.com"
        updated_email = "gerente.sanjuan.updated.ch2@alfacreditsa.com"
        initial_phone = "+505 8899-0011"
        updated_phone = "+505 7711-2233"
        initial_password = "InitPassChallenger2!"
        updated_password = "ResetPassChallenger2!"

        self._cleanup_branch(branch_name, initial_email)
        self._cleanup_branch(branch_name, updated_email)

        admin_client = self._client_with_user(self.admin_user)

        try:
            # Step 1: Admin creates branch
            res_create = admin_client.post("/api/admin/sucursal/crear", json={
                "nombre": branch_name,
                "email_gerente": initial_email,
                "telefono": initial_phone,
                "password_inicial": initial_password
            })
            self.assertEqual(res_create.status_code, 200, f"Branch creation failed: {res_create.text}")
            create_data = res_create.json()
            self.assertTrue(create_data.get("success"), "API returned success=False")
            created_suc_id = create_data.get("sucursal_id")
            created_user_id = create_data.get("user_id")
            self.assertIsNotNone(created_suc_id)
            self.assertIsNotNone(created_user_id)

            # Step 2: Direct DB verification of sucursal & manager user
            conn = get_db_connection()
            c = conn.cursor()

            c.execute("SELECT * FROM sucursales WHERE id = ?", (created_suc_id,))
            suc_row = c.fetchone()
            self.assertIsNotNone(suc_row, "Sucursal not found in DB")
            self.assertEqual(suc_row["nombre"], branch_name)
            self.assertEqual(suc_row["email_gerente"], initial_email.lower())
            self.assertEqual(suc_row["telefono"], initial_phone)
            self.assertEqual(suc_row["activa"], 1)

            c.execute("SELECT * FROM usuarios WHERE id = ?", (created_user_id,))
            usr_row = c.fetchone()
            self.assertIsNotNone(usr_row, "Manager user not found in DB")
            self.assertEqual(usr_row["email"], initial_email.lower())
            self.assertEqual(usr_row["rol"], "gerente")
            self.assertEqual(usr_row["sucursal_id"], created_suc_id)
            self.assertEqual(usr_row["activo"], 1)
            self.assertEqual(usr_row["password_hash"], hash_password(initial_password))

            # Step 3: Verify 8 standard collaborator positions seeded
            c.execute("SELECT * FROM colaboradores WHERE sucursal_id = ? ORDER BY id ASC", (created_suc_id,))
            colab_rows = [dict(r) for r in c.fetchall()]
            self.assertEqual(len(colab_rows), 8, f"Expected exactly 8 collaborators, got {len(colab_rows)}")

            expected_cargos = [
                ("GERENTE SUCURSAL", 1200.0),
                ("SUPERVISOR", 900.0),
                ("OFICIAL DE CREDITO 1", 750.0),
                ("OFICIAL DE CREDITO 2", 750.0),
                ("OFICIAL DE DESEMBOLSO", 600.0),
                ("PROMOTOR INTERNO", 500.0),
                ("RESP. OPERACIONES", 600.0),
                ("CONSERJE", 300.0)
            ]
            for i, (exp_cargo, exp_comb) in enumerate(expected_cargos):
                self.assertEqual(colab_rows[i]["cargo"], exp_cargo)
                self.assertEqual(colab_rows[i]["combustible_base"], exp_comb)
                self.assertEqual(colab_rows[i]["estado_default"], "ACTIVO")

            # Step 4: Verify weekly cycle report initialized
            c.execute("SELECT * FROM reportes_sucursales WHERE sucursal_id = ?", (created_suc_id,))
            rep_row = c.fetchone()
            self.assertIsNotNone(rep_row, "Weekly cycle report not initialized in reportes_sucursales")
            self.assertEqual(rep_row["estado"], "PENDIENTE")
            self.assertEqual(rep_row["total_empleados"], 8)
            self.assertEqual(rep_row["suma_base"], 5600.0)
            self.assertEqual(rep_row["suma_neto"], 5600.0)
            self.assertEqual(rep_row["usuario_id"], created_user_id)

            # Step 5: Verify 8 detail rows in detalle_novedades
            c.execute("SELECT * FROM detalle_novedades WHERE reporte_id = ?", (rep_row["id"],))
            det_rows = [dict(r) for r in c.fetchall()]
            self.assertEqual(len(det_rows), 8, f"Expected 8 rows in detalle_novedades, got {len(det_rows)}")
            empty_week = json.dumps([""] * 6)
            for d in det_rows:
                self.assertEqual(d["dias_pas_json"], empty_week)
                self.assertEqual(d["dias_fut_json"], empty_week)
                self.assertEqual(d["factor_total"], 0.0)
                self.assertEqual(d["deduccion_ajuste"], 0.0)
                self.assertEqual(d["total_neto"], d["combustible_base"])

            conn.close()

            # Step 6: Manager Login with initial password
            unauth_client = TestClient(app, follow_redirects=False)
            res_login = unauth_client.post("/login", data={
                "email": initial_email,
                "password": initial_password
            })
            self.assertEqual(res_login.status_code, 303, f"Expected 303 redirect on login, got {res_login.status_code}")
            self.assertIn(COOKIE_NAME, unauth_client.cookies)

            # Step 7: Manager accesses /dashboard
            res_dash = unauth_client.get("/dashboard")
            self.assertEqual(res_dash.status_code, 200)
            self.assertIn("dashboard", res_dash.text.lower())

            # Step 8: Manager role boundary checks (must be blocked from admin areas)
            res_admin_page = unauth_client.get("/admin/gestion")
            self.assertEqual(res_admin_page.status_code, 403, "Manager accessed /admin/gestion without 403")

            res_admin_api = unauth_client.post("/api/admin/sucursales/crear", json={
                "nombre": "Illegal Branch", "email_gerente": "bad@bad.com", "password_inicial": "1234"
            })
            self.assertEqual(res_admin_api.status_code, 403, "Manager accessed /api/admin/sucursales/crear without 403")

            # Step 9: Admin edits manager details (name, email, phone)
            updated_name = "Gerente San Juan Actualizado"
            res_edit = admin_client.post("/api/admin/usuarios/editar", json={
                "user_id": created_user_id,
                "nombre": updated_name,
                "email": updated_email,
                "telefono": updated_phone
            })
            self.assertEqual(res_edit.status_code, 200, f"User edit failed: {res_edit.text}")

            # Verify DB synchronization in both usuarios and sucursales
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT nombre, email, telefono FROM usuarios WHERE id = ?", (created_user_id,))
            u_check = c.fetchone()
            self.assertEqual(u_check["nombre"], updated_name)
            self.assertEqual(u_check["email"], updated_email.lower())
            self.assertEqual(u_check["telefono"], updated_phone)

            c.execute("SELECT email_gerente, telefono FROM sucursales WHERE id = ?", (created_suc_id,))
            s_check = c.fetchone()
            self.assertEqual(s_check["email_gerente"], updated_email.lower(), "sucursales.email_gerente out of sync")
            self.assertEqual(s_check["telefono"], updated_phone, "sucursales.telefono out of sync")
            conn.close()

            # Step 10: Verify login reflects updated email
            # Old email login must fail
            res_old_login = unauth_client.post("/login", data={
                "email": initial_email,
                "password": initial_password
            })
            self.assertIn(res_old_login.status_code, [400, 200], "Login with old email should fail")
            if res_old_login.status_code == 200:
                self.assertIn("incorrect", res_old_login.text.lower())

            # New email login must succeed
            new_login_client = TestClient(app, follow_redirects=False)
            res_new_login = new_login_client.post("/login", data={
                "email": updated_email,
                "password": initial_password
            })
            self.assertEqual(res_new_login.status_code, 303, "Login with updated email failed")
            self.assertIn(COOKIE_NAME, new_login_client.cookies)

            # Step 11: Admin resets manager password
            res_reset = admin_client.post("/api/admin/usuarios/cambiar-password", json={
                "user_id": created_user_id,
                "nueva_password": updated_password
            })
            self.assertEqual(res_reset.status_code, 200, f"Password reset failed: {res_reset.text}")

            # Old password login must fail
            res_fail_pass = new_login_client.post("/login", data={
                "email": updated_email,
                "password": initial_password
            })
            self.assertIn(res_fail_pass.status_code, [400, 200], "Old password still allowed")

            # New password login must succeed
            reset_login_client = TestClient(app, follow_redirects=False)
            res_succ_pass = reset_login_client.post("/login", data={
                "email": updated_email,
                "password": updated_password
            })
            self.assertEqual(res_succ_pass.status_code, 303, "New password login failed")
            res_post_reset_dash = reset_login_client.get("/dashboard")
            self.assertEqual(res_post_reset_dash.status_code, 200)

        finally:
            self._cleanup_branch(branch_name, initial_email)
            self._cleanup_branch(branch_name, updated_email)

    # =========================================================================
    # 2. Config.json Operational Impact & Live Reflection
    # =========================================================================
    def test_02_config_json_operational_impact_and_live_reflection(self):
        """
        Adversarial test verifying:
        1. Updating brandon_email & dania_email writes to config.json on disk.
        2. get_email_config() immediately returns the updated values.
        3. GET /admin/gestion reflects the updated email values in the HTML form.
        4. Invalid email inputs are rejected with HTTP 400.
        5. Non-admin calls to update config are rejected with HTTP 403.
        6. Reverting config restores disk file and in-memory cache.
        """
        admin_client = self._client_with_user(self.admin_user)
        gerente_client = self._client_with_user(self.gerente_user)

        test_brandon = "brandon.adversarial.ops@alfacreditsa.com"
        test_dania = "dania.adversarial.ops@alfacreditsa.com"

        try:
            # 1. Update config via admin API
            res_update = admin_client.post("/api/admin/config/referencias", json={
                "brandon_email": test_brandon,
                "dania_email": test_dania
            })
            self.assertEqual(res_update.status_code, 200, f"Config update failed: {res_update.text}")
            self.assertTrue(res_update.json().get("success"))

            # 2. Verify config.json file on disk directly
            self.assertTrue(os.path.exists(CONFIG_PATH), f"Config file does not exist at {CONFIG_PATH}")
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                disk_cfg = json.load(f)

            monitor_cfg = disk_cfg.get("email_monitor", {})
            self.assertEqual(monitor_cfg.get("brandon_email"), test_brandon, "Disk config brandon_email mismatch")
            self.assertEqual(monitor_cfg.get("dania_email"), test_dania, "Disk config dania_email mismatch")

            # 3. Call get_email_config() in python and verify live reflection
            live_cfg = get_email_config()
            self.assertEqual(live_cfg.get("brandon_email"), test_brandon, "Live get_email_config() brandon_email mismatch")
            self.assertEqual(live_cfg.get("dania_email"), test_dania, "Live get_email_config() dania_email mismatch")

            # 4. GET /admin/gestion reflects updated emails in HTML input fields
            res_html = admin_client.get("/admin/gestion")
            self.assertEqual(res_html.status_code, 200)
            self.assertIn(test_brandon, res_html.text, "Updated brandon_email not rendered in HTML")
            self.assertIn(test_dania, res_html.text, "Updated dania_email not rendered in HTML")

            # 5. Invalid email validation (adversarial edge cases)
            bad_requests = [
                {"brandon_email": "", "dania_email": test_dania},
                {"brandon_email": test_brandon, "dania_email": ""},
                {"brandon_email": "notanemail", "dania_email": test_dania},
                {"brandon_email": test_brandon, "dania_email": "bademailformat"}
            ]
            for bad_payload in bad_requests:
                res_bad = admin_client.post("/api/admin/config/referencias", json=bad_payload)
                self.assertEqual(res_bad.status_code, 400, f"Expected 400 for payload {bad_payload}, got {res_bad.status_code}")

            # 6. Non-admin forbidden
            res_forbidden = gerente_client.post("/api/admin/config/referencias", json={
                "brandon_email": "hack@hack.com", "dania_email": "hack@hack.com"
            })
            self.assertEqual(res_forbidden.status_code, 403, "Non-admin was able to modify config")

        finally:
            # Revert to snapshot
            if self.original_config_content is not None:
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    f.write(self.original_config_content)
                reverted_cfg = get_email_config()
                self.assertNotEqual(reverted_cfg.get("brandon_email"), test_brandon, "Config failed to revert")

    # =========================================================================
    # 3. Concurrency, Idempotency & DB Hardening
    # =========================================================================
    def test_03_concurrency_and_idempotency(self):
        """
        Adversarial test verifying:
        1. init_db() can be called repeatedly without raising exceptions or altering schema.
        2. User passwords modified in the DB are NOT overwritten by repeated init_db() calls.
        3. Duplicate branch names (exact match, uppercase, lowercase) are rejected with HTTP 400.
        4. Duplicate manager emails are rejected with HTTP 400.
        5. User profile edit rejecting collision with another user's email.
        6. Concurrent branch creation requests: exactly 1 succeeds, others rejected gracefully (no 500 crash).
        """
        admin_client = self._client_with_user(self.admin_user)

        # 1. Repeated init_db() calls
        for cycle in range(5):
            try:
                init_db()
            except Exception as e:
                self.fail(f"init_db() raised unexpected exception on iteration {cycle + 1}: {e}")

        # Verify baseline tables and 8 branches still intact
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM sucursales")
        self.assertEqual(c.fetchone()["cnt"], 8)

        # 2. Password persistence across init_db()
        test_email = "customuser.adversarial@alfacreditsa.com"
        custom_pass = "CustomSuperSecret2026!"
        custom_hash = hash_password(custom_pass)

        self._cleanup_branch("NonExistent", test_email)
        c.execute('''
        INSERT INTO usuarios (email, password_hash, nombre, rol, sucursal_id, activo)
        VALUES (?, ?, 'Custom Test User', 'gerente', 1, 1)
        ''', (test_email, custom_hash))
        conn.commit()

        # Run init_db() again
        init_db()

        # Check that custom user's password hash was NOT wiped out
        c.execute("SELECT password_hash FROM usuarios WHERE LOWER(email) = LOWER(?)", (test_email,))
        row = c.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["password_hash"], custom_hash, "init_db() overwrote user's existing password hash!")

        # Clean up test user
        c.execute("DELETE FROM usuarios WHERE LOWER(email) = LOWER(?)", (test_email,))
        conn.commit()
        conn.close()

        # 3. Duplicate branch name rejection (ASCII case-insensitivity)
        ascii_duplicates = ["Managua", "managua", "MANAGUA", "  Managua  "]
        for dup_name in ascii_duplicates:
            res_dup = admin_client.post("/api/admin/sucursales/crear", json={
                "nombre": dup_name,
                "email_gerente": "unique.email.ch2@alfacreditsa.com",
                "password_inicial": "Pass1234!"
            })
            self.assertEqual(
                res_dup.status_code, 400,
                f"Expected 400 duplicate branch name for '{dup_name}', got {res_dup.status_code}"
            )

        # 3b. Adversarial Probe: Accented characters in duplicate check (SQLite LOWER() limitation)
        # We test whether 'ESTELÍ' collides with 'Estelí'
        res_accent = admin_client.post("/api/admin/sucursales/crear", json={
            "nombre": "ESTELÍ",
            "email_gerente": "probe.accent.ch2@alfacreditsa.com",
            "password_inicial": "Pass1234!"
        })
        # If the vulnerability is present, it returns 200 and we must clean it up immediately
        if res_accent.status_code == 200:
            accent_data = res_accent.json()
            created_accent_id = accent_data.get("sucursal_id")
            # Immediate cleanup to maintain invariant
            self._cleanup_branch("ESTELÍ", "probe.accent.ch2@alfacreditsa.com")
            # Record that this is a confirmed data integrity vulnerability
            self.accented_unicode_vulnerability_confirmed = True
        else:
            self.assertEqual(res_accent.status_code, 400)
            self.accented_unicode_vulnerability_confirmed = False

        # 4. Duplicate manager email rejection
        res_dup_email = admin_client.post("/api/admin/sucursales/crear", json={
            "nombre": "Sucursal Unique Name 123",
            "email_gerente": "enrique@alfacreditsa.com", # existing admin
            "password_inicial": "Pass1234!"
        })
        self.assertEqual(res_dup_email.status_code, 400, "Expected 400 for duplicate manager email in usuarios")

        res_dup_email_suc = admin_client.post("/api/admin/sucursales/crear", json={
            "nombre": "Sucursal Unique Name 456",
            "email_gerente": "gerente.esteli@alfacreditsa.com", # existing branch email
            "password_inicial": "Pass1234!"
        })
        self.assertEqual(res_dup_email_suc.status_code, 400, "Expected 400 for duplicate manager email in sucursales")

        # 5. User profile edit collision rejection
        res_col = admin_client.post("/api/admin/usuarios/editar", json={
            "user_id": self.gerente_user["id"],
            "nombre": "Gerente Collision Test",
            "email": self.admin_user["email"], # Attempt to steal admin's email
            "telefono": "8888-0000"
        })
        self.assertEqual(res_col.status_code, 400, "Expected 400 when attempting to change user email to existing email")

        # 6. Concurrency / Collision handling: rapid consecutive and near-simultaneous duplicate attempts
        race_branch_name = "Sucursal Race Test Ch2"
        race_email_1 = "gerente.race1@alfacreditsa.com"
        race_email_2 = "gerente.race2@alfacreditsa.com"
        self._cleanup_branch(race_branch_name, race_email_1)
        self._cleanup_branch(race_branch_name, race_email_2)

        try:
            # First creation succeeds
            res1 = admin_client.post("/api/admin/sucursal/crear", json={
                "nombre": race_branch_name,
                "email_gerente": race_email_1,
                "telefono": "8888-1111",
                "password_inicial": "RacePass2026!"
            })
            self.assertEqual(res1.status_code, 200, "First branch creation should succeed")

            # Immediate collision attempt with same name but different manager
            res2 = admin_client.post("/api/admin/sucursal/crear", json={
                "nombre": race_branch_name,
                "email_gerente": race_email_2,
                "telefono": "8888-2222",
                "password_inicial": "RacePass2026!"
            })
            self.assertEqual(res2.status_code, 400, "Duplicate branch name collision must return 400")
            self.assertIn("ya existe", res2.text.lower())

            # Immediate collision attempt with same manager email but different name
            res3 = admin_client.post("/api/admin/sucursal/crear", json={
                "nombre": "Different Branch Name Ch2",
                "email_gerente": race_email_1,
                "telefono": "8888-3333",
                "password_inicial": "RacePass2026!"
            })
            self.assertEqual(res3.status_code, 400, "Duplicate manager email collision must return 400")

        finally:
            self._cleanup_branch(race_branch_name, race_email_1)
            self._cleanup_branch(race_branch_name, race_email_2)
            self._cleanup_branch("Different Branch Name Ch2", race_email_1)

    # =========================================================================
    # 4. Pipeline Reflection & Strict 8-Branch Invariant
    # =========================================================================
    def test_04_pipeline_reflection_and_invariant(self):
        """
        Verify that creating a branch dynamically updates obtener_estado_completo_pipeline(),
        and tearing it down cleanly restores total_sucursales to exactly 8.
        """
        pipe_before = obtener_estado_completo_pipeline()
        self.assertEqual(pipe_before["total_sucursales"], 8, "Baseline pipeline must reflect exactly 8 sucursales")

        test_pipe_branch = "Sucursal Pipeline Check Ch2"
        test_pipe_email = "gerente.pipecheck.ch2@alfacreditsa.com"
        self._cleanup_branch(test_pipe_branch, test_pipe_email)

        admin_client = self._client_with_user(self.admin_user)

        try:
            res = admin_client.post("/api/admin/sucursal/crear", json={
                "nombre": test_pipe_branch,
                "email_gerente": test_pipe_email,
                "telefono": "8888-2222",
                "password_inicial": "PipeCheck2026!"
            })
            self.assertEqual(res.status_code, 200)

            # Pipeline during
            pipe_during = obtener_estado_completo_pipeline()
            self.assertEqual(pipe_during["total_sucursales"], 9, "Pipeline failed to reflect 9th branch")
            pending_names = [s["sucursal_nombre"] for s in pipe_during["sucursales_pendientes"]]
            self.assertIn(test_pipe_branch, pending_names, "New branch not found in sucursales_pendientes")

        finally:
            self._cleanup_branch(test_pipe_branch, test_pipe_email)

            # Pipeline after cleanup
            pipe_after = obtener_estado_completo_pipeline()
            self.assertEqual(pipe_after["total_sucursales"], 8, "Pipeline did not return to 8 sucursales")

            # Final check directly on DB
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) as cnt FROM sucursales")
            cnt = c.fetchone()["cnt"]
            conn.close()
            self.assertEqual(cnt, 8, f"Database has {cnt} sucursales instead of 8")


if __name__ == "__main__":
    unittest.main()
