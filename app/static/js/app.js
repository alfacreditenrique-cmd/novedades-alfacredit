// ==========================================
// ALFACREDIT S.A. - APP JS EN TIEMPO REAL
// ==========================================

const LEYENDA_MAP = {
    "V": -1.0,
    "MD": -0.5,
    "S": -1.0,
    "B": -1.0,
    "F": -1.0,
    "N": 1.0,
    "R": 1.0,
    "PE": 0.0
};

// WebSocket Connection
let ws = null;
function initWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/status`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log("⚡ Conectado al servidor en tiempo real (WebSockets)");
        const liveDot = document.getElementById("ws-live-indicator");
        if (liveDot) liveDot.classList.remove("hidden");
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            if (msg.type === "STATUS_UPDATE" && msg.data) {
                updateDashboardStatus(msg.data);
            }
        } catch (e) {
            console.error("Error procesando mensaje WS:", e);
        }
    };

    ws.onclose = () => {
        console.log("Desconectado de WebSockets. Reintentando en 3s...");
        setTimeout(initWebSocket, 3000);
    };
}

function updateDashboardStatus(data) {
    // 1. Actualizar barra de estado / stepper
    const step1 = document.getElementById("step-1");
    const step2 = document.getElementById("step-2");
    const step3 = document.getElementById("step-3");
    const step4 = document.getElementById("step-4");

    if (step1 && step2 && step3 && step4) {
        [step1, step2, step3, step4].forEach(s => s.className = "step-item");
        
        if (data.fase_actual === 1) {
            step1.classList.add("active");
        } else if (data.fase_actual === 2) {
            step1.classList.add("completed");
            step2.classList.add("active");
        } else if (data.fase_actual === 3) {
            step1.classList.add("completed");
            step2.classList.add("completed");
            step3.classList.add("active");
        } else if (data.fase_actual === 4) {
            step1.classList.add("completed");
            step2.classList.add("completed");
            step3.classList.add("completed");
            step4.classList.add("completed");
        }
    }

    // 2. Banner de Atraso
    const delayBanner = document.getElementById("delay-banner");
    const delayMsg = document.getElementById("delay-message");
    const delayResp = document.getElementById("delay-responsible");
    if (delayBanner && delayMsg) {
        delayBanner.className = `delay-banner ${data.nivel_alerta} mb-6 flex items-start space-x-3`;
        delayMsg.innerText = data.atraso_mensaje;
        if (delayResp) delayResp.innerText = data.atraso_responsable;
    }

    // 3. Progreso de Gerentes
    const progBar = document.getElementById("gerentes-progress-bar");
    const progText = document.getElementById("gerentes-progress-text");
    if (progBar) progBar.style.width = `${data.porcentaje_gerentes}%`;
    if (progText) progText.innerText = `${data.conteo_enviadas} de ${data.total_sucursales} Sucursales (${data.porcentaje_gerentes}%)`;

    // 4. Totales monetarios
    const totalBase = document.getElementById("kpi-total-base");
    const totalNeto = document.getElementById("kpi-total-neto");
    if (totalBase) totalBase.innerText = `C$ ${data.suma_total_base.toLocaleString('es-NI', {minimumFractionDigits: 2})}`;
    if (totalNeto) totalNeto.innerText = `C$ ${data.suma_total_neto.toLocaleString('es-NI', {minimumFractionDigits: 2})}`;
}

// LÓGICA DE CÁLCULO DE NOVEDADES EN LA TABLA
function recalcularFila(rowElement) {
    const baseInput = rowElement.querySelector(".combustible-base");
    const base = parseFloat(baseInput ? baseInput.value : 0) || 0;

    const selects = rowElement.querySelectorAll(".novedad-select");
    let factorTotal = 0;

    selects.forEach(sel => {
        const val = sel.value;
        sel.className = "novedad-select";
        if (["V", "F", "B", "MD", "S"].includes(val)) {
            sel.classList.add("deduccion");
        } else if (["N", "R"].includes(val)) {
            sel.classList.add("adicion");
        }
        if (val in LEYENDA_MAP) {
            factorTotal += LEYENDA_MAP[val];
        }
    });

    const ajusteDinero = base > 0 ? (base / 6.0) * factorTotal : 0;
    const totalNeto = Math.max(0, base + ajusteDinero);

    const dedElement = rowElement.querySelector(".display-deduccion");
    const netoElement = rowElement.querySelector(".display-neto");

    if (dedElement) {
        dedElement.innerText = Math.abs(ajusteDinero).toLocaleString('es-NI', {minimumFractionDigits: 2});
        dedElement.className = "display-deduccion money-display font-bold " + (factorTotal < 0 ? "text-red-600" : (factorTotal > 0 ? "text-green-600" : "text-gray-700"));
    }

    if (netoElement) {
        netoElement.innerText = totalNeto.toLocaleString('es-NI', {minimumFractionDigits: 2});
    }

    recalcularGranTotal();
}

function recalcularGranTotal() {
    let totalBase = 0;
    let totalNeto = 0;
    let totalEmpleados = 0;

    document.querySelectorAll("#novedades-tbody tr").forEach(tr => {
        totalEmpleados++;
        const base = parseFloat(tr.querySelector(".combustible-base")?.value || 0) || 0;
        const netoStr = tr.querySelector(".display-neto")?.innerText.replace(/,/g, '') || "0";
        const neto = parseFloat(netoStr) || 0;

        totalBase += base;
        totalNeto += neto;
    });

    const lblGranNeto = document.getElementById("gran-total-neto");
    const lblGranBase = document.getElementById("gran-total-base");
    const lblTotalEmp = document.getElementById("total-empleados-conteo");

    if (lblGranNeto) lblGranNeto.innerText = `C$ ${totalNeto.toLocaleString('es-NI', {minimumFractionDigits: 2})}`;
    if (lblGranBase) lblGranBase.innerText = `C$ ${totalBase.toLocaleString('es-NI', {minimumFractionDigits: 2})}`;
    if (lblTotalEmp) lblTotalEmp.innerText = totalEmpleados;
}

// AGREGAR FILA DINÁMICA
function agregarFilaEmpleado() {
    const tbody = document.getElementById("novedades-tbody");
    if (!tbody) return;

    const rowIdx = tbody.children.length + 1;
    const tr = document.createElement("tr");
    tr.className = "border-b border-gray-200 hover:bg-slate-50 transition";
    tr.innerHTML = `
        <td class="p-2 text-center text-xs font-bold text-gray-500 row-number">${rowIdx}</td>
        <td class="p-2">
            <input type="text" class="input-nombre w-full border border-gray-300 rounded px-2 py-1 text-sm font-semibold focus:ring-1 focus:ring-blue-500" placeholder="Nombre completo...">
        </td>
        <td class="p-2">
            <select class="input-cargo w-full border border-gray-300 rounded px-2 py-1 text-xs font-medium">
                <option value="OFICIAL DE CREDITO">OFICIAL DE CREDITO</option>
                <option value="SUPERVISOR">SUPERVISOR</option>
                <option value="OFICIAL DE DESEMBOLSO">OFICIAL DE DESEMBOLSO</option>
                <option value="PROMOTOR INTERNO">PROMOTOR INTERNO</option>
                <option value="RESP. OPERACIONES">RESP. OPERACIONES</option>
                <option value="CONSERJE">CONSERJE</option>
                <option value="GERENTE SUCURSAL">GERENTE SUCURSAL</option>
            </select>
        </td>
        ${[0,1,2,3,4,5].map(() => `
            <td class="p-1 text-center bg-orange-50/40">
                <select class="novedad-select" onchange="recalcularFila(this.closest('tr'))">
                    <option value=""></option>
                    <option value="V">V</option>
                    <option value="MD">MD</option>
                    <option value="S">S</option>
                    <option value="B">B</option>
                    <option value="F">F</option>
                    <option value="N">N</option>
                    <option value="R">R</option>
                    <option value="PE">PE</option>
                </select>
            </td>
        `).join('')}
        ${[0,1,2,3,4,5].map(() => `
            <td class="p-1 text-center bg-green-50/40">
                <select class="novedad-select" onchange="recalcularFila(this.closest('tr'))">
                    <option value=""></option>
                    <option value="V">V</option>
                    <option value="MD">MD</option>
                    <option value="S">S</option>
                    <option value="B">B</option>
                    <option value="F">F</option>
                    <option value="N">N</option>
                    <option value="R">R</option>
                    <option value="PE">PE</option>
                </select>
            </td>
        `).join('')}
        <td class="p-2">
            <select class="input-estado w-full border border-gray-300 rounded px-1 py-1 text-xs font-bold text-center">
                <option value="ACTIVO">ACTIVO</option>
                <option value="NUEVO">NUEVO</option>
                <option value="BAJA">BAJA</option>
                <option value="SUBSIDIO">SUBSIDIO</option>
            </select>
        </td>
        <td class="p-2">
            <input type="number" step="0.5" value="0.0" class="combustible-base combustible-input w-20 px-2 py-1 text-xs rounded text-center font-bold" oninput="recalcularFila(this.closest('tr'))">
        </td>
        <td class="p-2 text-center bg-slate-50">
            <span class="display-deduccion money-display text-xs">0.00</span>
        </td>
        <td class="p-2 text-center bg-emerald-50">
            <span class="display-neto money-display text-xs font-bold text-emerald-800">0.00</span>
        </td>
        <td class="p-2">
            <input type="text" class="input-talla w-12 border border-gray-300 rounded px-1 py-1 text-xs text-center uppercase" placeholder="Talla">
        </td>
        <td class="p-2">
            <input type="text" class="input-req w-full border border-gray-300 rounded px-2 py-1 text-xs" placeholder="Req...">
        </td>
        <td class="p-2">
            <input type="text" class="input-obs w-full border border-gray-300 rounded px-2 py-1 text-xs" placeholder="Obs...">
        </td>
        <td class="p-2 text-center">
            <button type="button" onclick="eliminarFila(this)" class="text-red-500 hover:text-red-700 font-bold p-1">✖</button>
        </td>
    `;
    tbody.appendChild(tr);
    recalcularFila(tr);
}

function eliminarFila(btn) {
    const tr = btn.closest("tr");
    const nombre = tr.querySelector(".input-nombre")?.value || "este colaborador";
    Swal.fire({
        title: "¿Eliminar colaborador?",
        text: `¿Deseas eliminar a ${nombre}?`,
        icon: "warning",
        showCancelButton: true,
        confirmButtonColor: "#d33",
        cancelButtonColor: "#3085d6",
        confirmButtonText: "Sí, eliminar",
        cancelButtonText: "Cancelar"
    }).then((result) => {
        if (result.isConfirmed) {
            tr.remove();
            renumerarFilas();
            recalcularGranTotal();
        }
    });
}

function renumerarFilas() {
    document.querySelectorAll("#novedades-tbody tr").forEach((tr, idx) => {
        const numCol = tr.querySelector(".row-number");
        if (numCol) numCol.innerText = idx + 1;
    });
}

// GUARDAR O ENVIAR REPORTE
async function guardarReporte(reporteId, esEnvioFinal = false) {
    const filas = [];
    document.querySelectorAll("#novedades-tbody tr").forEach(tr => {
        const nombre = tr.querySelector(".input-nombre")?.value.trim();
        if (!nombre) return;

        const cargo = tr.querySelector(".input-cargo")?.value;
        const estado = tr.querySelector(".input-estado")?.value;
        const base = parseFloat(tr.querySelector(".combustible-base")?.value || 0) || 0;
        const talla = tr.querySelector(".input-talla")?.value || "";
        const req = tr.querySelector(".input-req")?.value || "";
        const obs = tr.querySelector(".input-obs")?.value || "";

        const selects = Array.from(tr.querySelectorAll(".novedad-select"));
        const diasPas = selects.slice(0, 6).map(s => s.value);
        const diasFut = selects.slice(6, 12).map(s => s.value);

        filas.push({
            nombre: nombre,
            cargo: cargo,
            estado: estado,
            combustible_base: base,
            dias_pas: diasPas,
            dias_fut: diasFut,
            talla: talla,
            req: req,
            obs: obs
        });
    });

    if (esEnvioFinal && filas.length === 0) {
        Swal.fire("Atención", "Debes ingresar al menos un colaborador antes de enviar.", "warning");
        return;
    }

    if (esEnvioFinal) {
        const confirm = await Swal.fire({
            title: "¿Enviar Informe de Novedades Oficial?",
            text: "Una vez enviado, el coordinador Brandon podrá consolidar la solicitud de combustible.",
            icon: "question",
            showCancelButton: true,
            confirmButtonColor: "#27ae60",
            cancelButtonColor: "#94a3b8",
            confirmButtonText: "Sí, enviar ahora",
            cancelButtonText: "Revisar más tarde"
        });
        if (!confirm.isConfirmed) return;
    }

    Swal.fire({
        title: "Procesando...",
        text: "Guardando cambios y recalculando deducciones",
        allowOutsideClick: false,
        didOpen: () => Swal.showLoading()
    });

    try {
        const res = await fetch("/api/novedades/save", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({
                reporte_id: reporteId,
                filas: filas,
                es_envio_final: esEnvioFinal
            })
        });
        const data = await res.json();
        if (data.success) {
            Swal.fire({
                icon: "success",
                title: esEnvioFinal ? "¡Reporte Enviado con Éxito!" : "¡Borrador Guardado!",
                text: data.message,
                timer: 2000,
                showConfirmButton: false
            }).then(() => {
                if (esEnvioFinal) window.location.href = "/dashboard";
            });
        } else {
            Swal.fire("Error", data.detail || "No se pudo guardar", "error");
        }
    } catch (e) {
        Swal.fire("Error de Red", "No se pudo comunicar con el servidor: " + e, "error");
    }
}

// SINCRONIZAR CORREO MANUALMENTE
async function sincronizarCorreo() {
    Swal.fire({
        title: "Escaneando Correo de Brandon...",
        text: "Conectando al servidor IMAP para auditar solicitudes y comprobantes",
        allowOutsideClick: false,
        didOpen: () => Swal.showLoading()
    });

    try {
        const res = await fetch("/api/email/sync", {method: "POST"});
        const data = await res.json();
        if (data.success) {
            Swal.fire({
                icon: "success",
                title: "Sincronización Exitosa",
                text: data.mensaje,
                confirmButtonColor: "#154360"
            });
        } else {
            Swal.fire({
                icon: "warning",
                title: "Aviso de Sincronización",
                text: data.message,
                confirmButtonColor: "#d35400"
            });
        }
    } catch (e) {
        Swal.fire("Error", "Fallo al conectar con el servicio de correo.", "error");
    }
}

// CREAR RESPALDO COMPLETO
async function crearRespaldo() {
    Swal.fire({
        title: "Generando Respaldo Integral...",
        text: "Comprimiendo base de datos, volcado JSON y comprobantes bancarios en .ZIP",
        allowOutsideClick: false,
        didOpen: () => Swal.showLoading()
    });

    try {
        const res = await fetch("/api/backups/create", {method: "POST"});
        const data = await res.json();
        if (data.success) {
            Swal.fire({
                icon: "success",
                title: "¡Respaldo Generado!",
                html: `Archivo: <b>${data.filename}</b><br>Tamaño: <b>${data.size_kb} KB</b>`,
                confirmButtonColor: "#27ae60"
            }).then(() => location.reload());
        } else {
            Swal.fire("Error", data.message || "Error al crear respaldo", "error");
        }
    } catch (e) {
        Swal.fire("Error", "No se pudo generar el respaldo: " + e, "error");
    }
}

// RESTAURAR RESPALDO
async function restaurarRespaldo(filename) {
    const confirm = await Swal.fire({
        title: "¿Restaurar Respaldo del Sistema?",
        html: `Se restaurará toda la información al estado del archivo <b>${filename}</b>.<br><span class="text-red-600 font-bold">¡Esta acción sobreescribirá los datos actuales!</span>`,
        icon: "warning",
        showCancelButton: true,
        confirmButtonColor: "#d33",
        cancelButtonColor: "#3085d6",
        confirmButtonText: "Sí, restaurar ahora",
        cancelButtonText: "Cancelar"
    });

    if (!confirm.isConfirmed) return;

    Swal.fire({
        title: "Restaurando...",
        text: "Descomprimiendo y aplicando estado del sistema",
        allowOutsideClick: false,
        didOpen: () => Swal.showLoading()
    });

    try {
        const res = await fetch(`/api/backups/restore/${filename}`, {method: "POST"});
        const data = await res.json();
        if (data.success) {
            Swal.fire({
                icon: "success",
                title: "¡Sistema Restaurado!",
                text: data.message,
                confirmButtonColor: "#154360"
            }).then(() => location.reload());
        } else {
            Swal.fire("Error de Restauración", data.message, "error");
        }
    } catch (e) {
        Swal.fire("Error", "Error al restaurar: " + e, "error");
    }
}

// INICIAR AL CARGAR
document.addEventListener("DOMContentLoaded", () => {
    initWebSocket();
    recalcularGranTotal();
});
