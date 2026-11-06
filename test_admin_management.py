"""
Suite de Pruebas Automatizadas E2E — Panel de Gestión Administrativa
AlfaCredit S.A. - Sistema de Novedades y Combustible

4-Tier Test Suite Architecture:
- Tier 1: Feature Coverage (RBAC 403, HTML 302, Admin 200, Sucursal creation, Login, Password Reset, User Edit, Config Edit)
- Tier 2: Boundary & Corner Cases (Duplicate name/email, Missing fields, Non-existent user, Invalid email format)
- Tier 3: Cross-Feature Combinations (Full branch & manager operational lifecycle)
- Tier 4: Real-World Scenarios (Multi-branch admin, Config reflection in email service, Active cycle sync)

CRITICAL ISOLATION INVARIANT:
The database must maintain exactly 8 active baseline branches so test_platform.py passes 100% without state pollution.
All created test branches, users, and config modifications are guaranteed to be cleaned up in teardowns.
"""

import os
import sys
import json
import unittest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from app.main import app, obtener_estado_completo_pipeline
from app.database import init_db, get_db_connection, hash_password
from app.auth import create_session_token, COOKIE_NAME
from app.email_service import get_email_config, CONFIG_PATH


class TestAdminManagementSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["DISABLE_CLOUDSYNC"] = "1"
        init_db()
        cls.client = TestClient(app, follow_redirects=False)

        # Snapshot baseline config.json
        cls.original_config_content = None
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cls.original_config_content = f.read()

        # Retrieve principal users for RBAC testing
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, email, nombre, rol, sucursal_id FROM usuarios WHERE rol = 'admin' AND activo = 1 LIMIT 1")
        row_admin = c.fetchone()
        cls.admin_user = dict(row_admin) if row_admin else None

        c.execute("SELECT id, email, nombre, rol, sucursal_id FROM usuarios WHERE rol = 'gerente' AND activo = 1 LIMIT 1")
        row_gerente = c.fetchone()
        cls.gerente_user = dict(row_gerente) if row_gerente else None

        c.execute("SELECT id, email, nombre, rol, sucursal_id FROM usuarios WHERE rol = 'brandon' AND activo = 1 LIMIT 1")
        row_brandon = c.fetchone()
        cls.brandon_user = dict(row_brandon) if row_brandon else None

        c.execute("SELECT id, email, nombre, rol, sucursal_id FROM usuarios WHERE rol = 'jefe_credito' AND activo = 1 LIMIT 1")
        row_jefe = c.fetchone()
        cls.jefe_credito_user = dict(row_jefe) if row_jefe else None
        conn.close()

        # Ensure starting state has zero test leftovers
        cls._cleanup_test_records_global()

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("DISABLE_CLOUDSYNC", None)
        # Revert config.json to pristine state
        if cls.original_config_content is not None:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(cls.original_config_content)
        # Final cleanup pass
        cls._cleanup_test_records_global()

    @classmethod
    def _cleanup_test_records_global(cls):
        """Purge any non-standard branch or test user to guarantee the 8-branch invariant"""
        conn = get_db_connection()
        c = conn.cursor()
        official_branches = ('Estelí', 'Masaya', 'Managua', 'Sébaco', 'Jinotepe', 'Juigalpa', 'Rivas', 'León')
        
        # 1. Clean up any non-official sucursales
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

        # 2. Clean up any test users
        keywords = ["%Test%", "%test%", "%Somoto%", "%Boaco%", "%Granada%", "%Bluefields%", "%Bilwi%", "%San Carlos%"]
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
        """Returns a TestClient instance authenticated as the provided user"""
        client = TestClient(app, follow_redirects=False)
        token = create_session_token(user["id"], user["email"])
        client.cookies.set(COOKIE_NAME, token)
        return client

    def _cleanup_branch_by_name(self, branch_name: str):
        """Helper to cleanly purge a specific test branch and its linked accounts"""
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
        conn.commit()
        conn.close()

    def _cleanup_user_by_email(self, email: str):
        """Helper to cleanly purge a specific test user account"""
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id FROM usuarios WHERE LOWER(email) = LOWER(?)", (email.strip(),))
        u_row = c.fetchone()
        if u_row:
            uid = u_row["id"]
            c.execute("DELETE FROM recuperacion_tokens WHERE usuario_id = ?", (uid,))
            c.execute("DELETE FROM usuarios WHERE id = ?", (uid,))
        conn.commit()
        conn.close()

    def _call_post_admin(self, primary_url: str, alt_url: str, payload: dict, user: dict = None):
        """Dispatches an admin POST request, with automatic fallback to alias endpoint if 404"""
        active_user = user or self.admin_user
        client = self._client_with_user(active_user)
        res = client.post(primary_url, json=payload)
        if res.status_code == 404 and alt_url:
            res = client.post(alt_url, json=payload)
        return res

    # =========================================================================
    # TIER 1: FEATURE COVERAGE
    # =========================================================================

    def test_01_rbac_unauthenticated_access_blocked(self):
        """
        Unauthenticated requests to admin HTML view must redirect to /login (302/303)
        or return 401/403. Admin APIs must return 401/403 or redirect.
        """
        client = TestClient(app, follow_redirects=False)

        # 1. HTML View
        res_view = client.get("/admin/gestion")
        self.assertIn(
            res_view.status_code, [302, 303, 307, 401, 403],
            f"Expected redirect or 401/403 for unauth /admin/gestion, got {res_view.status_code}"
        )
        if res_view.status_code in [302, 303, 307]:
            location = res_view.headers.get("location", "")
            self.assertIn("/login", location, f"Expected redirect to /login, got {location}")

        # 2. Administrative API endpoints
        api_endpoints = [
            "/api/admin/sucursal/crear",
            "/api/admin/usuario/reset-password",
            "/api/admin/usuario/editar",
            "/api/admin/config/referencias"
        ]
        for ep in api_endpoints:
            res_api = client.post(ep, json={})
            self.assertIn(
                res_api.status_code, [302, 303, 307, 401, 403],
                f"Expected 401/403 or redirect for unauthenticated {ep}, got {res_api.status_code}"
            )

    def test_02_rbac_non_admin_roles_forbidden(self):
        """
        Users with roles 'gerente', 'brandon', and 'jefe_credito' must receive
        HTTP 403 Forbidden on /admin/gestion and all /api/admin/... endpoints.
        """
        test_principals = [
            ("gerente", self.gerente_user),
            ("brandon", self.brandon_user),
            ("jefe_credito", self.jefe_credito_user)
        ]

        endpoints_to_test = [
            ("GET", "/admin/gestion", None),
            ("POST", "/api/admin/sucursal/crear", {"nombre": "X", "email_gerente": "x@test.com", "password_inicial": "1234"}),
            ("POST", "/api/admin/usuario/reset-password", {"user_id": 1, "nueva_password": "NewPass2026!"}),
            ("POST", "/api/admin/usuario/editar", {"user_id": 1, "nombre": "X", "email": "x@x.com", "telefono": "123"}),
            ("POST", "/api/admin/config/referencias", {"brandon_email": "b@test.com", "dania_email": "d@test.com"})
        ]

        for role_name, user_dict in test_principals:
            self.assertIsNotNone(user_dict, f"Principal for role '{role_name}' must exist in DB")
            client = self._client_with_user(user_dict)

            for method, path, body in endpoints_to_test:
                if method == "GET":
                    res = client.get(path)
                else:
                    res = client.post(path, json=body or {})

                self.assertEqual(
                    res.status_code, 403,
                    f"Security violation: role '{role_name}' accessed {method} {path} with status {res.status_code} instead of 403 Forbidden"
                )

    def test_03_admin_access_allowed_on_panel(self):
        """Role 'admin' must receive HTTP 200 OK on GET /admin/gestion with valid HTML content"""
        self.assertIsNotNone(self.admin_user, "Admin user must exist in DB")
        client = self._client_with_user(self.admin_user)
        res = client.get("/admin/gestion")
        self.assertEqual(res.status_code, 200, f"Expected 200 for admin on /admin/gestion, got {res.status_code}")
        self.assertIn("text/html", res.headers.get("content-type", ""))
        self.assertTrue(
            "admin" in res.text.lower() or "gesti" in res.text.lower() or "sucursal" in res.text.lower(),
            "Expected admin panel template content in response body"
        )

    def test_04_create_branch_with_manager(self):
        """
        POST /api/admin/sucursal/crear creates branch in 'sucursales' and linked manager
        account in 'usuarios' with initial password.
        """
        branch_name = "Sucursal Boaco Test"
        manager_email = "gerente.boaco.test@alfacreditsa.com"
        manager_phone = "+505 2542-0001"
        initial_password = "BoacoPassword2026!"

        self._cleanup_branch_by_name(branch_name)
        self._cleanup_user_by_email(manager_email)

        try:
            res = self._call_post_admin(
                "/api/admin/sucursal/crear",
                "/api/admin/sucursales/crear",
                {
                    "nombre": branch_name,
                    "email_gerente": manager_email,
                    "telefono": manager_phone,
                    "password_inicial": initial_password
                }
            )
            self.assertEqual(res.status_code, 200, f"Branch creation failed: {res.text}")
            data = res.json()
            self.assertTrue(data.get("success", False), f"Expected success=True: {data}")

            # Verify branch in DB
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT * FROM sucursales WHERE nombre = ?", (branch_name,))
            suc_row = c.fetchone()
            self.assertIsNotNone(suc_row, "Branch must be inserted in 'sucursales'")
            self.assertEqual(suc_row["email_gerente"].lower(), manager_email.lower())

            # Verify manager in DB
            c.execute("SELECT * FROM usuarios WHERE LOWER(email) = LOWER(?)", (manager_email,))
            usr_row = c.fetchone()
            self.assertIsNotNone(usr_row, "Manager user must be inserted in 'usuarios'")
            self.assertEqual(usr_row["rol"], "gerente")
            self.assertEqual(usr_row["sucursal_id"], suc_row["id"])
            self.assertEqual(usr_row["password_hash"], hash_password(initial_password))
            conn.close()

        finally:
            self._cleanup_branch_by_name(branch_name)
            self._cleanup_user_by_email(manager_email)

    def test_05_immediate_login_new_manager(self):
        """
        Newly created branch manager can immediately authenticate via /login
        with the initial password and access /dashboard.
        """
        branch_name = "Sucursal Somoto Test"
        manager_email = "gerente.somoto.test@alfacreditsa.com"
        initial_password = "SomotoInitial2026!"

        self._cleanup_branch_by_name(branch_name)
        self._cleanup_user_by_email(manager_email)

        try:
            # 1. Admin creates branch
            res_create = self._call_post_admin(
                "/api/admin/sucursal/crear",
                "/api/admin/sucursales/crear",
                {
                    "nombre": branch_name,
                    "email_gerente": manager_email,
                    "telefono": "+505 2722-1111",
                    "password_inicial": initial_password
                }
            )
            self.assertEqual(res_create.status_code, 200)

            # 2. Manager logs in with unauthenticated client
            unauth_client = TestClient(app, follow_redirects=False)
            res_login = unauth_client.post("/login", data={
                "email": manager_email,
                "password": initial_password
            })
            self.assertEqual(
                res_login.status_code, 303,
                f"Expected 303 redirect upon successful login, got {res_login.status_code}: {res_login.text}"
            )
            self.assertIn(COOKIE_NAME, unauth_client.cookies, "Session cookie must be set after login")

            # 3. Manager can access dashboard
            res_dash = unauth_client.get("/dashboard")
            self.assertEqual(res_dash.status_code, 200)
            self.assertIn("dashboard", res_dash.text.lower())

        finally:
            self._cleanup_branch_by_name(branch_name)
            self._cleanup_user_by_email(manager_email)

    def test_06_direct_password_reset(self):
        """
        Admin resets password for any user account; immediate login enabled with new password.
        """
        test_email = "gerente.reset.test@alfacreditsa.com"
        initial_pass = "OldPass2026!"
        new_pass = "BrandNewSecurePass2026!"

        self._cleanup_user_by_email(test_email)

        # Seed temporary test user
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
        INSERT INTO usuarios (email, password_hash, nombre, rol, sucursal_id, activo)
        VALUES (?, ?, 'User Reset Test', 'gerente', 1, 1)
        ''', (test_email, hash_password(initial_pass)))
        target_user_id = c.lastrowid
        conn.commit()
        conn.close()

        try:
            res_reset = self._call_post_admin(
                "/api/admin/usuario/reset-password",
                "/api/admin/usuarios/cambiar-password",
                {
                    "user_id": target_user_id,
                    "nueva_password": new_pass
                }
            )
            self.assertEqual(res_reset.status_code, 200, f"Password reset failed: {res_reset.text}")
            self.assertTrue(res_reset.json().get("success", False))

            # Verify in DB
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT password_hash FROM usuarios WHERE id = ?", (target_user_id,))
            updated_hash = c.fetchone()["password_hash"]
            self.assertEqual(updated_hash, hash_password(new_pass))
            conn.close()

            # Verify login with new credentials
            unauth_client = TestClient(app, follow_redirects=False)
            res_login = unauth_client.post("/login", data={"email": test_email, "password": new_pass})
            self.assertEqual(res_login.status_code, 303)

        finally:
            self._cleanup_user_by_email(test_email)

    def test_07_user_profile_edit(self):
        """
        Admin updates 'nombre', 'email', and 'telefono' in usuarios table
        and syncs branch manager details if applicable.
        """
        test_email = "gerente.profile.edit@alfacreditsa.com"
        updated_email = "gerente.profile.modificado@alfacreditsa.com"
        self._cleanup_user_by_email(test_email)
        self._cleanup_user_by_email(updated_email)

        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
        INSERT INTO usuarios (email, password_hash, nombre, rol, sucursal_id, telefono, activo)
        VALUES (?, ?, 'Perfil Original', 'gerente', 1, '2222-0000', 1)
        ''', (test_email, hash_password("1234")))
        test_uid = c.lastrowid
        conn.commit()
        conn.close()

        try:
            res_edit = self._call_post_admin(
                "/api/admin/usuario/editar",
                "/api/admin/usuarios/editar",
                {
                    "user_id": test_uid,
                    "nombre": "Nombre Actualizado Admin",
                    "email": updated_email,
                    "telefono": "+505 8888-9999"
                }
            )
            self.assertEqual(res_edit.status_code, 200, f"User edit failed: {res_edit.text}")
            self.assertTrue(res_edit.json().get("success", False))

            # Verify DB
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT nombre, email, telefono FROM usuarios WHERE id = ?", (test_uid,))
            row = c.fetchone()
            self.assertEqual(row["nombre"], "Nombre Actualizado Admin")
            self.assertEqual(row["email"].lower(), updated_email.lower())
            self.assertEqual(row["telefono"], "+505 8888-9999")
            conn.close()

        finally:
            self._cleanup_user_by_email(test_email)
            self._cleanup_user_by_email(updated_email)

    def test_08_config_references_update(self):
        """
        POST /api/admin/config/referencias updates brandon_email and dania_email in config.json.
        """
        new_brandon = "brandon.referencia.test@alfacreditsa.com"
        new_dania = "dania.referencia.test@alfacreditsa.com"

        res = self._call_post_admin(
            "/api/admin/config/referencias",
            None,
            {
                "brandon_email": new_brandon,
                "dania_email": new_dania
            }
        )
        self.assertEqual(res.status_code, 200, f"Config update failed: {res.text}")
        self.assertTrue(res.json().get("success", False))

        # Check config on disk
        cfg = get_email_config()
        self.assertEqual(cfg.get("brandon_email"), new_brandon)
        self.assertEqual(cfg.get("dania_email"), new_dania)

    # =========================================================================
    # TIER 2: BOUNDARY & CORNER CASES
    # =========================================================================

    def test_09_duplicate_branch_rejection(self):
        """Attempting to create a branch with an existing branch name must be rejected"""
        branch_name = "Sucursal Granada Unica"
        email_1 = "gerente.g1@alfacreditsa.com"
        email_2 = "gerente.g2@alfacreditsa.com"

        self._cleanup_branch_by_name(branch_name)
        self._cleanup_user_by_email(email_1)
        self._cleanup_user_by_email(email_2)

        try:
            # First creation succeeds
            res1 = self._call_post_admin(
                "/api/admin/sucursal/crear",
                "/api/admin/sucursales/crear",
                {"nombre": branch_name, "email_gerente": email_1, "password_inicial": "Pass1234!"}
            )
            self.assertEqual(res1.status_code, 200)

            # Duplicate creation must fail (400, 409, 422, or success=False)
            res2 = self._call_post_admin(
                "/api/admin/sucursal/crear",
                "/api/admin/sucursales/crear",
                {"nombre": branch_name, "email_gerente": email_2, "password_inicial": "Pass1234!"}
            )
            if res2.status_code == 200:
                self.assertFalse(res2.json().get("success", False), "Duplicate branch name must return success=False")
            else:
                self.assertIn(res2.status_code, [400, 409, 422])

        finally:
            self._cleanup_branch_by_name(branch_name)
            self._cleanup_user_by_email(email_1)
            self._cleanup_user_by_email(email_2)

    def test_10_duplicate_user_email_rejection(self):
        """Registering or editing a user with an already existing email must be rejected"""
        existing_email = self.admin_user["email"]
        res = self._call_post_admin(
            "/api/admin/sucursal/crear",
            "/api/admin/sucursales/crear",
            {"nombre": "Branch Duplicate Email Test", "email_gerente": existing_email, "password_inicial": "1234"}
        )
        if res.status_code == 200:
            self.assertFalse(res.json().get("success", False), "Duplicate email must not succeed")
        else:
            self.assertIn(res.status_code, [400, 409, 422])

    def test_11_missing_required_fields_rejection(self):
        """Submitting empty branch name or empty email must fail validation"""
        invalid_payloads = [
            {"nombre": "", "email_gerente": "valido@alfacreditsa.com", "password_inicial": "1234"},
            {"nombre": "Sucursal Valida", "email_gerente": "", "password_inicial": "1234"},
            {"nombre": "Sucursal Valida 2", "email_gerente": "sin_pass@alfacreditsa.com", "password_inicial": ""}
        ]

        for p in invalid_payloads:
            res = self._call_post_admin("/api/admin/sucursal/crear", "/api/admin/sucursales/crear", p)
            if res.status_code == 200:
                self.assertFalse(res.json().get("success", False), f"Missing fields payload should fail: {p}")
            else:
                self.assertIn(res.status_code, [400, 422])

    def test_12_password_reset_nonexistent_user(self):
        """Password reset on a non-existent user ID must return 400/404 or success=False"""
        res = self._call_post_admin(
            "/api/admin/usuario/reset-password",
            "/api/admin/usuarios/cambiar-password",
            {"user_id": 999999, "nueva_password": "ImpossiblePass2026!"}
        )
        if res.status_code == 200:
            self.assertFalse(res.json().get("success", False))
        else:
            self.assertIn(res.status_code, [400, 404, 422])

    def test_13_invalid_email_format_config(self):
        """Updating config with malformed email addresses must fail validation"""
        res = self._call_post_admin(
            "/api/admin/config/referencias",
            None,
            {"brandon_email": "not_an_email", "dania_email": "also_invalid"}
        )
        if res.status_code == 200:
            self.assertFalse(res.json().get("success", False), "Invalid email format must return success=False")
        else:
            self.assertIn(res.status_code, [400, 422])

    # =========================================================================
    # TIER 3: CROSS-FEATURE COMBINATIONS
    # =========================================================================

    def test_14_full_admin_lifecycle(self):
        """
        End-to-End full operational lifecycle:
        1. Admin creates new branch & gerente.
        2. Gerente logs in with initial password.
        3. Admin resets gerente password.
        4. Old password login rejected.
        5. New password login succeeds.
        6. Admin updates gerente name, email, and phone.
        7. Verify DB persistence and login with updated email.
        """
        branch_name = "Sucursal San Carlos Lifecycle"
        initial_email = "gerente.sancarlos.init@alfacreditsa.com"
        updated_email = "gerente.sancarlos.mod@alfacreditsa.com"
        pass_v1 = "SanCarlosInitial2026!"
        pass_v2 = "SanCarlosNewPassword2026!"

        self._cleanup_branch_by_name(branch_name)
        self._cleanup_user_by_email(initial_email)
        self._cleanup_user_by_email(updated_email)

        try:
            # Step 1: Admin creates branch
            res_create = self._call_post_admin(
                "/api/admin/sucursal/crear",
                "/api/admin/sucursales/crear",
                {
                    "nombre": branch_name,
                    "email_gerente": initial_email,
                    "telefono": "+505 2583-1111",
                    "password_inicial": pass_v1
                }
            )
            self.assertEqual(res_create.status_code, 200)
            data_create = res_create.json()
            self.assertTrue(data_create.get("success", False))

            # Retrieve created user ID
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT id FROM usuarios WHERE LOWER(email) = LOWER(?)", (initial_email,))
            uid = c.fetchone()["id"]
            conn.close()

            # Step 2: Gerente logs in with pass_v1
            c1 = TestClient(app, follow_redirects=False)
            res_l1 = c1.post("/login", data={"email": initial_email, "password": pass_v1})
            self.assertEqual(res_l1.status_code, 303)

            # Step 3: Admin resets password to pass_v2
            res_reset = self._call_post_admin(
                "/api/admin/usuario/reset-password",
                "/api/admin/usuarios/cambiar-password",
                {"user_id": uid, "nueva_password": pass_v2}
            )
            self.assertEqual(res_reset.status_code, 200)
            self.assertTrue(res_reset.json().get("success", False))

            # Step 4: Login with old password fails
            c_fail = TestClient(app, follow_redirects=False)
            res_fail = c_fail.post("/login", data={"email": initial_email, "password": pass_v1})
            self.assertNotEqual(res_fail.status_code, 303, "Login with old password must not succeed")

            # Step 5: Login with new password succeeds
            c_ok = TestClient(app, follow_redirects=False)
            res_ok = c_ok.post("/login", data={"email": initial_email, "password": pass_v2})
            self.assertEqual(res_ok.status_code, 303)

            # Step 6: Admin edits profile
            res_edit = self._call_post_admin(
                "/api/admin/usuario/editar",
                "/api/admin/usuarios/editar",
                {
                    "user_id": uid,
                    "nombre": "Gerente San Carlos Actualizado",
                    "email": updated_email,
                    "telefono": "+505 8888-7777"
                }
            )
            self.assertEqual(res_edit.status_code, 200)
            self.assertTrue(res_edit.json().get("success", False))

            # Step 7: Verify login with updated email & pass_v2
            c_mod = TestClient(app, follow_redirects=False)
            res_mod = c_mod.post("/login", data={"email": updated_email, "password": pass_v2})
            self.assertEqual(res_mod.status_code, 303)

        finally:
            self._cleanup_branch_by_name(branch_name)
            self._cleanup_user_by_email(initial_email)
            self._cleanup_user_by_email(updated_email)

    # =========================================================================
    # TIER 4: REAL-WORLD APPLICATION SCENARIOS
    # =========================================================================

    def test_15_multi_branch_and_config_pipeline_reflection(self):
        """
        Admin manages multiple branches and config references; verifies
        pipeline and email services accurately reflect mutations, followed by
        strict cleanup verifying the 8-branch invariant.
        """
        b1_name = "Sucursal Bluefields E2E"
        b1_email = "gerente.bluefields.e2e@alfacreditsa.com"
        b2_name = "Sucursal Bilwi E2E"
        b2_email = "gerente.bilwi.e2e@alfacreditsa.com"

        self._cleanup_branch_by_name(b1_name)
        self._cleanup_branch_by_name(b2_name)
        self._cleanup_user_by_email(b1_email)
        self._cleanup_user_by_email(b2_email)

        try:
            # 1. Create first branch
            res1 = self._call_post_admin(
                "/api/admin/sucursal/crear",
                "/api/admin/sucursales/crear",
                {"nombre": b1_name, "email_gerente": b1_email, "password_inicial": "Bluefields2026!"}
            )
            self.assertEqual(res1.status_code, 200)

            # 2. Create second branch
            res2 = self._call_post_admin(
                "/api/admin/sucursal/crear",
                "/api/admin/sucursales/crear",
                {"nombre": b2_name, "email_gerente": b2_email, "password_inicial": "Bilwi2026!"}
            )
            self.assertEqual(res2.status_code, 200)

            # 3. Update config references
            test_brandon = "brandon.ops.multi@alfacreditsa.com"
            test_dania = "dania.ops.multi@alfacreditsa.com"
            res_cfg = self._call_post_admin(
                "/api/admin/config/referencias",
                None,
                {"brandon_email": test_brandon, "dania_email": test_dania}
            )
            self.assertEqual(res_cfg.status_code, 200)

            # 4. Verify config reflected
            cfg = get_email_config()
            self.assertEqual(cfg.get("brandon_email"), test_brandon)
            self.assertEqual(cfg.get("dania_email"), test_dania)

            # 5. Verify pipeline status reflects the new branches
            pipeline = obtener_estado_completo_pipeline()
            self.assertGreaterEqual(pipeline.get("total_sucursales", 0), 10)
            names_in_pipeline = [s["sucursal_nombre"] for s in pipeline.get("sucursales", [])]
            self.assertIn(b1_name, names_in_pipeline)
            self.assertIn(b2_name, names_in_pipeline)

        finally:
            self._cleanup_branch_by_name(b1_name)
            self._cleanup_branch_by_name(b2_name)
            self._cleanup_user_by_email(b1_email)
            self._cleanup_user_by_email(b2_email)

            # Assert strict 8-branch baseline invariant
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT COUNT(*) as cnt FROM sucursales")
            suc_count = c.fetchone()["cnt"]
            conn.close()
            self.assertEqual(suc_count, 8, f"Test isolation violation: expected 8 sucursales, got {suc_count}")

    def test_16_base_template_navbar_link_visibility(self):
        """
        Verify that the navbar link to /admin/gestion is visible strictly to role 'admin'
        and hidden from non-admin roles and unauthenticated users.
        """
        # Admin sees it
        admin_client = self._client_with_user(self.admin_user)
        res_admin = admin_client.get("/dashboard")
        self.assertEqual(res_admin.status_code, 200)
        self.assertIn("/admin/gestion", res_admin.text)

        # Gerente does NOT see it
        gerente_client = self._client_with_user(self.gerente_user)
        res_gerente = gerente_client.get("/dashboard")
        self.assertEqual(res_gerente.status_code, 200)
        self.assertNotIn("/admin/gestion", res_gerente.text)

        # Brandon does NOT see it
        brandon_client = self._client_with_user(self.brandon_user)
        res_brandon = brandon_client.get("/dashboard")
        self.assertEqual(res_brandon.status_code, 200)
        self.assertNotIn("/admin/gestion", res_brandon.text)

        # Unauthenticated does NOT see it
        unauth_client = TestClient(app, follow_redirects=False)
        res_unauth = unauth_client.get("/login")
        self.assertEqual(res_unauth.status_code, 200)
        self.assertNotIn("/admin/gestion", res_unauth.text)


if __name__ == "__main__":
    unittest.main()
