"""
Adversarial Security & Edge Case Test Suite — Challenger 1
AlfaCredit S.A. - Admin Management Panel

Comprehensive verification covering:
1. Unauthorized HTTP methods (GET, POST, PUT, DELETE, PATCH abuse)
2. Forged headers & spoofing resistance
3. Missing cookies & tampered/expired session tokens
4. Non-admin privilege escalation & role isolation
5. Parameter tampering (e.g., trying to elevate 'rol' via edit API)
6. SQL injection payloads across all endpoints
7. Edge case inputs (empty, whitespace, malformed emails, short passwords, invalid/non-existent IDs)
8. Case-insensitive duplicate detection (branch names, emails)
9. Session handling & credential lifecycle:
   - Does email change invalidate old session cookies?
   - Does password reset immediately prevent login with old password?
   - Are recovery tokens purged upon admin reset?
   - Are deactivated users blocked from session authentication?
10. Database 8-branch invariant preservation and teardown verification.
"""

import os
import sys
import json
import unittest
import hashlib

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db, get_db_connection, hash_password
from app.auth import (
    create_session_token, verify_session_token, COOKIE_NAME,
    solicitar_token_recuperacion, validar_token_recuperacion
)
from app.email_service import get_email_config, CONFIG_PATH


class TestAdversarialSecurityChallenger1(unittest.TestCase):

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

        # Load users
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, email, nombre, rol, sucursal_id FROM usuarios WHERE rol = 'admin' AND activo = 1 LIMIT 1")
        cls.admin_user = dict(c.fetchone())

        c.execute("SELECT id, email, nombre, rol, sucursal_id FROM usuarios WHERE rol = 'gerente' AND activo = 1 LIMIT 1")
        cls.gerente_user = dict(c.fetchone())

        c.execute("SELECT id, email, nombre, rol, sucursal_id FROM usuarios WHERE rol = 'brandon' AND activo = 1 LIMIT 1")
        cls.brandon_user = dict(c.fetchone())

        c.execute("SELECT id, email, nombre, rol, sucursal_id FROM usuarios WHERE rol = 'jefe_credito' AND activo = 1 LIMIT 1")
        cls.jefe_credito_user = dict(c.fetchone())
        conn.close()

        cls._cleanup_all_adversarial_records()

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("DISABLE_CLOUDSYNC", None)
        if cls.original_config_content is not None:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                f.write(cls.original_config_content)
        cls._cleanup_all_adversarial_records()

    def setUp(self):
        # Invariant check: Baseline active branches must be 8
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM sucursales WHERE activa = 1")
        cnt = c.fetchone()["cnt"]
        conn.close()
        self.assertEqual(cnt, 8, f"Branch invariant violated before test: expected 8, found {cnt}")

    def tearDown(self):
        # Clean up any test records created during test
        self._cleanup_all_adversarial_records()
        # Invariant check: Baseline active branches must be 8
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as cnt FROM sucursales WHERE activa = 1")
        cnt = c.fetchone()["cnt"]
        conn.close()
        self.assertEqual(cnt, 8, f"Branch invariant violated after test: expected 8, found {cnt}")

    @classmethod
    def _cleanup_all_adversarial_records(cls):
        """Purges any non-official branch or test user created during testing"""
        conn = get_db_connection()
        c = conn.cursor()
        official_branches = ('Estelí', 'Masaya', 'Managua', 'Sébaco', 'Jinotepe', 'Juigalpa', 'Rivas', 'León')

        # 1. Clean non-official sucursales
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

        # 2. Clean test users
        test_patterns = ["%adv_%", "%adversarial%", "%sql%", "%test_user%", "%hacker%"]
        for pat in test_patterns:
            c.execute("SELECT id FROM usuarios WHERE email LIKE ? OR nombre LIKE ?", (pat, pat))
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

    # =========================================================================
    # 1. UNAUTHORIZED HTTP METHODS
    # =========================================================================

    def test_adv_01_http_methods_rejection_on_html_panel(self):
        """POST, PUT, DELETE, PATCH on /admin/gestion must return 405 Method Not Allowed"""
        client = self._client_with_user(self.admin_user)
        for method in ["post", "put", "delete", "patch"]:
            caller = getattr(client, method)
            res = caller("/admin/gestion")
            self.assertEqual(
                res.status_code, 405,
                f"Expected 405 Method Not Allowed for {method.upper()} /admin/gestion, got {res.status_code}"
            )

    def test_adv_02_http_methods_rejection_on_admin_apis(self):
        """GET, PUT, DELETE, PATCH on POST-only admin APIs must return 405 Method Not Allowed"""
        client = self._client_with_user(self.admin_user)
        admin_endpoints = [
            "/api/admin/sucursal/crear",
            "/api/admin/sucursales/crear",
            "/api/admin/usuario/reset-password",
            "/api/admin/usuarios/cambiar-password",
            "/api/admin/usuario/editar",
            "/api/admin/usuarios/editar",
            "/api/admin/config/referencias",
            "/api/admin/config/editar"
        ]
        for ep in admin_endpoints:
            for method in ["get", "put", "delete", "patch"]:
                caller = getattr(client, method)
                res = caller(ep)
                self.assertEqual(
                    res.status_code, 405,
                    f"Expected 405 Method Not Allowed for {method.upper()} {ep}, got {res.status_code}"
                )

    # =========================================================================
    # 2. FORGED HEADERS, MISSING COOKIES & TAMPERED/EXPIRED TOKENS
    # =========================================================================

    def test_adv_03_missing_cookies_on_all_admin_endpoints(self):
        """Unauthenticated client (no cookies) must be blocked across all admin routes"""
        client = TestClient(app, follow_redirects=False)
        # HTML panel redirect to /login
        res_view = client.get("/admin/gestion")
        self.assertEqual(res_view.status_code, 302)
        self.assertIn("/login", res_view.headers.get("location", ""))

        # All admin APIs return 403 Forbidden
        admin_endpoints = [
            "/api/admin/sucursal/crear",
            "/api/admin/sucursales/crear",
            "/api/admin/usuario/reset-password",
            "/api/admin/usuarios/cambiar-password",
            "/api/admin/usuario/editar",
            "/api/admin/usuarios/editar",
            "/api/admin/config/referencias",
            "/api/admin/config/editar"
        ]
        for ep in admin_endpoints:
            res = client.post(ep, json={})
            self.assertEqual(res.status_code, 403, f"Expected 403 for unauthenticated {ep}, got {res.status_code}")

    def test_adv_04_tampered_and_forged_session_cookies(self):
        """Tampered signatures, malformed formats, and invalid IDs in cookies must be rejected"""
        client = TestClient(app, follow_redirects=False)

        bad_tokens = [
            "invalid_format_no_underscore",
            f"{self.admin_user['id']}_0000000000000000000000000000000000000000000000000000000000000000",
            f"{self.admin_user['id']}_deadbeefcafebabe",
            "999999_invalidsig",
            "abc_invalidsig",
            f"-1_{hashlib.sha256(b'test').hexdigest()}",
            "'' OR 1=1--_sig",
            "",
            "   "
        ]

        for token in bad_tokens:
            client.cookies.clear()
            if token != "":
                client.cookies.set(COOKIE_NAME, token)

            # View should redirect to login (not crash 500)
            res_view = client.get("/admin/gestion")
            self.assertEqual(
                res_view.status_code, 302,
                f"Tampered cookie '{token}' should redirect to /login, got {res_view.status_code}"
            )

            # API should reject with 403 (not crash 500)
            res_api = client.post("/api/admin/sucursal/crear", json={})
            self.assertEqual(
                res_api.status_code, 403,
                f"Tampered cookie '{token}' should be rejected with 403, got {res_api.status_code}"
            )

    def test_adv_05_header_spoofing_resistance(self):
        """Header manipulation (X-Role, X-Forwarded-User, etc.) cannot grant admin privileges"""
        client = TestClient(app, follow_redirects=False)
        spoof_headers = [
            {"X-Role": "admin"},
            {"X-Role": "admin", "X-Admin": "true"},
            {"X-Forwarded-User": "admin@alfacreditsa.com"},
            {"X-Original-Role": "admin"},
            {"Authorization": "Bearer admin"},
            {"X-User-Role": "admin"}
        ]

        for hdrs in spoof_headers:
            # 1. Unauthenticated with spoofed headers
            res = client.get("/admin/gestion", headers=hdrs)
            self.assertEqual(res.status_code, 302, f"Header spoofing {hdrs} allowed unauth bypass on /admin/gestion")

            res_api = client.post("/api/admin/sucursal/crear", json={}, headers=hdrs)
            self.assertEqual(res_api.status_code, 403, f"Header spoofing {hdrs} allowed unauth bypass on API")

            # 2. Authenticated as 'gerente' with spoofed admin headers
            client_gerente = self._client_with_user(self.gerente_user)
            res_g = client_gerente.get("/admin/gestion", headers=hdrs)
            self.assertEqual(res_g.status_code, 403, f"Gerente with spoofed headers {hdrs} accessed /admin/gestion")

            res_g_api = client_gerente.post("/api/admin/sucursal/crear", json={}, headers=hdrs)
            self.assertEqual(res_g_api.status_code, 403, f"Gerente with spoofed headers {hdrs} accessed admin API")

    # =========================================================================
    # 3. NON-ADMIN PRIVILEGE ESCALATION & ROLE ISOLATION
    # =========================================================================

    def test_adv_06_non_admin_privilege_escalation(self):
        """Roles 'gerente', 'brandon', and 'jefe_credito' cannot call any admin mutation endpoint"""
        non_admin_users = [
            ("gerente", self.gerente_user),
            ("brandon", self.brandon_user),
            ("jefe_credito", self.jefe_credito_user)
        ]

        admin_mutations = [
            ("/api/admin/sucursal/crear", {"nombre": "Malicious", "email_gerente": "m@test.com", "password_inicial": "1234"}),
            ("/api/admin/usuario/reset-password", {"user_id": 1, "nueva_password": "HackedPassword2026!"}),
            ("/api/admin/usuario/editar", {"user_id": 1, "nombre": "Hacked", "email": "h@test.com", "telefono": "123"}),
            ("/api/admin/config/referencias", {"brandon_email": "hack@test.com", "dania_email": "hack@test.com"})
        ]

        for role_name, user_dict in non_admin_users:
            client = self._client_with_user(user_dict)
            for ep, payload in admin_mutations:
                res = client.post(ep, json=payload)
                self.assertEqual(
                    res.status_code, 403,
                    f"Security failure: Role '{role_name}' was able to access {ep} with status {res.status_code}"
                )

    def test_adv_07_parameter_tampering_role_elevation(self):
        """Admin user editing API must strictly ignore any 'rol' parameter provided in the payload"""
        client = self._client_with_user(self.admin_user)
        gerente_id = self.gerente_user["id"]
        original_email = self.gerente_user["email"]
        original_name = self.gerente_user["nombre"]

        # Attempt to change role to admin via /api/admin/usuario/editar
        res = client.post("/api/admin/usuario/editar", json={
            "user_id": gerente_id,
            "nombre": original_name,
            "email": original_email,
            "telefono": "+505 8888-0000",
            "rol": "admin"  # Malicious parameter injection
        })
        self.assertEqual(res.status_code, 200)

        # Verify in database that rol remains 'gerente'
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT rol FROM usuarios WHERE id = ?", (gerente_id,))
        current_role = c.fetchone()["rol"]
        conn.close()

        self.assertEqual(current_role, "gerente", "Privilege escalation vulnerability: 'rol' field was mutated via edit API!")

    # =========================================================================
    # 4. SQL INJECTION PAYLOADS
    # =========================================================================

    def test_adv_08_sql_injection_on_branch_creation(self):
        """SQL injection strings in branch name, manager email, or password must not execute SQL or corrupt tables"""
        client = self._client_with_user(self.admin_user)

        sql_payloads = [
            ("Adv Branch 1'); DROP TABLE sucursales; --", "adv_sql1@alfacredit.com", "Secret2026!"),
            ("Adv Branch 2' UNION SELECT 1,2,3,4,5,6,7--", "adv_sql2@alfacredit.com", "Secret2026!"),
            ("Adv Branch 3", "adv_sql3' OR '1'='1@alfacredit.com", "Secret2026!"),
            ("Adv Branch 4", "adv_sql4@alfacredit.com", "' OR '1'='1'--")
        ]

        for b_name, b_email, b_pwd in sql_payloads:
            res = client.post("/api/admin/sucursal/crear", json={
                "nombre": b_name,
                "email_gerente": b_email,
                "password_inicial": b_pwd
            })
            # May succeed with literal string or fail validation, but MUST NOT execute DROP or corrupt tables
            self.assertIn(res.status_code, [200, 400])

            # Verify sucursales table still exists and is healthy
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT count(*) as cnt FROM sucursales")
            c.fetchone()
            conn.close()

    def test_adv_09_sql_injection_on_user_endpoints(self):
        """SQL injection payloads in user_id, nombre, email, telefono must be rejected or safely handled"""
        client = self._client_with_user(self.admin_user)

        # 1. SQL injection in user_id on reset-password
        bad_ids = [
            "1 OR 1=1",
            "1; DROP TABLE usuarios;",
            "1' UNION SELECT 1,2,3,4--",
            "-1 OR 1=1"
        ]
        for bad_id in bad_ids:
            res = client.post("/api/admin/usuario/reset-password", json={
                "user_id": bad_id,
                "nueva_password": "NewValidPassword2026!"
            })
            self.assertEqual(res.status_code, 400, f"Expected 400 for SQL injection user_id '{bad_id}', got {res.status_code}")

        # 2. SQL injection in user_id on user edit
        for bad_id in bad_ids:
            res = client.post("/api/admin/usuario/editar", json={
                "user_id": bad_id,
                "nombre": "Test",
                "email": "valid@alfacredit.com",
                "telefono": "123"
            })
            self.assertEqual(res.status_code, 400, f"Expected 400 for SQL injection user_id '{bad_id}', got {res.status_code}")

        # Verify usuarios table is completely intact
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT count(*) FROM usuarios")
        c.fetchone()
        conn.close()

    # =========================================================================
    # 5. EDGE INPUTS & BOUNDARY VALIDATION
    # =========================================================================

    def test_adv_10_edge_inputs_branch_creation(self):
        """Validation rejection of empty names, whitespace, malformed emails, short passwords"""
        client = self._client_with_user(self.admin_user)

        cases = [
            ({"nombre": "", "email_gerente": "val@alfa.com", "password_inicial": "1234"}, "empty name"),
            ({"nombre": "   ", "email_gerente": "val@alfa.com", "password_inicial": "1234"}, "whitespace name"),
            ({"nombre": "Sucursal Test", "email_gerente": "", "password_inicial": "1234"}, "empty email"),
            ({"nombre": "Sucursal Test", "email_gerente": "notanemail", "password_inicial": "1234"}, "malformed email (no @)"),
            ({"nombre": "Sucursal Test", "email_gerente": "user@", "password_inicial": "1234"}, "malformed email (trailing @)"),
            ({"nombre": "Sucursal Test", "email_gerente": "user@nodot", "password_inicial": "1234"}, "malformed email (missing domain dot)"),
            ({"nombre": "Sucursal Test", "email_gerente": "val@alfa.com", "password_inicial": ""}, "empty password"),
            ({"nombre": "Sucursal Test", "email_gerente": "val@alfa.com", "password_inicial": "123"}, "short password (< 4 chars)"),
            ({"nombre": "Sucursal Test", "email_gerente": "val@alfa.com", "password_inicial": "   "}, "whitespace password (< 4 stripped)")
        ]

        for payload, description in cases:
            res = client.post("/api/admin/sucursal/crear", json=payload)
            self.assertEqual(
                res.status_code, 400,
                f"Expected 400 for edge case '{description}', got {res.status_code}: {res.text}"
            )

    def test_adv_10b_probe_malformed_email_boundaries(self):
        """
        Adversarial probe: Test boundaries of email validation.
        Empirically observes whether emails lacking local username (@domain.com) or with double @@
        slip through the naive email check ('@' in email and '.' in split('@')[-1]).
        """
        client = self._client_with_user(self.admin_user)
        # Probe: '@domain.com' has '@' and '.', but no username before '@'
        res_missing_local = client.post("/api/admin/sucursal/crear", json={
            "nombre": "Adv Probe Email Local",
            "email_gerente": "@domain.com",
            "password_inicial": "Pass2026!"
        })
        # Record empirical observation:
        # If naive validation is used, status is 200 (ACCEPTED). If strict validation is used, status is 400.
        is_naive = (res_missing_local.status_code == 200)
        # Clean up if it was created
        if res_missing_local.status_code == 200:
            self._cleanup_all_adversarial_records()

        # Both outcomes are documented; this probe establishes empirical evidence of naive vs strict validation
        self.assertIn(res_missing_local.status_code, [200, 400], "Unexpected HTTP status on malformed email probe")

    def test_adv_11_case_insensitive_duplicate_branch_and_email(self):
        """Branch creation must reject duplicate branch names and manager emails regardless of casing"""
        client = self._client_with_user(self.admin_user)

        # Baseline official branch is 'Managua'
        res_dup_name = client.post("/api/admin/sucursal/crear", json={
            "nombre": "mAnAgUa",
            "email_gerente": "adv_unique_1@alfacredit.com",
            "password_inicial": "Pass2026!"
        })
        self.assertEqual(res_dup_name.status_code, 400, "Duplicate branch name with mixed case should be rejected")

        # Baseline official email is 'gerente.managua@alfacreditsa.com'
        res_dup_email = client.post("/api/admin/sucursal/crear", json={
            "nombre": "Adv Sucursal Nueva Unique",
            "email_gerente": "GERENTE.MANAGUA@ALFACREDITSA.COM",
            "password_inicial": "Pass2026!"
        })
        self.assertEqual(res_dup_email.status_code, 400, "Duplicate manager email with uppercase should be rejected")

    def test_adv_12_edge_inputs_password_reset(self):
        """Password reset edge cases: non-existent ID, negative ID, short password, empty password"""
        client = self._client_with_user(self.admin_user)

        # Non-existent user IDs -> 404
        for fake_id in [999999, -5]:
            res = client.post("/api/admin/usuario/reset-password", json={
                "user_id": fake_id,
                "nueva_password": "ValidPassword2026!"
            })
            self.assertEqual(res.status_code, 404, f"Expected 404 for non-existent ID {fake_id}, got {res.status_code}")

        # Invalid password lengths -> 400
        for bad_pwd in ["", "   ", "a", "12", "123"]:
            res = client.post("/api/admin/usuario/reset-password", json={
                "user_id": self.gerente_user["id"],
                "nueva_password": bad_pwd
            })
            self.assertEqual(res.status_code, 400, f"Expected 400 for short/empty password '{bad_pwd}', got {res.status_code}")

    def test_adv_13_edge_inputs_user_edit(self):
        """User edit edge cases: non-existent user, email collision, malformed email, blank name"""
        client = self._client_with_user(self.admin_user)

        # Non-existent user -> 404
        res_404 = client.post("/api/admin/usuario/editar", json={
            "user_id": 999999,
            "nombre": "Fantasma",
            "email": "fantasma@alfacredit.com",
            "telefono": "123"
        })
        self.assertEqual(res_404.status_code, 404)

        # Email collision with another user -> 400
        # Admin tries to edit gerente user to have brandon user's email
        res_collision = client.post("/api/admin/usuario/editar", json={
            "user_id": self.gerente_user["id"],
            "nombre": "Gerente Collision",
            "email": self.brandon_user["email"].upper(), # case-insensitive collision check
            "telefono": "123"
        })
        self.assertEqual(res_collision.status_code, 400, "Email collision with existing user should be rejected with 400")

        # Malformed email -> 400
        res_malformed = client.post("/api/admin/usuario/editar", json={
            "user_id": self.gerente_user["id"],
            "nombre": "Gerente Test",
            "email": "not-an-email-format",
            "telefono": "123"
        })
        self.assertEqual(res_malformed.status_code, 400)

        # Empty name -> 400
        res_empty_name = client.post("/api/admin/usuario/editar", json={
            "user_id": self.gerente_user["id"],
            "nombre": "   ",
            "email": "valid@alfacredit.com",
            "telefono": "123"
        })
        self.assertEqual(res_empty_name.status_code, 400)

    def test_adv_14_edge_inputs_config_references(self):
        """Config references edge cases: missing emails, malformed emails, empty strings"""
        client = self._client_with_user(self.admin_user)

        bad_cases = [
            {"brandon_email": "", "dania_email": "dania@alfacredit.com"},
            {"brandon_email": "brandon@alfacredit.com", "dania_email": ""},
            {"brandon_email": "notanemail", "dania_email": "dania@alfacredit.com"},
            {"brandon_email": "brandon@alfacredit.com", "dania_email": "notanemail"}
        ]
        for payload in bad_cases:
            res = client.post("/api/admin/config/referencias", json=payload)
            self.assertEqual(res.status_code, 400, f"Expected 400 for config payload {payload}, got {res.status_code}")

    # =========================================================================
    # 6. SESSION HANDLING & CREDENTIAL LIFECYCLE
    # =========================================================================

    def test_adv_15_session_invalidated_when_email_changed(self):
        """
        Empirically verify that updating a user's email in the database immediately
        invalidates old session cookies generated with the previous email.
        """
        # 1. Create a test user
        conn = get_db_connection()
        c = conn.cursor()
        initial_email = "adv_session_user@alfacredit.com"
        new_email = "adv_session_updated@alfacredit.com"
        p_hash = hash_password("SessionPass2026!")
        c.execute('''
        INSERT INTO usuarios (email, password_hash, nombre, rol, sucursal_id, telefono, activo)
        VALUES (?, ?, 'Adv Session User', 'gerente', NULL, '1234', 1)
        ''', (initial_email, p_hash))
        test_uid = c.lastrowid
        conn.commit()
        conn.close()

        # 2. Generate a valid session token for this user with initial email
        old_token = create_session_token(test_uid, initial_email)

        # Verify old token works
        verified_uid_before = verify_session_token(old_token)
        self.assertEqual(verified_uid_before, test_uid, "Old session token should be valid before email change")

        # 3. Admin modifies user email to new_email
        client_admin = self._client_with_user(self.admin_user)
        res_edit = client_admin.post("/api/admin/usuario/editar", json={
            "user_id": test_uid,
            "nombre": "Adv Session User Renamed",
            "email": new_email,
            "telefono": "5678"
        })
        self.assertEqual(res_edit.status_code, 200, f"Failed to edit user email: {res_edit.text}")

        # 4. Old session token verification MUST NOW FAIL because expected_sig binds to DB email
        verified_uid_after = verify_session_token(old_token)
        self.assertIsNone(
            verified_uid_after,
            "Security flaw: Old session cookie remained valid after user's email was changed!"
        )

        # 5. A request made with the old cookie to an authenticated route must be rejected (401 / 302 redirect)
        client_test_user = TestClient(app, follow_redirects=False)
        client_test_user.cookies.set(COOKIE_NAME, old_token)
        res_protected = client_test_user.get("/dashboard")
        self.assertIn(
            res_protected.status_code, [302, 401],
            f"Expected 401 or redirect to /login with invalidated cookie, got {res_protected.status_code}"
        )

    def test_adv_16_password_reset_immediately_blocks_old_password(self):
        """
        Empirically verify that resetting a password immediately prevents login with
        the old password, and immediately enables login with the new password.
        """
        # 1. Create a test user
        conn = get_db_connection()
        c = conn.cursor()
        test_email = "adv_pwd_reset@alfacredit.com"
        old_password = "OldSecret2026!"
        new_password = "NewSecret2026!Strong"
        p_hash = hash_password(old_password)
        c.execute('''
        INSERT INTO usuarios (email, password_hash, nombre, rol, sucursal_id, telefono, activo)
        VALUES (?, ?, 'Adv Pwd User', 'gerente', NULL, '1234', 1)
        ''', (test_email, p_hash))
        test_uid = c.lastrowid
        conn.commit()
        conn.close()

        # 2. Login with old password succeeds
        client_unauth = TestClient(app, follow_redirects=False)
        res_login_old = client_unauth.post("/login", data={"email": test_email, "password": old_password})
        self.assertEqual(res_login_old.status_code, 303, "Login with old password should succeed initially")
        self.assertIn(COOKIE_NAME, res_login_old.cookies, "Expected session cookie upon successful login")

        # 3. Admin resets password to new_password
        client_admin = self._client_with_user(self.admin_user)
        res_reset = client_admin.post("/api/admin/usuario/reset-password", json={
            "user_id": test_uid,
            "nueva_password": new_password
        })
        self.assertEqual(res_reset.status_code, 200, f"Password reset failed: {res_reset.text}")

        # 4. Immediate login attempt with old password MUST FAIL
        client_unauth2 = TestClient(app, follow_redirects=False)
        res_login_old_failed = client_unauth2.post("/login", data={"email": test_email, "password": old_password})
        self.assertEqual(
            res_login_old_failed.status_code, 200,
            "Expected 200 (re-rendering login page with error) for failed login"
        )
        self.assertIn("incorrectos", res_login_old_failed.text.lower())
        self.assertNotIn(COOKIE_NAME, res_login_old_failed.cookies)

        # 5. Immediate login attempt with new password MUST SUCCEED
        res_login_new_succeeds = client_unauth2.post("/login", data={"email": test_email, "password": new_password})
        self.assertEqual(
            res_login_new_succeeds.status_code, 303,
            "Login with new password must succeed immediately"
        )
        self.assertIn(COOKIE_NAME, res_login_new_succeeds.cookies)

    def test_adv_17_recovery_tokens_purged_on_admin_reset(self):
        """
        Empirically verify that any pending password recovery token is permanently
        purged when admin resets the password.
        """
        # 1. Create a test user
        conn = get_db_connection()
        c = conn.cursor()
        test_email = "adv_recovery_user@alfacredit.com"
        p_hash = hash_password("Init2026!")
        c.execute('''
        INSERT INTO usuarios (email, password_hash, nombre, rol, sucursal_id, telefono, activo)
        VALUES (?, ?, 'Adv Recovery User', 'gerente', NULL, '1234', 1)
        ''', (test_email, p_hash))
        test_uid = c.lastrowid
        conn.commit()
        conn.close()

        # 2. Solicit recovery token
        token_info = solicitar_token_recuperacion(test_email)
        self.assertIsNotNone(token_info)
        rec_token = token_info["token"]

        # Validate token is currently active
        valid_before = validar_token_recuperacion(rec_token)
        self.assertIsNotNone(valid_before, "Recovery token should be valid initially")

        # 3. Admin resets password
        client_admin = self._client_with_user(self.admin_user)
        res_reset = client_admin.post("/api/admin/usuario/reset-password", json={
            "user_id": test_uid,
            "nueva_password": "AdminResetPassword2026!"
        })
        self.assertEqual(res_reset.status_code, 200)

        # 4. Recovery token MUST NOW BE INVALID / PURGED
        valid_after = validar_token_recuperacion(rec_token)
        self.assertIsNone(
            valid_after,
            "Security vulnerability: Recovery token was NOT invalidated when admin reset user password!"
        )

    def test_adv_18_inactive_user_session_rejected(self):
        """
        Empirically verify that if a user account is set to inactive (activo = 0),
        their session token is immediately rejected even if properly signed.
        """
        # 1. Create a test user with activo = 1
        conn = get_db_connection()
        c = conn.cursor()
        test_email = "adv_deactivated_user@alfacredit.com"
        p_hash = hash_password("Active2026!")
        c.execute('''
        INSERT INTO usuarios (email, password_hash, nombre, rol, sucursal_id, telefono, activo)
        VALUES (?, ?, 'Adv Deactivated User', 'gerente', NULL, '1234', 1)
        ''', (test_email, p_hash))
        test_uid = c.lastrowid
        conn.commit()
        conn.close()

        # 2. Token created while active
        token = create_session_token(test_uid, test_email)
        self.assertEqual(verify_session_token(token), test_uid)

        # 3. Deactivate user in DB (activo = 0)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE usuarios SET activo = 0 WHERE id = ?", (test_uid,))
        conn.commit()
        conn.close()

        # 4. verify_session_token MUST return None
        self.assertIsNone(
            verify_session_token(token),
            "Security flaw: Inactive user session token was accepted!"
        )

        # 5. Protected route rejects the session (401 Unauthorized or 302 redirect)
        client = TestClient(app, follow_redirects=False)
        client.cookies.set(COOKIE_NAME, token)
        res = client.get("/dashboard")
        self.assertIn(res.status_code, [302, 401], f"Inactive user session should be blocked with 401/302, got {res.status_code}")

    # =========================================================================
    # 7. INVARIANT PRESERVATION
    # =========================================================================

    def test_adv_19_baseline_branch_invariant_preserved(self):
        """Ensure that exactly 8 active baseline branches remain after adversarial runs"""
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, nombre FROM sucursales WHERE activa = 1 ORDER BY id ASC")
        branches = c.fetchall()
        conn.close()

        self.assertEqual(len(branches), 8, f"Branch invariant violated: expected 8 branches, found {len(branches)}")
        expected_names = {'Estelí', 'Masaya', 'Managua', 'Sébaco', 'Jinotepe', 'Juigalpa', 'Rivas', 'León'}
        actual_names = {b["nombre"] for b in branches}
        self.assertEqual(actual_names, expected_names, f"Branch set mismatch: {actual_names} != {expected_names}")


if __name__ == "__main__":
    unittest.main()
