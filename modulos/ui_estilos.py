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


def render_sidebar(cliente_activo):
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
        st.divider()

        nav_labels = [
            "📸 Carga Stock",
            "📦 Inventario",
            "🛒 Mostrador",
            "🤖 Asistente",
            "⚙️ Configuración",
        ]
        nav_keys = ["carga", "inventario", "mostrador", "asistente", "config"]
        idx = nav_keys.index(st.session_state.get("pagina", "carga"))
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
