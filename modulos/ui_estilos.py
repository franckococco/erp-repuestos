"""Estilos y componentes visuales compartidos para la app Streamlit."""
import streamlit as st

from modulos.util_branding import ruta_logo_hafid


def aplicar_estilos_globales():
    st.markdown(
        """
        <style>
        /* Menos ruido visual */
        div[data-testid="stAlert"] { padding: 0.55rem 0.85rem; margin-bottom: 0.45rem; }
        .block-container { padding-top: 1.25rem; padding-bottom: 2rem; max-width: 1180px; }
        h1 { font-size: 1.55rem !important; font-weight: 600 !important; }
        h2, h3 { font-weight: 600 !important; }
        hr { margin: 0.75rem 0 !important; }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
        }
        [data-testid="stSidebar"] * { color: #e2e8f0 !important; }
        [data-testid="stSidebar"] .stRadio label { font-size: 0.95rem; }
        [data-testid="stSidebar"] hr { border-color: #334155 !important; }
        .hafid-badge {
            display: inline-block;
            background: #1d4ed8;
            color: #fff;
            font-size: 0.72rem;
            font-weight: 600;
            padding: 0.15rem 0.5rem;
            border-radius: 999px;
            margin-left: 0.35rem;
            vertical-align: middle;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def aplicar_estilos_mostrador():
    """PC: más ancho y grilla legible; móvil: columnas apiladas."""
    st.markdown(
        """
        <style>
        @media (min-width: 900px) {
            .main .block-container { max-width: 96rem; padding-left: 1.5rem; padding-right: 1.5rem; }
        }
        div[data-testid="stDataEditor"] { width: 100% !important; }
        div[data-testid="stDataEditor"] input[type="number"] { min-width: 4.5rem; font-size: 1rem; }
        div[data-testid="stHorizontalBlock"]:has(div[data-testid="stDataEditor"]) {
            width: 100% !important;
        }
        .mostrador-cobro-panel [data-testid="stMetric"] {
            background: #f8fafc;
            padding: 0.35rem 0.65rem;
            border-radius: 0.5rem;
        }
        @media (max-width: 768px) {
            .main .block-container { padding-left: 0.75rem; padding-right: 0.75rem; }
            div[data-testid="column"] { min-width: 100% !important; flex: 1 1 100% !important; }
        }
        /* Select de clientes / facturas: texto completo legible */
        div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
            min-height: 2.75rem;
        }
        div[data-testid="stSelectbox"] [data-testid="stMarkdownContainer"] p {
            white-space: normal !important;
            line-height: 1.35;
        }
        .mostrador-orden-rapida {
            background: linear-gradient(135deg, #eff6ff 0%, #dbeafe 100%);
            border: 2px solid #2563eb;
            border-radius: 0.65rem;
            padding: 0.85rem 1rem 0.5rem;
            margin-bottom: 0.75rem;
        }
        .mostrador-orden-rapida h3 {
            color: #1e3a8a !important;
            font-weight: 700 !important;
            font-size: 1.05rem !important;
            margin: 0 0 0.35rem 0 !important;
        }
        .mostrador-orden-rapida p {
            color: #1e40af;
            font-size: 0.82rem;
            margin: 0 0 0.5rem 0;
        }
        .mostrador-buscador-box {
            background: #f8fafc;
            border: 1px solid #cbd5e1;
            border-radius: 0.5rem;
            padding: 0.65rem 0.85rem 0.35rem;
            margin-bottom: 0.5rem;
        }
        .mostrador-buscador-box strong {
            color: #0f172a;
            font-size: 0.95rem;
        }
        div[data-testid="stRadio"] label p {
            font-size: 0.88rem !important;
            line-height: 1.3 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar(cliente_activo, rol="admin", nombre_usuario=""):
    with st.sidebar:
        logo = ruta_logo_hafid()
        if logo:
            col_logo, col_tit = st.columns([1, 1.4])
            with col_logo:
                st.image(logo, use_container_width=True)
            with col_tit:
                st.markdown("### Hafid Repuestos")
                st.caption("Inventario · Mostrador · IA")
        else:
            st.markdown("### Hafid Repuestos")
            st.caption("Inventario · Mostrador · IA")

        if nombre_usuario:
            st.caption(f"Usuario: **{nombre_usuario}**")

        st.divider()

        nav_labels = [
            "📸 Carga Stock",
            "📦 Inventario",
            "🛒 Mostrador",
            "🤖 Asistente",
            "⚙️ Configuración",
        ]
        nav_keys = ["carga", "inventario", "mostrador", "asistente", "config"]
        idx = nav_keys.index(st.session_state.get("pagina", "carga")) if st.session_state.get("pagina") in nav_keys else 0
        elegido = st.radio(
            "Menú",
            nav_labels,
            index=idx,
            label_visibility="collapsed",
        )
        st.session_state.pagina = nav_keys[nav_labels.index(elegido)]

        st.divider()
        st.markdown("**Cliente activo**")
        st.write(cliente_activo.get("nombre", "Particular"))
        cbte = str(cliente_activo.get("tipo_comprobante", "6"))
        st.caption(f"Factura {'A' if cbte == '1' else 'B'} · CUIT {cliente_activo.get('cuit', '—')}")
        if float(cliente_activo.get("descuento", 0)) > 0:
            st.caption(f"Descuento: {cliente_activo['descuento']}%")

        st.divider()
        st.caption("Atajos: Ctrl+S · I · M · A · C")

    return st.session_state.pagina


def titulo_seccion(titulo, atajo=None):
    if atajo:
        st.markdown(f"## {titulo} <span class='hafid-badge'>{atajo}</span>", unsafe_allow_html=True)
    else:
        st.header(titulo)


def ayuda(titulo, texto):
    with st.expander(titulo, expanded=False):
        st.markdown(texto)


def metricas_inventario(items):
    if not items:
        return
    total_var = len(items)
    stock_bajo = sum(1 for p in items if isinstance(p, dict) and int(p.get("stock", 0)) <= 3)
    c1, c2, c3 = st.columns(3)
    c1.metric("Variantes", total_var)
    c2.metric("Stock bajo (≤3)", stock_bajo)
    c3.metric("Maestros", len({p.get("id_maestro") or p.get("codigo") for p in items if isinstance(p, dict)}))
