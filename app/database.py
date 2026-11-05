import sqlite3
import os
import json
import hashlib
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Detección de entorno Serverless efímero (Vercel, AWS Lambda, o variable explícita)
IS_SERVERLESS = bool(
    os.environ.get("VERCEL") 
    or os.environ.get("AWS_LAMBDA_FUNCTION_NAME") 
    or os.environ.get("USE_TMP_DB")
)

if IS_SERVERLESS:
    TMP_DIR = os.environ.get("TMPDIR", "/tmp")
    STORAGE_DIR = os.path.join(TMP_DIR, "alfacredit_storage")
    DB_PATH = os.path.join(STORAGE_DIR, "alfacredit_novedades.db")
else:
    STORAGE_DIR = os.path.join(BASE_DIR, "storage")
    DB_PATH = os.path.join(STORAGE_DIR, "database", "alfacredit_novedades.db")

def hash_password(password: str) -> str:
    """Genera un hash seguro para contraseñas usando SHA-256 con salt fija"""
    salt = "alfacredit_salt_2026_!"
    return hashlib.sha256((salt + password).encode('utf-8')).hexdigest()

def get_db_connection():
    """Obtiene una conexión a la base de datos con row_factory para diccionarios y journal_mode según entorno"""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    
    db_exists = os.path.exists(DB_PATH)
    if IS_SERVERLESS and not db_exists:
        seed_db = os.path.join(BASE_DIR, "storage", "database", "alfacredit_novedades.db")
        if os.path.exists(seed_db):
            try:
                import shutil
                shutil.copy2(seed_db, DB_PATH)
                db_exists = True
            except Exception as e:
                print(f"Aviso: no se pudo copiar seed_db a /tmp: {e}")

    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    if IS_SERVERLESS:
        conn.execute("PRAGMA journal_mode=DELETE;")
    else:
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    """Crea todas las tablas si no existen e inserta datos iniciales"""
    conn = get_db_connection()
    c = conn.cursor()

    # 1. Sucursales
    c.execute('''
    CREATE TABLE IF NOT EXISTS sucursales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT UNIQUE NOT NULL,
        email_gerente TEXT UNIQUE NOT NULL,
        telefono TEXT,
        activa INTEGER DEFAULT 1
    )
    ''')

    # 2. Usuarios
    c.execute('''
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        nombre TEXT NOT NULL,
        rol TEXT NOT NULL, -- 'admin', 'gerente', 'brandon', 'dania'
        sucursal_id INTEGER REFERENCES sucursales(id),
        activo INTEGER DEFAULT 1,
        ultimo_acceso TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 3. Colaboradores Maestros
    c.execute('''
    CREATE TABLE IF NOT EXISTS colaboradores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sucursal_id INTEGER REFERENCES sucursales(id),
        nombre TEXT NOT NULL,
        cargo TEXT NOT NULL,
        combustible_base REAL DEFAULT 0.0,
        estado_default TEXT DEFAULT 'ACTIVO',
        talla TEXT DEFAULT '',
        req_default TEXT DEFAULT '',
        obs_default TEXT DEFAULT '',
        activo INTEGER DEFAULT 1
    )
    ''')

    # 4. Ciclos Semanales (Periodos de corte de sábado)
    c.execute('''
    CREATE TABLE IF NOT EXISTS ciclos_semanales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha_sabado TEXT UNIQUE NOT NULL,
        fecha_inicio_pas TEXT NOT NULL,
        fecha_fin_pas TEXT NOT NULL,
        fecha_inicio_fut TEXT NOT NULL,
        fecha_fin_fut TEXT NOT NULL,
        estado_general TEXT DEFAULT 'FASE_1_GERENTES', -- 'FASE_1_GERENTES', 'FASE_2_BRANDON', 'FASE_3_DANIA', 'FASE_4_COMPLETADO'
        total_combustible_calculado REAL DEFAULT 0.0,
        total_combustible_aprobado REAL DEFAULT 0.0,
        notas TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        cerrado_en TEXT
    )
    ''')

    # 5. Reportes de Sucursales por Ciclo
    c.execute('''
    CREATE TABLE IF NOT EXISTS reportes_sucursales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ciclo_id INTEGER REFERENCES ciclos_semanales(id),
        sucursal_id INTEGER REFERENCES sucursales(id),
        usuario_id INTEGER REFERENCES usuarios(id),
        estado TEXT DEFAULT 'BORRADOR', -- 'PENDIENTE', 'BORRADOR', 'ENVIADO', 'APROBADO'
        enviado_en TEXT,
        total_empleados INTEGER DEFAULT 0,
        suma_base REAL DEFAULT 0.0,
        suma_ajustes REAL DEFAULT 0.0,
        suma_neto REAL DEFAULT 0.0,
        notas TEXT,
        actualizado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(ciclo_id, sucursal_id)
    )
    ''')

    # 6. Detalle de Novedades (Filas individuales de empleados en cada reporte)
    c.execute('''
    CREATE TABLE IF NOT EXISTS detalle_novedades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reporte_id INTEGER REFERENCES reportes_sucursales(id) ON DELETE CASCADE,
        colaborador_id INTEGER,
        nombre TEXT NOT NULL,
        cargo TEXT NOT NULL,
        estado TEXT DEFAULT 'ACTIVO',
        combustible_base REAL DEFAULT 0.0,
        factor_total REAL DEFAULT 0.0,
        deduccion_ajuste REAL DEFAULT 0.0,
        total_neto REAL DEFAULT 0.0,
        dias_pas_json TEXT NOT NULL, -- JSON array de 6 strings
        dias_fut_json TEXT NOT NULL, -- JSON array de 6 strings
        talla TEXT DEFAULT '',
        req TEXT DEFAULT '',
        obs TEXT DEFAULT ''
    )
    ''')

    # 7. Eventos de Brandon (Envío de Solicitud de Combustible por correo o web)
    c.execute('''
    CREATE TABLE IF NOT EXISTS eventos_brandon (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ciclo_id INTEGER REFERENCES ciclos_semanales(id),
        usuario_id INTEGER REFERENCES usuarios(id),
        correo_destinatario TEXT,
        asunto TEXT,
        cuerpo TEXT,
        archivo_adjunto TEXT,
        archivos_adjuntos_json TEXT DEFAULT '[]',
        fecha_envio TEXT,
        metodo TEXT DEFAULT 'DETECCION_CORREO', -- 'DETECCION_CORREO', 'MANUAL_WEB'
        estado_envio TEXT DEFAULT 'ENVIADO',
        registrado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Migración segura: agregar columna si no existe
    try:
        c.execute("ALTER TABLE eventos_brandon ADD COLUMN archivos_adjuntos_json TEXT DEFAULT '[]'")
    except Exception:
        pass

    # 8. Eventos de Dania (Depósito bancario y comprobante)
    c.execute('''
    CREATE TABLE IF NOT EXISTS eventos_dania (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ciclo_id INTEGER REFERENCES ciclos_semanales(id),
        usuario_id INTEGER REFERENCES usuarios(id),
        monto_depositado REAL DEFAULT 0.0,
        numero_referencia TEXT,
        comprobante_archivo TEXT,
        fecha_deposito TEXT,
        remitente_correo TEXT,
        asunto_correo TEXT,
        metodo TEXT DEFAULT 'DETECCION_CORREO', -- 'DETECCION_CORREO', 'MANUAL_WEB'
        notas TEXT,
        registrado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # 9. Log de Monitoreo de Correo Electrónico
    c.execute('''
    CREATE TABLE IF NOT EXISTS monitoreo_correos_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo TEXT NOT NULL, -- 'ENVIADO_BRANDON', 'RECIBIDO_DANIA', 'ERROR', 'INFO'
        remitente TEXT,
        destinatario TEXT,
        asunto TEXT,
        fecha_correo TEXT,
        procesado_en TEXT DEFAULT CURRENT_TIMESTAMP,
        detalles_json TEXT
    )
    ''')

    # 10. Auditoría Total Inmutable
    c.execute('''
    CREATE TABLE IF NOT EXISTS auditoria_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
        usuario_email TEXT NOT NULL,
        usuario_nombre TEXT NOT NULL,
        rol TEXT NOT NULL,
        sucursal_nombre TEXT,
        accion TEXT NOT NULL,
        detalle TEXT NOT NULL,
        ip_address TEXT
    )
    ''')

    # 11. Respaldos del Sistema
    c.execute('''
    CREATE TABLE IF NOT EXISTS backups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
        nombre_archivo TEXT NOT NULL,
        ruta_archivo TEXT NOT NULL,
        tamano_bytes INTEGER,
        generado_por TEXT NOT NULL,
        tipo TEXT DEFAULT 'MANUAL' -- 'AUTOMATICO', 'MANUAL'
    )
    ''')

    # 12. Tokens de Recuperación de Contraseña
    c.execute('''
    CREATE TABLE IF NOT EXISTS recuperacion_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario_id INTEGER REFERENCES usuarios(id),
        email TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        expira_en TEXT NOT NULL,
        usado INTEGER DEFAULT 0,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    conn.commit()

    # --- DATOS SEMILLA CON DOMINIO OFICIAL @ALFACREDITSA.COM ---
    p_hash = hash_password("1234")

    # Sucursales de AlfaCredit S.A.
    sucursales_data = [
        ("Estelí", "gerente.esteli@alfacreditsa.com", "2713-0001"),
        ("Masaya", "gerente.masaya@alfacreditsa.com", "2522-0002"),
        ("Managua Bolonia", "gerente.bolonia@alfacreditsa.com", "2266-0003"),
        ("Managua Norte", "gerente.managuanorte@alfacreditsa.com", "2255-0004"),
        ("Sébaco", "gerente.sebaco@alfacreditsa.com", "2775-0005"),
        ("Jinotepe", "gerente.jinotepe@alfacreditsa.com", "2532-0006"),
        ("Juigalpa", "gerente.juigalpa@alfacreditsa.com", "2512-0007"),
        ("Rivas", "gerente.rivas@alfacreditsa.com", "2563-0008"),
        ("León", "gerente.leon@alfacreditsa.com", "2311-0009")
    ]

    for nom, email, tel in sucursales_data:
        c.execute('''
        INSERT INTO sucursales (nombre, email_gerente, telefono)
        VALUES (?, ?, ?)
        ON CONFLICT(nombre) DO UPDATE SET email_gerente = excluded.email_gerente, telefono = excluded.telefono
        ''', (nom, email.lower(), tel))
        c.execute("UPDATE sucursales SET email_gerente = ? WHERE nombre = ?", (email.lower(), nom))

    conn.commit()

    # Mapeo de IDs de Sucursales
    c.execute("SELECT id, nombre, email_gerente FROM sucursales")
    suc_map = {row["nombre"]: row["id"] for row in c.fetchall()}

    usuarios_data = [
        # Administrador
        ("enrique@alfacreditsa.com", p_hash, "Enrique (Administrador)", "admin", None),
        # Brandon (Coordinador Combustible y Logística)
        ("adquisicionesylogistica@alfacreditsa.com", p_hash, "Brandon (Coordinador Combustible)", "brandon", None),
        # Yeser (Jefe de Crédito / Supervisión de Gerentes)
        ("yeser@alfacreditsa.com", p_hash, "Yeser (Jefe de Crédito)", "jefe_credito", None),
        # Gerentes de Sucursales
        ("gerente.esteli@alfacreditsa.com", p_hash, "Gerente Estelí", "gerente", suc_map.get("Estelí")),
        ("gerente.masaya@alfacreditsa.com", p_hash, "Gerente Masaya", "gerente", suc_map.get("Masaya")),
        ("gerente.bolonia@alfacreditsa.com", p_hash, "Gerente Managua Bolonia", "gerente", suc_map.get("Managua Bolonia")),
        ("gerente.managuanorte@alfacreditsa.com", p_hash, "Gerente Managua Norte", "gerente", suc_map.get("Managua Norte")),
        ("gerente.sebaco@alfacreditsa.com", p_hash, "Gerente Sébaco", "gerente", suc_map.get("Sébaco")),
        ("gerente.jinotepe@alfacreditsa.com", p_hash, "Gerente Jinotepe", "gerente", suc_map.get("Jinotepe")),
        ("gerente.juigalpa@alfacreditsa.com", p_hash, "Gerente Juigalpa", "gerente", suc_map.get("Juigalpa")),
        ("gerente.rivas@alfacreditsa.com", p_hash, "Gerente Rivas", "gerente", suc_map.get("Rivas")),
        ("gerente.leon@alfacreditsa.com", p_hash, "Gerente León", "gerente", suc_map.get("León"))
    ]

    c.execute("DELETE FROM usuarios WHERE rol = 'dania'")
    conn.commit()

    # Migración de usuarios previos a correos @alfacreditsa.com
    email_migration_map = {
        "alfacreditenrique@gmail.com": "enrique@alfacreditsa.com",
        "dania.tesoreria@alfacredit.com": "dania.tesoreria@alfacreditsa.com",
        "gerenteesteli7@gmail.com": "gerente.esteli@alfacreditsa.com",
        "gerentemasaya25@gmail.com": "gerente.masaya@alfacreditsa.com",
        "gerentemanaguabolonia@gmail.com": "gerente.bolonia@alfacreditsa.com",
        "gerentemanaguanorte@gmail.com": "gerente.managuanorte@alfacreditsa.com",
        "gerentesebaco@gmail.com": "gerente.sebaco@alfacreditsa.com",
        "gerentejinotepe@gmail.com": "gerente.jinotepe@alfacreditsa.com",
        "gerentejuigalpa@gmail.com": "gerente.juigalpa@alfacreditsa.com",
        "gerenterivas1@gmail.com": "gerente.rivas@alfacreditsa.com",
        "gerenteleon8@gmail.com": "gerente.leon@alfacreditsa.com"
    }

    for old_em, new_em in email_migration_map.items():
        c.execute("UPDATE usuarios SET email = ? WHERE LOWER(email) = ?", (new_em, old_em.lower()))

    # Insertar usuarios nuevos que falten
    for email, pw, nom, rol, suc_id in usuarios_data:
        c.execute('''
        INSERT OR IGNORE INTO usuarios (email, password_hash, nombre, rol, sucursal_id)
        VALUES (?, ?, ?, ?, ?)
        ''', (email.strip().lower(), pw, nom, rol, suc_id))

    conn.commit()

    # Colaboradores plantilla inicial por sucursal si está vacía
    c.execute("SELECT COUNT(*) as cnt FROM colaboradores")
    if c.fetchone()["cnt"] == 0:
        cargos_base = [
            ("GERENTE SUCURSAL", 1200.0),
            ("SUPERVISOR", 900.0),
            ("OFICIAL DE CREDITO 1", 750.0),
            ("OFICIAL DE CREDITO 2", 750.0),
            ("OFICIAL DE DESEMBOLSO", 600.0),
            ("PROMOTOR INTERNO", 500.0),
            ("RESP. OPERACIONES", 600.0),
            ("CONSERJE", 300.0)
        ]
        for email_g, suc_id in suc_map.items():
            suc_nom = [s[0] for s in sucursales_data if s[1] == email_g][0]
            for cargo, comb in cargos_base:
                nom_ej = f"Colaborador {cargo.title()} - {suc_nom}"
                c.execute('''
                INSERT INTO colaboradores (sucursal_id, nombre, cargo, combustible_base, estado_default)
                VALUES (?, ?, ?, ?, 'ACTIVO')
                ''', (suc_id, nom_ej, cargo.split(" ")[0] + (" " + cargo.split(" ")[1] if len(cargo.split(" ")) > 1 and not cargo.split(" ")[1].isdigit() else ""), comb))

        conn.commit()

    # Asegurar ciclo semanal activo (próximo o actual sábado)
    asegurar_ciclo_actual(conn)
    conn.close()

def calcular_sabado_actual() -> datetime.date:
    """Calcula la fecha del sábado correspondiente para el ciclo semanal activo"""
    hoy = datetime.now().date()
    # Si hoy es lunes (0), el sábado de corte fue hace 2 días o el próximo sábado (+5 días)
    # En la lógica de AlfaCredit: el sábado de corte define la semana
    dias_para_sabado = (5 - hoy.weekday()) % 7
    sabado = hoy + timedelta(days=dias_para_sabado)
    return sabado

def asegurar_ciclo_actual(conn=None):
    """Garantiza que exista un ciclo semanal activo con sus reportes creados para cada sucursal"""
    close_at_end = False
    if conn is None:
        conn = get_db_connection()
        close_at_end = True

    c = conn.cursor()
    fsab = calcular_sabado_actual()
    fsab_str = fsab.strftime("%Y-%m-%d")

    lunes_pas = fsab - timedelta(days=5)
    sab_pas = fsab
    lunes_fut = fsab + timedelta(days=2)
    sab_fut = fsab + timedelta(days=7)

    c.execute("SELECT id FROM ciclos_semanales WHERE fecha_sabado = ?", (fsab_str,))
    ciclo = c.fetchone()

    if not ciclo:
        c.execute('''
        INSERT INTO ciclos_semanales (fecha_sabado, fecha_inicio_pas, fecha_fin_pas, fecha_inicio_fut, fecha_fin_fut, estado_general)
        VALUES (?, ?, ?, ?, ?, 'FASE_1_GERENTES')
        ''', (fsab_str, lunes_pas.strftime("%Y-%m-%d"), sab_pas.strftime("%Y-%m-%d"), lunes_fut.strftime("%Y-%m-%d"), sab_fut.strftime("%Y-%m-%d")))
        ciclo_id = c.lastrowid
    else:
        ciclo_id = ciclo["id"]

    # Crear reportes vacíos para cada sucursal en este ciclo si aún no existen
    c.execute("SELECT id, email_gerente FROM sucursales WHERE activa = 1")
    sucursales = c.fetchall()

    for suc in sucursales:
        c.execute('''
        SELECT id FROM reportes_sucursales 
        WHERE ciclo_id = ? AND sucursal_id = ?
        ''', (ciclo_id, suc["id"]))
        rep = c.fetchone()
        if not rep:
            # Obtener usuario_id del gerente
            c.execute("SELECT id FROM usuarios WHERE sucursal_id = ? LIMIT 1", (suc["id"],))
            u = c.fetchone()
            u_id = u["id"] if u else None

            c.execute('''
            INSERT INTO reportes_sucursales (ciclo_id, sucursal_id, usuario_id, estado)
            VALUES (?, ?, ?, 'PENDIENTE')
            ''', (ciclo_id, suc["id"], u_id))
            rep_id = c.lastrowid

            # Cargar colaboradores de la sucursal al detalle
            c.execute("SELECT * FROM colaboradores WHERE sucursal_id = ? AND activo = 1", (suc["id"],))
            colabs = c.fetchall()
            for col in colabs:
                dias_vacio = json.dumps([""] * 6)
                c.execute('''
                INSERT INTO detalle_novedades (
                    reporte_id, colaborador_id, nombre, cargo, estado, 
                    combustible_base, factor_total, deduccion_ajuste, total_neto, 
                    dias_pas_json, dias_fut_json, talla, req, obs
                ) VALUES (?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, ?, ?, ?, ?, ?)
                ''', (rep_id, col["id"], col["nombre"], col["cargo"], col["estado_default"],
                      col["combustible_base"], col["combustible_base"], dias_vacio, dias_vacio,
                      col["talla"], col["req_default"], col["obs_default"]))

    conn.commit()
    if close_at_end:
        conn.close()
    return ciclo_id

if __name__ == "__main__":
    init_db()
    print("✅ Base de datos inicializada correctamente.")
