const $ = (id) => document.getElementById(id);
const LS_KEY = "hafid_pos_borrador";
const LS_VEND = "hafid_pos_vendedor";

let debounceTimer = null;
let cliTimer = null;
let busquedaPresuActiva = false;
let presupuestoCargadoId = null;
let presupuestoCargadoNro = null;
let lastResultados = [];
let usuarioSesion = null;
let eventosPosVinculados = false;
let ultimoTotal = 0;
let cambioInicialPendiente = null;

function parseBusqueda(raw) {
  const t = String(raw || "").trim();
  const m = t.match(/^(.*)\*(\d+)\s*$/);
  if (m && m[1].trim()) {
    return { q: m[1].trim(), cant: Math.max(1, parseInt(m[2], 10) || 1), star: true };
  }
  return { q: t, cant: null, star: false };
}

function money(n) {
  return Number(n || 0).toLocaleString("es-AR", {
    style: "currency",
    currency: "ARS",
  });
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const raw = await res.text();
  let data = {};
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch (_) {
    data = {};
  }
  if (!res.ok) {
    const d = data.detail;
    const msg = Array.isArray(d)
      ? d.map((x) => x.msg || JSON.stringify(x)).join("; ")
      : d || data.message || raw || `Error HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

function aplicarSesion(usuario) {
  usuarioSesion = usuario;
  $("loginOverlay").hidden = true;
  $("usuarioBox").hidden = false;
  $("usuarioNombre").textContent = `${usuario.nombre} · ${usuario.rol}`;
  const esVendedor = usuario.rol === "vendedor";
  const esAdmin = usuario.rol === "admin";
  $("vendedor").readOnly = esVendedor;
  if (esVendedor) {
    $("vendedor").value = String(usuario.vendedor_id || usuario.usuario).toUpperCase();
  }
  $("panelAdminPuntos").hidden = !esAdmin;
  $("panelAdminUsuarios").hidden = !esAdmin;
  $("railSinStock").hidden = !esAdmin;
  const railPend = $("railPendientes");
  if (railPend) railPend.hidden = false;
  document.body.classList.toggle("admin-mode", esAdmin);
  document.body.classList.toggle("shared-rails", true);
  if (esAdmin) {
    cargarAlertasAdmin().catch(() => {});
  }
  cargarPendientesAlta().catch(() => {});
}

async function cargarPuntosAdmin() {
  if (!usuarioSesion || usuarioSesion.rol !== "admin") return;
  const data = await api("/api/admin/puntos");
  const box = $("listaPuntos");
  box.innerHTML = (data.resultados || [])
    .map(
      (v) => `
      <div class="presu-row">
        <div>
          <div class="cod">${v.nombre}</div>
          <div class="meta">Acumulado actual: ${money(v.ventas_acumuladas)} · faltan ${money(v.faltan_proximo)}</div>
        </div>
        <strong>${v.puntos} punto(s)</strong>
      </div>`
    )
    .join("") || '<div class="empty">No hay vendedores registrados.</div>';
}

async function cargarUsuariosAdmin() {
  if (!usuarioSesion || usuarioSesion.rol !== "admin") return;
  const data = await api("/api/admin/usuarios");
  $("listaUsuarios").innerHTML = (data.resultados || [])
    .map(
      (u) => `
      <div class="presu-row">
        <div>
          <div class="cod">${u.usuario}</div>
          <div class="meta">${u.nombre} · ${u.rol}</div>
        </div>
      </div>`
    )
    .join("") || '<div class="empty">No hay usuarios.</div>';
}

function renderRail(boxId, items) {
  const box = $(boxId);
  if (!box) return;
  box.innerHTML = (items || [])
    .map((a) => {
      const it = a.item || {};
      const titulo = it.codigo || it.descripcion || "Ítem";
      const stockTxt = it.stock == null ? "s/d" : it.stock;
      const precioTxt = it.precio_unitario
        ? ` · ${money(it.precio_unitario)}`
        : "";
      const marcaTxt = it.marca ? ` · ${it.marca}` : "";
      return `
      <div class="rail-item">
        <div class="cod">${titulo}</div>
        <div class="meta">${it.descripcion || ""}${marcaTxt}${precioTxt}</div>
        <div class="meta">${a.vendedor || ""} · stock ${stockTxt}</div>
        <button type="button" class="ghost" data-alerta="${a.id}">Visto</button>
      </div>`;
    })
    .join("") || '<div class="empty">Sin pendientes</div>';
}

function escAttr(v) {
  return String(v || "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;");
}

function renderPendientesAlta(items) {
  const box = $("listaPendientes");
  if (!box) return;
  box.innerHTML = (items || [])
    .map((p) => {
      const fc = p.nro_factura ? `FC ${p.nro_factura}` : "Sin facturar aún";
      return `
      <div class="rail-item pendiente-item" data-pid="${escAttr(p.id)}">
        <div class="cod">${escAttr(p.descripcion) || "Ítem manual"}</div>
        <div class="meta">${money(p.precio_unitario)} · x${p.cantidad || 1}${
          p.marca ? ` · ${escAttr(p.marca)}` : ""
        }</div>
        <div class="meta">${escAttr(p.vendedor)} · ${fc}</div>
        <button type="button" class="ghost btn-toggle-cargar" data-pid="${escAttr(p.id)}">
          Cargar definitivo
        </button>
        <div class="alta-inline" hidden data-form="${escAttr(p.id)}">
          <input class="pend-cod" type="text" placeholder="Código *" autocomplete="off" />
          <input class="pend-marca" type="text" placeholder="Marca" value="${escAttr(p.marca || "GENERICO")}" />
          <input class="pend-precio" type="number" min="0.01" step="0.01" placeholder="Precio"
            value="${Number(p.precio_unitario || 0)}" />
          <input class="pend-stock" type="number" min="0" value="0" placeholder="Stock" />
          <button type="button" class="primary btn-confirmar-alta" data-pid="${escAttr(p.id)}"
            data-desc="${escAttr(p.descripcion)}">
            Guardar en inventario
          </button>
        </div>
      </div>`;
    })
    .join("") || '<div class="empty">Sin pendientes de carga</div>';

  box.querySelectorAll(".btn-toggle-cargar").forEach((btn) => {
    btn.addEventListener("click", () => {
      const form = box.querySelector(`[data-form="${btn.dataset.pid}"]`);
      if (form) form.hidden = !form.hidden;
    });
  });
  box.querySelectorAll(".btn-confirmar-alta").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const wrap = btn.closest(".pendiente-item");
      if (!wrap) return;
      const codigo = wrap.querySelector(".pend-cod")?.value?.trim() || "";
      const marca = wrap.querySelector(".pend-marca")?.value?.trim() || "GENERICO";
      const precio = parseFloat(wrap.querySelector(".pend-precio")?.value || "0") || 0;
      const stock = Math.max(0, parseInt(wrap.querySelector(".pend-stock")?.value || "0", 10));
      if (!codigo || codigo.length < 2) {
        showMsg("Código obligatorio (mín. 2 caracteres)", true);
        return;
      }
      if (precio <= 0) {
        showMsg("Precio inválido", true);
        return;
      }
      try {
        btn.disabled = true;
        const r = await api(`/api/pendientes-alta/${encodeURIComponent(btn.dataset.pid)}/cargar`, {
          method: "POST",
          body: JSON.stringify({
            codigo,
            descripcion: btn.dataset.desc || "",
            precio_venta: precio,
            marca,
            stock,
            agregar_carrito: false,
          }),
        });
        showMsg(r.mensaje || "Producto cargado");
        await cargarPendientesAlta();
        if (usuarioSesion && usuarioSesion.rol === "admin") {
          cargarAlertasAdmin().catch(() => {});
        }
      } catch (err) {
        showMsg(err.message, true);
      } finally {
        btn.disabled = false;
      }
    });
  });
}

async function cargarAlertasAdmin() {
  if (!usuarioSesion || usuarioSesion.rol !== "admin") return;
  const data = await api("/api/admin/alertas");
  const rows = data.resultados || [];
  renderRail(
    "listaSinStock",
    rows.filter((r) => r.tipo === "sin_stock")
  );
  renderRail(
    "listaAltas",
    rows.filter((r) => r.tipo === "alta_pos")
  );
}

async function cargarPendientesAlta() {
  if (!usuarioSesion) return;
  try {
    const data = await api("/api/pendientes-alta");
    renderPendientesAlta(data.resultados || []);
  } catch (_) {
    renderPendientesAlta([]);
  }
}

function marcarPresetActivo() {
  const cuotas = parseInt($("tarjetaCuotas").value || "1", 10);
  const interes = parseFloat($("tarjetaInteres").value || "0");
  document.querySelectorAll("#tarjetaPresets button").forEach((btn) => {
    const c = parseInt(btn.dataset.cuotas || "0", 10);
    const i = parseFloat(btn.dataset.interes || "0");
    btn.classList.toggle("active", c === cuotas && i === interes);
  });
}

function actualizarFinanciacion() {
  const esTarjeta = $("formaPago").value === "Tarjeta";
  $("tarjetaOpts").hidden = !esTarjeta;
  const cuotas = esTarjeta
    ? Math.max(1, parseInt($("tarjetaCuotas").value || "1", 10))
    : 1;
  const interes = esTarjeta
    ? Math.max(0, parseFloat($("tarjetaInteres").value || "0"))
    : 0;
  const total = ultimoTotal * (1 + interes / 100);
  $("totalFinanciado").textContent = money(total);
  $("valorCuota").textContent = `${cuotas} cuota(s) de ${money(total / cuotas)}`;
  marcarPresetActivo();
}

function payloadVenta() {
  const pago = $("formaPago").value || "Contado";
  const cuotas = pago === "Tarjeta"
    ? Math.max(1, parseInt($("tarjetaCuotas").value || "1", 10))
    : 1;
  const interes = pago === "Tarjeta"
    ? Math.max(0, parseFloat($("tarjetaInteres").value || "0"))
    : 0;
  return {
    forma_pago: pago,
    observacion: $("nota").value || "",
    cuotas,
    interes_pct: interes,
    cliente: clienteFormPayload(),
    vendedor: $("vendedor").value || "CAJA",
    confirmar: true,
    permitir_sin_stock: Boolean($("chkSinStock").checked),
  };
}

function showMsg(text, err = false) {
  const el = $("msg");
  el.hidden = false;
  el.className = "msg" + (err ? " err" : "");
  el.textContent = text;
}

function ventanaEspera(texto) {
  const w = window.open("", "_blank");
  if (w) {
    w.document.write(
      `<title>HAFID</title><body style="font:700 20px Segoe UI;padding:40px;color:#1e3a8a">${texto}</body>`
    );
  }
  return w;
}

function abrirPdfBase64(b64, nombre, ventana) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], { type: "application/pdf" });
  const url = URL.createObjectURL(blob);
  const w = ventana || window.open("", "_blank");
  if (!w) {
    showMsg("El navegador bloqueó la impresión. Habilitá ventanas emergentes.", true);
    return;
  }
  // Visor nativo del navegador + diálogo de impresora (sin descargar).
  try {
    w.location.replace(url);
  } catch (_) {
    w.location.href = url;
  }
  let intentos = 0;
  const intentarImprimir = () => {
    intentos += 1;
    try {
      w.focus();
      w.print();
    } catch (_) {}
    if (intentos < 3) setTimeout(intentarImprimir, 700);
  };
  setTimeout(intentarImprimir, 500);
  setTimeout(() => {
    try {
      URL.revokeObjectURL(url);
    } catch (_) {}
  }, 180000);
}

function abrirTicketHtml(html, ventana) {
  const w = ventana || window.open("", "_blank");
  if (!w) {
    showMsg("El navegador bloqueó la impresión. Habilitá ventanas emergentes.", true);
    return;
  }
  if (!String(html).includes("window.print")) {
    html = String(html).replace(
      "</body>",
      "<script>window.addEventListener('afterprint',function(){window.close()});window.onload=function(){setTimeout(function(){window.print()},100)}</script></body>"
    );
  }
  w.document.open();
  w.document.write(html);
  w.document.close();
}

function guardarBorrador(data) {
  try {
    localStorage.setItem(
      LS_KEY,
      JSON.stringify({
        items: data.items || [],
        cliente: data.cliente || null,
        nota: $("nota")?.value || data.nota || "",
        vendedor: $("vendedor")?.value || data.vendedor || "CAJA",
      })
    );
  } catch (_) {}
}

function leerBorrador() {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || "null");
  } catch (_) {
    return null;
  }
}

function limpiarBorrador() {
  try {
    localStorage.removeItem(LS_KEY);
  } catch (_) {}
}

function clienteFormPayload() {
  return {
    nombre: $("cliNombre").value || "CONSUMIDOR FINAL",
    cuit: $("cliCuit").value || "",
    tipo_comprobante: $("cliTipo").value || "6",
    descuento: parseFloat($("cliDesc").value || "0") || 0,
    condicion_iva:
      ($("cliTipo").dataset.condicionIva || "").trim()
      || ($("cliTipo").value === "1" ? "RESPONSABLE INSCRIPTO" : "CONSUMIDOR FINAL"),
  };
}

async function syncCliente() {
  await api("/api/cliente", {
    method: "PUT",
    body: JSON.stringify(clienteFormPayload()),
  });
}

async function syncVendedor() {
  const v = ($("vendedor").value || "CAJA").trim().toUpperCase() || "CAJA";
  $("vendedor").value = v;
  try {
    localStorage.setItem(LS_VEND, v);
  } catch (_) {}
  await api("/api/vendedor", {
    method: "PUT",
    body: JSON.stringify({ vendedor: v }),
  });
}

function setCuitHint(text, ok) {
  const el = $("cuitHint");
  if (!el) return;
  if (!text) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = text;
  el.className = "cuit-hint " + (ok === true ? "ok" : ok === false ? "err" : "");
}

async function resolverCuitCampo() {
  const raw = ($("cliCuit").value || "").replace(/\D/g, "");
  if (raw.length !== 11) {
    if (raw.length > 0 && raw.length !== 11) {
      setCuitHint("CUIT: 11 dígitos para validar", false);
    }
    return;
  }
  setCuitHint("Consultando…", null);
  try {
    const r = await api(`/api/clientes/cuit/${raw}`);
    const cli = r.cliente || {};
    aplicarClienteEnForm(cli);
    if (cli.condicion_iva) {
      $("cliTipo").dataset.condicionIva = cli.condicion_iva;
    }
    await syncCliente();
    await refreshCarrito();
    setCuitHint(r.mensaje || "Cliente cargado", true);
  } catch (err) {
    setCuitHint(err.message || "No se pudo validar", false);
  }
}

function aplicarClienteEnForm(cli) {
  if (!cli) return;
  $("cliNombre").value = cli.nombre || "CONSUMIDOR FINAL";
  $("cliCuit").value = cli.cuit && cli.cuit !== "00000000000" ? cli.cuit : "";
  $("cliDesc").value = String(cli.descuento || 0);
  $("cliTipo").value = String(cli.tipo_comprobante || cli.cbte_tipo || "6");
  if (cli.condicion_iva) {
    $("cliTipo").dataset.condicionIva = cli.condicion_iva;
  }
}

function renderCliHits(lista) {
  const box = $("cliHits");
  if (!lista.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = lista
    .map(
      (c) => `
    <button type="button" data-cuit="${c.cuit}">
      <div>${c.nombre}</div>
      <div class="meta">${c.cuit} · desc ${c.descuento}%</div>
    </button>`
    )
    .join("");
  box.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const c = lista.find((x) => x.cuit === btn.dataset.cuit);
      if (!c) return;
      aplicarClienteEnForm(c);
      $("cliBusca").value = c.nombre;
      box.hidden = true;
      await syncCliente();
      await refreshCarrito();
      showMsg(`Cliente ${c.nombre}`);
    });
  });
}

function renderResultados(lista) {
  lastResultados = lista || [];
  const box = $("resultados");
  if (!lista.length) {
    box.innerHTML = '<div class="empty">Sin coincidencias</div>';
    return;
  }
  box.innerHTML = lista
    .map((p) => {
      const badge =
        (p.variantes_mismo_codigo || 0) > 1
          ? `<span class="badge-var">${p.variantes_mismo_codigo} marcas</span>`
          : "";
      const stockBajo = Number(p.stock) <= 2;
      return `
    <button type="button" class="res${stockBajo ? " stock-bajo" : ""}" data-id="${p.id}">
      <div>
        <div class="cod">${p.codigo} · ${p.marca}${badge}</div>
        <div class="desc">${p.descripcion}</div>
        <div class="meta">${p.vehiculo || ""} · stock ${p.stock}${stockBajo ? " · bajo" : ""}</div>
      </div>
      <div class="precio">${money(p.precio_venta)}</div>
    </button>`;
    })
    .join("");
  box.querySelectorAll(".res").forEach((btn) => {
    btn.addEventListener("click", () => addItem(btn.dataset.id));
  });
}

function actualizarBannerPresu() {
  const b = $("bannerPresu");
  const btn = $("btnPresu");
  if (presupuestoCargadoId && presupuestoCargadoNro) {
    b.hidden = false;
    b.textContent = `Editando presupuesto Nº ${String(presupuestoCargadoNro).padStart(4, "0")} · F4 actualiza el mismo número`;
    btn.textContent = `Actualizar e imprimir Nº ${String(presupuestoCargadoNro).padStart(4, "0")}`;
  } else {
    b.hidden = true;
    b.textContent = "";
    btn.textContent = "Imprimir presupuesto";
  }
}

function renderCarrito(data) {
  const items = data.items || [];
  presupuestoCargadoId = data.presupuesto_cargado_id || null;
  presupuestoCargadoNro = data.presupuesto_cargado_nro || null;
  actualizarBannerPresu();
  const box = $("items");
  if (!items.length) {
    box.innerHTML = '<div class="empty">Carrito vacío — buscá arriba y tocá un resultado</div>';
  } else {
    box.innerHTML = items
      .map((i) => {
        const stock = i.stock;
        const sinStock =
          stock != null && !i.manual && Number(i.cantidad) > Number(stock);
        const stockTxt =
          stock == null || i.manual
            ? ""
            : sinStock
              ? `<div class="meta stock-warn">Pedido ${i.cantidad} · stock ${stock}</div>`
              : `<div class="meta">stock ${stock}</div>`;
        return `
      <div class="item${sinStock ? " warn" : ""}">
        <div>
          <div class="cod">${i.codigo || "MANUAL"} · ${i.marca}</div>
          <input class="desc-edit" type="text" value="${String(i.descripcion || "").replace(/"/g, "&quot;")}"
            data-id="${i.id}" title="Descripción" />
          ${stockTxt}
          <label class="precio-label">
            Precio unitario (editable)
            <span class="precio-control">
              <span>$</span>
              <input class="precio-edit" type="number" min="0" step="0.01"
                value="${Number(i.precio_unitario).toFixed(2)}"
                data-id="${i.id}" title="Precio unitario" />
            </span>
          </label>
        </div>
        <div class="qty">
          <button type="button" data-act="-" data-id="${i.id}">−</button>
          <input class="cant-edit" type="number" min="1" value="${i.cantidad}" data-id="${i.id}" />
          <button type="button" data-act="+" data-id="${i.id}">+</button>
          <button type="button" data-act="x" data-id="${i.id}" title="Quitar">✕</button>
        </div>
      </div>`;
      })
      .join("");
    box.querySelectorAll("button[data-act]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.dataset.id;
        const item = items.find((x) => x.id === id);
        if (!item) return;
        if (btn.dataset.act === "x") {
          await api(`/api/carrito/items/${encodeURIComponent(id)}`, { method: "DELETE" });
        } else {
          const cant = btn.dataset.act === "+" ? item.cantidad + 1 : item.cantidad - 1;
          await api(`/api/carrito/items/${encodeURIComponent(id)}`, {
            method: "PATCH",
            body: JSON.stringify({ cantidad: cant }),
          });
        }
        await refreshCarrito();
      });
    });
    box.querySelectorAll(".precio-edit").forEach((inp) => {
      const guardarPrecio = async () => {
        await api(`/api/carrito/items/${encodeURIComponent(inp.dataset.id)}`, {
          method: "PATCH",
          body: JSON.stringify({ precio_unitario: parseFloat(inp.value || "0") }),
        });
        await refreshCarrito();
        showMsg("Precio actualizado para este presupuesto");
      };
      inp.addEventListener("change", guardarPrecio);
      inp.addEventListener("keydown", async (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        await guardarPrecio();
      });
    });
    box.querySelectorAll(".desc-edit").forEach((inp) => {
      inp.addEventListener("change", async () => {
        await api(`/api/carrito/items/${encodeURIComponent(inp.dataset.id)}`, {
          method: "PATCH",
          body: JSON.stringify({ descripcion: inp.value }),
        });
        await refreshCarrito();
      });
    });
    box.querySelectorAll(".cant-edit").forEach((inp) => {
      inp.addEventListener("change", async () => {
        await api(`/api/carrito/items/${encodeURIComponent(inp.dataset.id)}`, {
          method: "PATCH",
          body: JSON.stringify({ cantidad: Math.max(1, parseInt(inp.value || "1", 10)) }),
        });
        await refreshCarrito();
      });
    });
  }
  const t = data.totales || {};
  $("tItems").textContent = String(t.items || 0);
  $("tBruto").textContent = t.bruto_txt || money(0);
  $("tDesc").textContent = t.descuento_txt || money(0);
  $("tTotal").textContent = t.total_txt || money(0);
  ultimoTotal = Number(t.total || 0);
  actualizarFinanciacion();
  if (data.cliente) aplicarClienteEnForm(data.cliente);
  if (data.vendedor && $("vendedor") && document.activeElement !== $("vendedor")) {
    $("vendedor").value = data.vendedor;
  }
  if (
    data.nota != null &&
    $("nota") &&
    !$("nota").value &&
    document.activeElement !== $("nota")
  ) {
    $("nota").value = data.nota || "";
  }
  guardarBorrador(data);
}

function renderListaPresu(lista) {
  const box = $("listaPresu");
  if (!lista.length) {
    box.innerHTML = '<div class="empty">Sin presupuestos</div>';
    return;
  }
  box.innerHTML = lista
    .map((p) => {
      const anulado = String(p.estado || "") === "anulado";
      const acciones = anulado
        ? `<button type="button" data-act="pdf" data-id="${p.id}">PDF</button>`
        : `
        <button type="button" class="cargar" data-act="cargar" data-id="${p.id}">Cargar</button>
        <button type="button" data-act="dup" data-id="${p.id}">Duplicar</button>
        <button type="button" data-act="pdf" data-id="${p.id}">PDF</button>
        <button type="button" data-act="anular" data-id="${p.id}" title="Anular">✕</button>`;
      return `
    <div class="presu-row${anulado ? " anulado" : ""}">
      <div>
        <div class="cod">Nº ${p.numero_txt} · ${p.cliente}${
          anulado ? ' <span class="estado-anulado">ANULADO</span>' : ""
        }</div>
        <div class="meta">${p.creado} · ${p.cuit || "sin DNI/CUIT"} · ${p.items} ítem(s) · ${p.total_txt}</div>
      </div>
      <div class="presu-actions">${acciones}</div>
    </div>`;
    })
    .join("");
  box.querySelectorAll("button[data-act]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        if (btn.dataset.act === "cargar") {
          const r = await api(`/api/presupuestos/${encodeURIComponent(btn.dataset.id)}/cargar`, {
            method: "POST",
          });
          renderCarrito(r);
          showMsg(r.mensaje);
          $("q").focus();
        } else if (btn.dataset.act === "dup") {
          const r = await api(`/api/presupuestos/${encodeURIComponent(btn.dataset.id)}/duplicar`, {
            method: "POST",
          });
          renderCarrito(r);
          showMsg(r.mensaje);
          $("q").focus();
        } else if (btn.dataset.act === "pdf") {
          const r = await api(`/api/presupuestos/${encodeURIComponent(btn.dataset.id)}/pdf`, {
            method: "POST",
          });
          if (r.pdf_base64) abrirPdfBase64(r.pdf_base64, r.pdf_nombre);
          showMsg(r.mensaje || "PDF listo");
        } else if (btn.dataset.act === "anular") {
          if (!confirm("¿Anular este presupuesto?")) return;
          const r = await api(`/api/presupuestos/${encodeURIComponent(btn.dataset.id)}/anular`, {
            method: "POST",
          });
          showMsg(r.mensaje);
          await refreshPresupuestos();
        }
      } catch (err) {
        showMsg(err.message, true);
      }
    });
  });
}

async function refreshCarrito() {
  const data = await api("/api/carrito");
  renderCarrito(data);
  return data;
}

async function refreshPresupuestos(q) {
  const term = q != null ? q : $("presuQ")?.value || "";
  const anul = $("chkAnulados")?.checked ? "true" : "false";
  const desde = $("presuDesde")?.value || "";
  const hasta = $("presuHasta")?.value || "";
  if (!term.trim() && !desde && !hasta) {
    busquedaPresuActiva = false;
    $("listaPresu").innerHTML =
      '<div class="empty">Ingresá cliente, DNI/CUIT, número o fechas y tocá Buscar.</div>';
    return;
  }
  busquedaPresuActiva = true;
  const data = await api(
    `/api/presupuestos?q=${encodeURIComponent(term)}` +
      `&fecha_desde=${encodeURIComponent(desde)}` +
      `&fecha_hasta=${encodeURIComponent(hasta)}` +
      `&incluir_anulados=${anul}`
  );
  renderListaPresu(data.resultados || []);
}

function renderFacturas(lista) {
  const box = $("listaFacturas");
  if (!lista.length) {
    box.innerHTML = '<div class="empty">No se encontraron facturas.</div>';
    return;
  }
  box.innerHTML = lista
    .map(
      (f) => `
      <div class="presu-row">
        <div>
          <div class="cod">Factura ${f.letra} ${f.numero} · ${f.cliente}</div>
          <div class="meta">${f.fecha} · ${f.cuit || "sin DNI/CUIT"} · ${f.forma_pago} · ${f.total_txt} · CAE ${f.cae}</div>
        </div>
        <div class="presu-actions">
          <button type="button" data-ticket="${f.id}">Reimprimir</button>
        </div>
      </div>`
    )
    .join("");
  box.querySelectorAll("button[data-ticket]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const w = window.open("", "_blank");
      try {
        const r = await api(`/api/facturas/${encodeURIComponent(btn.dataset.ticket)}/ticket`);
        abrirTicketHtml(r.ticket_html, w);
      } catch (err) {
        if (w) w.close();
        showMsg(err.message, true);
      }
    });
  });
}

async function buscarFacturas() {
  const term = $("facturaQ").value || "";
  const desde = $("facturaDesde").value || "";
  const hasta = $("facturaHasta").value || "";
  if (!term.trim() && !desde && !hasta) {
    $("listaFacturas").innerHTML =
      '<div class="empty">Ingresá cliente, DNI/CUIT, número, CAE o fechas y tocá Buscar.</div>';
    return;
  }
  const data = await api(
    `/api/facturas?q=${encodeURIComponent(term)}` +
      `&fecha_desde=${encodeURIComponent(desde)}` +
      `&fecha_hasta=${encodeURIComponent(hasta)}`
  );
  renderFacturas(data.resultados || []);
}

async function addItem(id, cantOverride) {
  const cant = Math.max(
    1,
    cantOverride != null ? cantOverride : parseInt($("cant").value || "1", 10)
  );
  await api("/api/carrito/items", {
    method: "POST",
    body: JSON.stringify({ id, cantidad: cant }),
  });
  $("q").select();
  await refreshCarrito();
  showMsg(cant > 1 ? `Agregado x${cant}` : "Agregado al carrito");
}

async function buscar(raw) {
  const parsed = parseBusqueda(raw);
  try {
    if (parsed.cant) $("cant").value = String(parsed.cant);
    const data = await api(`/api/productos?q=${encodeURIComponent(parsed.q)}`);
    renderResultados(data.resultados || []);
    $("hint").textContent = parsed.q
      ? `${data.total} resultado(s)${parsed.star ? ` · cant ${parsed.cant}` : ""} · click o Enter`
      : "Escribí arriba. Tip: 111*3 agrega 3 unidades.";
  } catch (err) {
    renderResultados([]);
    $("hint").textContent = `No se pudo leer el inventario: ${err.message}`;
    showMsg(`Error de inventario: ${err.message}`, true);
  }
}

async function buscarClientes(q) {
  if (!q.trim()) {
    renderCliHits([]);
    return;
  }
  const data = await api(`/api/clientes?q=${encodeURIComponent(q)}`);
  renderCliHits(data.resultados || []);
}

function bind() {
  if (eventosPosVinculados) return;
  eventosPosVinculados = true;
  $("q").addEventListener("input", () => {
    clearTimeout(debounceTimer);
    const raw = $("q").value;
    debounceTimer = setTimeout(() => buscar(raw), 180);
  });

  $("q").addEventListener("keydown", async (e) => {
    if (
      e.key === "Tab" &&
      $("q").value.trim() &&
      !lastResultados.length
    ) {
      e.preventDefault();
      if ($("manDesc")) {
        $("manDesc").value = $("q").value.trim();
        $("manDesc").focus();
        $("manDesc").select();
      }
      return;
    }
    if (e.key !== "Enter") return;
    e.preventDefault();
    const parsed = parseBusqueda($("q").value);
    if (parsed.cant) $("cant").value = String(parsed.cant);
    if (!lastResultados.length && parsed.q) {
      await buscar($("q").value);
    }
    const first = lastResultados[0] || null;
    if (first) {
      await addItem(first.id, parsed.cant || undefined);
      $("q").value = "";
      $("resultados").innerHTML = "";
      lastResultados = [];
      $("cant").value = "1";
      $("hint").textContent = "Escribí arriba. Tip: 111*3 agrega 3 unidades.";
    }
  });

  $("cliBusca").addEventListener("input", () => {
    clearTimeout(cliTimer);
    cliTimer = setTimeout(() => buscarClientes($("cliBusca").value), 200);
  });

  $("presuQ").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    refreshPresupuestos();
  });

  $("btnBuscarPresu").addEventListener("click", () => refreshPresupuestos());

  $("btnLimpiarPresu").addEventListener("click", () => {
    $("presuQ").value = "";
    $("presuDesde").value = "";
    $("presuHasta").value = "";
    $("chkAnulados").checked = false;
    busquedaPresuActiva = false;
    $("listaPresu").innerHTML =
      '<div class="empty">Ingresá cliente, DNI/CUIT, número o fechas y tocá Buscar.</div>';
  });

  $("facturaQ").addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    buscarFacturas();
  });
  $("btnBuscarFactura").addEventListener("click", buscarFacturas);
  $("btnLimpiarFactura").addEventListener("click", () => {
    $("facturaQ").value = "";
    $("facturaDesde").value = "";
    $("facturaHasta").value = "";
    $("listaFacturas").innerHTML =
      '<div class="empty">Ingresá cliente, DNI/CUIT, número, CAE o fechas y tocá Buscar.</div>';
  });

  $("cliDesc").addEventListener("change", async () => {
    await syncCliente();
    await refreshCarrito();
  });
  $("cliTipo").addEventListener("change", syncCliente);

  $("btnGuardarCli").addEventListener("click", async () => {
    await syncCliente();
    await refreshCarrito();
    showMsg("Cliente / descuento aplicados");
  });

  $("btnManual").addEventListener("click", async () => {
    try {
      await api("/api/carrito/manual", {
        method: "POST",
        body: JSON.stringify({
          descripcion: $("manDesc").value,
          precio_unitario: parseFloat($("manPrecio").value || "0") || 0,
          cantidad: Math.max(1, parseInt($("manCant").value || "1", 10)),
        }),
      });
      $("manDesc").value = "";
      $("manPrecio").value = "";
      $("manCant").value = "1";
      await refreshCarrito();
      cargarPendientesAlta().catch(() => {});
      showMsg("Ítem manual agregado · quedó en pendientes de carga");
    } catch (err) {
      showMsg(err.message, true);
    }
  });

  $("btnAltaProd").addEventListener("click", async () => {
    try {
      $("btnAltaProd").disabled = true;
      const r = await api("/api/productos", {
        method: "POST",
        body: JSON.stringify({
          codigo: $("altaCod").value,
          descripcion: $("altaDesc").value,
          precio_venta: parseFloat($("altaPrecio").value || "0") || 0,
          marca: $("altaMarca").value || "GENERICO",
          stock: Math.max(0, parseInt($("altaStock").value || "0", 10)),
          cantidad: 1,
          agregar_carrito: true,
        }),
      });
      $("altaCod").value = "";
      $("altaDesc").value = "";
      $("altaPrecio").value = "";
      $("altaMarca").value = "GENERICO";
      $("altaStock").value = "0";
      if (r.items) renderCarrito(r);
      else await refreshCarrito();
      showMsg(r.mensaje || "Producto cargado");
      if (usuarioSesion && usuarioSesion.rol === "admin") {
        cargarAlertasAdmin().catch(() => {});
      }
      $("q").focus();
    } catch (err) {
      showMsg(err.message, true);
    } finally {
      $("btnAltaProd").disabled = false;
    }
  });

  $("cliCuit").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      resolverCuitCampo();
    }
  });
  $("cliCuit").addEventListener("blur", () => {
    resolverCuitCampo();
  });

  $("btnVaciar").addEventListener("click", async () => {
    if (!confirm("¿Vaciar el carrito?")) return;
    await api("/api/carrito/vaciar", { method: "POST" });
    limpiarBorrador();
    presupuestoCargadoId = null;
    presupuestoCargadoNro = null;
    await refreshCarrito();
    showMsg("Carrito vacío");
  });

  $("btnUndo").addEventListener("click", () => deshacer());

  $("btnEspera").addEventListener("click", async () => {
    try {
      const r = await api("/api/carrito/esperar", {
        method: "POST",
        body: JSON.stringify({ etiqueta: $("cliNombre").value || "" }),
      });
      limpiarBorrador();
      presupuestoCargadoId = null;
      presupuestoCargadoNro = null;
      renderCarrito(r);
      renderEspera(r.espera || []);
      showMsg(r.mensaje);
      $("q").focus();
    } catch (err) {
      showMsg(err.message, true);
    }
  });

  $("vendedor").addEventListener("change", async () => {
    await syncVendedor();
    await refreshCarrito();
  });

  $("btnPresu").addEventListener("click", emitirPresupuesto);
  $("btnFactura").addEventListener("click", emitirFactura);
  $("btnInterno").addEventListener("click", emitirInterno);
  $("formaPago").addEventListener("change", actualizarFinanciacion);
  $("tarjetaCuotas").addEventListener("input", actualizarFinanciacion);
  $("tarjetaInteres").addEventListener("input", actualizarFinanciacion);
  $("tarjetaPresets").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-cuotas]");
    if (!btn) return;
    $("tarjetaCuotas").value = btn.dataset.cuotas;
    $("tarjetaInteres").value = btn.dataset.interes;
    actualizarFinanciacion();
  });
  $("panelAdminPuntos").addEventListener("toggle", () => {
    if ($("panelAdminPuntos").open) {
      cargarPuntosAdmin().catch((err) => showMsg(err.message, true));
    }
  });
  $("panelAdminUsuarios").addEventListener("toggle", () => {
    if ($("panelAdminUsuarios").open) {
      cargarUsuariosAdmin().catch((err) => showMsg(err.message, true));
    }
  });
  $("formUsuario").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/admin/usuarios", {
        method: "POST",
        body: JSON.stringify({
          usuario: $("usuNuevoUser").value,
          nombre: $("usuNuevoNombre").value,
          rol: $("usuNuevoRol").value,
          clave: $("usuNuevoClave").value || "111",
        }),
      });
      showMsg(r.mensaje);
      $("usuNuevoUser").value = "";
      $("usuNuevoNombre").value = "";
      await cargarUsuariosAdmin();
    } catch (err) {
      showMsg(err.message, true);
    }
  });
  document.querySelectorAll(".rail-refresh").forEach((btn) => {
    btn.addEventListener("click", () => {
      const rail = btn.dataset.rail || "";
      if (rail === "pendientes") {
        cargarPendientesAlta().catch((err) => showMsg(err.message, true));
      } else {
        cargarAlertasAdmin().catch((err) => showMsg(err.message, true));
      }
    });
  });
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-alerta]");
    if (!btn) return;
    try {
      await api(`/api/admin/alertas/${btn.dataset.alerta}/resolver`, { method: "POST" });
      await cargarAlertasAdmin();
    } catch (err) {
      showMsg(err.message, true);
    }
  });
  $("btnLogout").addEventListener("click", async () => {
    const ok = confirm(
      "¿Cerrar sesión?\n\nEl próximo vendedor va a tener que ingresar con su usuario.\nSe limpia el carrito de esta PC."
    );
    if (!ok) return;
    try {
      limpiarBorrador();
      await api("/api/auth/logout", { method: "POST" });
    } catch (_) {
      /* igual recargamos para forzar login */
    }
    usuarioSesion = null;
    location.reload();
  });
  $("btnClave").addEventListener("click", async () => {
    const actual = prompt("Clave actual:");
    if (actual == null) return;
    const nueva = prompt("Nueva clave (mínimo 4 caracteres):");
    if (!nueva) return;
    try {
      const r = await api("/api/auth/cambiar-clave", {
        method: "POST",
        body: JSON.stringify({ actual, nueva }),
      });
      showMsg(r.mensaje);
    } catch (err) {
      showMsg(err.message, true);
    }
  });

  $("btnFirebase").addEventListener("click", conectarFirebase);

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === "z" || e.key === "Z")) {
      if (["INPUT", "TEXTAREA"].includes((e.target || {}).tagName) && e.target.id !== "q") {
        return;
      }
      e.preventDefault();
      deshacer();
      return;
    }
    if (e.key === "F2") {
      e.preventDefault();
      $("q").focus();
      $("q").select();
    } else if (e.key === "F3") {
      e.preventDefault();
      $("cliBusca").focus();
      $("cliBusca").select();
    } else if (e.key === "F4") {
      e.preventDefault();
      emitirPresupuesto();
    } else if (e.key === "F5") {
      e.preventDefault();
      emitirFactura();
    } else if (e.key === "F6") {
      e.preventDefault();
      $("btnEspera").click();
    } else if (e.key === "Escape") {
      $("q").value = "";
      $("resultados").innerHTML = "";
      lastResultados = [];
      $("cant").value = "1";
      $("hint").textContent = "Escribí arriba. Tip: 111*3 agrega 3 unidades.";
      $("cliHits").hidden = true;
      $("q").focus();
    }
  });
}

function renderEspera(lista) {
  const box = $("listaEspera");
  if (!lista || !lista.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = lista
    .map(
      (e) => `
    <div class="espera-row">
      <div>
        <div class="cod">${e.etiqueta}</div>
        <div class="meta">${e.creado || ""} · ${e.items} ítem(s) · ${e.total_txt}</div>
      </div>
      <div class="espera-actions">
        <button type="button" class="retomar" data-act="retomar" data-id="${e.id}">Retomar</button>
        <button type="button" data-act="del" data-id="${e.id}">✕</button>
      </div>
    </div>`
    )
    .join("");
  box.querySelectorAll("button[data-act]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        if (btn.dataset.act === "retomar") {
          const r = await api(`/api/carrito/espera/${encodeURIComponent(btn.dataset.id)}/retomar`, {
            method: "POST",
          });
          renderCarrito(r);
          renderEspera(r.espera || []);
          showMsg(r.mensaje);
        } else {
          const r = await api(`/api/carrito/espera/${encodeURIComponent(btn.dataset.id)}`, {
            method: "DELETE",
          });
          renderEspera(r.espera || []);
        }
      } catch (err) {
        showMsg(err.message, true);
      }
    });
  });
}

async function deshacer() {
  try {
    const r = await api("/api/carrito/deshacer", { method: "POST" });
    renderCarrito(r);
    showMsg("Deshecho");
  } catch (err) {
    showMsg(err.message, true);
  }
}

async function refreshEspera() {
  try {
    const r = await api("/api/carrito/espera");
    renderEspera(r.resultados || []);
  } catch (_) {}
}

async function emitirPresupuesto() {
  const ventana = ventanaEspera("Generando presupuesto…");
  try {
    $("btnPresu").disabled = true;
    await syncCliente();
    await syncVendedor();
    const actualizar = Boolean(presupuestoCargadoId);
    const r = await api("/api/presupuestos/emitir", {
      method: "POST",
      body: JSON.stringify({
        nota: $("nota").value || "",
        vendedor: $("vendedor").value || "CAJA",
        actualizar,
      }),
    });
    showMsg(`${r.mensaje} · ${r.total_txt || r.total}`);
    if (r.pdf_base64) abrirPdfBase64(r.pdf_base64, r.pdf_nombre, ventana);
    $("nota").value = "";
    $("cliBusca").value = "";
    presupuestoCargadoId = null;
    presupuestoCargadoNro = null;
    limpiarBorrador();
    await refreshCarrito();
    if (busquedaPresuActiva) await refreshPresupuestos();
  } catch (err) {
    if (ventana) ventana.close();
    showMsg(err.message, true);
  } finally {
    $("btnPresu").disabled = false;
  }
}

async function emitirFactura() {
  const letra = $("cliTipo").value === "1" ? "A" : "B";
  const cliente = $("cliNombre").value || "CONSUMIDOR FINAL";
  const cuit = $("cliCuit").value || "sin DNI/CUIT";
  const payload = payloadVenta();
  const pago = payload.forma_pago;
  const cuotas = payload.cuotas;
  const interes = payload.interes_pct;
  const totalFinal = ultimoTotal * (1 + interes / 100);
  const detalleTarjeta = pago === "Tarjeta"
    ? `\nCuotas: ${cuotas}\nInterés: ${interes}%\nValor por cuota: ${money(totalFinal / cuotas)}`
    : "";
  const sinStock = payload.permitir_sin_stock ? "\n⚠ Permitir sin stock: SÍ" : "";
  const ok = confirm(
    `FACTURA REAL ARCA\n\nFactura ${letra}\nCliente: ${cliente}\nDNI/CUIT: ${cuit}\nPago: ${pago}${detalleTarjeta}${sinStock}\nTotal: ${money(totalFinal)}\n\n¿Emitir y solicitar CAE?`
  );
  if (!ok) return;

  const ventana = ventanaEspera("Solicitando CAE a ARCA…");
  try {
    $("btnFactura").disabled = true;
    $("btnFactura").textContent = "Consultando ARCA…";
    const r = await api("/api/venta/factura", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    abrirTicketHtml(r.ticket_html, ventana);
    showMsg(`${r.mensaje} · ${r.total_txt} · ${r.stock_msg || ""}`);
    $("nota").value = "";
    $("cliBusca").value = "";
    $("chkSinStock").checked = false;
    presupuestoCargadoId = null;
    presupuestoCargadoNro = null;
    limpiarBorrador();
    await refreshCarrito();
    cargarPendientesAlta().catch(() => {});
    if (usuarioSesion && usuarioSesion.rol === "admin") {
      cargarAlertasAdmin().catch(() => {});
    }
  } catch (err) {
    if (ventana) ventana.close();
    showMsg(`No se emitió la factura: ${err.message}`, true);
  } finally {
    $("btnFactura").disabled = false;
    $("btnFactura").textContent = "Facturar ARCA";
  }
}

async function emitirInterno() {
  const letra = $("cliTipo").value === "1" ? "A" : "B";
  const cliente = $("cliNombre").value || "CONSUMIDOR FINAL";
  const payload = payloadVenta();
  const pago = payload.forma_pago;
  const interes = payload.interes_pct;
  const totalFinal = ultimoTotal * (1 + interes / 100);
  const sinStock = payload.permitir_sin_stock ? "\n⚠ Permitir sin stock: SÍ" : "";
  const ok = confirm(
    `COMPROBANTE INTERNO\n\nTipo ${letra}\nCliente: ${cliente}\nPago: ${pago}${sinStock}\nTotal: ${money(totalFinal)}\n\n¿Emitir ticket sin CAE?`
  );
  if (!ok) return;

  const ventana = ventanaEspera("Generando comprobante…");
  try {
    $("btnInterno").disabled = true;
    $("btnInterno").textContent = "Emitiendo…";
    const r = await api("/api/venta/comprobante-interno", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    abrirTicketHtml(r.ticket_html, ventana);
    showMsg(`${r.mensaje} · ${r.total_txt} · ${r.stock_msg || ""}`);
    $("nota").value = "";
    $("cliBusca").value = "";
    $("chkSinStock").checked = false;
    presupuestoCargadoId = null;
    presupuestoCargadoNro = null;
    limpiarBorrador();
    await refreshCarrito();
    cargarPendientesAlta().catch(() => {});
    if (usuarioSesion && usuarioSesion.rol === "admin") {
      cargarAlertasAdmin().catch(() => {});
    }
  } catch (err) {
    if (ventana) ventana.close();
    showMsg(`No se emitió el comprobante: ${err.message}`, true);
  } finally {
    $("btnInterno").disabled = false;
    $("btnInterno").textContent = "Comprobante interno";
  }
}

async function actualizarBannerFirebase(h) {
  const box = $("bannerFirebase");
  const titulo = $("fbTitulo");
  const det = $("fbDetalle");
  const btn = $("btnFirebase");
  if (!box) return;
  box.hidden = false;
  if (h.firebase) {
    box.classList.add("ok");
    titulo.textContent = "Firebase conectado";
    det.textContent = `${h.inventario} productos · ${h.ruta_claves || "ok"}`;
    btn.textContent = "Recargar inventario";
  } else {
    box.classList.remove("ok");
    if (h.tiene_claves && h.error_firebase) {
      titulo.textContent = "Firebase temporalmente no disponible";
      det.textContent = h.error_firebase;
    } else {
      titulo.textContent = "Modo emulador (sin Firebase en esta PC)";
      det.textContent =
        h.instruccion ||
        "Copiá firebase_claves.json a la carpeta erp-repuestos y tocá Conectar Firebase.";
    }
    btn.textContent = "Conectar Firebase";
  }
}

async function conectarFirebase() {
  try {
    $("btnFirebase").disabled = true;
    const r = await api("/api/inventario/recargar", { method: "POST" });
    const h = {
      firebase: r.firebase || r.modo === "firebase",
      inventario: r.inventario || r.productos || 0,
      ruta_claves: r.ruta_claves,
      instruccion: r.instruccion,
      tiene_claves: r.tiene_claves,
      error_firebase: r.error_firebase,
      etiqueta: r.firebase ? "Firebase real" : "inventario de muestra",
    };
    $("statusPill").textContent = `OK · ${h.inventario} · ${h.etiqueta}`;
    await actualizarBannerFirebase({ ...r, ...h });
    if (h.firebase) {
      showMsg(`Firebase OK · ${h.inventario} productos`);
    } else {
      showMsg(
        r.error_firebase || r.instruccion || "Todavía no hay firebase_claves.json",
        true
      );
    }
  } catch (err) {
    showMsg(err.message, true);
  } finally {
    $("btnFirebase").disabled = false;
  }
}

async function boot() {
  try {
    let sesion;
    try {
      sesion = await api("/api/auth/me");
    } catch (_) {
      $("loginOverlay").hidden = false;
      $("statusPill").textContent = "Esperando ingreso";
      return;
    }
    aplicarSesion(sesion);
    bind();
    try {
      const vendLs = localStorage.getItem(LS_VEND);
      if (vendLs) $("vendedor").value = vendLs;
    } catch (_) {}

    const h = await api("/api/health");
    $("statusPill").textContent = `OK · ${h.inventario} · ${h.etiqueta || "muestra"}`;
    $("statusPill").classList.add("ok");
    await actualizarBannerFirebase(h);

    await syncVendedor();

    let data = await api("/api/carrito");
    if (!(data.items || []).length) {
      const borrador = leerBorrador();
      if (borrador && (borrador.items || []).length) {
        data = await api("/api/carrito/restaurar", {
          method: "POST",
          body: JSON.stringify(borrador),
        });
        showMsg("Se restauró el carrito del borrador");
      }
    }
    if (data.vendedor) $("vendedor").value = data.vendedor;
    renderCarrito(data);
    await refreshPresupuestos();
    await refreshEspera();
    $("q").focus();
  } catch (err) {
    $("statusPill").textContent = "Sin conexión API";
    $("statusPill").classList.add("err");
  }
}

$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const error = $("loginError");
  error.hidden = true;
  try {
    const claveActual = $("loginClave").value;
    const usuario = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({
        usuario: $("loginUsuario").value,
        clave: claveActual,
      }),
    });
    if (usuario.debe_cambiar_clave) {
      cambioInicialPendiente = { usuario, claveActual };
      $("loginForm").hidden = true;
      $("cambioInicialForm").hidden = false;
      $("claveNuevaInicial").focus();
      return;
    }
    $("loginClave").value = "";
    aplicarSesion(usuario);
    await boot();
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
});

$("cambioInicialForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const error = $("cambioInicialError");
  error.hidden = true;
  const nueva = $("claveNuevaInicial").value;
  if (nueva !== $("claveNuevaRepetir").value) {
    error.textContent = "Las claves nuevas no coinciden";
    error.hidden = false;
    return;
  }
  try {
    await api("/api/auth/cambiar-clave", {
      method: "POST",
      body: JSON.stringify({
        actual: cambioInicialPendiente.claveActual,
        nueva,
      }),
    });
    const usuario = { ...cambioInicialPendiente.usuario, debe_cambiar_clave: false };
    cambioInicialPendiente = null;
    $("loginClave").value = "";
    $("claveNuevaInicial").value = "";
    $("claveNuevaRepetir").value = "";
    $("cambioInicialForm").hidden = true;
    $("loginForm").hidden = false;
    aplicarSesion(usuario);
    await boot();
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
});

boot();
