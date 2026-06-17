"""Inicialización ligera del mostrador (sin importar UI pesada)."""
import streamlit as st

CUIT_EMISOR_ARCA = "20265010505"
CLAVE_EMISOR_ARCA = "111"


def init_credenciales_arca_session():
    if st.session_state.get("_credenciales_arca_inited"):
        return
    st.session_state.facturador_cuit_ui = CUIT_EMISOR_ARCA
    st.session_state.facturador_clave_ui = CLAVE_EMISOR_ARCA
    st.session_state._credenciales_arca_inited = True
