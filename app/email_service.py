import imaplib
import email
from email.header import decode_header
import json
import os
import re
from datetime import datetime
from typing import Dict, Any, List, Optional
from app.database import get_db_connection, IS_SERVERLESS, STORAGE_DIR
from app.audit_service import log_audit
import shutil

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if IS_SERVERLESS:
    CONFIG_PATH = os.path.join(STORAGE_DIR, "config.json")
    STORAGE_COMPROBANTES = os.path.join(STORAGE_DIR, "comprobantes")
else:
    CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
    STORAGE_COMPROBANTES = os.path.join(BASE_DIR, "storage", "comprobantes")

def get_email_config() -> Dict[str, Any]:
    """Carga la configuración actual del monitor de correo"""
    if IS_SERVERLESS and not os.path.exists(CONFIG_PATH):
        orig = os.path.join(BASE_DIR, "config.json")
        if os.path.exists(orig):
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            try:
                shutil.copy2(orig, CONFIG_PATH)
            except Exception:
                pass

    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            return cfg.get("email_monitor", {})
    return {}

def update_email_config(new_config: Dict[str, Any]) -> bool:
    """Actualiza la configuración del monitor de correo"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            full_cfg = json.load(f)
        full_cfg["email_monitor"].update(new_config)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(full_cfg, f, indent=2)
        return True
    except Exception as e:
        print(f"Error actualizando config: {e}")
        return False

def decode_mime_words(s: str) -> str:
    """Decodifica encabezados de correo que puedan estar en base64/quoted-printable"""
    if not s:
        return ""
    decoded_fragments = []
    for fragment, encoding in decode_header(s):
        if isinstance(fragment, bytes):
            try:
                decoded_fragments.append(fragment.decode(encoding or 'utf-8', errors='ignore'))
            except Exception:
                decoded_fragments.append(fragment.decode('latin1', errors='ignore'))
        else:
            decoded_fragments.append(str(fragment))
    return "".join(decoded_fragments)

def test_imap_connection(imap_server: str, imap_port: int, use_ssl: bool, email_user: str, email_pass: str) -> Dict[str, Any]:
    """Prueba la conexión real con el servidor IMAP"""
    try:
        if use_ssl:
            mail = imaplib.IMAP4_SSL(imap_server, imap_port, timeout=10)
        else:
            mail = imaplib.IMAP4(imap_server, imap_port, timeout=10)

        mail.login(email_user, email_pass)
        status, folders = mail.list()
        mail.logout()
        return {"success": True, "message": f"Conexión exitosa a {imap_server}. Se encontraron {len(folders)} carpetas."}
    except Exception as e:
        return {"success": False, "message": f"Error de autenticación o conexión: {str(e)}"}

def escanear_correos_brandon() -> Dict[str, Any]:
    """
    Escanea la bandeja de Enviados y Entrada del correo de Brandon
    para detectar automáticamente el envío de la solicitud de combustible
    y la llegada del comprobante de depósito de Dania.
    """
    cfg = get_email_config()
    if not cfg.get("enabled"):
        return {"success": False, "message": "El monitoreo de correo está desactivado en la configuración."}

    user = cfg.get("brandon_email", "").strip()
    pwd = cfg.get("brandon_password", "").strip()
    server = cfg.get("imap_server", "outlook.office365.com").strip()
    port = int(cfg.get("imap_port", 993))
    use_ssl = cfg.get("use_ssl", True)

    if not user or not pwd:
        return {
            "success": False,
            "message": "Faltan credenciales de correo de Brandon (usuario o contraseña de aplicación)."
        }

    solicitudes_detectadas = 0
    depositos_detectados = 0
    errores = []

    try:
        if use_ssl:
            mail = imaplib.IMAP4_SSL(server, port, timeout=15)
        else:
            mail = imaplib.IMAP4(server, port, timeout=15)
        mail.login(user, pwd)
    except Exception as e:
        msg = f"Error al conectar con el servidor IMAP ({server}): {e}"
        registrar_log_correo("ERROR", "", "", "", "", {"error": str(e)})
        return {"success": False, "message": msg}

    conn = get_db_connection()
    c = conn.cursor()

    # Obtener el ciclo activo
    c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    ciclo = c.fetchone()
    if not ciclo:
        conn.close()
        mail.logout()
        return {"success": False, "message": "No hay ciclo semanal activo."}

    ciclo_id = ciclo["id"]

    # 1. ESCANEAR CARPETA DE ENVIADOS (Buscar solicitud de combustible de Brandon)
    try:
        # Carpetas de enviados para Outlook (Office 365), Gmail y estándar
        carpetas_enviados = ['"Sent Items"', '"Elementos enviados"', '"Sent"', '"[Gmail]/Sent Mail"', '"[Gmail]/Enviados"', 'INBOX.Sent', 'Sent']
        carpeta_seleccionada = None
        for carpeta in carpetas_enviados:
            try:
                status, _ = mail.select(carpeta, readonly=True)
                if status == 'OK':
                    carpeta_seleccionada = carpeta
                    break
            except Exception:
                continue

        if carpeta_seleccionada:
            # Buscar correos recientes (últimos 7 días)
            status, data = mail.search(None, 'ALL')
            if status == 'OK' and data[0]:
                email_ids = data[0].split()
                # Revisar los últimos 15 correos enviados
                for e_id in email_ids[-15:]:
                    res, msg_data = mail.fetch(e_id, '(RFC822)')
                    if res != 'OK':
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    asunto = decode_mime_words(msg.get("Subject", ""))
                    destinatario = decode_mime_words(msg.get("To", ""))
                    fecha_str = msg.get("Date", "")

                    # Verificar si coincide con palabras clave de solicitud
                    asunto_lower = asunto.lower()
                    if any(k in asunto_lower for k in cfg.get("keywords_solicitud", [])):
                        # Registrar evento si no existe para este ciclo
                        c.execute("SELECT id FROM eventos_brandon WHERE ciclo_id = ?", (ciclo_id,))
                        ev = c.fetchone()
                        if not ev:
                            c.execute('''
                            INSERT INTO eventos_brandon (ciclo_id, correo_destinatario, asunto, fecha_envio, metodo)
                            VALUES (?, ?, ?, ?, 'DETECCION_CORREO')
                            ''', (ciclo_id, destinatario, asunto, fecha_str))

                            # Si el ciclo estaba en FASE_1 o FASE_2, avanzar a FASE_3_DANIA
                            c.execute('''
                            UPDATE ciclos_semanales 
                            SET estado_general = 'FASE_3_DANIA' 
                            WHERE id = ? AND estado_general IN ('FASE_1_GERENTES', 'FASE_2_BRANDON')
                            ''', (ciclo_id,))

                            log_audit(
                                "sistema@alfacredit.com", "Monitor de Correo", "sistema",
                                "DETECCION_CORREO_BRANDON",
                                f"Detectado envío de solicitud de combustible por Brandon: '{asunto}' a {destinatario}"
                            )
                            solicitudes_detectadas += 1
                            conn.commit()
    except Exception as e:
        errores.append(f"Error escaneando enviados: {e}")

    # 2. ESCANEAR BANDEJA DE ENTRADA (Buscar confirmación de depósito de Dania)
    try:
        status, _ = mail.select("INBOX", readonly=True)
        if status == 'OK':
            status, data = mail.search(None, 'ALL')
            if status == 'OK' and data[0]:
                email_ids = data[0].split()
                # Revisar los últimos 20 correos recibidos
                for e_id in email_ids[-20:]:
                    res, msg_data = mail.fetch(e_id, '(RFC822)')
                    if res != 'OK':
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    remitente = decode_mime_words(msg.get("From", ""))
                    asunto = decode_mime_words(msg.get("Subject", ""))
                    fecha_str = msg.get("Date", "")

                    asunto_lower = asunto.lower()
                    remitente_lower = remitente.lower()
                    dania_ref = cfg.get("dania_email", "").lower()

                    # Coincide si el asunto tiene palabras de depósito o viene de Dania con comprobante
                    es_deposito = any(k in asunto_lower for k in cfg.get("keywords_deposito", [])) or (dania_ref and dania_ref in remitente_lower)

                    if es_deposito:
                        # Extraer adjuntos si los hay
                        adjunto_guardado = None
                        for part in msg.walk():
                            if part.get_content_maintype() == 'multipart':
                                continue
                            if part.get('Content-Disposition') is None:
                                continue
                            filename = part.get_filename()
                            if filename:
                                filename = decode_mime_words(filename)
                                clean_fn = f"comprobante_ciclo_{ciclo_id}_{int(datetime.now().timestamp())}_{filename}"
                                os.makedirs(STORAGE_COMPROBANTES, exist_ok=True)
                                f_path = os.path.join(STORAGE_COMPROBANTES, clean_fn)
                                with open(f_path, "wb") as f:
                                    f.write(part.get_payload(decode=True))
                                adjunto_guardado = clean_fn
                                break

                        # Registrar en eventos_dania si aún no existe
                        c.execute("SELECT id FROM eventos_dania WHERE ciclo_id = ?", (ciclo_id,))
                        ev_d = c.fetchone()
                        if not ev_d:
                            c.execute('''
                            INSERT INTO eventos_dania (ciclo_id, comprobante_archivo, fecha_deposito, remitente_correo, asunto_correo, metodo)
                            VALUES (?, ?, ?, ?, ?, 'DETECCION_CORREO')
                            ''', (ciclo_id, adjunto_guardado, fecha_str, remitente, asunto))

                            # Avanzar a FASE_4_COMPLETADO
                            c.execute('''
                            UPDATE ciclos_semanales 
                            SET estado_general = 'FASE_4_COMPLETADO', cerrado_en = ?
                            WHERE id = ?
                            ''', (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ciclo_id))

                            log_audit(
                                "sistema@alfacredit.com", "Monitor de Correo", "sistema",
                                "DETECCION_CORREO_DANIA",
                                f"Detectado comprobante de depósito enviado por Dania: '{asunto}' desde {remitente}"
                            )
                            depositos_detectados += 1
                            conn.commit()
    except Exception as e:
        errores.append(f"Error escaneando entrada: {e}")

    try:
        mail.logout()
    except Exception:
        pass

    conn.close()

    # Actualizar estado de última sincronización en config
    update_email_config({
        "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_status": f"Completado. Solicitudes: {solicitudes_detectadas}, Depósitos: {depositos_detectados}"
    })

    return {
        "success": True,
        "solicitudes_detectadas": solicitudes_detectadas,
        "depositos_detectados": depositos_detectados,
        "errores": errores,
        "mensaje": f"Sincronización completada. Solicitudes detectadas: {solicitudes_detectadas}, Comprobantes detectados: {depositos_detectados}."
    }

def registrar_log_correo(tipo: str, remitente: str, destinatario: str, asunto: str, fecha: str, detalles: Dict[str, Any]):
    """Registra un evento en la tabla de logs de correo"""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute('''
        INSERT INTO monitoreo_correos_log (tipo, remitente, destinatario, asunto, fecha_correo, detalles_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (tipo, remitente, destinatario, asunto, fecha, json.dumps(detalles)))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error registrando log de correo: {e}")
