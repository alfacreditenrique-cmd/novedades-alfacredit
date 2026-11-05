"""
Módulo de cálculo matemático de novedades y combustible para AlfaCredit S.A.
Garantiza el cálculo exacto de deducciones, adiciones y neto a pagar por colaborador y por sucursal.
"""
from typing import List, Dict, Any

LEYENDA_MAESTRA = [
    {"clave": "V", "descripcion": "Vacaciones", "efecto": "-1 día", "valor": -1.0, "color": "danger"},
    {"clave": "MD", "descripcion": "Medio Día", "efecto": "-0.5 día", "valor": -0.5, "color": "warning"},
    {"clave": "S", "descripcion": "Subsidio", "efecto": "-1 día", "valor": -1.0, "color": "danger"},
    {"clave": "B", "descripcion": "Baja / Ausencia", "efecto": "-1 día", "valor": -1.0, "color": "danger"},
    {"clave": "F", "descripcion": "Falta", "efecto": "-1 día", "valor": -1.0, "color": "danger"},
    {"clave": "N", "descripcion": "Nuevo", "efecto": "+1 (Suma)", "valor": 1.0, "color": "success"},
    {"clave": "R", "descripcion": "Reposición", "efecto": "+1 (Suma)", "valor": 1.0, "color": "success"},
    {"clave": "PE", "descripcion": "Proc. Entrega", "efecto": "---", "valor": 0.0, "color": "info"}
]

VALORES_MAP = {item["clave"]: item["valor"] for item in LEYENDA_MAESTRA}

def calcular_fila_novedades(
    combustible_base: float,
    dias_pas: List[str],
    dias_fut: List[str]
) -> Dict[str, Any]:
    """
    Calcula el factor acumulado de ambas semanas (pasada y futura), el ajuste en dinero y el neto.
    Fórmula AlfaCredit:
    - Base semanal dividida entre 6 días laborables por el factor de novedades.
    - Sábado con deducción resta 1 día completo.
    - N y R suman 1 día completo.
    """
    try:
        base = float(combustible_base or 0.0)
    except (ValueError, TypeError):
        base = 0.0

    factor_total = 0.0
    todos_los_dias = (dias_pas or []) + (dias_fut or [])

    for clave in todos_los_dias:
        if clave in VALORES_MAP:
            factor_total += VALORES_MAP[clave]

    if base > 0:
        ajuste_dinero = (base / 6.0) * factor_total
        total_pago = max(0.0, base + ajuste_dinero)
    else:
        ajuste_dinero = 0.0
        total_pago = 0.0

    return {
        "combustible_base": round(base, 2),
        "factor_total": round(factor_total, 2),
        "ajuste_dinero": round(ajuste_dinero, 2),
        "deduccion_mostrada": round(abs(ajuste_dinero), 2),
        "total_neto": round(total_pago, 2),
        "es_deduccion": factor_total < 0,
        "es_adicion": factor_total > 0
    }

def calcular_resumen_reporte(filas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Calcula los totales consolidados de un reporte de sucursal.
    """
    total_empleados = len(filas)
    suma_base = 0.0
    suma_ajustes = 0.0
    suma_neto = 0.0

    for fila in filas:
        res = calcular_fila_novedades(
            fila.get("combustible_base", 0.0),
            fila.get("dias_pas", []),
            fila.get("dias_fut", [])
        )
        suma_base += res["combustible_base"]
        suma_ajustes += res["ajuste_dinero"]
        suma_neto += res["total_neto"]

    return {
        "total_empleados": total_empleados,
        "suma_base": round(suma_base, 2),
        "suma_ajustes": round(suma_ajustes, 2),
        "suma_neto": round(suma_neto, 2)
    }
