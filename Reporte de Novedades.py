import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from tkcalendar import DateEntry
import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from datetime import timedelta, datetime
import os
import json
import traceback
import sys

# --- CONFIGURACIÓN PARA EJECUTABLE ---
def resource_path(relative_path):
    """ Obtiene la ruta al recurso (logo) ya sea en código o en exe """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --- RUTAS ---
DOCS_DIR = os.path.join(os.path.expanduser("~"), "Documents")
DB_FILE = os.path.join(DOCS_DIR, "empleados_alfacredit_db.json")
LOGO_FILE = resource_path("logo.png")

# --- INTENTO DE IMPORTAR IMÁGENES ---
try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

class NovedadesApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Sistema de Novedades - AlfaCredit S.A.")
        self.root.geometry("1600x900")
        
        # --- ESTILOS DE LISTA (NEGRITA Y CENTRADO) ---
        root.option_add('*TCombobox*Listbox.font', ('Segoe UI', 10, 'bold'))
        root.option_add('*TCombobox*Listbox.justify', 'center')
        
        style = ttk.Style()
        style.theme_use('clam')
        
        # Colores Interfaz
        self.C_AZUL = "#154360"
        self.C_NARANJA = "#d35400"
        self.C_VERDE = "#27ae60"
        
        style.configure("HeaderPast.TLabel", font=('Segoe UI', 9, 'bold'), background=self.C_NARANJA, foreground="white", anchor="center") 
        style.configure("HeaderFuture.TLabel", font=('Segoe UI', 9, 'bold'), background=self.C_VERDE, foreground="white", anchor="center") 
        style.configure("HeaderGeneral.TLabel", font=('Segoe UI', 9, 'bold'), background=self.C_AZUL, foreground="white", anchor="center")
        
        self.font_bold = ('Segoe UI', 9, 'bold')
        self.filas_widgets = []
        
        # --- LEYENDA MAESTRA ---
        self.leyenda_maestra = [
            ("V", "Vacaciones", "-1 día", -1.0),
            ("MD", "Medio Día", "-0.5 día", -0.5),
            ("S", "Subsidio", "-1 día", -1.0),
            ("B", "Baja", "-1 día", -1.0),
            ("F", "Falta", "-1 día", -1.0),
            ("N", "Nuevo", "+1 (Suma)", 1.0),
            ("R", "Reposición", "+1 (Suma)", 1.0),
            ("PE", "Proc. Entrega", "---", 0.0)
        ]
        self.opciones_novedad = [""] + [item[0] for item in self.leyenda_maestra]
        self.lista_cargos = ["SUPERVISOR", "OFICIAL DE CREDITO", "OFICIAL DE DESEMBOLSO", "PROMOTOR INTERNO", "RESP. OPERACIONES", "CONSERJE", "GERENTE SUCURSAL"]
        self.lista_sucursales = ["Estelí", "Masaya", "Managua", "Managua Norte", "Sébaco", "Jinotepe", "Juigalpa", "Rivas", "León"]

        self.header_labels_pas = [] 
        self.header_labels_fut = []

        # --- PANEL SUPERIOR ---
        panel_top = ttk.Frame(root)
        panel_top.pack(fill="x", padx=15, pady=10)
        
        # LOGO
        frame_logo = tk.Frame(panel_top)
        frame_logo.pack(side="left", padx=10)
        
        logo_cargado = False
        if HAS_PIL and os.path.exists(LOGO_FILE):
            try:
                pil_img = Image.open(LOGO_FILE)
                pil_img.thumbnail((180, 80), Image.Resampling.LANCZOS) 
                self.logo_img = ImageTk.PhotoImage(pil_img)
                tk.Label(frame_logo, image=self.logo_img).pack()
                logo_cargado = True
            except: pass
        
        if not logo_cargado:
            tk.Label(frame_logo, text="ALFACREDIT S.A.", font=("Arial Black", 16), fg=self.C_AZUL).pack(anchor="w")

        # CONFIGURACIÓN
        frame_config = ttk.LabelFrame(panel_top, text="Datos Generales", padding=10)
        frame_config.pack(side="left", fill="y", padx=5)
        
        ttk.Label(frame_config, text="Sucursal:").grid(row=0, column=0, padx=5, pady=5)
        self.combo_sucursal = ttk.Combobox(frame_config, values=self.lista_sucursales, width=18, state="readonly", font=self.font_bold)
        self.combo_sucursal.current(0)
        self.combo_sucursal.grid(row=0, column=1, padx=5, pady=5)
        
        ttk.Label(frame_config, text="Fecha Sábado:").grid(row=1, column=0, padx=5, pady=5)
        self.cal_fecha = DateEntry(frame_config, width=12, background='#2980b9', foreground='white', borderwidth=2, date_pattern='dd/mm/yyyy', font=self.font_bold)
        self.cal_fecha.grid(row=1, column=1, padx=5, pady=5)
        self.cal_fecha.bind("<<DateEntrySelected>>", self.actualizar_headers_fechas)

        # LEYENDA VISUAL
        frame_leyenda = ttk.LabelFrame(panel_top, text="Leyenda", padding=5)
        frame_leyenda.pack(side="left", padx=10, fill="y")
        
        for i, (k, desc, ef, val) in enumerate(self.leyenda_maestra):
            row = i % 4 
            col = (i // 4) * 3 
            ttk.Label(frame_leyenda, text=k, foreground=self.C_AZUL, font=('Segoe UI', 8, 'bold')).grid(row=row, column=col, padx=2)
            ttk.Label(frame_leyenda, text=desc, font=('Segoe UI', 8)).grid(row=row, column=col+1, sticky="w", padx=2)
            color_ef = "green" if val > 0 else ("red" if val < 0 else "black")
            ttk.Label(frame_leyenda, text=ef, foreground=color_ef, font=('Segoe UI', 8)).grid(row=row, column=col+2, padx=5)

        # BOTONES
        frame_btns = ttk.Frame(panel_top)
        frame_btns.pack(side="right", padx=10, fill="y")
        
        tk.Button(frame_btns, text="💾 Guardar Cambios", command=self.guardar_db, bg="#2ecc71", fg="white", font=("Segoe UI", 9, "bold"), width=20).pack(pady=2)
        tk.Button(frame_btns, text="💎 Generar Excel", command=self.generar_excel_pro, bg="#3498db", fg="white", font=("Segoe UI", 9, "bold"), width=20).pack(pady=2)
        
        f_sub = tk.Frame(frame_btns)
        f_sub.pack(pady=5)
        tk.Button(f_sub, text="+ Empleado", command=self.agregar_fila, bg="#95a5a6", fg="white", width=12, font=("Segoe UI", 8, "bold")).pack(side="left", padx=2)
        tk.Button(f_sub, text="🧹 Limpiar", command=self.limpiar_fechas, bg="#e67e22", fg="white", width=12, font=("Segoe UI", 8, "bold")).pack(side="left", padx=2)

        # NOTA
        tk.Label(root, text="NOTA: Las deducciones del sábado equivalen a -1 día de combustible. (N y R suman +1 día)", fg="#c0392b", font=("Segoe UI", 10, "italic")).pack(pady=(0, 5))

        # GRID
        frame_main = ttk.Frame(root, relief="sunken", borderwidth=1)
        frame_main.pack(fill="both", expand=True, padx=15, pady=5)

        self.canvas = tk.Canvas(frame_main, bg="#ecf0f1")
        v_scroll = ttk.Scrollbar(frame_main, orient="vertical", command=self.canvas.yview)
        h_scroll = ttk.Scrollbar(frame_main, orient="horizontal", command=self.canvas.xview)
        
        self.grid_frame = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.grid_frame, anchor="nw")
        self.grid_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        
        v_scroll.pack(side="right", fill="y"); h_scroll.pack(side="bottom", fill="x"); self.canvas.pack(side="left", fill="both", expand=True)

        self.crear_encabezados()
        self.cargar_db()
        self.actualizar_headers_fechas(None)

    def crear_encabezados(self):
        static_headers = [("No.", 4), ("Nombre Colaborador", 30), ("Cargo", 18)]
        for i, (txt, w) in enumerate(static_headers):
            lbl = ttk.Label(self.grid_frame, text=txt, style="HeaderGeneral.TLabel", padding=6)
            lbl.grid(row=0, column=i, sticky="nsew", padx=1, pady=1)
            self.grid_frame.grid_columnconfigure(i, weight=1)

        self.dias_base = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB"]
        for i in range(6):
            lbl = ttk.Label(self.grid_frame, text=self.dias_base[i], style="HeaderPast.TLabel", padding=6)
            lbl.grid(row=0, column=3+i, sticky="nsew", padx=1, pady=1)
            self.grid_frame.grid_columnconfigure(3+i, weight=1)
            self.header_labels_pas.append(lbl)

        for i in range(6):
            lbl = ttk.Label(self.grid_frame, text=self.dias_base[i], style="HeaderFuture.TLabel", padding=6)
            lbl.grid(row=0, column=9+i, sticky="nsew", padx=1, pady=1)
            self.grid_frame.grid_columnconfigure(9+i, weight=1)
            self.header_labels_fut.append(lbl)

        final_headers = [("Estado", 10), ("Combust.", 8), ("Deducir/Sumar", 10), ("Talla", 5), ("Req", 15), ("Obs", 15), ("Acción", 4)]
        for i, (txt, w) in enumerate(final_headers):
            col_idx = 15 + i
            lbl = ttk.Label(self.grid_frame, text=txt, style="HeaderGeneral.TLabel", padding=6)
            lbl.grid(row=0, column=col_idx, sticky="nsew", padx=1, pady=1)
            self.grid_frame.grid_columnconfigure(col_idx, weight=1)

    def actualizar_headers_fechas(self, event):
        try:
            fsab = self.cal_fecha.get_date()
            lunes_pas = fsab - timedelta(days=5)
            lunes_fut = fsab + timedelta(days=2)
            curr = lunes_pas
            for i in range(6):
                self.header_labels_pas[i].config(text=f"{self.dias_base[i]}\n{curr.strftime('%d')}")
                curr += timedelta(days=1)
            curr = lunes_fut
            for i in range(6):
                self.header_labels_fut[i].config(text=f"{self.dias_base[i]}\n{curr.strftime('%d')}")
                curr += timedelta(days=1)
        except: pass

    def agregar_fila(self, datos=None):
        idx = len(self.filas_widgets) + 1
        d = datos or {}
        v = {
            "nombre": tk.StringVar(value=str(d.get("nombre", ""))),
            "cargo": tk.StringVar(value=str(d.get("cargo", ""))),
            "dias_pas": [tk.StringVar() for _ in range(6)],
            "dias_fut": [tk.StringVar() for _ in range(6)],
            "estado": tk.StringVar(value=str(d.get("estado", "ACTIVO"))),
            "combustible": tk.StringVar(value=str(d.get("combustible", "0.0"))),
            "deduccion": tk.StringVar(value="0.0"),
            "talla": tk.StringVar(value=str(d.get("talla", ""))),
            "req": tk.StringVar(value=str(d.get("req", ""))),
            "obs": tk.StringVar(value=str(d.get("obs", "")))
        }

        ttk.Label(self.grid_frame, text=str(idx), anchor="center", font=self.font_bold).grid(row=idx, column=0, sticky="nsew", padx=1, pady=1)
        ttk.Entry(self.grid_frame, textvariable=v["nombre"], font=self.font_bold).grid(row=idx, column=1, sticky="nsew", padx=1, pady=1)
        ttk.Combobox(self.grid_frame, textvariable=v["cargo"], values=self.lista_cargos, font=self.font_bold).grid(row=idx, column=2, sticky="nsew", padx=1, pady=1)
        
        # Días Pasados
        lp = d.get("dias_pas_list", [""]*6)
        for i in range(6):
            v["dias_pas"][i].set(lp[i] if i < len(lp) else "")
            cb = ttk.Combobox(self.grid_frame, textvariable=v["dias_pas"][i], values=self.opciones_novedad, width=3, state="readonly", justify='center', font=self.font_bold)
            cb.grid(row=idx, column=3+i, sticky="nsew", padx=1, pady=1)
            cb.bind("<<ComboboxSelected>>", lambda e, var=v, w=cb: self.actualizar_combo_y_calculo(var, w))
            self.colorear_inicial(cb, v["dias_pas"][i].get())

        # Días Futuros
        lf = d.get("dias_fut_list", [""]*6)
        for i in range(6):
            v["dias_fut"][i].set(lf[i] if i < len(lf) else "")
            cb = ttk.Combobox(self.grid_frame, textvariable=v["dias_fut"][i], values=self.opciones_novedad, width=3, state="readonly", justify='center', font=self.font_bold)
            cb.grid(row=idx, column=9+i, sticky="nsew", padx=1, pady=1)
            # AQUI ESTABA EL CAMBIO: Ahora llama a actualizar_combo_y_calculo
            cb.bind("<<ComboboxSelected>>", lambda e, var=v, w=cb: self.actualizar_combo_y_calculo(var, w))
            self.colorear_inicial(cb, v["dias_fut"][i].get())

        off = 15
        ttk.Combobox(self.grid_frame, textvariable=v["estado"], values=["ACTIVO", "NUEVO", "BAJA", "SUBSIDIO"], font=self.font_bold).grid(row=idx, column=off, sticky="nsew", padx=1, pady=1)
        
        e_c = ttk.Entry(self.grid_frame, textvariable=v["combustible"], justify="center", font=self.font_bold)
        e_c.grid(row=idx, column=off+1, sticky="nsew", padx=1, pady=1)
        e_c.bind("<KeyRelease>", lambda e, var=v: self.calcular(var))
        
        e_ded = ttk.Entry(self.grid_frame, textvariable=v["deduccion"], state="readonly", justify="center", font=self.font_bold)
        e_ded.grid(row=idx, column=off+2, sticky="nsew", padx=1, pady=1)
        
        ttk.Entry(self.grid_frame, textvariable=v["talla"], justify="center").grid(row=idx, column=off+3, sticky="nsew", padx=1, pady=1)
        ttk.Entry(self.grid_frame, textvariable=v["req"]).grid(row=idx, column=off+4, sticky="nsew", padx=1, pady=1)
        ttk.Entry(self.grid_frame, textvariable=v["obs"]).grid(row=idx, column=off+5, sticky="nsew", padx=1, pady=1)
        
        tk.Button(self.grid_frame, text="✖", bg="#c0392b", fg="white", bd=0, 
                  command=lambda r=idx, n=v["nombre"]: self.eliminar_confirmar(r, n)).grid(row=idx, column=off+6, sticky="nsew", padx=1, pady=1)
        
        self.filas_widgets.append({"vars": v, "idx": idx, "widget_ded": e_ded})
        self.calcular(v, e_ded)

    def actualizar_combo_y_calculo(self, v, widget):
        val = widget.get()
        self.colorear_inicial(widget, val)
        self.calcular(v)

    def colorear_inicial(self, widget, val):
        if val in ["V", "F", "B", "MD", "S"]: widget.config(foreground="red")
        elif val in ["N", "R"]: widget.config(foreground="green")
        else: widget.config(foreground="black")

    def calcular(self, v, widget_ded=None):
        if widget_ded is None:
            found = next((x for x in self.filas_widgets if x["vars"] == v), None)
            if found: widget_ded = found["widget_ded"]

        try: base = float(v["combustible"].get().strip() or 0)
        except: base = 0.0
        
        factor_total = 0.0
        
        # --- AQUÍ ESTÁ EL CAMBIO IMPORTANTE: SUMAR AMBAS SEMANAS ---
        # Concatenamos la lista de días pasados Y futuros
        todos_los_dias = v["dias_pas"] + v["dias_fut"]
        
        for dv in todos_los_dias:
            clave = dv.get()
            for k, _, _, val in self.leyenda_maestra:
                if k == clave:
                    factor_total += val
                    break
        
        monto = (base / 6 * factor_total) if base > 0 else 0.0
        v["deduccion"].set(f"{abs(monto):.2f}")
        
        if widget_ded:
            if factor_total < 0: widget_ded.config(foreground="red") 
            elif factor_total > 0: widget_ded.config(foreground="green")
            else: widget_ded.config(foreground="black")

    def eliminar_confirmar(self, idx, nombre_var):
        nom = nombre_var.get() or "este colaborador"
        if messagebox.askyesno("Confirmar Eliminación", f"¿Estás seguro de ELIMINAR a:\n{nom}?"):
            t = next((x for x in self.filas_widgets if x["idx"] == idx), None)
            if t: 
                self.filas_widgets.remove(t)
                self.guardar_db()
                self.recargar_vista()

    def limpiar_fechas(self):
        if messagebox.askyesno("Limpiar Fechas", "¿Borrar TODAS las asistencias de AMBAS SEMANAS?\n(Se mantiene el personal)"):
            for i in self.filas_widgets:
                v = i["vars"]
                for d in v["dias_pas"]: d.set("")
                for d in v["dias_fut"]: d.set("")
                self.calcular(v)
            self.guardar_db()
            self.recargar_vista()

    def recargar_vista(self):
        for w in self.grid_frame.winfo_children():
            if int(w.grid_info()["row"]) > 0: w.destroy()
        temp_data = [] 
        for item in self.filas_widgets:
            v = item["vars"]
            temp_data.append({
                "nombre":v["nombre"].get(), "cargo":v["cargo"].get(), "estado":v["estado"].get(), "combustible":v["combustible"].get(),
                "talla":v["talla"].get(), "req":v["req"].get(), "obs":v["obs"].get(),
                "dias_pas_list":[d.get() for d in v["dias_pas"]], "dias_fut_list":[d.get() for d in v["dias_fut"]]
            })
        self.filas_widgets = []
        for d in temp_data: self.agregar_fila(d)
        self.actualizar_headers_fechas(None)

    def guardar_db(self):
        try:
            data = []
            for i in self.filas_widgets:
                v = i["vars"]
                if v["nombre"].get().strip():
                    data.append({
                        "nombre":v["nombre"].get(), "cargo":v["cargo"].get(), "estado":v["estado"].get(), "combustible":v["combustible"].get(),
                        "talla":v["talla"].get(), "req":v["req"].get(), "obs":v["obs"].get(),
                        "dias_pas_list":[d.get() for d in v["dias_pas"]], "dias_fut_list":[d.get() for d in v["dias_fut"]]
                    })
            with open(DB_FILE, "w", encoding='utf-8') as f: json.dump(data, f, indent=4)
        except Exception as e: messagebox.showerror("Error Guardando", str(e))

    def cargar_db(self):
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, "r", encoding='utf-8') as f:
                    for d in json.load(f): self.agregar_fila(d)
            except: self.agregar_fila()
        else: self.agregar_fila()

    def generar_excel_pro(self):
        self.guardar_db()
        try:
            # Definir colores locales para evitar error
            E_AZUL = "154360"; E_NARANJA = "F5CBA7"; E_VERDE = "ABEBC6"; E_MONEY = "E8F8F5"; E_BORD = "BDC3C7"

            sucursal = self.combo_sucursal.get().strip() or "NO ESPECIFICADA"
            try: fsab = self.cal_fecha.get_date()
            except: fsab = datetime.now().date()
            lunes_pas = fsab - timedelta(days=5)
            lunes_fut = fsab + timedelta(days=2) 

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Novedades Semanales"
            
            fill_m = PatternFill(start_color=E_AZUL, end_color=E_AZUL, fill_type="solid")
            fill_p = PatternFill(start_color="D35400", end_color="D35400", fill_type="solid")
            fill_f  = PatternFill(start_color="27AE60", end_color="27AE60", fill_type="solid")
            fill_e = PatternFill(start_color="F4F6F7", end_color="F4F6F7", fill_type="solid")
            fill_mo= PatternFill(start_color=E_MONEY, end_color=E_MONEY, fill_type="solid")
            
            f_ti = Font(name='Segoe UI', size=16, bold=True, color="FFFFFF")
            f_hd  = Font(name='Segoe UI', size=9, bold=True, color="FFFFFF")
            f_dt  = Font(name='Segoe UI', size=9)
            f_bd  = Font(name='Segoe UI', size=9, bold=True)
            f_dt_az  = Font(name='Segoe UI', size=9, bold=True, color="154360")
            
            bd = Border(left=Side(style='thin', color=E_BORD), right=Side(style='thin', color=E_BORD), top=Side(style='thin', color=E_BORD), bottom=Side(style='thin', color=E_BORD))
            al_c = Alignment(horizontal="center", vertical="center", wrap_text=True)
            al_l = Alignment(horizontal="left", vertical="center", wrap_text=True)

            ws.merge_cells('A1:W2'); ws['A1']="REPORTE DE NOVEDADES - ALFACREDIT S.A."; ws['A1'].font=f_ti; ws['A1'].fill=fill_m; ws['A1'].alignment=al_c
            ws['B4']="SUCURSAL:"; ws['B4'].font=f_bd; ws['B4'].alignment=Alignment(horizontal='right')
            ws['C4']=sucursal; ws['C4'].font=Font(name='Segoe UI', size=10, color="E74C3C" if sucursal=="NO ESPECIFICADA" else "000000")
            ws['K4']="FECHA CORTE:"; ws['K4'].font=f_bd; ws['K4'].alignment=Alignment(horizontal='right')
            ws['L4']=fsab.strftime("%d/%m/%Y")
            ws.merge_cells('E5:K5'); ws['E5']="Nota: Las deducciones del sábado equivalen a -1 día de combustible"; ws['E5'].font=Font(name='Segoe UI', size=10, italic=True, color="C0392B", bold=True); ws['E5'].alignment=al_c

            col_ley = 23
            ws.merge_cells(start_row=3, start_column=col_ley, end_row=3, end_column=col_ley+2)
            ws.cell(3, col_ley).value="LEYENDA"; ws.cell(3, col_ley).fill=fill_m; ws.cell(3, col_ley).font=f_hd; ws.cell(3, col_ley).alignment=al_c
            r = 4
            for k, desc, ef, val in self.leyenda_maestra:
                c1 = ws.cell(r, col_ley); c1.value=k; c1.font=Font(bold=True, color="154360"); c1.alignment=al_c; c1.border=bd
                c2 = ws.cell(r, col_ley+1); c2.value=desc; c2.font=f_dt; c2.alignment=al_l; c2.border=bd
                c3 = ws.cell(r, col_ley+2); c3.value=ef; c3.font=Font(color="27AE60" if val>0 else ("C0392B" if val<0 else "000000")); c3.alignment=al_c; c3.border=bd
                r+=1

            ws.merge_cells('D7:I7'); ws['D7']=f"HISTÓRICO ({lunes_pas.strftime('%d/%m')})"; ws['D7'].fill=fill_p; ws['D7'].font=f_hd; ws['D7'].alignment=al_c
            ws.merge_cells('J7:O7'); ws['J7']=f"PROYECCIÓN ({lunes_fut.strftime('%d/%m')})"; ws['J7'].fill=fill_f; ws['J7'].font=f_hd; ws['J7'].alignment=al_c

            headers = [("A","No."), ("B","NOMBRE COLABORADOR"), ("C","CARGO"), ("P","ESTADO"), ("Q","ASIG. COMB."), ("R","DEDUCIR/SUMAR"), ("S","TOTAL"), ("T","REQUERIMIENTOS"), ("U","OBSERVACIONES")]
            for col_let, txt in headers:
                cell = ws[f'{col_let}8']; cell.value=txt; cell.fill=fill_m; cell.font=f_hd; cell.alignment=al_c; cell.border=bd

            curr = lunes_pas
            for i in range(6):
                col = openpyxl.utils.get_column_letter(4+i)
                ws[f'{col}8']=f"{self.dias_base[i]}\n{curr.strftime('%d')}"; ws[f'{col}8'].fill=PatternFill(start_color=E_NARANJA, fill_type="solid"); ws[f'{col}8'].font=f_dt_az; ws[f'{col}8'].alignment=al_c; ws[f'{col}8'].border=bd
                curr+=timedelta(days=1)
            curr = lunes_fut
            for i in range(6):
                col = openpyxl.utils.get_column_letter(10+i)
                ws[f'{col}8']=f"{self.dias_base[i]}\n{curr.strftime('%d')}"; ws[f'{col}8'].fill=PatternFill(start_color=E_VERDE, fill_type="solid"); ws[f'{col}8'].font=f_dt_az; ws[f'{col}8'].alignment=al_c; ws[f'{col}8'].border=bd
                curr+=timedelta(days=1)

            row = 9; total_neto = 0
            for i, item in enumerate(self.filas_widgets):
                v = item["vars"]
                cf = fill_e if (i % 2 == 0) else None
                
                try: base = float(v["combustible"].get().strip() or 0)
                except: base = 0.0
                factor = 0.0
                
                # --- CAMBIO IMPORTANTE: SUMAR AMBAS SEMANAS EN EL EXCEL ---
                todos_los_dias = v["dias_pas"] + v["dias_fut"]
                for dv in todos_los_dias:
                    for k, _, _, val_math in self.leyenda_maestra:
                        if dv.get() == k: factor += val_math
                
                ajuste_dinero = (base/6 * factor) if base > 0 else 0
                total_pago = base + ajuste_dinero 
                total_neto += total_pago
                monto_mostrar = abs(ajuste_dinero)

                data_map = [(1,i+1,al_c),(2,v["nombre"].get(),al_l),(3,v["cargo"].get(),al_c),(16,v["estado"].get(),al_c),(20,v["req"].get(),al_l),(21,v["obs"].get(),al_l)]
                for c_idx, val, alg in data_map:
                    c = ws.cell(row,c_idx); c.value=val; c.font=f_dt; c.alignment=alg; c.border=bd
                    if cf: c.fill=cf
                
                # DIAS
                for x in range(6):
                    c=ws.cell(row,4+x); c.value=v["dias_pas"][x].get(); c.alignment=al_c; c.border=bd; c.font=Font(name='Segoe UI', size=9, bold=True)
                    if c.value in ["V","F","B","S","MD"]: c.font=Font(name='Segoe UI', size=9, bold=True, color="E74C3C")
                    elif c.value in ["N","R"]: c.font=Font(name='Segoe UI', size=9, bold=True, color="27AE60")
                    if cf: c.fill=cf
                for x in range(6):
                    c=ws.cell(row,10+x); c.value=v["dias_fut"][x].get(); c.alignment=al_c; c.border=bd; c.font=Font(name='Segoe UI', size=9, bold=True)
                    if c.value in ["V","F","B","S","MD"]: c.font=Font(name='Segoe UI', size=9, bold=True, color="E74C3C")
                    elif c.value in ["N","R"]: c.font=Font(name='Segoe UI', size=9, bold=True, color="27AE60")
                    if cf: c.fill=cf
                
                c17 = ws.cell(row, 17); c17.value = base; c17.fill=fill_mo
                c18 = ws.cell(row, 18); c18.value = monto_mostrar; c18.fill=fill_mo
                c19 = ws.cell(row, 19); c19.value = total_pago; c19.fill=fill_mo
                
                if factor < 0: c18.font = Font(color="E74C3C", bold=True)
                elif factor > 0: c18.font = Font(color="27AE60", bold=True)
                
                for c in [c17, c18, c19]: c.number_format='#,##0.00 "C$"'; c.alignment=al_c; c.border=bd

                row+=1

            ws[f'R{row}']="TOTAL:"; ws[f'R{row}'].font=f_bd; ws[f'R{row}'].alignment=Alignment(horizontal='right')
            ws[f'S{row}']=total_neto; ws[f'S{row}'].font=Font(bold=True,color="FFFFFF",size=11); ws[f'S{row}'].fill=PatternFill("C0392B",fill_type="solid"); ws[f'S{row}'].number_format='#,##0.00 "C$"'
            ws.column_dimensions['B'].width=32
            
            fn = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")])
            if fn:
                wb.save(fn)
                try: os.startfile(fn)
                except: messagebox.showinfo("Éxito", f"Reporte guardado en:\n{fn}")

        except PermissionError:
            messagebox.showerror("Error de Archivo", "¡EL EXCEL ESTÁ ABIERTO!\n\nPor favor cierra el archivo Excel antes de generar uno nuevo.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

if __name__ == "__main__":
    root = tk.Tk()
    app = NovedadesApp(root)
    root.mainloop()