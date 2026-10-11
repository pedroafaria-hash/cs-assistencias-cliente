"""
app_cliente.py — Formulário público do cliente, Fase 2 das Assistências
Pós-Obra CS (10/10/2026). App Streamlit separada e sem login, pensada para
correr no Streamlit Community Cloud (link/QR enviado pelo Pedro ao
cliente — ver `APP_CS_ASSISTENCIAS/app.py`, botão "Gerar / Ver Link do
Cliente").

O cliente abre `<esta app>/?token=xxxx`, vê os dados básicos da sua
assistência (sem conseguir ver nem adivinhar as de outros clientes — o
token é um uuid4, nunca um id sequencial) e acrescenta os seus próprios
itens: tópico, descrição, observações e (opcional) uma foto. Cada item
enviado fica em `itens_cloud` no Supabase; o Pedro traz isso para a base
local quando quiser, com o botão "Sincronizar com o Cliente" na app
principal — esta app aqui nunca toca na base local nem no PHC.
"""
import streamlit as st

import db_cloud

st.set_page_config(page_title="Assistência Pós-Obra — CS Construtora", page_icon="🛠️", layout="centered")

_col_logo, _col_titulo = st.columns([1, 4])
with _col_logo:
    try:
        st.image("Logos/logo2.png", width=90)
    except Exception:
        pass
with _col_titulo:
    st.title("Assistência Pós-Obra")
    st.caption("CS Construtora")

_token = st.query_params.get("token", "").strip()

if not _token:
    st.error(
        "Link incompleto — falta o código da assistência. Usa o link ou o QR code que a CS "
        "Construtora te enviou."
    )
    st.stop()

try:
    _conn = db_cloud.get_connection()
except db_cloud.CloudNaoConfigurado as _erro:
    st.error(f"Não foi possível ligar ao servidor: {_erro}")
    st.stop()

_assistencia = db_cloud.obter_assistencia_publica(_conn, _token)
if _assistencia is None:
    st.error("Não encontrámos nenhuma assistência com este link. Verifica se o copiaste por inteiro, ou contacta a CS Construtora.")
    st.stop()

st.success(f"Assistência da obra **{_assistencia['obra_assunto']}** (processo {_assistencia['processo']})")
if _assistencia.get("cliente"):
    st.caption(f"Cliente: {_assistencia['cliente']}")

st.markdown(
    "Descreve aqui cada assunto que precisas de reportar — podes adicionar vários (por exemplo, "
    "\"Infiltração\" e \"Fissura\" em separado), cada um com a sua própria descrição, observações e, "
    "se quiseres, uma foto. Quando terminares, carrega em **Enviar à CS Construtora**."
)

if "itens_cliente" not in st.session_state:
    st.session_state["itens_cliente"] = []

with st.form("form_item_cliente", clear_on_submit=True):
    _topico = st.text_input("Assunto / Tópico *", placeholder="ex.: Infiltração, Fissura, Canalização…")
    _descricao = st.text_area("Descrição", placeholder="O que se passa, onde, desde quando…")
    _observacoes = st.text_area("Observações (opcional)")
    _foto = st.file_uploader("Foto (opcional)", type=["jpg", "jpeg", "png"])
    _submeter_item = st.form_submit_button("➕ Adicionar à lista")

if _submeter_item:
    if not _topico or not _topico.strip():
        st.error("O assunto/tópico é obrigatório.")
    else:
        st.session_state["itens_cliente"].append({
            "topico": _topico.strip(),
            "descricao": _descricao,
            "observacoes": _observacoes,
            "foto_bytes": _foto.getvalue() if _foto else None,
            "foto_nome": _foto.name if _foto else None,
        })
        st.rerun()

if st.session_state["itens_cliente"]:
    st.markdown("##### Itens a enviar")
    for _i, _item in enumerate(st.session_state["itens_cliente"], start=1):
        _foto_label = f"📷 {_item['foto_nome']}" if _item["foto_bytes"] else "sem foto"
        st.write(f"**{_i}. {_item['topico']}** — {_item['descricao'] or 'sem descrição'} ({_foto_label})")

    _remover_idx = st.number_input(
        "Remover item nº (posição na lista acima, a contar de 1)",
        min_value=0, max_value=len(st.session_state["itens_cliente"]), value=0, step=1,
    )
    if _remover_idx > 0 and st.button("🗑️ Remover"):
        st.session_state["itens_cliente"].pop(_remover_idx - 1)
        st.rerun()

    st.divider()
    if st.button("📨 Enviar à CS Construtora", type="primary"):
        for _item in st.session_state["itens_cliente"]:
            db_cloud.adicionar_item_cliente(
                _conn, _token, topico=_item["topico"], descricao=_item["descricao"],
                observacoes=_item["observacoes"], foto_bytes=_item["foto_bytes"],
            )
        st.session_state["itens_cliente"] = []
        st.session_state["envio_confirmado"] = True
        st.rerun()
else:
    st.info("Ainda sem itens — adiciona pelo menos um assunto antes de enviar.")

if st.session_state.get("envio_confirmado"):
    st.success("Obrigado! A CS Construtora foi notificada e vai tratar do assunto.")
    st.session_state["envio_confirmado"] = False
