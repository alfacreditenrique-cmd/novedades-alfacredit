import sqlite3
from typing import Optional, List, Dict, Any
from app.database import get_db_connection
from datetime import datetime

def log_audit(
    usuario_email: str,
    usuario_nombre: str,
    rol: str,
    accion: str,
    detalle: str,
    sucursal_nombre: Optional[str] = None,
    ip_address: Optional[str] = None
):
    """
    Registra una acción en el log de auditoría inmutable de la base de datos.
    """
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
        INSERT INTO auditoria_logs (timestamp, usuario_email, usuario_nombre, rol, sucursal_nombre, accion, detalle, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            usuario_email,
            usuario_nombre,
            rol,
            sucursal_nombre or "N/A",
            accion,
            detalle,
            ip_address or "127.0.0.1"
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[AUDIT ERROR]: No se pudo guardar el log: {e}")

def get_audit_logs(
    limit: int = 100,
    offset: int = 0,
    accion: Optional[str] = None,
    usuario: Optional[str] = None,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Recupera los registros de auditoría filtrados"""
    conn = get_db_connection()
    c = conn.cursor()

    query = "SELECT * FROM auditoria_logs WHERE 1=1"
    params = []

    if accion and accion != "TODAS":
        query += " AND accion = ?"
        params.append(accion)

    if usuario:
        query += " AND (usuario_email LIKE ? OR usuario_nombre LIKE ?)"
        params.extend([f"%{usuario}%", f"%{usuario}%"])

    if search:
        query += " AND (detalle LIKE ? OR accion LIKE ? OR sucursal_nombre LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

    query += " ORDER BY id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    c.execute(query, tuple(params))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows
