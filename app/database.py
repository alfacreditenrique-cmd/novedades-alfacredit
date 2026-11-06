import sqlite3
import os
import json
import hashlib
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Detección de entorno Serverless efímero (Vercel, AWS Lambda, o variable explícita)
def get_is_serverless() -> bool:
    return bool(
        os.environ.get("VERCEL") 
        or os.environ.get("AWS_LAMBDA_FUNCTION_NAME") 
        or os.environ.get("USE_TMP_DB")
    )

def get_storage_dir() -> str:
    if get_is_serverless():
        tmp_dir = os.environ.get("TMPDIR", "/tmp")
        return os.path.join(tmp_dir, "alfacredit_storage")
    return os.path.join(BASE_DIR, "storage")

def get_db_path() -> str:
    if get_is_serverless():
        return os.path.join(get_storage_dir(), "alfacredit_novedades.db")
    return os.path.join(get_storage_dir(), "database", "alfacredit_novedades.db")

IS_SERVERLESS = get_is_serverless()
STORAGE_DIR = get_storage_dir()
DB_PATH = get_db_path()

def hash_password(password: str) -> str:
    """Genera un hash seguro para contraseñas usando SHA-256 con salt fija"""
    salt = "alfacredit_salt_2026_!"
    return hashlib.sha256((salt + password).encode('utf-8')).hexdigest()

class LibsqlRow(dict):
    """
    Wrapper compatible con sqlite3.Row:
    Permite acceso por clave r['col'], por índice r[0], dict(r), r.get(), r.keys().
    Soporta búsqueda insensible a mayúsculas/minúsculas.
    """
    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._lower_map = {str(c).lower(): v for c, v in zip(columns, values)}

    def __getitem__(self, item):
        if isinstance(item, int):
            return self._values[item]
        if item in self:
            return super().__getitem__(item)
        item_lower = str(item).lower()
        if item_lower in self._lower_map:
            return self._lower_map[item_lower]
        raise KeyError(item)

    def get(self, item, default=None):
        try:
            return self[item]
        except (KeyError, IndexError):
            return default

    def keys(self):
        return self._columns


class LibsqlCursor:
    def __init__(self, client):
        self._client = client
        self._rows = []
        self._columns = ()
        self._idx = 0
        self.lastrowid = None
        self.rowcount = 0

    def __iter__(self):
        return self

    def __next__(self):
        r = self.fetchone()
        if r is None:
            raise StopIteration
        return r

    def execute(self, sql, params=None):
        clean_sql = sql.strip()
        # Manejar PRAGMAs locales que pueden diferir en la nube
        if clean_sql.upper().startswith("PRAGMA JOURNAL_MODE"):
            self._rows = []
            self._columns = ()
            self.rowcount = 0
            self.lastrowid = 0
            return self
        if clean_sql.upper().startswith("PRAGMA"):
            try:
                res = self._client.execute(clean_sql, [])
                self._columns = tuple(res.columns) if hasattr(res, 'columns') and res.columns else ()
                raw_rows = res.rows if hasattr(res, 'rows') else []
                self._rows = [LibsqlRow(self._columns, r) for r in raw_rows]
            except Exception:
                self._rows = []
                self._columns = ()
            self._idx = 0
            self.lastrowid = 0
            self.rowcount = 0
            return self

        args = list(params) if params is not None else []
        res = self._client.execute(clean_sql, args)
        self._columns = tuple(res.columns) if hasattr(res, 'columns') and res.columns else ()
        raw_rows = res.rows if hasattr(res, 'rows') else []
        self._rows = [LibsqlRow(self._columns, r) for r in raw_rows]
        self._idx = 0
        self.lastrowid = getattr(res, 'last_insert_rowid', None)
        self.rowcount = getattr(res, 'rows_affected', len(self._rows))
        return self

    def fetchone(self):
        if self._idx < len(self._rows):
            r = self._rows[self._idx]
            self._idx += 1
            return r
        return None

    def fetchall(self):
        remaining = self._rows[self._idx:]
        self._idx = len(self._rows)
        return remaining

    def fetchmany(self, size=None):
        if size is None:
            size = 1
        end = min(self._idx + size, len(self._rows))
        subset = self._rows[self._idx:end]
        self._idx = end
        return subset

    def close(self):
        pass


class LibsqlConnection:
    def __init__(self, client):
        self._client = client
        self.row_factory = None

    def cursor(self):
        return LibsqlCursor(self._client)

    def execute(self, sql, params=None):
        cur = self.cursor()
        return cur.execute(sql, params)

    def commit(self):
        # Libsql sobre HTTP auto-confirma sentencias
        pass

    def rollback(self):
        pass

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def is_using_turso() -> bool:
    """Verifica si la base de datos configurada es Turso libSQL en la nube"""
    return bool(os.environ.get("TURSO_DATABASE_URL", "").strip())


def sync_cloud_pull(force: bool = False) -> bool:
    """Sincroniza la base de datos local desde el Cloud Store centralizado"""
    if is_using_turso():
        return False
    try:
        from app.cloud_sync import is_cloud_sync_enabled, pull_latest_db_from_cloud
        if is_cloud_sync_enabled():
            return pull_latest_db_from_cloud(get_db_path(), force=force)
    except Exception as e:
        print(f"[CLOUDSYNC PULL NOTICE]: {e}")
    return False


def sync_cloud_push(user_email: str = "sistema", reapply_fn = None) -> bool:
    """Sube la base de datos local al Cloud Store centralizado"""
    if is_using_turso():
        return False
    try:
        from app.cloud_sync import is_cloud_sync_enabled, push_db_to_cloud
        if is_cloud_sync_enabled():
            return push_db_to_cloud(get_db_path(), user_email=user_email, reapply_fn=reapply_fn)
    except Exception as e:
        print(f"[CLOUDSYNC PUSH NOTICE]: {e}")
    return False


def get_db_connection():
    """Obtiene una conexión a la base de datos (Turso libSQL en la nube o SQLite con CloudSync)"""
    turso_url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    turso_token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()

    if turso_url:
        try:
            import libsql_client
            client = libsql_client.create_client_sync(url=turso_url, auth_token=turso_token or None)
            return LibsqlConnection(client)
        except Exception as e:
            print(f"Aviso: no se pudo conectar a Turso ({turso_url}): {e}. Usando fallback local.")

    db_path = get_db_path()
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    
    db_exists = os.path.exists(db_path)
    
    # En entorno Serverless efímero (Vercel), sincronizar de la nube primero
    try:
        from app.cloud_sync import is_cloud_sync_enabled, pull_latest_db_from_cloud
        if is_cloud_sync_enabled():
            pulled = pull_latest_db_from_cloud(db_path, force=not db_exists)
            if pulled and os.path.exists(db_path):
                db_exists = True
    except Exception as e:
        print(f"[CLOUDSYNC INIT NOTICE]: {e}")

    if not db_exists:
        seed_db = os.path.join(BASE_DIR, "storage", "database", "alfacredit_novedades.db")
        if os.path.exists(seed_db):
            try:
                import shutil
                shutil.copy2(seed_db, db_path)
                db_exists = True
            except Exception as e:
                print(f"Aviso: no se pudo copiar seed_db: {e}")

    conn = sqlite3.connect(db_path, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=DELETE;")
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
        telefono TEXT,
        activo INTEGER DEFAULT 1,
        ultimo_acceso TEXT,
        creado_en TEXT DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Migración segura: agregar telefono a usuarios si no existe
    try:
        c.execute("PRAGMA table_info(usuarios)")
        _cols = [r[1] for r in c.fetchall()]
        if "telefono" not in _cols:
            c.execute("ALTER TABLE usuarios ADD COLUMN telefono TEXT;")
    except Exception:
        try:
            c.execute("ALTER TABLE usuarios ADD COLUMN telefono TEXT;")
        except Exception:
            pass


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

    # --- DATOS SEMILLA Y MIGRACIONES ---
    p_hash = hash_password("1234")

    # --- MIGRACIÓN: UNIFICACIÓN DE MANAGUA BOLONIA Y MANAGUA NORTE EN UNA SOLA SUCURSAL 'MANAGUA' ---
    c.execute("SELECT id, nombre, email_gerente FROM sucursales WHERE nombre IN ('Managua Bolonia', 'Managua Norte', 'Managua')")
    m_rows = {row["nombre"]: row["id"] for row in c.fetchall()}

    if "Managua Bolonia" in m_rows or "Managua Norte" in m_rows:
        # Si 'Managua' aún no existe, renombramos 'Managua Bolonia' o 'Managua Norte' a 'Managua'
        if "Managua" not in m_rows:
            if "Managua Bolonia" in m_rows:
                target_id = m_rows["Managua Bolonia"]
                c.execute("UPDATE sucursales SET nombre = 'Managua', email_gerente = 'gerente.managua@alfacreditsa.com', telefono = '2266-0003' WHERE id = ?", (target_id,))
                m_rows["Managua"] = target_id
                del m_rows["Managua Bolonia"]
            else:
                target_id = m_rows["Managua Norte"]
                c.execute("UPDATE sucursales SET nombre = 'Managua', email_gerente = 'gerente.managua@alfacreditsa.com', telefono = '2266-0003' WHERE id = ?", (target_id,))
                m_rows["Managua"] = target_id
                del m_rows["Managua Norte"]

        managua_id = m_rows["Managua"]

        # Obtener o crear usuario Managua
        c.execute("SELECT id FROM usuarios WHERE email = 'gerente.managua@alfacreditsa.com'")
        u_managua = c.fetchone()
        if not u_managua:
            c.execute("SELECT id FROM usuarios WHERE email = 'gerente.bolonia@alfacreditsa.com'")
            u_bol = c.fetchone()
            if u_bol:
                c.execute("UPDATE usuarios SET email = 'gerente.managua@alfacreditsa.com', nombre = 'Gerente Managua', sucursal_id = ? WHERE id = ?", (managua_id, u_bol["id"]))
                managua_uid = u_bol["id"]
            else:
                c.execute("SELECT id FROM usuarios WHERE email = 'gerente.managuanorte@alfacreditsa.com'")
                u_nor = c.fetchone()
                if u_nor:
                    c.execute("UPDATE usuarios SET email = 'gerente.managua@alfacreditsa.com', nombre = 'Gerente Managua', sucursal_id = ? WHERE id = ?", (managua_id, u_nor["id"]))
                    managua_uid = u_nor["id"]
                else:
                    c.execute("INSERT OR IGNORE INTO usuarios (email, password_hash, nombre, rol, sucursal_id) VALUES ('gerente.managua@alfacreditsa.com', ?, 'Gerente Managua', 'gerente', ?)", (p_hash, managua_id))
                    managua_uid = c.lastrowid
        else:
            managua_uid = u_managua["id"]
            c.execute("UPDATE usuarios SET sucursal_id = ? WHERE id = ?", (managua_id, managua_uid))

        # Migrar dependencias de las sucursales viejas a Managua
        for old_nom, old_id in list(m_rows.items()):
            if old_nom != "Managua":
                # 1. Colaboradores
                c.execute("UPDATE colaboradores SET sucursal_id = ? WHERE sucursal_id = ?", (managua_id, old_id))
                # 2. Reportes por ciclo: evitar colisión con UNIQUE(ciclo_id, sucursal_id)
                c.execute("SELECT ciclo_id, id FROM reportes_sucursales WHERE sucursal_id = ?", (old_id,))
                for rep_old in c.fetchall():
                    c.execute("SELECT id FROM reportes_sucursales WHERE ciclo_id = ? AND sucursal_id = ?", (rep_old["ciclo_id"], managua_id))
                    existing_rep = c.fetchone()
                    if existing_rep:
                        c.execute("UPDATE detalle_novedades SET reporte_id = ? WHERE reporte_id = ?", (existing_rep["id"], rep_old["id"]))
                        c.execute("DELETE FROM reportes_sucursales WHERE id = ?", (rep_old["id"],))
                    else:
                        c.execute("UPDATE reportes_sucursales SET sucursal_id = ?, usuario_id = ? WHERE id = ?", (managua_id, managua_uid, rep_old["id"]))
                # 3. Reasignar cualquier referencia de usuario viejo a managua_uid
                c.execute("SELECT id FROM usuarios WHERE sucursal_id = ? OR email IN ('gerente.bolonia@alfacreditsa.com', 'gerente.managuanorte@alfacreditsa.com')", (old_id,))
                for old_u in c.fetchall():
                    if old_u["id"] != managua_uid:
                        c.execute("UPDATE reportes_sucursales SET usuario_id = ? WHERE usuario_id = ?", (managua_uid, old_u["id"]))
                        c.execute("UPDATE recuperacion_tokens SET usuario_id = ? WHERE usuario_id = ?", (managua_uid, old_u["id"]))
                        c.execute("UPDATE eventos_brandon SET usuario_id = ? WHERE usuario_id = ?", (managua_uid, old_u["id"]))
                        c.execute("UPDATE eventos_dania SET usuario_id = ? WHERE usuario_id = ?", (managua_uid, old_u["id"]))
                        c.execute("DELETE FROM usuarios WHERE id = ?", (old_u["id"],))

                # 4. Eliminar la sucursal obsoleta
                c.execute("DELETE FROM sucursales WHERE id = ?", (old_id,))

        conn.commit()

    # --- DATOS SEMILLA CON DOMINIO OFICIAL @ALFACREDITSA.COM (8 SUCURSALES) ---
    # Sucursales de AlfaCredit S.A. (Total 8 Sucursales Oficiales)
    sucursales_data = [
        ("Estelí", "gerente.esteli@alfacreditsa.com", "2713-0001"),
        ("Masaya", "gerente.masaya@alfacreditsa.com", "2522-0002"),
        ("Managua", "gerente.managua@alfacreditsa.com", "2266-0003"),
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
        # Gerentes de Sucursales (8 Gerentes)
        ("gerente.esteli@alfacreditsa.com", p_hash, "Gerente Estelí", "gerente", suc_map.get("Estelí")),
        ("gerente.masaya@alfacreditsa.com", p_hash, "Gerente Masaya", "gerente", suc_map.get("Masaya")),
        ("gerente.managua@alfacreditsa.com", p_hash, "Gerente Managua", "gerente", suc_map.get("Managua")),
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
        "gerentemanagua@gmail.com": "gerente.managua@alfacreditsa.com",
        "gerentemanaguabolonia@gmail.com": "gerente.managua@alfacreditsa.com",
        "gerentemanaguanorte@gmail.com": "gerente.managua@alfacreditsa.com",
        "gerente.bolonia@alfacreditsa.com": "gerente.managua@alfacreditsa.com",
        "gerente.managuanorte@alfacreditsa.com": "gerente.managua@alfacreditsa.com",
        "gerentesebaco@gmail.com": "gerente.sebaco@alfacreditsa.com",
        "gerentejinotepe@gmail.com": "gerente.jinotepe@alfacreditsa.com",
        "gerentejuigalpa@gmail.com": "gerente.juigalpa@alfacreditsa.com",
        "gerenterivas1@gmail.com": "gerente.rivas@alfacreditsa.com",
        "gerenteleon8@gmail.com": "gerente.leon@alfacreditsa.com"
    }

    for old_em, new_em in email_migration_map.items():
        c.execute("SELECT id FROM usuarios WHERE LOWER(email) = ?", (old_em.lower(),))
        old_u = c.fetchone()
        if old_u:
            c.execute("SELECT id FROM usuarios WHERE LOWER(email) = ?", (new_em.lower(),))
            new_u = c.fetchone()
            if new_u:
                if old_u["id"] != new_u["id"]:
                    c.execute("UPDATE reportes_sucursales SET usuario_id = ? WHERE usuario_id = ?", (new_u["id"], old_u["id"]))
                    c.execute("UPDATE recuperacion_tokens SET usuario_id = ? WHERE usuario_id = ?", (new_u["id"], old_u["id"]))
                    c.execute("UPDATE eventos_brandon SET usuario_id = ? WHERE usuario_id = ?", (new_u["id"], old_u["id"]))
                    c.execute("UPDATE eventos_dania SET usuario_id = ? WHERE usuario_id = ?", (new_u["id"], old_u["id"]))
                    c.execute("DELETE FROM usuarios WHERE id = ?", (old_u["id"],))
            else:
                c.execute("UPDATE usuarios SET email = ? WHERE id = ?", (new_em.lower(), old_u["id"]))

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
        for suc_nom, email_g, _ in sucursales_data:
            suc_id = suc_map.get(suc_nom)
            if not suc_id:
                continue
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
    # Si ya existe al menos un ciclo semanal, tomar el último ciclo activo
    c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    ciclo = c.fetchone()

    if not ciclo:
        fsab = calcular_sabado_actual()
        fsab_str = fsab.strftime("%Y-%m-%d")

        lunes_pas = fsab - timedelta(days=5)
        sab_pas = fsab
        lunes_fut = fsab + timedelta(days=2)
        sab_fut = fsab + timedelta(days=7)

        c.execute('''
        INSERT INTO ciclos_semanales (fecha_sabado, fecha_inicio_pas, fecha_fin_pas, fecha_inicio_fut, fecha_fin_fut, estado_general)
        VALUES (?, ?, ?, ?, ?, 'FASE_1_GERENTES')
        ''', (fsab_str, lunes_pas.strftime("%Y-%m-%d"), sab_pas.strftime("%Y-%m-%d"), lunes_fut.strftime("%Y-%m-%d"), sab_fut.strftime("%Y-%m-%d")))
        ciclo_id = c.lastrowid
    else:
        ciclo_id = ciclo["id"]

    # Crear reportes vacíos para cada sucursal en este ciclo si aún no existen
    c.execute("SELECT id, email_gerente FROM sucursales WHERE activa = 1 ORDER BY id ASC")
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

            # Cargar colaboradores de la sucursal al detalle
            c.execute("SELECT * FROM colaboradores WHERE sucursal_id = ? AND activo = 1 ORDER BY id ASC", (suc["id"],))
            colabs = [dict(r) for r in c.fetchall()]
            suma_base = sum(float(col.get("combustible_base", 0.0) or 0.0) for col in colabs)
            
            c.execute('''
            INSERT INTO reportes_sucursales (ciclo_id, sucursal_id, usuario_id, estado, total_empleados, suma_base, suma_ajustes, suma_neto)
            VALUES (?, ?, ?, 'PENDIENTE', ?, ?, 0.0, ?)
            ''', (ciclo_id, suc["id"], u_id, len(colabs), round(suma_base, 2), round(suma_base, 2)))
            rep_id = c.lastrowid

            for col in colabs:
                dias_vacio = json.dumps([""] * 6)
                c_base = float(col.get("combustible_base", 0.0) or 0.0)
                c.execute('''
                INSERT INTO detalle_novedades (
                    reporte_id, colaborador_id, nombre, cargo, estado, 
                    combustible_base, factor_total, deduccion_ajuste, total_neto, 
                    dias_pas_json, dias_fut_json, talla, req, obs
                ) VALUES (?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, ?, ?, ?, ?, ?)
                ''', (rep_id, col["id"], col["nombre"], col["cargo"], col["estado_default"],
                      c_base, c_base, dias_vacio, dias_vacio,
                      col.get("talla", ""), col.get("req_default", ""), col.get("obs_default", "")))

    conn.commit()
    if close_at_end:
        conn.close()
    return ciclo_id


def realizar_corte_semanal_logica(usuario_dict: dict, conn=None) -> dict:
    """
    Realiza el corte semanal recurrente:
    1. Valida que el ciclo actual esté en Fase 4 (Fondos Despachados / Depósito Dania).
    2. Archiva históricamente el ciclo actual (con sus reportes, adjuntos y registros intactos).
    3. Avanza 7 días a la fecha del siguiente sábado e inserta el nuevo ciclo en 'FASE_1_GERENTES'.
    4. Para cada una de las 8 sucursales:
       - Preserva TODOS los colaboradores trabajados en la semana previa (nombre, cargo, talla, req, obs, combustible_base).
       - Limpia ÚNICAMENTE las novedades (días pasados y futuros en blanco ["", "", "", "", "", ""]).
       - Reinicia los ajustes a 0.0, el total neto a la asignación base y el estado del reporte a 'PENDIENTE'.
    5. Devuelve los datos del nuevo ciclo creado.
    """
    close_at_end = False
    if conn is None:
        conn = get_db_connection()
        close_at_end = True

    c = conn.cursor()

    # 1. Obtener el ciclo actual
    c.execute("SELECT * FROM ciclos_semanales ORDER BY id DESC LIMIT 1")
    ciclo_actual = c.fetchone()
    if not ciclo_actual:
        if close_at_end: conn.close()
        raise ValueError("No se encontró ningún ciclo activo en la base de datos.")

    ciclo_dict = dict(ciclo_actual)
    ciclo_id = ciclo_dict["id"]

    # 2. Verificar que esté en Fase 4 (evento_dania o estado_general == 'FASE_4_COMPLETADO')
    c.execute("SELECT id FROM eventos_dania WHERE ciclo_id = ?", (ciclo_id,))
    ev_dania = c.fetchone()
    if not ev_dania and ciclo_dict.get("estado_general") != "FASE_4_COMPLETADO":
        if close_at_end: conn.close()
        raise ValueError("No se puede realizar el corte semanal hasta que el acápite de fondos despachados esté completado (Fase 4 - Depósito de Dania confirmado).")

    # 3. Archivar ciclo actual
    fecha_ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("UPDATE ciclos_semanales SET estado_general = 'FASE_4_COMPLETADO', cerrado_en = COALESCE(cerrado_en, ?) WHERE id = ?", (fecha_ahora, ciclo_id))

    # 4. Calcular el nuevo sábado (+7 días) y fechas de corte
    fsab_actual = datetime.strptime(ciclo_dict["fecha_sabado"], "%Y-%m-%d").date()
    fsab_nuevo = fsab_actual + timedelta(days=7)
    fsab_nuevo_str = fsab_nuevo.strftime("%Y-%m-%d")

    lunes_pas = fsab_nuevo - timedelta(days=5)
    sab_pas = fsab_nuevo
    lunes_fut = fsab_nuevo + timedelta(days=2)
    sab_fut = fsab_nuevo + timedelta(days=7)

    # Verificar si ya existía el ciclo o crearlo
    c.execute("SELECT id FROM ciclos_semanales WHERE fecha_sabado = ?", (fsab_nuevo_str,))
    ciclo_existente = c.fetchone()
    if ciclo_existente:
        nuevo_ciclo_id = ciclo_existente["id"]
        c.execute("UPDATE ciclos_semanales SET estado_general = 'FASE_1_GERENTES' WHERE id = ?", (nuevo_ciclo_id,))
    else:
        c.execute('''
        INSERT INTO ciclos_semanales (
            fecha_sabado, fecha_inicio_pas, fecha_fin_pas, fecha_inicio_fut, fecha_fin_fut, estado_general
        ) VALUES (?, ?, ?, ?, ?, 'FASE_1_GERENTES')
        ''', (
            fsab_nuevo_str,
            lunes_pas.strftime("%Y-%m-%d"),
            sab_pas.strftime("%Y-%m-%d"),
            lunes_fut.strftime("%Y-%m-%d"),
            sab_fut.strftime("%Y-%m-%d")
        ))
        nuevo_ciclo_id = c.lastrowid

    # 5. Obtener sucursales activas
    c.execute("SELECT id, nombre, email_gerente FROM sucursales WHERE activa = 1 ORDER BY id ASC")
    sucursales = [dict(r) for r in c.fetchall()]

    dias_vacio = json.dumps([""] * 6)

    for suc in sucursales:
        suc_id = suc["id"]

        # Obtener usuario_id del gerente
        c.execute("SELECT id FROM usuarios WHERE sucursal_id = ? LIMIT 1", (suc_id,))
        u = c.fetchone()
        u_id = u["id"] if u else None

        # Obtener los colaboradores trabajados por el gerente en el ciclo que se acaba de cerrar
        c.execute("SELECT id FROM reportes_sucursales WHERE ciclo_id = ? AND sucursal_id = ?", (ciclo_id, suc_id))
        rep_ant = c.fetchone()
        colaboradores_anteriores = []
        if rep_ant:
            c.execute('''
            SELECT colaborador_id, nombre, cargo, estado, combustible_base, talla, req, obs
            FROM detalle_novedades
            WHERE reporte_id = ?
            ORDER BY id ASC
            ''', (rep_ant["id"],))
            colaboradores_anteriores = [dict(r) for r in c.fetchall()]

        # Filtrar filas vacías o sin nombre
        colaboradores_anteriores = [c_item for c_item in colaboradores_anteriores if (c_item.get("nombre") or "").strip()]

        # Fallback: si no había filas en el reporte anterior, obtener del maestro de colaboradores
        if not colaboradores_anteriores:
            c.execute("SELECT id as colaborador_id, nombre, cargo, estado_default as estado, combustible_base, talla, req_default as req, obs_default as obs FROM colaboradores WHERE sucursal_id = ? AND activo = 1 ORDER BY id ASC", (suc_id,))
            colaboradores_anteriores = [dict(r) for r in c.fetchall()]

        # Evolución y depuración automática de estados para el nuevo ciclo:
        # 1. 'BAJA' o 'INACTIVO': Excluidos automáticamente de la clonación y desactivados en la tabla maestra
        # 2. 'NUEVO': Evoluciona automáticamente a 'ACTIVO' y se registra en la tabla maestra si era colaborador nuevo
        # 3. 'ACTIVO': Se mantiene intacto con su cargo y combustible base
        colaboradores_evolucionados = []
        for c_item in colaboradores_anteriores:
            st = (c_item.get("estado") or "").strip().upper()
            nom = (c_item.get("nombre") or "").strip()
            colab_id = c_item.get("colaborador_id")

            if st in ("BAJA", "INACTIVO"):
                if colab_id:
                    c.execute("UPDATE colaboradores SET activo = 0 WHERE id = ?", (colab_id,))
                elif nom:
                    c.execute("UPDATE colaboradores SET activo = 0 WHERE sucursal_id = ? AND LOWER(nombre) = LOWER(?)", (suc_id, nom))
                continue

            c_nuevo = dict(c_item)
            if st == "NUEVO":
                c_nuevo["estado"] = "ACTIVO"
                if colab_id:
                    c.execute("UPDATE colaboradores SET estado_default = 'ACTIVO', activo = 1 WHERE id = ?", (colab_id,))
                elif nom:
                    c.execute("SELECT id FROM colaboradores WHERE sucursal_id = ? AND LOWER(nombre) = LOWER(?)", (suc_id, nom))
                    existing_colab = c.fetchone()
                    if existing_colab:
                        c.execute("UPDATE colaboradores SET estado_default = 'ACTIVO', activo = 1 WHERE id = ?", (existing_colab["id"],))
                        c_nuevo["colaborador_id"] = existing_colab["id"]
                    else:
                        c.execute('''
                        INSERT INTO colaboradores (sucursal_id, nombre, cargo, combustible_base, estado_default, talla, req_default, obs_default, activo)
                        VALUES (?, ?, ?, ?, 'ACTIVO', ?, ?, ?, 1)
                        ''', (suc_id, nom, c_item.get("cargo", "OFICIAL DE CREDITO"), float(c_item.get("combustible_base", 0.0) or 0.0), c_item.get("talla", ""), c_item.get("req", ""), c_item.get("obs", "")))
                        c_nuevo["colaborador_id"] = c.lastrowid
            elif not st:
                c_nuevo["estado"] = "ACTIVO"
            else:
                c_nuevo["estado"] = st

            colaboradores_evolucionados.append(c_nuevo)

        colaboradores_anteriores = colaboradores_evolucionados

        total_empleados = len(colaboradores_anteriores)
        suma_base = sum(float(c_item.get("combustible_base", 0.0) or 0.0) for c_item in colaboradores_anteriores)
        suma_neto = suma_base  # Con novedades limpias, deducciones son 0 y neto = base

        # Crear o actualizar reporte de la nueva semana en PENDIENTE
        c.execute("SELECT id FROM reportes_sucursales WHERE ciclo_id = ? AND sucursal_id = ?", (nuevo_ciclo_id, suc_id))
        rep_nuevo = c.fetchone()
        if rep_nuevo:
            nuevo_rep_id = rep_nuevo["id"]
            c.execute('''
            UPDATE reportes_sucursales
            SET estado = 'PENDIENTE', enviado_en = NULL, total_empleados = ?,
                suma_base = ?, suma_ajustes = 0.0, suma_neto = ?,
                usuario_id = ?, actualizado_en = CURRENT_TIMESTAMP
            WHERE id = ?
            ''', (total_empleados, round(suma_base, 2), round(suma_neto, 2), u_id, nuevo_rep_id))
            c.execute("DELETE FROM detalle_novedades WHERE reporte_id = ?", (nuevo_rep_id,))
        else:
            c.execute('''
            INSERT INTO reportes_sucursales (
                ciclo_id, sucursal_id, usuario_id, estado, total_empleados,
                suma_base, suma_ajustes, suma_neto
            ) VALUES (?, ?, ?, 'PENDIENTE', ?, ?, 0.0, ?)
            ''', (nuevo_ciclo_id, suc_id, u_id, total_empleados, round(suma_base, 2), round(suma_neto, 2)))
            nuevo_rep_id = c.lastrowid

        # Insertar filas preservando colaboradores pero con casillas de novedades limpias
        for c_item in colaboradores_anteriores:
            c_base = float(c_item.get("combustible_base", 0.0) or 0.0)
            c.execute('''
            INSERT INTO detalle_novedades (
                reporte_id, colaborador_id, nombre, cargo, estado,
                combustible_base, factor_total, deduccion_ajuste, total_neto,
                dias_pas_json, dias_fut_json, talla, req, obs
            ) VALUES (?, ?, ?, ?, ?, ?, 0.0, 0.0, ?, ?, ?, ?, ?, ?)
            ''', (
                nuevo_rep_id,
                c_item.get("colaborador_id"),
                c_item.get("nombre"),
                c_item.get("cargo"),
                c_item.get("estado") or "ACTIVO",
                c_base,
                c_base,
                dias_vacio,
                dias_vacio,
                c_item.get("talla") or "",
                c_item.get("req") or "",
                c_item.get("obs") or ""
            ))

    conn.commit()
    if close_at_end:
        conn.close()

    return {
        "ciclo_anterior_id": ciclo_id,
        "nuevo_ciclo_id": nuevo_ciclo_id,
        "fecha_sabado": fsab_nuevo_str,
        "total_sucursales": len(sucursales)
    }

if __name__ == "__main__":
    init_db()
    print("✅ Base de datos inicializada correctamente.")

