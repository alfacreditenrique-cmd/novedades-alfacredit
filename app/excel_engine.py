import io
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from app.calculations import LEYENDA_MAESTRA

def generar_excel_sucursal(
    sucursal_nombre: str,
    fecha_sabado_str: str,
    filas_datos: List[Dict[str, Any]]
) -> io.BytesIO:
    """
    Genera el archivo Excel corporativo exacto para una sucursal individual.
    """
    try:
        fsab = datetime.strptime(fecha_sabado_str, "%Y-%m-%d").date()
    except Exception:
        fsab = datetime.now().date()

    lunes_pas = fsab - timedelta(days=5)
    lunes_fut = fsab + timedelta(days=2)
    dias_base = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB"]

    E_AZUL = "154360"
    E_NARANJA = "D35400"
    E_VERDE = "27AE60"
    E_NARANJA_LGT = "F5CBA7"
    E_VERDE_LGT = "ABEBC6"
    E_MONEY = "E8F8F5"
    E_BORD = "BDC3C7"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sucursal_nombre[:30]

    fill_m = PatternFill(start_color=E_AZUL, end_color=E_AZUL, fill_type="solid")
    fill_p = PatternFill(start_color=E_NARANJA, end_color=E_NARANJA, fill_type="solid")
    fill_f = PatternFill(start_color=E_VERDE, end_color=E_VERDE, fill_type="solid")
    fill_e = PatternFill(start_color="F4F6F7", end_color="F4F6F7", fill_type="solid")
    fill_mo = PatternFill(start_color=E_MONEY, end_color=E_MONEY, fill_type="solid")

    f_ti = Font(name='Segoe UI', size=15, bold=True, color="FFFFFF")
    f_hd = Font(name='Segoe UI', size=9, bold=True, color="FFFFFF")
    f_dt = Font(name='Segoe UI', size=9)
    f_bd = Font(name='Segoe UI', size=9, bold=True)
    f_dt_az = Font(name='Segoe UI', size=9, bold=True, color="154360")

    bd = Border(
        left=Side(style='thin', color=E_BORD),
        right=Side(style='thin', color=E_BORD),
        top=Side(style='thin', color=E_BORD),
        bottom=Side(style='thin', color=E_BORD)
    )
    al_c = Alignment(horizontal="center", vertical="center", wrap_text=True)
    al_l = Alignment(horizontal="left", vertical="center", wrap_text=True)

    # 1. Título
    ws.merge_cells('A1:W2')
    ws['A1'] = "REPORTE DE NOVEDADES - ALFACREDIT S.A."
    ws['A1'].font = f_ti
    ws['A1'].fill = fill_m
    ws['A1'].alignment = al_c

    # 2. Metadatos
    ws['B4'] = "SUCURSAL:"
    ws['B4'].font = f_bd
    ws['B4'].alignment = Alignment(horizontal='right')
    ws['C4'] = sucursal_nombre
    ws['C4'].font = Font(name='Segoe UI', size=10, bold=True, color="154360")

    ws['K4'] = "FECHA CORTE:"
    ws['K4'].font = f_bd
    ws['K4'].alignment = Alignment(horizontal='right')
    ws['L4'] = fsab.strftime("%d/%m/%Y")
    ws['L4'].font = Font(name='Segoe UI', size=10, bold=True)

    ws.merge_cells('E5:K5')
    ws['E5'] = "Nota: Las deducciones del sábado equivalen a -1 día de combustible (N y R suman +1 día)"
    ws['E5'].font = Font(name='Segoe UI', size=9, italic=True, color="C0392B", bold=True)
    ws['E5'].alignment = al_c

    # 3. Leyenda en el Excel
    col_ley = 23
    ws.merge_cells(start_row=3, start_column=col_ley, end_row=3, end_column=col_ley+2)
    ws.cell(3, col_ley).value = "LEYENDA"
    ws.cell(3, col_ley).fill = fill_m
    ws.cell(3, col_ley).font = f_hd
    ws.cell(3, col_ley).alignment = al_c

    r = 4
    for item in LEYENDA_MAESTRA:
        c1 = ws.cell(r, col_ley)
        c1.value = item["clave"]
        c1.font = Font(name='Segoe UI', size=8, bold=True, color="154360")
        c1.alignment = al_c
        c1.border = bd

        c2 = ws.cell(r, col_ley+1)
        c2.value = item["descripcion"]
        c2.font = Font(name='Segoe UI', size=8)
        c2.alignment = al_l
        c2.border = bd

        c3 = ws.cell(r, col_ley+2)
        c3.value = item["efecto"]
        c3.font = Font(name='Segoe UI', size=8, color="27AE60" if item["valor"] > 0 else ("C0392B" if item["valor"] < 0 else "000000"))
        c3.alignment = al_c
        c3.border = bd
        r += 1

    # 4. Agrupadores Semanales
    ws.merge_cells('D7:I7')
    ws['D7'] = f"HISTÓRICO ({lunes_pas.strftime('%d/%m')})"
    ws['D7'].fill = fill_p
    ws['D7'].font = f_hd
    ws['D7'].alignment = al_c

    ws.merge_cells('J7:O7')
    ws['J7'] = f"PROYECCIÓN ({lunes_fut.strftime('%d/%m')})"
    ws['J7'].fill = fill_f
    ws['J7'].font = f_hd
    ws['J7'].alignment = al_c

    # 5. Encabezados de Columnas
    headers = [
        ("A", "No."), ("B", "NOMBRE COLABORADOR"), ("C", "CARGO"),
        ("P", "ESTADO"), ("Q", "ASIG. COMB."), ("R", "DEDUCIR/SUMAR"),
        ("S", "TOTAL"), ("T", "TALLA"), ("U", "REQUERIMIENTOS"), ("V", "OBSERVACIONES")
    ]
    for col_let, txt in headers:
        cell = ws[f'{col_let}8']
        cell.value = txt
        cell.fill = fill_m
        cell.font = f_hd
        cell.alignment = al_c
        cell.border = bd

    curr = lunes_pas
    for i in range(6):
        col = openpyxl.utils.get_column_letter(4 + i)
        ws[f'{col}8'] = f"{dias_base[i]}\n{curr.strftime('%d')}"
        ws[f'{col}8'].fill = PatternFill(start_color=E_NARANJA_LGT, fill_type="solid")
        ws[f'{col}8'].font = f_dt_az
        ws[f'{col}8'].alignment = al_c
        ws[f'{col}8'].border = bd
        curr += timedelta(days=1)

    curr = lunes_fut
    for i in range(6):
        col = openpyxl.utils.get_column_letter(10 + i)
        ws[f'{col}8'] = f"{dias_base[i]}\n{curr.strftime('%d')}"
        ws[f'{col}8'].fill = PatternFill(start_color=E_VERDE_LGT, fill_type="solid")
        ws[f'{col}8'].font = f_dt_az
        ws[f'{col}8'].alignment = al_c
        ws[f'{col}8'].border = bd
        curr += timedelta(days=1)

    # 6. Llenado de Filas
    row = 9
    total_neto = 0.0

    for i, fila in enumerate(filas_datos):
        cf = fill_e if (i % 2 == 0) else None
        base = float(fila.get("combustible_base", 0.0))
        dias_pas = fila.get("dias_pas", [""] * 6)
        dias_fut = fila.get("dias_fut", [""] * 6)
        if isinstance(dias_pas, str):
            dias_pas = json.loads(dias_pas)
        if isinstance(dias_fut, str):
            dias_fut = json.loads(dias_fut)

        factor = 0.0
        for d in (dias_pas + dias_fut):
            for itm in LEYENDA_MAESTRA:
                if d == itm["clave"]:
                    factor += itm["valor"]

        ajuste_dinero = (base / 6.0 * factor) if base > 0 else 0.0
        total_pago = max(0.0, base + ajuste_dinero)
        total_neto += total_pago
        monto_mostrar = abs(ajuste_dinero)

        data_map = [
            (1, i + 1, al_c),
            (2, fila.get("nombre", ""), al_l),
            (3, fila.get("cargo", ""), al_c),
            (16, fila.get("estado", "ACTIVO"), al_c),
            (20, fila.get("talla", ""), al_c),
            (21, fila.get("req", ""), al_l),
            (22, fila.get("obs", ""), al_l)
        ]

        for c_idx, val, alg in data_map:
            c = ws.cell(row, c_idx)
            c.value = val
            c.font = f_dt
            c.alignment = alg
            c.border = bd
            if cf:
                c.fill = cf

        # Días Pasados
        for x in range(6):
            c = ws.cell(row, 4 + x)
            v = dias_pas[x] if x < len(dias_pas) else ""
            c.value = v
            c.alignment = al_c
            c.border = bd
            c.font = Font(name='Segoe UI', size=9, bold=True)
            if v in ["V", "F", "B", "S", "MD"]:
                c.font = Font(name='Segoe UI', size=9, bold=True, color="E74C3C")
            elif v in ["N", "R"]:
                c.font = Font(name='Segoe UI', size=9, bold=True, color="27AE60")
            if cf:
                c.fill = cf

        # Días Futuros
        for x in range(6):
            c = ws.cell(row, 10 + x)
            v = dias_fut[x] if x < len(dias_fut) else ""
            c.value = v
            c.alignment = al_c
            c.border = bd
            c.font = Font(name='Segoe UI', size=9, bold=True)
            if v in ["V", "F", "B", "S", "MD"]:
                c.font = Font(name='Segoe UI', size=9, bold=True, color="E74C3C")
            elif v in ["N", "R"]:
                c.font = Font(name='Segoe UI', size=9, bold=True, color="27AE60")
            if cf:
                c.fill = cf

        # Combustible y Totales
        c17 = ws.cell(row, 17)
        c17.value = base
        c17.fill = fill_mo

        c18 = ws.cell(row, 18)
        c18.value = monto_mostrar
        c18.fill = fill_mo

        c19 = ws.cell(row, 19)
        c19.value = total_pago
        c19.fill = fill_mo

        if factor < 0:
            c18.font = Font(name='Segoe UI', size=9, color="E74C3C", bold=True)
        elif factor > 0:
            c18.font = Font(name='Segoe UI', size=9, color="27AE60", bold=True)
        else:
            c18.font = f_dt

        for c in [c17, c18, c19]:
            c.number_format = '#,##0.00 "C$"'
            c.alignment = al_c
            c.border = bd

        row += 1

    # 7. Fila de Total Neto
    ws[f'R{row}'] = "TOTAL NETO:"
    ws[f'R{row}'].font = f_bd
    ws[f'R{row}'].alignment = Alignment(horizontal='right')

    ws[f'S{row}'] = total_neto
    ws[f'S{row}'].font = Font(name='Segoe UI', bold=True, color="FFFFFF", size=11)
    ws[f'S{row}'].fill = PatternFill("C0392B", fill_type="solid")
    ws[f'S{row}'].number_format = '#,##0.00 "C$"'
    ws[f'S{row}'].alignment = al_c
    ws[f'S{row}'].border = bd

    ws.column_dimensions['B'].width = 30
    ws.column_dimensions['C'].width = 20
    ws.column_dimensions['U'].width = 20
    ws.column_dimensions['V'].width = 20

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf

def generar_excel_consolidado_todas_sucursales(
    fecha_sabado_str: str,
    datos_sucursales: List[Dict[str, Any]]
) -> io.BytesIO:
    """
    Genera un libro Excel con una hoja Resumen Gerencial y una pestaña por cada sucursal.
    """
    wb = openpyxl.Workbook()
    ws_resumen = wb.active
    ws_resumen.title = "Resumen General"

    E_AZUL = "154360"
    fill_m = PatternFill(start_color=E_AZUL, end_color=E_AZUL, fill_type="solid")
    f_ti = Font(name='Segoe UI', size=14, bold=True, color="FFFFFF")
    f_hd = Font(name='Segoe UI', size=9, bold=True, color="FFFFFF")
    f_bd = Font(name='Segoe UI', size=9, bold=True)
    bd = Border(
        left=Side(style='thin', color="BDC3C7"),
        right=Side(style='thin', color="BDC3C7"),
        top=Side(style='thin', color="BDC3C7"),
        bottom=Side(style='thin', color="BDC3C7")
    )
    al_c = Alignment(horizontal="center", vertical="center")
    al_l = Alignment(horizontal="left", vertical="center")

    ws_resumen.merge_cells('A1:F2')
    ws_resumen['A1'] = f"ALFACREDIT S.A. - CONSOLIDADO GENERAL DE COMBUSTIBLE ({fecha_sabado_str})"
    ws_resumen['A1'].font = f_ti
    ws_resumen['A1'].fill = fill_m
    ws_resumen['A1'].alignment = al_c

    cols = ["No.", "Sucursal", "Gerente / Contacto", "Colaboradores", "Asig. Base (C$)", "Total Neto Aprobado (C$)"]
    for idx, col_name in enumerate(cols, start=1):
        c = ws_resumen.cell(4, idx)
        c.value = col_name
        c.font = f_hd
        c.fill = fill_m
        c.alignment = al_c
        c.border = bd

    row_idx = 5
    gran_total_base = 0.0
    gran_total_neto = 0.0
    gran_total_colabs = 0

    for i, suc in enumerate(datos_sucursales, start=1):
        total_col = suc.get("total_empleados", 0)
        base = suc.get("suma_base", 0.0)
        neto = suc.get("suma_neto", 0.0)

        gran_total_colabs += total_col
        gran_total_base += base
        gran_total_neto += neto

        ws_resumen.cell(row_idx, 1).value = i
        ws_resumen.cell(row_idx, 1).alignment = al_c
        ws_resumen.cell(row_idx, 2).value = suc.get("sucursal_nombre", "")
        ws_resumen.cell(row_idx, 2).alignment = al_l
        ws_resumen.cell(row_idx, 3).value = suc.get("email_gerente", "")
        ws_resumen.cell(row_idx, 3).alignment = al_l
        ws_resumen.cell(row_idx, 4).value = total_col
        ws_resumen.cell(row_idx, 4).alignment = al_c
        ws_resumen.cell(row_idx, 5).value = base
        ws_resumen.cell(row_idx, 5).number_format = '#,##0.00 "C$"'
        ws_resumen.cell(row_idx, 5).alignment = al_c
        ws_resumen.cell(row_idx, 6).value = neto
        ws_resumen.cell(row_idx, 6).number_format = '#,##0.00 "C$"'
        ws_resumen.cell(row_idx, 6).alignment = al_c

        for c_i in range(1, 7):
            ws_resumen.cell(row_idx, c_i).border = bd
            ws_resumen.cell(row_idx, c_i).font = Font(name='Segoe UI', size=9)

        row_idx += 1

    # Total General
    ws_resumen.cell(row_idx, 2).value = "GRAN TOTAL"
    ws_resumen.cell(row_idx, 2).font = f_bd
    ws_resumen.cell(row_idx, 4).value = gran_total_colabs
    ws_resumen.cell(row_idx, 4).font = f_bd
    ws_resumen.cell(row_idx, 4).alignment = al_c
    ws_resumen.cell(row_idx, 5).value = gran_total_base
    ws_resumen.cell(row_idx, 5).font = f_bd
    ws_resumen.cell(row_idx, 5).number_format = '#,##0.00 "C$"'
    ws_resumen.cell(row_idx, 5).alignment = al_c
    ws_resumen.cell(row_idx, 6).value = gran_total_neto
    ws_resumen.cell(row_idx, 6).font = Font(name='Segoe UI', size=11, bold=True, color="FFFFFF")
    ws_resumen.cell(row_idx, 6).fill = PatternFill("C0392B", fill_type="solid")
    ws_resumen.cell(row_idx, 6).number_format = '#,##0.00 "C$"'
    ws_resumen.cell(row_idx, 6).alignment = al_c

    for c_i in range(1, 7):
        ws_resumen.cell(row_idx, c_i).border = bd

    ws_resumen.column_dimensions['B'].width = 25
    ws_resumen.column_dimensions['C'].width = 32
    ws_resumen.column_dimensions['E'].width = 18
    ws_resumen.column_dimensions['F'].width = 24

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
