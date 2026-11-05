import hashlib
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException, status, Depends
from app.database import get_db_connection, hash_password

COOKIE_NAME = "alfacredit_session"

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return hash_password(plain_password) == hashed_password

def create_session_token(user_id: int, email: str) -> str:
    """Genera un token de sesión seguro con firma"""
    email_clean = email.strip().lower()
    raw = f"{user_id}:{email_clean}:alfacredit_token_secret_2026"
    sig = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    return f"{user_id}_{sig}"

def verify_session_token(token: str) -> Optional[int]:
    """Verifica la validez del token y retorna el user_id"""
    if not token or "_" not in token:
        return None
    try:
        user_id_str, sig = token.split("_", 1)
        user_id = int(user_id_str)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, email, activo FROM usuarios WHERE id = ?", (user_id,))
        user = c.fetchone()
        conn.close()

        if not user or not user["activo"]:
            return None

        email_clean = user['email'].strip().lower()
        raw = f"{user_id}:{email_clean}:alfacredit_token_secret_2026"
        expected_sig = hashlib.sha256(raw.encode('utf-8')).hexdigest()

        if sig == expected_sig:
            return user_id
        return None
    except Exception:
        return None

def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """Obtiene el usuario actual de la cookie de sesión"""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    user_id = verify_session_token(token)
    if not user_id:
        return None

    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
    SELECT u.id, u.email, u.nombre, u.rol, u.sucursal_id, u.activo, s.nombre as sucursal_nombre
    FROM usuarios u
    LEFT JOIN sucursales s ON u.sucursal_id = s.id
    WHERE u.id = ?
    ''', (user_id,))
    user = c.fetchone()
    conn.close()

    if user:
        return dict(user)
    return None

def require_auth(request: Request) -> Dict[str, Any]:
    """Dependencia que exige usuario autenticado"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sesión expirada o no iniciada",
            headers={"Location": "/login"}
        )
    return user

def require_role(allowed_roles: list):
    """Dependencia para autorizar roles específicos"""
    def role_checker(user: Dict[str, Any] = Depends(require_auth)):
        if user["rol"] == "admin":
            return user # Admin tiene acceso total
        if user["rol"] not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos para realizar esta acción"
            )
        return user
    return role_checker

import secrets
from datetime import datetime, timedelta

def solicitar_token_recuperacion(email: str) -> Optional[Dict[str, Any]]:
    """
    Genera un token seguro con expiración de 2 horas para recuperación de contraseña.
    """
    clean_email = email.strip().lower()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id, email, nombre, rol FROM usuarios WHERE LOWER(email) = ? AND activo = 1", (clean_email,))
    user = c.fetchone()
    if not user:
        conn.close()
        return None

    token = secrets.token_urlsafe(32)
    expira_en = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")

    c.execute('''
    INSERT INTO recuperacion_tokens (usuario_id, email, token, expira_en, usado)
    VALUES (?, ?, ?, ?, 0)
    ''', (user["id"], clean_email, token, expira_en))
    conn.commit()
    conn.close()

    return {
        "token": token,
        "email": user["email"],
        "nombre": user["nombre"],
        "expira_en": expira_en,
        "usuario_id": user["id"]
    }

def validar_token_recuperacion(token: str) -> Optional[Dict[str, Any]]:
    """Verifica si un token de recuperación es válido y no ha expirado"""
    if not token:
        return None
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
    SELECT r.id as token_id, r.usuario_id, r.email, r.expira_en, u.nombre, u.rol
    FROM recuperacion_tokens r
    JOIN usuarios u ON r.usuario_id = u.id
    WHERE r.token = ? AND r.usado = 0 AND r.expira_en >= ?
    ''', (token, now_str))
    res = c.fetchone()
    conn.close()
    return dict(res) if res else None

def restablecer_password_con_token(token: str, nueva_password: str) -> Dict[str, Any]:
    """Cambia la contraseña del usuario tras validar el token"""
    info = validar_token_recuperacion(token)
    if not info:
        return {"success": False, "message": "El enlace de recuperación es inválido o ha expirado."}

    p_hash = hash_password(nueva_password)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?", (p_hash, info["usuario_id"]))
    c.execute("UPDATE recuperacion_tokens SET usado = 1 WHERE id = ?", (info["token_id"],))
    conn.commit()
    conn.close()

    return {
        "success": True,
        "message": "Contraseña actualizada exitosamente.",
        "email": info["email"],
        "nombre": info["nombre"],
        "rol": info["rol"]
    }
