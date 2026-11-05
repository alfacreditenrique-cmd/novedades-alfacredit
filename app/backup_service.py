import os
import zipfile
import json
import shutil
import sqlite3
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.database import get_db_connection, DB_PATH, IS_SERVERLESS, STORAGE_DIR
from app.audit_service import log_audit

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if IS_SERVERLESS:
    BACKUP_DIR = os.path.join(STORAGE_DIR, "backups")
    COMPROBANTES_DIR = os.path.join(STORAGE_DIR, "comprobantes")
    ADJUNTOS_BRANDON_DIR = os.path.join(STORAGE_DIR, "adjuntos_brandon")
    CONFIG_PATH = os.path.join(STORAGE_DIR, "config.json")
else:
    BACKUP_DIR = os.path.join(BASE_DIR, "storage", "backups")
    COMPROBANTES_DIR = os.path.join(BASE_DIR, "storage", "comprobantes")
    ADJUNTOS_BRANDON_DIR = os.path.join(BASE_DIR, "storage", "adjuntos_brandon")
    CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

def crear_respaldo_completo(usuario_email: str = "sistema@alfacredit.com", tipo: str = "MANUAL") -> Dict[str, Any]:
    """
    Genera un archivo .zip con el respaldo integral de la base de datos,
    los comprobantes bancarios, solicitudes/PDFs de Brandon, un volcado JSON completo y la configuración.
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"RESPALDO_ALFACREDIT_NOVEDADES_{timestamp_str}.zip"
    backup_filepath = os.path.join(BACKUP_DIR, backup_filename)

    # 1. Extraer Volcado JSON de la Base de Datos
    conn = get_db_connection()
    c = conn.cursor()

    tablas = [
        "sucursales", "usuarios", "colaboradores", "ciclos_semanales",
        "reportes_sucursales", "detalle_novedades", "eventos_brandon",
        "eventos_dania", "auditoria_logs", "monitoreo_correos_log"
    ]
    dump_data = {}
    for t in tablas:
        try:
            c.execute(f"SELECT * FROM {t}")
            dump_data[t] = [dict(r) for r in c.fetchall()]
        except Exception:
            dump_data[t] = []

    conn.close()

    # 2. Empaquetar todo en ZIP
    with zipfile.ZipFile(backup_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # DB SQLite
        if os.path.exists(DB_PATH):
            zipf.write(DB_PATH, arcname="alfacredit_novedades.db")

        # JSON Dump
        json_bytes = json.dumps(dump_data, indent=2, ensure_ascii=False).encode('utf-8')
        zipf.writestr("database_dump.json", json_bytes)

        # Configuración
        if os.path.exists(CONFIG_PATH):
            zipf.write(CONFIG_PATH, arcname="config.json")

        # Comprobantes y Adjuntos de Dania
        if os.path.exists(COMPROBANTES_DIR):
            for root, _, files in os.walk(COMPROBANTES_DIR):
                for file in files:
                    full_p = os.path.join(root, file)
                    rel_p = os.path.relpath(full_p, BASE_DIR)
                    zipf.write(full_p, arcname=rel_p)

        # Adjuntos y PDFs de Brandon
        if os.path.exists(ADJUNTOS_BRANDON_DIR):
            for root, _, files in os.walk(ADJUNTOS_BRANDON_DIR):
                for file in files:
                    full_p = os.path.join(root, file)
                    rel_p = os.path.relpath(full_p, BASE_DIR)
                    zipf.write(full_p, arcname=rel_p)

        # Manifiesto
        manifest = {
            "app": "AlfaCredit Novedades y Combustible",
            "version": "2.0.0",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "created_by": usuario_email,
            "tipo": tipo,
            "included_tables": list(dump_data.keys()),
            "total_records": sum(len(v) for v in dump_data.values())
        }
        zipf.writestr("manifest.json", json.dumps(manifest, indent=2))

    tamano = os.path.getsize(backup_filepath)

    # 3. Registrar en BD
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
    INSERT INTO backups (timestamp, nombre_archivo, ruta_archivo, tamano_bytes, generado_por, tipo)
    VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        backup_filename,
        backup_filepath,
        tamano,
        usuario_email,
        tipo
    ))
    conn.commit()
    conn.close()

    log_audit(
        usuario_email, "Sistema / Usuario", "admin",
        "CREAR_RESPALDO",
        f"Generado respaldo completo: {backup_filename} ({round(tamano / 1024, 2)} KB)"
    )

    return {
        "success": True,
        "filename": backup_filename,
        "filepath": backup_filepath,
        "size_kb": round(tamano / 1024, 2),
        "timestamp": timestamp_str
    }

def listar_respaldos() -> List[Dict[str, Any]]:
    """Devuelve la lista de respaldos disponibles ordenados por fecha descendente"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM backups ORDER BY id DESC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()

    # Verificar que el archivo exista en disco
    for r in rows:
        r["existe_en_disco"] = os.path.exists(r["ruta_archivo"])
        if r["tamano_bytes"]:
            r["tamano_mb"] = round(r["tamano_bytes"] / (1024 * 1024), 2)
        else:
            r["tamano_mb"] = 0.0

    return rows

def restaurar_respaldo(backup_filename: str, usuario_email: str) -> Dict[str, Any]:
    """
    Restaura la base de datos y archivos desde un respaldo ZIP existente.
    """
    target_path = os.path.join(BACKUP_DIR, backup_filename)
    if not os.path.exists(target_path):
        return {"success": False, "message": "El archivo de respaldo no existe en el disco."}

    try:
        temp_dir = os.path.join(BACKUP_DIR, "temp_restore")
        os.makedirs(temp_dir, exist_ok=True)

        with zipfile.ZipFile(target_path, 'r') as zipf:
            zipf.extractall(temp_dir)

        # Restaurar DB
        restored_db = os.path.join(temp_dir, "alfacredit_novedades.db")
        if os.path.exists(restored_db):
            shutil.copy2(restored_db, DB_PATH)

        # Restaurar comprobantes si existen
        restored_storage = os.path.join(temp_dir, "storage", "comprobantes")
        if os.path.exists(restored_storage):
            os.makedirs(COMPROBANTES_DIR, exist_ok=True)
            for f in os.listdir(restored_storage):
                shutil.copy2(os.path.join(restored_storage, f), os.path.join(COMPROBANTES_DIR, f))

        # Restaurar adjuntos de Brandon si existen
        restored_brandon = os.path.join(temp_dir, "storage", "adjuntos_brandon")
        if os.path.exists(restored_brandon):
            os.makedirs(ADJUNTOS_BRANDON_DIR, exist_ok=True)
            for f in os.listdir(restored_brandon):
                shutil.copy2(os.path.join(restored_brandon, f), os.path.join(ADJUNTOS_BRANDON_DIR, f))

        shutil.rmtree(temp_dir, ignore_errors=True)

        log_audit(
            usuario_email, "Administrador", "admin",
            "RESTAURAR_RESPALDO",
            f"Se restauró exitosamente el sistema desde el respaldo: {backup_filename}"
        )

        return {"success": True, "message": f"Sistema restaurado exitosamente desde {backup_filename}."}
    except Exception as e:
        return {"success": False, "message": f"Error durante la restauración: {str(e)}"}
