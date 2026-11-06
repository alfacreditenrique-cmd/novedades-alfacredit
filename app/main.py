import os
import json
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, Request, Response, Depends, Form, File, UploadFile, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import (
    get_db_connection, init_db, asegurar_ciclo_actual, hash_password,
    IS_SERVERLESS, STORAGE_DIR, realizar_corte_semanal_logica
)
from app.auth import (
    get_current_user, require_auth, require_role, create_session_token, COOKIE_NAME, verify_password,
    solicitar_token_recuperacion, validar_token_recuperacion, restablecer_password_con_token
)
from app.calculations import LEYENDA_MAESTRA, calcular_fila_novedades, calcular_resumen_reporte
from app.excel_engine import generar_excel_sucursal, generar_excel_consolidado_todas_sucursales
from app.email_service import (
    get_email_config, update_email_config, test_imap_connection,
    escanear_correos_brandon, STORAGE_COMPROBANTES
)
from app.audit_service import log_audit, get_audit_logs
from app.backup_service import (
    crear_respaldo_completo, listar_respaldos, restaurar_respaldo,
    BACKUP_DIR
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "app", "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "app", "templates")
STORAGE_ADJUNTOS_BRANDON = os.path.join(STORAGE_DIR, "adjuntos_brandon")

app = FastAPI(title="AlfaCredit Novedades y Combustible", version="2.0.0")

# Montar Estáticos y Templates
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Gestor de WebSockets para actualizaciones en tiempo real
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_status(self, data: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except Exception:
                pass

ws_manager = ConnectionManager()

# --- FUNCIONES AUXILIARES DE ESTADO ---
def obtener_estado_completo_pipeline(ciclo_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Calcula el estado general del pipeline, quién falta y los semáforos.
    Permite consultar un ciclo específico mediante ciclo_id o el ciclo activo actual.
    """
    conn = get_db_connection()
    c = conn.cursor()

    c.execute("SELECT id FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    row_latest = c.fetchone()
    if not row_latest:
        asegurar_ciclo_actual(conn)
        c.execute("SELECT id FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
        row_latest = c.fetchone()
    latest_ciclo_id = row_latest["id"] if row_latest else None

    if ciclo_id:
        c.execute("SELECT * FROM ciclos_semanales WHERE id = ?", (ciclo_id,))
        ciclo = c.fetchone()
        if not ciclo:
            c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
            ciclo = c.fetchone()
    else:
        c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
        ciclo = c.fetchone()

    ciclo_dict = dict(ciclo)
    ciclo_id = ciclo_dict["id"]
    es_ciclo_actual = (ciclo_id == latest_ciclo_id)

    # 1. Sucursales y sus estados
    c.execute('''
    SELECT s.id as sucursal_id, s.nombre as sucursal_nombre, s.email_gerente, s.telefono as sucursal_telefono,
           r.id as reporte_id, r.estado as reporte_estado, r.enviado_en,
           r.total_empleados, r.suma_base, r.suma_ajustes, r.suma_neto,
           u.nombre as gerente_nombre
    FROM sucursales s
    LEFT JOIN reportes_sucursales r ON s.id = r.sucursal_id AND r.ciclo_id = ?
    LEFT JOIN usuarios u ON s.email_gerente = u.email
    WHERE s.activa = 1
    ORDER BY s.id ASC
    ''', (ciclo_id,))
    sucursales_raw = [dict(r) for r in c.fetchall()]

    # Generar enlaces y datos de recordatorio WhatsApp para cada sucursal
    import urllib.parse
    for s in sucursales_raw:
        tel = s.get("sucursal_telefono") or ""
        tel_digits = "".join(ch for ch in str(tel) if ch.isdigit())
        if len(tel_digits) == 8:
            wa_num = "505" + tel_digits
        elif len(tel_digits) > 8:
            wa_num = tel_digits
        else:
            wa_num = ""
        s["whatsapp_numero"] = wa_num

        nom_g = s.get("gerente_nombre") or f"Gerente {s['sucursal_nombre']}"
        sab_str = ciclo_dict.get("fecha_sabado", "")
        wa_msg = (
            f"Hola {nom_g}, te recordamos que el reporte de novedades de combustible de la sucursal "
            f"{s['sucursal_nombre']} para el corte del sábado {sab_str} aún está pendiente de envío. "
            f"Por favor completarlo y enviarlo antes del cierre. Enlace: https://alfacredit-novedades.vercel.app/novedades"
        )
        s["whatsapp_mensaje"] = wa_msg
        s["whatsapp_url"] = f"https://wa.me/{wa_num}?text={urllib.parse.quote(wa_msg)}" if wa_num else f"https://wa.me/?text={urllib.parse.quote(wa_msg)}"

    total_sucursales = len(sucursales_raw)
    sucursales_enviadas = [s for s in sucursales_raw if s.get("reporte_estado") in ["ENVIADO", "APROBADO"]]
    sucursales_pendientes = [s for s in sucursales_raw if s.get("reporte_estado") not in ["ENVIADO", "APROBADO"]]
    conteo_enviadas = len(sucursales_enviadas)
    porcentaje_gerentes = int((conteo_enviadas / total_sucursales * 100)) if total_sucursales > 0 else 0

    # Recordatorio masivo general para el grupo de WhatsApp de Gerentes
    nombres_pendientes = [s["sucursal_nombre"] for s in sucursales_pendientes]
    str_pendientes = ", ".join(nombres_pendientes) if nombres_pendientes else "Ninguna"
    sab_str = ciclo_dict.get("fecha_sabado", "")
    recordatorio_general = (
        f"🚨 *RECORDATORIO DE NOVEDADES - ALFACREDIT S.A.* 🚨\n\n"
        f"Estimados Gerentes, les recordamos que el corte semanal para el sábado *{sab_str}* está próximo a cerrar.\n"
        f"Sucursales pendientes de enviar reporte: *{str_pendientes}*.\n\n"
        f"Agradecemos completar y enviar su informe a la brevedad en:\n"
        f"👉 https://alfacredit-novedades.vercel.app/novedades"
    )
    recordatorio_general_encoded = urllib.parse.quote(recordatorio_general)

    # 2. Eventos Brandon
    c.execute("SELECT * FROM eventos_brandon WHERE ciclo_id = ? ORDER BY id DESC LIMIT 1", (ciclo_id,))
    evento_brandon = c.fetchone()
    evento_brandon_dict = dict(evento_brandon) if evento_brandon else None
    if evento_brandon_dict:
        raw_adj = evento_brandon_dict.get("archivos_adjuntos_json")
        if raw_adj:
            try:
                evento_brandon_dict["archivos_adjuntos"] = json.loads(raw_adj)
            except Exception:
                evento_brandon_dict["archivos_adjuntos"] = []
        elif evento_brandon_dict.get("archivo_adjunto"):
            evento_brandon_dict["archivos_adjuntos"] = [evento_brandon_dict["archivo_adjunto"]]
        else:
            evento_brandon_dict["archivos_adjuntos"] = []

    # 3. Eventos Dania
    c.execute("SELECT * FROM eventos_dania WHERE ciclo_id = ? ORDER BY id DESC LIMIT 1", (ciclo_id,))
    evento_dania = c.fetchone()
    evento_dania_dict = dict(evento_dania) if evento_dania else None

    # 4. Determinar Fase Actual y Responsable del Atraso
    # Fase 1: Gerentes
    # Fase 2: Brandon
    # Fase 3: Dania
    # Fase 4: Completado
    fase_actual = 1
    atraso_mensaje = ""
    atraso_responsable = ""
    nivel_alerta = "info" # 'info', 'warning', 'danger', 'success'

    if evento_dania_dict:
        fase_actual = 4
        atraso_responsable = "Ninguno"
        atraso_mensaje = "¡Proceso de Novedades y Combustible Completado con Éxito! Fondos listos y comprobante verificado."
        nivel_alerta = "success"
    elif evento_brandon_dict:
        fase_actual = 3
        atraso_responsable = "Dania (Tesorería / Finanzas)"
        atraso_mensaje = "Atraso en Fase 3: Brandon ya envió la solicitud. Dania tiene pendiente realizar el depósito y enviar/subir el comprobante bancario."
        nivel_alerta = "warning"
    elif conteo_enviadas >= total_sucursales and total_sucursales > 0:
        fase_actual = 2
        atraso_responsable = "Brandon (Coordinador Combustible)"
        atraso_mensaje = "Atraso en Fase 2: Todas las sucursales completaron sus reportes. Brandon tiene pendiente enviar la solicitud consolidada por correo a Finanzas."
        nivel_alerta = "warning"
    else:
        fase_actual = 1
        nombres_faltantes = ", ".join([s["sucursal_nombre"] for s in sucursales_pendientes]) if sucursales_pendientes else "Ninguna"
        atraso_responsable = "Gerentes de Sucursal"
        atraso_mensaje = f"Atraso en Fase 1: Faltan {len(sucursales_pendientes)} sucursales por enviar su reporte ({nombres_faltantes})."
        nivel_alerta = "danger" if conteo_enviadas < (total_sucursales / 2) else "warning"

    # Actualizar estado del ciclo en BD solo si es el ciclo actual (no alterar ciclos archivados)
    estado_enum_map = {
        1: 'FASE_1_GERENTES',
        2: 'FASE_2_BRANDON',
        3: 'FASE_3_DANIA',
        4: 'FASE_4_COMPLETADO'
    }
    nuevo_estado = estado_enum_map[fase_actual]
    if es_ciclo_actual and ciclo_dict.get("estado_general") != nuevo_estado:
        c.execute("UPDATE ciclos_semanales SET estado_general = ? WHERE id = ?", (nuevo_estado, ciclo_id))
        conn.commit()
        ciclo_dict["estado_general"] = nuevo_estado

    # Suma total combustible calculado y neto
    suma_total_base = sum(s.get("suma_base", 0.0) or 0.0 for s in sucursales_raw)
    suma_total_neto = sum(s.get("suma_neto", 0.0) or 0.0 for s in sucursales_raw)

    conn.close()

    return {
        "ciclo": ciclo_dict,
        "fase_actual": fase_actual,
        "fase_nombre": nuevo_estado,
        "total_sucursales": total_sucursales,
        "conteo_enviadas": conteo_enviadas,
        "porcentaje_gerentes": porcentaje_gerentes,
        "atraso_responsable": atraso_responsable,
        "atraso_mensaje": atraso_mensaje,
        "nivel_alerta": nivel_alerta,
        "sucursales": sucursales_raw,
        "sucursales_pendientes": sucursales_pendientes,
        "sucursales_enviadas": sucursales_enviadas,
        "evento_brandon": evento_brandon_dict,
        "evento_dania": evento_dania_dict,
        "suma_total_base": round(suma_total_base, 2),
        "suma_total_neto": round(suma_total_neto, 2),
        "servidor_hora": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "es_ciclo_actual": es_ciclo_actual,
        "latest_ciclo_id": latest_ciclo_id,
        "recordatorio_general": recordatorio_general,
        "recordatorio_general_encoded": recordatorio_general_encoded
    }

def verificar_bloqueo_novedades(ciclo_id: int, conn) -> tuple[bool, str]:
    """
    Determina si las novedades están bloqueadas para los gerentes de sucursal.
    Regla de Bloqueo:
    En cuanto Brandon envía la solicitud de combustible (o Fase 2 enviada / Fase 3 / Fase 4),
    NINGÚN gerente puede editar ni guardar novedades de su sucursal hasta que el despacho
    de fondos (Fase 4) esté completado y Brandon realice el corte semanal e inicie el nuevo ciclo.
    """
    c = conn.cursor()
    c.execute("SELECT estado_general FROM ciclos_semanales WHERE id = ?", (ciclo_id,))
    ciclo = c.fetchone()
    if not ciclo:
        return False, ""

    estado_gen = ciclo["estado_general"]

    # Revisar si Brandon ya registró envío de solicitud
    c.execute("SELECT id FROM eventos_brandon WHERE ciclo_id = ?", (ciclo_id,))
    ev_b = c.fetchone()

    # Revisar si Dania ya registró depósito (Fase 4 completada)
    c.execute("SELECT id FROM eventos_dania WHERE ciclo_id = ?", (ciclo_id,))
    ev_d = c.fetchone()

    if ev_d or estado_gen == "FASE_4_COMPLETADO":
        return True, "El ciclo semanal actual ha concluido con éxito y los fondos han sido despachados (Fase 4). Las novedades están en modo de SOLO LECTURA hasta que la Coordinación (Brandon) realice el corte semanal e inicie el nuevo ciclo con el botón de limpiar."
    elif ev_b or estado_gen == "FASE_3_DANIA":
        return True, "La Coordinación (Brandon) ya formalizó y envió la solicitud de combustible a Finanzas. El reporte de novedades se encuentra bloqueado en modo de SOLO LECTURA hasta que se complete el despacho de fondos (Fase 4) y se realice el corte semanal."

    return False, ""

# --- EVENTOS DE INICIO ---
@app.on_event("startup")
async def startup_event():
    init_db()
    # Tarea de fondo para auto-sincronización de correo si está habilitada
    asyncio.create_task(background_email_worker())

async def background_email_worker():
    """Worker asíncrono que revisa el correo de Brandon periódicamente"""
    while True:
        try:
            cfg = get_email_config()
            if cfg.get("enabled") and cfg.get("brandon_password"):
                res = escanear_correos_brandon()
                if res.get("solicitudes_detectadas") or res.get("depositos_detectados"):
                    # Notificar por WebSockets
                    estado = obtener_estado_completo_pipeline()
                    await ws_manager.broadcast_status({"type": "STATUS_UPDATE", "data": estado})
        except Exception as e:
            print(f"[BACKGROUND EMAIL ERROR]: {e}")
        await asyncio.sleep(60)

# --- WEBSOCKET ENDPOINT ---
@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        # Enviar estado inicial
        estado = obtener_estado_completo_pipeline()
        await websocket.send_json({"type": "STATUS_UPDATE", "data": estado})
        while True:
            # Mantener viva la conexión
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)

# --- RUTAS DE NAVEGACIÓN Y VISTAS HTML ---

@app.get("/api/index.py", response_class=HTMLResponse)
@app.get("/api", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return RedirectResponse(url="/dashboard", status_code=302)

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(request=request, name="login.html", context={})

@app.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)
):
    email = email.strip().lower()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM usuarios WHERE LOWER(email) = LOWER(?) AND activo = 1", (email,))
    user = c.fetchone()

    if not user or not verify_password(password, user["password_hash"]):
        conn.close()
        log_audit(email, "Intento Fallido", "desconocido", "LOGIN_FALLIDO", "Credenciales incorrectas", ip_address=request.client.host)
        return templates.TemplateResponse(request=request, name="login.html", context={
            "error": "Correo o contraseña incorrectos.",
            "email_previo": email
        })

    # Actualizar último acceso
    c.execute("UPDATE usuarios SET ultimo_acceso = ? WHERE id = ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user["id"]))
    conn.commit()
    conn.close()

    token = create_session_token(user["id"], user["email"])
    log_audit(user["email"], user["nombre"], user["rol"], "LOGIN_EXITOSO", f"Inicio de sesión exitoso como {user['rol']}", ip_address=request.client.host)

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        max_age=86400 * 7, # 7 días
        samesite="lax"
    )
    return response

# --- RECUPERACIÓN DE CONTRASEÑA ---

@app.get("/recuperar-password", response_class=HTMLResponse)
async def recuperar_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="recuperar_password.html", context={})

@app.post("/recuperar-password", response_class=HTMLResponse)
async def recuperar_password_submit(request: Request, email: str = Form(...)):
    res = solicitar_token_recuperacion(email)
    if not res:
        return templates.TemplateResponse(request=request, name="recuperar_password.html", context={
            "error": "El correo ingresado no se encuentra registrado en el sistema."
        })

    enlace = f"{str(request.base_url).rstrip('/')}/restablecer-password?token={res['token']}"
    log_audit(
        res["email"], res["nombre"], "usuario",
        "SOLICITUD_RECUPERAR_PASSWORD",
        f"Se generó token de recuperación de contraseña para {res['email']}",
        ip_address=request.client.host
    )
    return templates.TemplateResponse(request=request, name="recuperar_password.html", context={
        "mensaje_exito": f"Se ha validado la cuenta de {res['nombre']}. Utiliza el siguiente enlace para restablecer tu acceso.",
        "enlace_directo": enlace
    })

@app.get("/restablecer-password", response_class=HTMLResponse)
async def restablecer_password_page(request: Request, token: str):
    info = validar_token_recuperacion(token)
    return templates.TemplateResponse(request=request, name="restablecer_password.html", context={
        "token": token,
        "token_valido": info is not None,
        "usuario": info
    })

@app.post("/restablecer-password", response_class=HTMLResponse)
async def restablecer_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...)
):
    if password != confirm_password:
        info = validar_token_recuperacion(token)
        return templates.TemplateResponse(request=request, name="restablecer_password.html", context={
            "token": token,
            "token_valido": info is not None,
            "usuario": info,
            "error": "Las contraseñas no coinciden. Por favor verifica e intenta de nuevo."
        })

    if len(password) < 4:
        info = validar_token_recuperacion(token)
        return templates.TemplateResponse(request=request, name="restablecer_password.html", context={
            "token": token,
            "token_valido": info is not None,
            "usuario": info,
            "error": "La contraseña debe tener al menos 4 caracteres."
        })

    res = restablecer_password_con_token(token, password)
    if not res["success"]:
        return templates.TemplateResponse(request=request, name="restablecer_password.html", context={
            "token": token,
            "token_valido": False,
            "error": res["message"]
        })

    log_audit(
        res["email"], res["nombre"], res["rol"],
        "PASSWORD_RESTABLECIDO",
        f"El usuario {res['email']} restableció su contraseña exitosamente con token de seguridad",
        ip_address=request.client.host
    )

    return templates.TemplateResponse(request=request, name="login.html", context={
        "error": None,
        "mensaje_exito": "¡Contraseña actualizada exitosamente! Ya puedes iniciar sesión con tu nueva contraseña.",
        "email_previo": res["email"]
    })

@app.get("/logout")
async def logout(request: Request):
    user = get_current_user(request)
    if user:
        log_audit(user["email"], user["nombre"], user["rol"], "LOGOUT", "Cierre de sesión voluntario", ip_address=request.client.host)
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(COOKIE_NAME)
    return response

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    ciclo_id: Optional[int] = None,
    user: dict = Depends(require_auth)
):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
    SELECT id, fecha_sabado, fecha_inicio_pas, fecha_fin_pas, estado_general, cerrado_en
    FROM ciclos_semanales
    ORDER BY id DESC
    ''')
    ciclos_historial = [dict(r) for r in c.fetchall()]
    conn.close()

    estado = obtener_estado_completo_pipeline(ciclo_id=ciclo_id)
    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "user": user,
        "estado": estado,
        "ciclos_historial": ciclos_historial,
        "selected_ciclo_id": estado["ciclo"]["id"],
        "leyenda": LEYENDA_MAESTRA
    })

@app.get("/novedades", response_class=HTMLResponse)
async def novedades_page(
    request: Request,
    sucursal_id: Optional[int] = None,
    user: dict = Depends(require_auth)
):
    conn = get_db_connection()
    c = conn.cursor()

    # Si es jefe de crédito, su rol es exclusivo de monitoreo en el Dashboard
    if user["rol"] == "jefe_credito":
        return RedirectResponse(url="/dashboard", status_code=302)

    # Si es gerente, forzar su propia sucursal
    if user["rol"] == "gerente":
        target_sucursal_id = user["sucursal_id"]
    else:
        target_sucursal_id = sucursal_id or user.get("sucursal_id") or 1

    # Obtener info de la sucursal
    c.execute("SELECT * FROM sucursales WHERE id = ?", (target_sucursal_id,))
    sucursal = c.fetchone()
    if not sucursal:
        target_sucursal_id = 1
        c.execute("SELECT * FROM sucursales WHERE id = 1")
        sucursal = c.fetchone()

    # Ciclo activo
    c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    ciclo = c.fetchone()
    if not ciclo:
        ciclo_id = asegurar_ciclo_actual(conn)
        c.execute("SELECT * FROM ciclos_semanales WHERE id = ?", (ciclo_id,))
        ciclo = c.fetchone()
    ciclo = dict(ciclo)

    # Reporte de esta sucursal
    c.execute('''
    SELECT * FROM reportes_sucursales 
    WHERE ciclo_id = ? AND sucursal_id = ?
    ''', (ciclo["id"], target_sucursal_id))
    reporte = c.fetchone()

    if not reporte:
        asegurar_ciclo_actual(conn)
        c.execute('''
        SELECT * FROM reportes_sucursales 
        WHERE ciclo_id = ? AND sucursal_id = ?
        ''', (ciclo["id"], target_sucursal_id))
        reporte = c.fetchone()

    reporte_dict = dict(reporte)

    # Detalle de novedades (filas de colaboradores)
    c.execute('''
    SELECT * FROM detalle_novedades 
    WHERE reporte_id = ? 
    ORDER BY id ASC
    ''', (reporte_dict["id"],))
    filas_raw = [dict(r) for r in c.fetchall()]

    for f in filas_raw:
        f["dias_pas"] = json.loads(f["dias_pas_json"])
        f["dias_fut"] = json.loads(f["dias_fut_json"])

    # Lista de todas las sucursales para el combo si es admin/brandon
    c.execute("SELECT id, nombre FROM sucursales WHERE activa = 1 ORDER BY id ASC")
    lista_sucursales = [dict(r) for r in c.fetchall()]

    # Verificar si el ciclo está bloqueado para gerentes
    bloqueado = False
    bloqueado_motivo = ""
    if user["rol"] == "gerente":
        bloqueado, bloqueado_motivo = verificar_bloqueo_novedades(ciclo["id"], conn)

    conn.close()

    # Calcular fechas de cabecera
    fsab = datetime.strptime(ciclo["fecha_sabado"], "%Y-%m-%d").date()
    lunes_pas = fsab - timedelta(days=5)
    lunes_fut = fsab + timedelta(days=2)
    dias_base = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB"]

    fechas_pas = [(dias_base[i], (lunes_pas + timedelta(days=i)).strftime("%d/%m")) for i in range(6)]
    fechas_fut = [(dias_base[i], (lunes_fut + timedelta(days=i)).strftime("%d/%m")) for i in range(6)]

    cargos_opciones = ["GERENTE SUCURSAL", "SUPERVISOR", "OFICIAL DE CREDITO", "OFICIAL DE DESEMBOLSO", "PROMOTOR INTERNO", "RESP. OPERACIONES", "CONSERJE"]

    return templates.TemplateResponse(request=request, name="novedades.html", context={
        "user": user,
        "sucursal": dict(sucursal),
        "ciclo": ciclo,
        "reporte": reporte_dict,
        "filas": filas_raw,
        "fechas_pas": fechas_pas,
        "fechas_fut": fechas_fut,
        "leyenda": LEYENDA_MAESTRA,
        "cargos_opciones": cargos_opciones,
        "lista_sucursales": lista_sucursales,
        "bloqueado": bloqueado,
        "bloqueado_motivo": bloqueado_motivo
    })

@app.get("/consolidado", response_class=HTMLResponse)
async def consolidado_page(request: Request, user: dict = Depends(require_auth)):
    if user["rol"] not in ["admin", "brandon"]:
        raise HTTPException(status_code=403, detail="Acceso exclusivo para Administración y Coordinación de Logística (Brandon)")
    estado = obtener_estado_completo_pipeline()
    cfg_email = get_email_config()
    return templates.TemplateResponse(request=request, name="consolidado.html", context={
        "user": user,
        "estado": estado,
        "cfg_email": cfg_email
    })

@app.get("/deposito", response_class=HTMLResponse)
async def deposito_page(request: Request, user: dict = Depends(require_auth)):
    if user["rol"] not in ["admin", "brandon"]:
        raise HTTPException(status_code=403, detail="Acceso exclusivo para Administración y Coordinación de Logística (Brandon)")
    estado = obtener_estado_completo_pipeline()
    return templates.TemplateResponse(request=request, name="dania_deposito.html", context={
        "user": user,
        "estado": estado
    })

@app.get("/auditoria", response_class=HTMLResponse)
async def auditoria_page(
    request: Request,
    accion: Optional[str] = None,
    search: Optional[str] = None,
    user: dict = Depends(require_auth)
):
    if user["rol"] not in ["admin", "brandon"]:
        raise HTTPException(status_code=403, detail="Acceso exclusivo para Administración y Coordinación de Logística (Brandon)")
    logs = get_audit_logs(limit=200, accion=accion, search=search)
    return templates.TemplateResponse(request=request, name="auditoria.html", context={
        "user": user,
        "logs": logs,
        "filtro_accion": accion or "TODAS",
        "search_term": search or ""
    })

@app.get("/email-config", response_class=HTMLResponse)
async def email_config_page(request: Request, user: dict = Depends(require_auth)):
    if user["rol"] not in ["admin", "brandon"]:
        raise HTTPException(status_code=403, detail="Acceso exclusivo para Administración y Coordinación de Logística (Brandon)")
    cfg = get_email_config()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM monitoreo_correos_log ORDER BY id DESC LIMIT 50")
    logs_correo = [dict(r) for r in c.fetchall()]
    conn.close()

    return templates.TemplateResponse(request=request, name="email_config.html", context={
        "user": user,
        "cfg": cfg,
        "logs_correo": logs_correo
    })

@app.get("/respaldos", response_class=HTMLResponse)
async def respaldos_page(request: Request, user: dict = Depends(require_auth)):
    if user["rol"] != "admin":
        raise HTTPException(status_code=403, detail="Acceso exclusivo para Administradores")
    backups = listar_respaldos()
    return templates.TemplateResponse(request=request, name="respaldos.html", context={
        "user": user,
        "backups": backups
    })

# --- ENDPOINTS API REST ---

@app.get("/api/status")
async def api_get_status(ciclo_id: Optional[int] = None):
    return obtener_estado_completo_pipeline(ciclo_id=ciclo_id)

@app.post("/api/novedades/save")
async def api_save_novedades(request: Request, payload: dict, user: dict = Depends(require_auth)):
    reporte_id = payload.get("reporte_id")
    filas = payload.get("filas", [])
    es_envio_final = payload.get("es_envio_final", False)

    if not reporte_id:
        raise HTTPException(status_code=400, detail="Falta reporte_id")

    conn = get_db_connection()
    c = conn.cursor()

    # Verificar reporte y permisos
    c.execute('''
    SELECT r.*, s.nombre as sucursal_nombre 
    FROM reportes_sucursales r
    JOIN sucursales s ON r.sucursal_id = s.id
    WHERE r.id = ?
    ''', (reporte_id,))
    rep = c.fetchone()
    if not rep:
        conn.close()
        raise HTTPException(status_code=404, detail="Reporte no encontrado")

    if user["rol"] == "gerente":
        if rep["sucursal_id"] != user["sucursal_id"]:
            conn.close()
            raise HTTPException(status_code=403, detail="No puedes modificar reportes de otra sucursal")

        bloqueado, motivo = verificar_bloqueo_novedades(rep["ciclo_id"], conn)
        if bloqueado:
            conn.close()
            raise HTTPException(status_code=403, detail=motivo)

    # Eliminar filas viejas y reinsertar nuevas calculadas
    c.execute("DELETE FROM detalle_novedades WHERE reporte_id = ?", (reporte_id,))

    suma_base = 0.0
    suma_ajustes = 0.0
    suma_neto = 0.0

    for f in filas:
        nombre = f.get("nombre", "").strip()
        if not nombre:
            continue
        cargo = f.get("cargo", "OFICIAL DE CREDITO")
        estado = f.get("estado", "ACTIVO")
        comb_base = float(f.get("combustible_base", 0.0) or 0.0)
        dias_pas = f.get("dias_pas", [""] * 6)
        dias_fut = f.get("dias_fut", [""] * 6)
        talla = f.get("talla", "")
        req = f.get("req", "")
        obs = f.get("obs", "")

        calc = calcular_fila_novedades(comb_base, dias_pas, dias_fut)
        suma_base += calc["combustible_base"]
        suma_ajustes += calc["ajuste_dinero"]
        suma_neto += calc["total_neto"]

        c.execute('''
        INSERT INTO detalle_novedades (
            reporte_id, colaborador_id, nombre, cargo, estado,
            combustible_base, factor_total, deduccion_ajuste, total_neto,
            dias_pas_json, dias_fut_json, talla, req, obs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            reporte_id, f.get("colaborador_id"), nombre, cargo, estado,
            calc["combustible_base"], calc["factor_total"], calc["deduccion_mostrada"], calc["total_neto"],
            json.dumps(dias_pas), json.dumps(dias_fut), talla, req, obs
        ))

    nuevo_estado_rep = "ENVIADO" if es_envio_final else "BORRADOR"
    fecha_envio = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if es_envio_final else rep["enviado_en"]

    c.execute('''
    UPDATE reportes_sucursales 
    SET estado = ?, enviado_en = ?, total_empleados = ?,
        suma_base = ?, suma_ajustes = ?, suma_neto = ?,
        usuario_id = ?, actualizado_en = CURRENT_TIMESTAMP
    WHERE id = ?
    ''', (
        nuevo_estado_rep, fecha_envio, len(filas),
        round(suma_base, 2), round(suma_ajustes, 2), round(suma_neto, 2),
        user["id"], reporte_id
    ))
    conn.commit()
    conn.close()

    accion_txt = "ENVIO_REPORTE" if es_envio_final else "GUARDAR_BORRADOR"
    log_audit(
        user["email"], user["nombre"], user["rol"],
        accion_txt,
        f"{'Envió oficialmente' if es_envio_final else 'Guardó borrador de'} novedades de {rep['sucursal_nombre']} (Total: C$ {round(suma_neto, 2):,.2f})",
        sucursal_nombre=rep["sucursal_nombre"],
        ip_address=request.client.host
    )

    # Notificar WebSocket
    nuevo_estado_pipeline = obtener_estado_completo_pipeline()
    await ws_manager.broadcast_status({"type": "STATUS_UPDATE", "data": nuevo_estado_pipeline})

    return {
        "success": True,
        "message": "Reporte enviado exitosamente" if es_envio_final else "Borrador guardado correctamente",
        "suma_neto": round(suma_neto, 2)
    }

@app.post("/api/novedades/submit")
async def api_submit_novedades(request: Request, payload: dict, user: dict = Depends(require_auth)):
    payload["es_envio_final"] = True
    return await api_save_novedades(request, payload, user)

@app.post("/api/ciclo/corte-semanal")
@app.post("/api/ciclo/limpiar")
async def api_corte_semanal(request: Request, user: dict = Depends(require_auth)):
    """
    Corte Semanal y Apertura de Nuevo Ciclo para Coordinación (Brandon) y Admin:
    - Valida que el ciclo actual esté en Fase 4 (Fondos Despachados / Depósito de Dania verificado).
    - Archiva el ciclo actual en la base de datos con toda su trazabilidad histórica intacta.
    - Crea el nuevo ciclo (+7 días al siguiente sábado) en FASE_1_GERENTES.
    - Preserva todos los colaboradores ya trabajados por los gerentes pero limpia las novedades a blanco.
    - Vuelve a habilitar la edición de novedades para todas las sucursales.
    """
    if user["rol"] not in ["admin", "brandon"]:
        raise HTTPException(
            status_code=403,
            detail="Acceso restringido: Solo la Coordinación de Logística (Brandon) y Administradores tienen autorización para realizar el corte semanal e iniciar el nuevo ciclo."
        )

    # Validar que el ciclo actual esté en Fase 4
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    ciclo = c.fetchone()
    if not ciclo:
        conn.close()
        raise HTTPException(status_code=400, detail="No se encontró un ciclo activo en el sistema.")

    c.execute("SELECT id FROM eventos_dania WHERE ciclo_id = ?", (ciclo["id"],))
    ev_d = c.fetchone()
    if not ev_d and ciclo["estado_general"] != "FASE_4_COMPLETADO":
        conn.close()
        raise HTTPException(
            status_code=400,
            detail="No se puede realizar el corte semanal hasta que el acápite de fondos despachados esté completado (Fase 4 - Depósito de Dania confirmado)."
        )
    conn.close()

    try:
        resultado = realizar_corte_semanal_logica(user)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al ejecutar el corte semanal: {str(e)}")

    log_audit(
        user["email"], user["nombre"], user["rol"],
        "CORTE_SEMANAL_EJECUTADO",
        f"Brandon realizó el corte semanal y habilitó el nuevo ciclo para el sábado {resultado['fecha_sabado']}. Novedades limpias y colaboradores preservados para las {resultado['total_sucursales']} sucursales.",
        ip_address=request.client.host
    )

    nuevo_estado = obtener_estado_completo_pipeline()
    await ws_manager.broadcast_status({"type": "STATUS_UPDATE", "data": nuevo_estado})

    return {
        "success": True,
        "message": f"Corte semanal completado exitosamente. Se ha iniciado el nuevo ciclo para el corte del sábado {resultado['fecha_sabado']}. Todas las sucursales están listas en estado PENDIENTE con novedades limpias y colaboradores intactos para que los gerentes comiencen a trabajar.",
        "ciclo_anterior_id": resultado["ciclo_anterior_id"],
        "nuevo_ciclo_id": resultado["nuevo_ciclo_id"],
        "fecha_sabado": resultado["fecha_sabado"],
        "nuevo_estado": nuevo_estado
    }

@app.post("/api/brandon/send-request")
async def api_brandon_send(
    request: Request,
    asunto: str = Form(...),
    cuerpo: Optional[str] = Form(""),
    destinatario: str = Form("dania.tesoreria@alfacredit.com"),
    archivos_pdf: List[UploadFile] = File(None),
    user: dict = Depends(require_auth)
):
    if user["rol"] not in ["admin", "brandon"]:
        raise HTTPException(status_code=403, detail="Solo Brandon o Administradores pueden registrar el envío de la solicitud.")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    ciclo = c.fetchone()
    if not ciclo:
        conn.close()
        raise HTTPException(status_code=400, detail="No hay ciclo activo")

    ciclo_id = ciclo["id"]
    fecha_envio = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Guardar múltiples archivos PDF adjuntos
    os.makedirs(STORAGE_ADJUNTOS_BRANDON, exist_ok=True)
    guardados = []
    if archivos_pdf:
        for arch in archivos_pdf:
            if arch and arch.filename:
                safe_name = os.path.basename(arch.filename).replace(" ", "_")
                clean_fn = f"solicitud_ciclo_{ciclo_id}_{int(datetime.now().timestamp())}_{safe_name}"
                f_path = os.path.join(STORAGE_ADJUNTOS_BRANDON, clean_fn)
                contents = await arch.read()
                if len(contents) > 0:
                    with open(f_path, "wb") as f:
                        f.write(contents)
                    guardados.append(clean_fn)

    c.execute('''
    INSERT INTO eventos_brandon (ciclo_id, usuario_id, correo_destinatario, asunto, cuerpo, archivo_adjunto, archivos_adjuntos_json, fecha_envio, metodo)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'MANUAL_WEB')
    ''', (ciclo_id, user["id"], destinatario, asunto, cuerpo, guardados[0] if guardados else None, json.dumps(guardados), fecha_envio))

    c.execute("UPDATE ciclos_semanales SET estado_general = 'FASE_3_DANIA' WHERE id = ?", (ciclo_id,))
    conn.commit()
    conn.close()

    log_audit(
        user["email"], user["nombre"], user["rol"],
        "SOLICITUD_COMBUSTIBLE_ENVIADA",
        f"Brandon registró el envío de la solicitud de combustible '{asunto}' hacia {destinatario} con {len(guardados)} archivo(s) PDF adjunto(s)",
        ip_address=request.client.host
    )

    nuevo_estado = obtener_estado_completo_pipeline()
    await ws_manager.broadcast_status({"type": "STATUS_UPDATE", "data": nuevo_estado})

    return {
        "success": True, 
        "message": f"Solicitud registrada y enviada a Finanzas (Dania) con {len(guardados)} archivo(s) PDF adjunto(s).",
        "archivos_adjuntos": guardados
    }

@app.get("/api/adjuntos-brandon/download/{filename}")
async def api_download_adjunto_brandon(filename: str, user: dict = Depends(require_auth)):
    f_path = os.path.join(STORAGE_ADJUNTOS_BRANDON, filename)
    if not os.path.exists(f_path):
        raise HTTPException(status_code=404, detail="Archivo adjunto no encontrado")
    return FileResponse(f_path, filename=filename)

@app.post("/api/dania/register-deposit")
async def api_dania_deposit(
    request: Request,
    monto: float = Form(...),
    referencia: str = Form(...),
    notas: Optional[str] = Form(""),
    comprobante_file: Optional[UploadFile] = File(None),
    user: dict = Depends(require_auth)
):
    if user["rol"] not in ["admin", "brandon"]:
        raise HTTPException(status_code=403, detail="Solo Brandon o Administradores pueden registrar o confirmar el depósito.")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    ciclo = c.fetchone()
    if not ciclo:
        conn.close()
        raise HTTPException(status_code=400, detail="No hay ciclo activo")

    ciclo_id = ciclo["id"]
    archivo_guardado = None

    if comprobante_file and comprobante_file.filename:
        os.makedirs(STORAGE_COMPROBANTES, exist_ok=True)
        ext = os.path.splitext(comprobante_file.filename)[1]
        archivo_guardado = f"comprobante_ciclo_{ciclo_id}_{int(datetime.now().timestamp())}{ext}"
        f_path = os.path.join(STORAGE_COMPROBANTES, archivo_guardado)
        contents = await comprobante_file.read()
        with open(f_path, "wb") as f:
            f.write(contents)

    fecha_dep = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    c.execute('''
    INSERT INTO eventos_dania (ciclo_id, usuario_id, monto_depositado, numero_referencia, comprobante_archivo, fecha_deposito, metodo, notas)
    VALUES (?, ?, ?, ?, ?, ?, 'MANUAL_WEB', ?)
    ''', (ciclo_id, user["id"], monto, referencia, archivo_guardado, fecha_dep, notas))

    c.execute('''
    UPDATE ciclos_semanales 
    SET estado_general = 'FASE_4_COMPLETADO', total_combustible_aprobado = ?, cerrado_en = ?
    WHERE id = ?
    ''', (monto, fecha_dep, ciclo_id))
    conn.commit()
    conn.close()

    log_audit(
        user["email"], user["nombre"], user["rol"],
        "DEPOSITO_COMBUSTIBLE_REGISTRADO",
        f"Dania registró depósito de C$ {monto:,.2f} con Ref: {referencia}. Proceso completado al 100%.",
        ip_address=request.client.host
    )

    nuevo_estado = obtener_estado_completo_pipeline()
    await ws_manager.broadcast_status({"type": "STATUS_UPDATE", "data": nuevo_estado})

    return {"success": True, "message": "Comprobante de depósito registrado exitosamente. Ciclo completado."}

@app.post("/api/email/sync")
async def api_email_sync(request: Request, user: dict = Depends(require_auth)):
    res = escanear_correos_brandon()
    log_audit(
        user["email"], user["nombre"], user["rol"],
        "SINCRONIZACION_CORREO_MANUAL",
        f"Sincronización manual de correo ejecutada: {res.get('mensaje')}",
        ip_address=request.client.host
    )
    nuevo_estado = obtener_estado_completo_pipeline()
    await ws_manager.broadcast_status({"type": "STATUS_UPDATE", "data": nuevo_estado})
    return res

@app.post("/api/email/confirm-secondary")
async def api_confirm_secondary_email(
    request: Request,
    tipo: Optional[str] = Form(None),
    notas: Optional[str] = Form(None),
    user: dict = Depends(require_auth)
):
    """
    Confirmación secundaria inmediata de recepción de correo para Brandon / Administrador.
    Permite validar que el correo ya cayó sin depender de la conexión o comprobación automática IMAP.
    Soporta peticiones enviadas tanto por Form-data como por JSON.
    """
    if user["rol"] not in ["admin", "brandon"]:
        raise HTTPException(
            status_code=403, 
            detail="Acceso restringido: Solo Administrador y Brandon pueden confirmar manualmente la recepción secundaria de correos."
        )

    # Permitir lectura tanto de form-data como de JSON
    if tipo is None:
        try:
            body = await request.json()
            if isinstance(body, dict):
                tipo = body.get("tipo")
                if notas is None:
                    notas = body.get("notas")
        except Exception:
            pass

    if tipo is None:
        tipo = "auto"
    if notas is None:
        notas = ""

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    ciclo = c.fetchone()
    if not ciclo:
        conn.close()
        raise HTTPException(status_code=400, detail="No se encontró un ciclo semanal activo.")

    ciclo_id = ciclo["id"]
    fecha_ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Determinar qué paso confirmar
    target = (tipo or "auto").strip().lower()
    if target == "auto":
        c.execute("SELECT id FROM eventos_brandon WHERE ciclo_id = ?", (ciclo_id,))
        ev_b = c.fetchone()
        if not ev_b:
            target = "solicitud_brandon"
        else:
            target = "comprobante_dania"

    mensaje_exito = ""
    if target == "solicitud_brandon":
        c.execute("SELECT id FROM eventos_brandon WHERE ciclo_id = ?", (ciclo_id,))
        ev_b = c.fetchone()
        if not ev_b:
            asunto = f"Solicitud de Combustible - Corte {ciclo['fecha_sabado']} (Confirmación Secundaria Manual)"
            destinatario = "dania.tesoreria@alfacreditsa.com"
            cuerpo = notas or "Confirmación manual de recepción de correo de solicitud de combustible."
            c.execute('''
            INSERT INTO eventos_brandon (ciclo_id, usuario_id, correo_destinatario, asunto, cuerpo, fecha_envio, metodo)
            VALUES (?, ?, ?, ?, ?, ?, 'MANUAL_SECUNDARIO')
            ''', (ciclo_id, user["id"], destinatario, asunto, cuerpo, fecha_ahora))

            c.execute('''
            UPDATE ciclos_semanales 
            SET estado_general = 'FASE_3_DANIA' 
            WHERE id = ? AND estado_general IN ('FASE_1_GERENTES', 'FASE_2_BRANDON')
            ''', (ciclo_id,))
            conn.commit()
            mensaje_exito = "Solicitud de Combustible confirmada exitosamente. El ciclo avanzó a Fase 3 (Dania)."
        else:
            mensaje_exito = "La solicitud de combustible ya había sido registrada previamente."

    elif target == "comprobante_dania":
        c.execute("SELECT id FROM eventos_dania WHERE ciclo_id = ?", (ciclo_id,))
        ev_d = c.fetchone()
        if not ev_d:
            # Asegurar evento_brandon previo para consistencia de auditoría y visualización
            c.execute("SELECT id FROM eventos_brandon WHERE ciclo_id = ?", (ciclo_id,))
            if not c.fetchone():
                c.execute('''
                INSERT INTO eventos_brandon (ciclo_id, usuario_id, correo_destinatario, asunto, cuerpo, fecha_envio, metodo)
                VALUES (?, ?, 'dania.tesoreria@alfacreditsa.com', ?, 'Auto-generado por confirmación secundaria de Dania', ?, 'MANUAL_SECUNDARIO')
                ''', (ciclo_id, user["id"], f"Solicitud de Combustible - Corte {ciclo['fecha_sabado']}", fecha_ahora))

            c.execute("SELECT SUM(suma_neto) as total_neto FROM reportes_sucursales WHERE ciclo_id = ?", (ciclo_id,))
            tot_row = c.fetchone()
            monto = float(tot_row["total_neto"] or 0.0) if tot_row else 0.0
            ref = f"CONF-MANUAL-{int(datetime.now().timestamp())}"
            nota_final = notas or "Confirmación secundaria de correo: comprobante recibido y validado manualmente."
            
            c.execute('''
            INSERT INTO eventos_dania (ciclo_id, usuario_id, monto_depositado, numero_referencia, fecha_deposito, metodo, notas)
            VALUES (?, ?, ?, ?, ?, 'MANUAL_SECUNDARIO', ?)
            ''', (ciclo_id, user["id"], monto, ref, fecha_ahora, nota_final))

            c.execute('''
            UPDATE ciclos_semanales 
            SET estado_general = 'FASE_4_COMPLETADO', total_combustible_aprobado = ?, cerrado_en = ?
            WHERE id = ?
            ''', (monto, fecha_ahora, ciclo_id))
            conn.commit()
            mensaje_exito = f"Comprobante de depósito de Dania confirmado manualmente por C$ {monto:,.2f}. Proceso semanal completado al 100%."
        else:
            mensaje_exito = "El comprobante de depósito ya había sido registrado para este ciclo."
    else:
        conn.close()
        raise HTTPException(status_code=400, detail=f"Tipo de confirmación no válido: {tipo}")

    conn.close()

    log_audit(
        user["email"], user["nombre"], user["rol"],
        "CONFIRMACION_MANUAL_CORREO_SECUNDARIA",
        f"Confirmación secundaria activada por usuario: {mensaje_exito}",
        ip_address=request.client.host
    )

    nuevo_estado = obtener_estado_completo_pipeline()
    await ws_manager.broadcast_status({"type": "STATUS_UPDATE", "data": nuevo_estado})

    return {
        "success": True,
        "message": mensaje_exito,
        "nuevo_estado": nuevo_estado
    }


@app.post("/api/email/test")
async def api_email_test(payload: dict, user: dict = Depends(require_auth)):
    return test_imap_connection(
        payload.get("imap_server", "imap.gmail.com"),
        int(payload.get("imap_port", 993)),
        payload.get("use_ssl", True),
        payload.get("brandon_email", ""),
        payload.get("brandon_password", "")
    )

@app.post("/api/email/save-config")
async def api_email_save_config(payload: dict, user: dict = Depends(require_auth)):
    ok = update_email_config(payload)
    if ok:
        log_audit(user["email"], user["nombre"], user["rol"], "ACTUALIZAR_CONFIG_CORREO", "Se actualizaron los parámetros del monitor de correo", ip_address=request.client.host)
        return {"success": True, "message": "Configuración de correo guardada."}
    raise HTTPException(status_code=500, detail="Error al guardar configuración")

# --- EXPORTACIONES Y DESCARGAS ---

@app.get("/api/ciclos/historial")
async def api_ciclos_historial(user: dict = Depends(require_auth)):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
    SELECT id, fecha_sabado, fecha_inicio_pas, fecha_fin_pas, fecha_inicio_fut, fecha_fin_fut,
           estado_general, total_combustible_aprobado, cerrado_en, creado_en
    FROM ciclos_semanales
    ORDER BY id DESC
    ''')
    historial = [dict(r) for r in c.fetchall()]
    conn.close()
    return {"historial": historial}

@app.get("/api/export/excel/sucursal/{sucursal_id}")
async def api_export_excel_sucursal(
    sucursal_id: int,
    ciclo_id: Optional[int] = None,
    user: dict = Depends(require_auth)
):
    # Restricción: Si es gerente, únicamente puede descargar el reporte de su propia sucursal
    if user["rol"] == "gerente" and user.get("sucursal_id") != sucursal_id:
        raise HTTPException(
            status_code=403, 
            detail="Acceso restringido: Como gerente únicamente tienes autorización para descargar el reporte de tu propia sucursal."
        )

    conn = get_db_connection()
    c = conn.cursor()
    if ciclo_id:
        c.execute("SELECT * FROM ciclos_semanales WHERE id = ?", (ciclo_id,))
    else:
        c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    ciclo = c.fetchone()
    c.execute("SELECT * FROM sucursales WHERE id = ?", (sucursal_id,))
    suc = c.fetchone()
    if not ciclo or not suc:
        conn.close()
        raise HTTPException(status_code=404, detail="Sucursal o ciclo no encontrado")

    c.execute("SELECT id FROM reportes_sucursales WHERE ciclo_id = ? AND sucursal_id = ?", (ciclo["id"], sucursal_id))
    rep = c.fetchone()
    filas = []
    if rep:
        c.execute("SELECT * FROM detalle_novedades WHERE reporte_id = ?", (rep["id"],))
        filas = [dict(r) for r in c.fetchall()]
        for f in filas:
            f["dias_pas"] = json.loads(f["dias_pas_json"])
            f["dias_fut"] = json.loads(f["dias_fut_json"])

    conn.close()

    excel_buf = generar_excel_sucursal(suc["nombre"], ciclo["fecha_sabado"], filas)
    import unicodedata
    clean_suc_name = unicodedata.normalize('NFKD', suc['nombre']).encode('ASCII', 'ignore').decode('ASCII').replace(" ", "_")
    nombre_archivo = f"Reporte_Novedades_{clean_suc_name}_{ciclo['fecha_sabado'].replace('-', '')}.xlsx"

    log_audit(user["email"], user["nombre"], user["rol"], "DESCARGA_EXCEL_SUCURSAL", f"Descargó reporte Excel de {suc['nombre']}")

    return StreamingResponse(
        excel_buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'}
    )

@app.get("/api/export/excel/consolidado")
async def api_export_excel_consolidado(
    ciclo_id: Optional[int] = None,
    user: dict = Depends(require_auth)
):
    if user["rol"] == "gerente":
        raise HTTPException(
            status_code=403,
            detail="Acceso restringido: El consolidado general solo está disponible para Administración, Brandon y Tesorería."
        )

    conn = get_db_connection()
    c = conn.cursor()
    if ciclo_id:
        c.execute("SELECT * FROM ciclos_semanales WHERE id = ?", (ciclo_id,))
    else:
        c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    ciclo = c.fetchone()
    if not ciclo:
        conn.close()
        raise HTTPException(status_code=404, detail="No hay ciclo activo")

    c.execute('''
    SELECT s.id, s.nombre as sucursal_nombre, s.email_gerente,
           r.total_empleados, r.suma_base, r.suma_neto
    FROM sucursales s
    LEFT JOIN reportes_sucursales r ON s.id = r.sucursal_id AND r.ciclo_id = ?
    WHERE s.activa = 1
    ORDER BY s.id ASC
    ''', (ciclo["id"],))
    sucursales_data = [dict(r) for r in c.fetchall()]
    conn.close()

    excel_buf = generar_excel_consolidado_todas_sucursales(ciclo["fecha_sabado"], sucursales_data)
    nombre_archivo = f"Consolidado_Combustible_AlfaCredit_{ciclo['fecha_sabado'].replace('-', '')}.xlsx"

    log_audit(user["email"], user["nombre"], user["rol"], "DESCARGA_EXCEL_CONSOLIDADO", "Descargó el consolidado general de combustible en Excel")

    return StreamingResponse(
        excel_buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo}"'}
    )

@app.get("/api/comprobantes/download/{filename}")
async def api_download_comprobante(filename: str, user: dict = Depends(require_auth)):
    f_path = os.path.join(STORAGE_COMPROBANTES, filename)
    if not os.path.exists(f_path):
        raise HTTPException(status_code=404, detail="Comprobante no encontrado")
    return FileResponse(f_path, filename=filename)

# --- BACKUPS API ---

@app.post("/api/backups/create")
async def api_backup_create(request: Request, user: dict = Depends(require_auth)):
    res = crear_respaldo_completo(usuario_email=user["email"], tipo="MANUAL")
    return res

@app.get("/api/backups/download/{filename}")
async def api_backup_download(filename: str, user: dict = Depends(require_auth)):
    f_path = os.path.join(BACKUP_DIR, filename)
    if not os.path.exists(f_path):
        raise HTTPException(status_code=404, detail="Archivo de respaldo no encontrado")
    log_audit(user["email"], user["nombre"], user["rol"], "DESCARGA_RESPALDO", f"Descargó el respaldo {filename}")
    return FileResponse(f_path, filename=filename, media_type="application/zip")

@app.post("/api/backups/restore/{filename}")
async def api_backup_restore(filename: str, request: Request, user: dict = Depends(require_auth)):
    if user["rol"] != "admin":
        raise HTTPException(status_code=403, detail="Solo administradores pueden restaurar respaldos")
    res = restaurar_respaldo(filename, usuario_email=user["email"])
    return res
