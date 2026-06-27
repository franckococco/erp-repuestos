"""Dictado por voz en el navegador (micrófono del celular vía Web Speech API)."""
import json

import streamlit.components.v1 as components

_SPEECH_HTML = """
<div id="wrap" style="font-family:system-ui,sans-serif;text-align:center;padding:4px 0;">
  <button id="mic-btn" type="button" style="
    width:100%;max-width:420px;padding:12px 16px;font-size:1.05rem;font-weight:600;
    border:2px solid #1f77b4;border-radius:12px;background:#e8f4fc;color:#0d47a1;
    cursor:pointer;touch-action:manipulation;
  ">🎤 Tocá para dictar la orden</button>
  <div id="mic-status" style="margin-top:6px;font-size:0.85rem;color:#555;min-height:1.2em;"></div>
</div>
<script>
(function() {
  const btn = document.getElementById('mic-btn');
  const status = document.getElementById('mic-status');
  const lang = __LANG__;
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

  function sendValue(val) {
    window.parent.postMessage({type: 'streamlit:setComponentValue', value: val}, '*');
  }

  if (!SpeechRecognition) {
    btn.disabled = true;
    btn.style.opacity = '0.55';
    status.textContent = 'Dictado no disponible en este navegador. Usá Chrome en el celular.';
    return;
  }

  let recognition = null;
  let listening = false;

  function setStatus(msg, color) {
    status.textContent = msg;
    status.style.color = color || '#555';
  }

  function startListening() {
    if (listening) {
      try { recognition.stop(); } catch (e) {}
      return;
    }
    recognition = new SpeechRecognition();
    recognition.lang = lang;
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onstart = function() {
      listening = true;
      btn.textContent = '⏹️ Tocá para detener';
      btn.style.background = '#ffe8e8';
      btn.style.borderColor = '#c62828';
      btn.style.color = '#b71c1c';
      setStatus('Escuchando… hablá la orden completa.', '#1565c0');
    };

    recognition.onresult = function(event) {
      let finalText = '';
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const t = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += t;
        else interim += t;
      }
      if (interim) setStatus('…' + interim.trim(), '#1565c0');
      if (finalText.trim()) {
        setStatus('✓ ' + finalText.trim(), '#2e7d32');
        sendValue(finalText.trim());
      }
    };

    recognition.onerror = function(e) {
      listening = false;
      btn.textContent = '🎤 Tocá para dictar la orden';
      btn.style.background = '#e8f4fc';
      btn.style.borderColor = '#1f77b4';
      btn.style.color = '#0d47a1';
      const err = (e.error || 'error');
      if (err === 'not-allowed') {
        setStatus('Permiso de micrófono denegado. Activá el micrófono en el navegador.', '#c62828');
      } else if (err !== 'aborted') {
        setStatus('Error de micrófono: ' + err, '#c62828');
      }
    };

    recognition.onend = function() {
      listening = false;
      btn.textContent = '🎤 Tocá para dictar la orden';
      btn.style.background = '#e8f4fc';
      btn.style.borderColor = '#1f77b4';
      btn.style.color = '#0d47a1';
      if (!status.textContent.startsWith('✓')) {
        setStatus('Tocá de nuevo para seguir dictando.', '#555');
      }
    };

    try { recognition.start(); } catch (e) {
      setStatus('No se pudo iniciar el micrófono.', '#c62828');
    }
  }

  btn.addEventListener('click', startListening);
})();
</script>
"""


def render_boton_dictado(component_key: str, lang: str = "es-AR") -> str | None:
    """
    Muestra botón de micrófono. En Chrome/Android devuelve el texto dictado.
    Retorna None si el usuario no dictó nada en este render.
    """
    html_block = _SPEECH_HTML.replace("__LANG__", json.dumps(lang))
    transcript = components.html(html_block, height=88, key=component_key)
    if transcript and isinstance(transcript, str) and transcript.strip():
        return transcript.strip()
    return None
