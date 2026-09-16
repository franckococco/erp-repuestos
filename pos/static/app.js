const $ = (id) => document.getElementById(id);
const LS_KEY = "hafid_pos_borrador";
const LS_VEND = "hafid_pos_vendedor";

let debounceTimer = null;
let cliTimer = null;
let presuTimer = null;
let presupuestoCargadoId = null;
let presupuestoCargadoNro = null;
let lastResultados = [];

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
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = Array.isArray(d)
      ? d.map((x) => x.msg || JSON.stringify(x)).join("; ")
      : d || data.message || "Error de API";
    throw new Error(msg);
  }
  return data;
}

function showMsg(text, err = false) {
  const el = $("msg");
  el.hidden = false;
  el.className = "msg" + (err ? " err" : "");
  el.textContent = text;
}

function abrirPdfBase64(b64, nombre) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], { type: "application/pdf" });
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank");
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

async function syncCliente() {
  await api("/api/cliente", {
    method: "PUT",
    body: JSON.stringify({
      nombre: $("cliNombre").value || "CONSUMIDOR FINAL",
      cuit: $("cliCuit").value || "",
      tipo_comprobante: "6",
      descuento: parseFloat($("cliDesc").value || "0") || 0,
    }),
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

function aplicarClienteEnForm(cli) {
  if (!cli) return;
  $("cliNombre").value = cli.nombre || "CONSUMIDOR FINAL";
  $("cliCuit").value = cli.cuit && cli.cuit !== "00000000000" ? cli.cuit : "";
  $("cliDesc").value = String(cli.descuento || 0);
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
    btn.textContent = `Actualizar presupuesto Nº ${String(presupuestoCargadoNro).padStart(4, "0")}`;
  } else {
    b.hidden = true;
    b.textContent = "";
    btn.textContent = "Generar presupuesto PDF";
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
  if (data.cliente) aplicarClienteEnForm(data.cliente);
  if (data.vendedor && $("vendedor") && document.activeElement !== $("vendedor")) {
    $("vendedor").value = data.vendedor;
  }
  if (data.nota != null && $("nota") && document.activeElement !== $("nota")) {
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
        <div class="meta">${p.creado} · ${p.items} ítem(s) · ${p.total_txt} · ${p.origen}</div>
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
  const data = await api(
    `/api/presupuestos?q=${encodeURIComponent(term)}&incluir_anulados=${anul}`
  );
  renderListaPresu(data.resultados || []);
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
  if (parsed.cant) $("cant").value = String(parsed.cant);
  const data = await api(`/api/productos?q=${encodeURIComponent(parsed.q)}`);
  renderResultados(data.resultados || []);
  $("hint").textContent = parsed.q
    ? `${data.total} resultado(s)${parsed.star ? ` · cant ${parsed.cant}` : ""} · click o Enter`
    : "Escribí arriba. Tip: 111*3 agrega 3 unidades.";
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
  $("q").addEventListener("input", () => {
    clearTimeout(debounceTimer);
    const raw = $("q").value;
    debounceTimer = setTimeout(() => buscar(raw), 180);
  });

  $("q").addEventListener("keydown", async (e) => {
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

  $("presuQ").addEventListener("input", () => {
    clearTimeout(presuTimer);
    presuTimer = setTimeout(() => refreshPresupuestos($("presuQ").value), 200);
  });

  $("chkAnulados").addEventListener("change", () => refreshPresupuestos());

  $("cliDesc").addEventListener("change", async () => {
    await syncCliente();
    await refreshCarrito();
  });

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
      showMsg("Ítem manual agregado");
    } catch (err) {
      showMsg(err.message, true);
    }
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

  $("btnRefreshPresu").addEventListener("click", () => refreshPresupuestos());

  $("btnPresu").addEventListener("click", emitirPresupuesto);

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
    if (r.pdf_base64) abrirPdfBase64(r.pdf_base64, r.pdf_nombre);
    $("nota").value = "";
    $("cliBusca").value = "";
    presupuestoCargadoId = null;
    presupuestoCargadoNro = null;
    limpiarBorrador();
    await refreshCarrito();
    await refreshPresupuestos();
  } catch (err) {
    showMsg(err.message, true);
  } finally {
    $("btnPresu").disabled = false;
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
    titulo.textContent = "Modo emulador (sin Firebase en esta PC)";
    det.textContent =
      h.instruccion ||
      "Copiá firebase_claves.json a la carpeta erp-repuestos y tocá Conectar Firebase.";
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
      etiqueta: r.firebase ? "Firebase real" : "inventario de muestra",
    };
    $("statusPill").textContent = `OK · ${h.inventario} · ${h.etiqueta}`;
    await actualizarBannerFirebase({ ...r, ...h });
    if (h.firebase) {
      showMsg(`Firebase OK · ${h.inventario} productos`);
    } else {
      showMsg(r.instruccion || "Todavía no hay firebase_claves.json", true);
    }
  } catch (err) {
    showMsg(err.message, true);
  } finally {
    $("btnFirebase").disabled = false;
  }
}

async function boot() {
  bind();
  try {
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

boot();
