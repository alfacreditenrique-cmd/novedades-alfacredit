import json
import os
import sys
from datetime import datetime, timedelta

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from app.database import get_db_connection, init_db, asegurar_ciclo_actual

def reset_to_fresh_real_state():
    """
    Limpia cualquier dato de simulación o prueba previa y deja la plataforma
    en un estado 100% real y limpio listo para operación:
    - Ciclo activo en Fase 1 (Esperando a los 8 Gerentes).
    - 8 Sucursales en estado PENDIENTE con su plantilla de colaboradores lista y casillas en blanco.
    - Sin solicitudes enviadas de Brandon ni comprobantes previos.
    - Brandon y Administradores en modo monitoreo en tiempo real.
    """
    init_db()
    conn = get_db_connection()
    c = conn.cursor()

    # 1. Asegurar ciclo actual
    ciclo_id = asegurar_ciclo_actual(conn)

    # 2. Resetear el ciclo a Fase 1
    c.execute('''
    UPDATE ciclos_semanales
    SET estado_general = 'FASE_1_GERENTES'
    WHERE id = ?
    ''', (ciclo_id,))

    # 3. Eliminar eventos de Brandon y Dania del ciclo actual
    c.execute("DELETE FROM eventos_brandon WHERE ciclo_id = ?", (ciclo_id,))
    c.execute("DELETE FROM eventos_dania WHERE ciclo_id = ?", (ciclo_id,))

    # 4. Obtener todas las sucursales
    c.execute("SELECT id, nombre, email_gerente FROM sucursales WHERE activa = 1 ORDER BY id ASC")
    sucursales = c.fetchall()

    for suc in sucursales:
        suc_id = suc["id"]
        # Buscar usuario gerente
        c.execute("SELECT id FROM usuarios WHERE email = ?", (suc["email_gerente"],))
        u = c.fetchone()
        u_id = u["id"] if u else None

        # Resetear reporte de sucursal a PENDIENTE
        c.execute('''
        INSERT INTO reportes_sucursales (ciclo_id, sucursal_id, usuario_id, estado, total_empleados, suma_base, suma_ajustes, suma_neto, notas)
        VALUES (?, ?, ?, 'PENDIENTE', 0, 0.0, 0.0, 0.0, '')
        ON CONFLICT(ciclo_id, sucursal_id) DO UPDATE SET
            estado = 'PENDIENTE',
            enviado_en = NULL,
            suma_ajustes = 0.0,
            notas = ''
        ''', (ciclo_id, suc_id, u_id))

        c.execute("SELECT id FROM reportes_sucursales WHERE ciclo_id = ? AND sucursal_id = ?", (ciclo_id, suc_id))
        rep_id = c.fetchone()["id"]

        # Limpiar novedades para cada colaborador de la sucursal (dejar días en blanco)
        c.execute("SELECT * FROM colaboradores WHERE sucursal_id = ? AND activo = 1 ORDER BY id ASC", (suc_id,))
        colabs = c.fetchall()

        c.execute("DELETE FROM detalle_novedades WHERE reporte_id = ?", (rep_id,))

        vacio_json = json.dumps(["", "", "", "", "", ""])
        suma_b = 0.0
        for colab in colabs:
            base = colab["combustible_base"]
            suma_b += base
            c.execute('''
            INSERT INTO detalle_novedades (
                reporte_id, colaborador_id, nombre, cargo, estado,
                combustible_base, factor_total, deduccion_ajuste, total_neto,
                dias_pas_json, dias_fut_json, talla, req, obs
            ) VALUES (?, ?, ?, ?, 'ACTIVO', ?, 0.0, 0.0, ?, ?, ?, '', '', '')
            ''', (rep_id, colab["id"], colab["nombre"], colab["cargo"], base, base, vacio_json, vacio_json))

        # Actualizar totales limpios del reporte
        c.execute('''
        UPDATE reportes_sucursales
        SET total_empleados = ?, suma_base = ?, suma_neto = ?
        WHERE id = ?
        ''', (len(colabs), suma_b, suma_b, rep_id))

    conn.commit()
    conn.close()

    print("✅ Plataforma reiniciada con éxito en ESTADO REAL DE PRODUCCIÓN:")
    print("   - Ciclo semanal activo en Fase 1 (Pendiente por Gerentes).")
    print("   - 8 Sucursales con todas sus novedades en blanco listas para ser llenadas por cada Gerente.")
    print("   - Brandon y Administrador listos para monitorear la barra de estado en vivo.")

if __name__ == "__main__":
    reset_to_fresh_real_state()
