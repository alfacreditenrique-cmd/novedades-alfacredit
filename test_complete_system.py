import io
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from fastapi.testclient import TestClient
from app.main import app, obtener_estado_completo_pipeline
from app.auth import create_session_token, COOKIE_NAME
from app.database import init_db, get_db_connection

from reset_fresh_state import reset_to_fresh_real_state

def test_all():
    os.environ["DISABLE_CLOUDSYNC"] = "1"
    seed_db = os.path.join(BASE_DIR, "storage", "database", "alfacredit_novedades.db")
    backup_bytes = open(seed_db, "rb").read() if os.path.exists(seed_db) else None
    reset_to_fresh_real_state()
    client = TestClient(app)

    # 1. VERIFICAR USUARIOS Y CORREOS @ALFACREDITSA.COM
    conn = get_db_connection()
    users = conn.execute("SELECT id, email, nombre, rol, sucursal_id FROM usuarios ORDER BY id ASC").fetchall()
    conn.close()

    print(f"Total usuarios registrados: {len(users)}")
    for u in users:
        assert "@alfacreditsa.com" in u["email"], f"El usuario {u['nombre']} no tiene correo @alfacreditsa.com: {u['email']}"
        print(f"  • {u['nombre']} -> {u['email']} ({u['rol']})")

    # 2. PROBAR LOGIN CON CORREO @ALFACREDITSA.COM
    res_login = client.post("/login", data={"email": "gerente.esteli@alfacreditsa.com", "password": "1234"}, follow_redirects=False)
    assert res_login.status_code == 303, f"Error en login: {res_login.status_code}"
    print("✅ Login exitoso con correo @alfacreditsa.com")

    # 3. PROBAR FLUJO DE RECUPERACIÓN DE CONTRASEÑA
    # A. Solicitar recuperación
    res_req = client.post("/recuperar-password", data={"email": "gerente.masaya@alfacreditsa.com"})
    assert res_req.status_code == 200
    assert "Enlace de acceso directo generado" in res_req.text
    print("✅ Solicitud de recuperación de contraseña generada con éxito")

    # B. Extraer token de la base de datos
    conn = get_db_connection()
    tok_row = conn.execute("SELECT token FROM recuperacion_tokens WHERE email = 'gerente.masaya@alfacreditsa.com' AND usado = 0 ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert tok_row is not None
    token_str = tok_row["token"]

    # C. Cargar página de restablecimiento
    res_page = client.get(f"/restablecer-password?token={token_str}")
    assert res_page.status_code == 200
    assert "Restableciendo acceso para" in res_page.text

    # D. Cambiar contraseña
    res_change = client.post("/restablecer-password", data={
        "token": token_str,
        "password": "nuevaclave2026",
        "confirm_password": "nuevaclave2026"
    })
    assert res_change.status_code == 200
    assert "Contraseña actualizada exitosamente" in res_change.text
    print("✅ Contraseña cambiada exitosamente con token de seguridad")

    # E. Probar login con la nueva contraseña
    res_new_login = client.post("/login", data={"email": "gerente.masaya@alfacreditsa.com", "password": "nuevaclave2026"}, follow_redirects=False)
    assert res_new_login.status_code == 303
    print("✅ Login con nueva contraseña exitoso")

    # F. Restaurar contraseña a 1234 para pruebas
    conn = get_db_connection()
    from app.database import hash_password
    conn.execute("UPDATE usuarios SET password_hash = ? WHERE email = 'gerente.masaya@alfacreditsa.com'", (hash_password("1234"),))
    conn.commit()
    conn.close()

    # 4. PROBAR PRIVACIDAD Y RESTRICCIÓN DE GERENTES (NO PUEDEN DESCARGAR EXCEL DE OTRAS SUCURSALES)
    # Gerente Estelí (sucursal_id = 1)
    conn = get_db_connection()
    esteli_user = conn.execute("SELECT id, email FROM usuarios WHERE email = 'gerente.esteli@alfacreditsa.com'").fetchone()
    conn.close()

    tok_esteli = create_session_token(esteli_user["id"], esteli_user["email"])
    client_esteli = TestClient(app)
    client_esteli.cookies.set(COOKIE_NAME, tok_esteli)

    # Puede descargar su propio Excel (Sucursal 1)
    res_propio = client_esteli.get("/api/export/excel/sucursal/1")
    assert res_propio.status_code == 200, f"Error al descargar su propio Excel: {res_propio.status_code}"
    print("✅ Gerente Estelí puede descargar su propio Excel (Sucursal 1)")

    # NO puede descargar Excel de Masaya (Sucursal 2) -> 403 Forbidden
    res_ajeno = client_esteli.get("/api/export/excel/sucursal/2")
    assert res_ajeno.status_code == 403, f"Se esperaba 403 Forbidden pero se obtuvo: {res_ajeno.status_code}"
    print("✅ Gerente Estelí bloqueado correctamente de descargar Excel de Masaya (403 Forbidden)")

    # NO puede ver panel de Auditoría -> 403 Forbidden
    res_aud_gerente = client_esteli.get("/auditoria")
    assert res_aud_gerente.status_code == 403
    print("✅ Gerente Estelí bloqueado correctamente del Panel de Auditoría (403 Forbidden)")

    # 5. PROBAR QUE BRANDON TIENE ACCESO A AUDITORÍA Y DESCARGAS
    conn = get_db_connection()
    brandon_user = conn.execute("SELECT id, email FROM usuarios WHERE rol = 'brandon'").fetchone()
    conn.close()

    tok_brandon = create_session_token(brandon_user["id"], brandon_user["email"])
    client_brandon = TestClient(app)
    client_brandon.cookies.set(COOKIE_NAME, tok_brandon)

    # Brandon accede al Panel de Auditoría
    res_aud_brandon = client_brandon.get("/auditoria")
    assert res_aud_brandon.status_code == 200
    print("✅ Brandon tiene acceso autorizado al Panel de Auditoría (200 OK)")

    res_b_suc2 = client_brandon.get("/api/export/excel/sucursal/2")
    assert res_b_suc2.status_code == 200
    res_b_cons = client_brandon.get("/api/export/excel/consolidado")
    assert res_b_cons.status_code == 200
    print("✅ Brandon tiene acceso completo de descarga para todas las sucursales y consolidado")

    # 6. PROBAR USUARIO YESER (JEFE DE CRÉDITO - SOLO DASHBOARD)
    conn = get_db_connection()
    yeser_user = conn.execute("SELECT id, email, rol, nombre FROM usuarios WHERE email = 'yeser@alfacreditsa.com'").fetchone()
    conn.close()

    assert yeser_user is not None
    assert yeser_user["rol"] == "jefe_credito"
    tok_yeser = create_session_token(yeser_user["id"], yeser_user["email"])
    client_yeser = TestClient(app)
    client_yeser.cookies.set(COOKIE_NAME, tok_yeser)

    # Yeser accede perfectamente al Dashboard
    res_dash_yeser = client_yeser.get("/dashboard")
    assert res_dash_yeser.status_code == 200
    assert "Supervisión Jefatura de Crédito" in res_dash_yeser.text
    print("✅ Yeser (Jefe de Crédito) accede exitosamente al Dashboard en modo supervisión (200 OK)")

    # Yeser no puede entrar a novedades (se redirige al dashboard)
    res_nov_yeser = client_yeser.get("/novedades", follow_redirects=False)
    assert res_nov_yeser.status_code == 302
    print("✅ Yeser redirigido correctamente desde novedades hacia el Dashboard")

    # Yeser bloqueado de auditoría y consolidado (403 Forbidden)
    res_aud_yeser = client_yeser.get("/auditoria")
    assert res_aud_yeser.status_code == 403
    res_cons_yeser = client_yeser.get("/consolidado")
    assert res_cons_yeser.status_code == 403
    print("✅ Yeser bloqueado correctamente de Auditoría y Consolidado (403 Forbidden)")

    # 7. VERIFICAR QUE NO EXISTE USUARIO WEB PARA DANIA (INTERACCIÓN 100% POR CORREO DE BRANDON)
    conn = get_db_connection()
    dania_user = conn.execute("SELECT id FROM usuarios WHERE rol = 'dania'").fetchone()
    conn.close()
    assert dania_user is None
    print("✅ Confirmado: Dania no tiene cuenta de acceso web (su confirmación se escanea del correo de Brandon)")

    # 8. VERIFICAR ESTADO EN LIMPIO (0/8 ENVIADAS, FASE 1)
    st = obtener_estado_completo_pipeline()
    assert st["fase_actual"] == 1
    assert st["conteo_enviadas"] == 0
    assert st["total_sucursales"] == 8
    print(f"✅ Estado del pipeline en limpio verificado: {st['conteo_enviadas']}/{st['total_sucursales']} enviadas (Fase 1)")

    print("\n🎉 VALIDACIÓN COMPLETA: TODOS LOS SISTEMAS, PRIVACIDADES, CORREOS @ALFACREDITSA.COM Y RECUPERACIÓN FUNCIONAN AL 100%")
    if backup_bytes and os.path.exists(seed_db):
        with open(seed_db, "wb") as f:
            f.write(backup_bytes)
    os.environ.pop("DISABLE_CLOUDSYNC", None)

if __name__ == "__main__":
    test_all()
