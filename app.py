"""
app.py — Assistências Pós-Obra CS, 10/10/2026.

App Streamlit independente do Painel de Obras (pedido do Pedro: "Quero que
me cries, uma app, independente do portal, mas que interliga com a app
painel de obras"), para abertura e gestão de assistências pós-obra.

1ª entrega ("núcleo", por fases, como combinado): criar uma assistência
associada a uma obra do PHC, vários itens/tópicos por assistência, gestão
de pendências (listagem + mudança de estado), impressão em PDF.
Fica para as entregas seguintes: QR code + formulário do cliente (com
fotos), ligação ao Asana, geração de orçamento de resolução — ver o
ficheiro do projeto para o plano completo e as decisões tomadas com o
Pedro.
"""
import os
import signal
import time
from datetime import datetime

import pandas as pd
import streamlit as st

import db_assist as dba
import db_cloud
import obras as obras_mod
import pdf
import qr
import sync
from auth import TABS_DISPONIVEIS, add_user, count_admins, load_users, remove_user, save_users, set_password, update_user_tabs, verify_password

CLIENTE_APP_URL = os.environ.get("CLIENTE_APP_URL", "").rstrip("/")
_FOTOS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Fotos")


def _ler_foto_reduzida(fotos_dir: str, foto_path: str, max_largura: int = 900, qualidade: int = 70):
    """Lê a foto de um tópico (pasta `Fotos/`, local) e devolve bytes JPEG
    reduzidos — mais leve para guardar na cloud (Fase 4b, pedido do Pedro:
    mostrar as fotos na listagem de tópicos da app DTO). Se o Pillow não
    estiver instalado, ou a foto não existir/não abrir, devolve os bytes
    originais (ou None) em vez de rebentar a sincronização."""
    if not foto_path:
        return None
    try:
        with open(os.path.join(fotos_dir, foto_path), "rb") as f:
            dados_originais = f.read()
    except OSError:
        return None
    try:
        import io as _io

        from PIL import Image
        imagem = Image.open(_io.BytesIO(dados_originais))
        if imagem.mode not in ("RGB", "L"):
            imagem = imagem.convert("RGB")
        if imagem.width > max_largura:
            nova_altura = int(imagem.height * (max_largura / imagem.width))
            imagem = imagem.resize((max_largura, nova_altura))
        buffer = _io.BytesIO()
        imagem.save(buffer, format="JPEG", quality=qualidade)
        return buffer.getvalue()
    except Exception:
        return dados_originais


def _itens_com_foto_e_fornecedores(conn, fotos_dir: str, itens: list) -> list:
    """Enriquece `itens` (formato de `get_assistencia`) com `foto_bytes`
    (reduzida) e `fornecedor_nomes` (string), para `db_cloud.
    publicar_itens_leitura` — para que o DTO veja, na app cloud, a foto e o
    fornecedor de cada tópico, não só o tópico/descrição/estado."""
    resultado = []
    for item in itens:
        item = dict(item)
        item["foto_bytes"] = _ler_foto_reduzida(fotos_dir, item.get("foto_path"))
        item["fornecedor_nomes"] = ", ".join(f["nome"] for f in dba.get_item_fornecedores(conn, item["id"]))
        resultado.append(item)
    return resultado


st.set_page_config(page_title="Assistências Pós-Obra CS", page_icon="🛠️", layout="wide")

SELECTION_CSS = """
<style>
::selection { background-color: #fff59d; color: inherit; }
::-moz-selection { background-color: #fff59d; color: inherit; }
</style>
"""
st.markdown(SELECTION_CSS, unsafe_allow_html=True)

_conn = dba.get_connection()
dba.init_db(_conn)

# =============================================================================
# Autenticação — mesmo mecanismo do Painel de Obras (auth.py), login próprio
# desta app (users.json independente).
# =============================================================================
if "auth_user" not in st.session_state:
    st.title("🛠️ Assistências Pós-Obra CS")
    st.caption("Acesso restrito — introduz as tuas credenciais para entrar.")
    with st.form("login_form"):
        _login_user = st.text_input("Utilizador")
        _login_pass = st.text_input("Password", type="password")
        _login_submit = st.form_submit_button("Entrar")
    if _login_submit:
        _users_check = load_users()
        if verify_password(_users_check, _login_user.strip(), _login_pass):
            st.session_state["auth_user"] = _login_user.strip()
            st.rerun()
        else:
            st.error("Utilizador ou password incorretos.")
    st.stop()

_users = load_users()
_user_info = _users.get(st.session_state["auth_user"])
if _user_info is None:
    del st.session_state["auth_user"]
    st.rerun()

_is_admin = bool(_user_info.get("is_admin", False))
_tabs_permitidas = _user_info.get("tabs", [])

with st.sidebar:
    st.markdown(f"👤 **{st.session_state['auth_user']}**" + (" · admin" if _is_admin else ""))
    if st.button("Sair"):
        del st.session_state["auth_user"]
        st.rerun()
    st.divider()
    # Atalho para voltar ao Painel de Obras — mesmo princípio do botão
    # "Abrir Gesobra" que já existe lá (porta fixa, para o link ser
    # sempre o mesmo endereço). Esta app corre na porta 8503 (ver
    # iniciar.bat) — 8501 é o Painel de Obras, 8502 é a Gesobra.
    st.link_button("🏗️ Abrir Painel de Obras", "http://localhost:8501", use_container_width=True)
    st.divider()

    # Botão "Fechar Assistências" — mesmo mecanismo já usado no Painel de
    # Obras (ver app.py de lá, "Fechar Portal"): o `iniciar.bat` arranca a
    # app com a janela cmd escondida (truque de VBScript), por isso já não
    # dá para parar com Ctrl+C — é preciso um botão dentro da própria app.
    # Confirmação em 2 passos para não fechar por engano com 1 clique.
    # `os.kill` com SIGTERM no processo atual — no Windows isto chama
    # `TerminateProcess` diretamente; fecha a app para todos os que a
    # estejam a usar nesse momento (processo único, sem multi-tenancy).
    if "confirmar_fechar_assist" not in st.session_state:
        st.session_state["confirmar_fechar_assist"] = False
    if not st.session_state["confirmar_fechar_assist"]:
        if st.button("🔴 Fechar Assistências", use_container_width=True):
            st.session_state["confirmar_fechar_assist"] = True
            st.rerun()
    else:
        st.warning("Isto fecha a app de Assistências para todos os que a estejam a usar agora.")
        _col_fechar_sim, _col_fechar_nao = st.columns(2)
        with _col_fechar_sim:
            if st.button("Sim, fechar", type="primary", use_container_width=True, key="confirma_fechar_sim"):
                st.info("A fechar...")
                time.sleep(1)
                os.kill(os.getpid(), signal.SIGTERM)
        with _col_fechar_nao:
            if st.button("Cancelar", use_container_width=True, key="confirma_fechar_nao"):
                st.session_state["confirmar_fechar_assist"] = False
                st.rerun()
    st.divider()

st.title("🛠️ Assistências Pós-Obra CS")

_nomes_tabs = [t for t in ["Nova Assistência", "Gestão de Pendências", "Conteúdos do Cliente"] if t in _tabs_permitidas]
if _is_admin and "Gestão de Utilizadores" in _tabs_permitidas:
    _nomes_tabs.append("Gestão de Utilizadores")
if not _nomes_tabs:
    st.warning("A tua conta não tem acesso a nenhum separador. Pede a um administrador para configurar o teu acesso.")
    st.stop()

_tabs = st.tabs(_nomes_tabs)
_tab_por_nome = dict(zip(_nomes_tabs, _tabs))


# =============================================================================
# "Nova Assistência"
# =============================================================================
if "Nova Assistência" in _tab_por_nome:
    with _tab_por_nome["Nova Assistência"]:
        st.subheader("Criar nova assistência")

        if "novos_itens" not in st.session_state:
            st.session_state["novos_itens"] = []

        with st.spinner("A carregar obras do PHC…"):
            _obras_cache_key = "obras_df"
            if _obras_cache_key not in st.session_state:
                st.session_state[_obras_cache_key] = obras_mod.carregar_obras()
        _obras = st.session_state[_obras_cache_key]

        if _obras.empty:
            st.error(
                "Não foi possível carregar a lista de obras do PHC. Confirma que a app está a correr "
                "no PC com a ligação PHC_EXCEL configurada (mesma ligação do Painel de Obras)."
            )
        else:
            _termo = st.text_input("Pesquisar obra (processo, assunto, diretor ou cliente)", key="busca_obra")
            _obras_filtradas = obras_mod.pesquisar_obras(_obras, _termo)
            _opcoes = {
                f"{r.processo} — {r.assunto} ({r.director or 'sem diretor'})": r
                for r in _obras_filtradas.itertuples(index=False)
            }
            if not _opcoes:
                st.info("Nenhuma obra encontrada com esse termo de pesquisa.")
            else:
                _escolha = st.selectbox("Obra", list(_opcoes.keys()), key="obra_escolhida")
                _obra_sel = _opcoes[_escolha]

                col1, col2 = st.columns(2)
                with col1:
                    _cliente = st.text_input("Cliente / Contacto da assistência", key="cliente_assist")
                with col2:
                    _contacto = st.text_input("Telefone / Email", key="contacto_assist")
                _obs_gerais = st.text_area("Observações gerais (opcional)", key="obs_gerais_assist")

                st.markdown("##### Itens / Tópicos")
                st.caption("Cada assistência pode ter vários itens — um por anomalia/pedido (ex.: Infiltração, Fissura, Canalização).")

                _fornecedores_ativos = dba.list_fornecedores(_conn, apenas_ativos=True)
                _opcoes_fornecedor = {f"{f['nome']} ({f['tipo']})": f["id"] for f in _fornecedores_ativos}

                with st.form("form_add_item", clear_on_submit=True):
                    fc1, fc2 = st.columns([1, 2])
                    with fc1:
                        _topico = st.text_input("Tópico *", placeholder="ex.: Infiltração")
                    with fc2:
                        _descricao = st.text_input("Descrição", placeholder="O que se passa, onde, desde quando…")
                    _observacoes = st.text_input("Observações", placeholder="Notas adicionais (opcional)")
                    # Pedido do Pedro: "na abertura da assistência, em cada
                    # tópico, pode-se adicionar fornecedores afetos a cada
                    # tópico" — para depois filtrar por fornecedor em
                    # Gestão de Pendências. Lista vem de "Conteúdos do
                    # Cliente" (só os ativos).
                    _fornecedores_escolhidos = st.multiselect(
                        "Fornecedores afetos a este tópico (opcional)", list(_opcoes_fornecedor.keys()),
                    )
                    _add_submit = st.form_submit_button("➕ Adicionar item à lista")
                if _add_submit:
                    if not _topico or not _topico.strip():
                        st.error("O Tópico é obrigatório.")
                    else:
                        st.session_state["novos_itens"].append({
                            "topico": _topico.strip(), "descricao": _descricao, "observacoes": _observacoes,
                            "fornecedor_ids": [_opcoes_fornecedor[n] for n in _fornecedores_escolhidos],
                            "fornecedor_nomes": ", ".join(_fornecedores_escolhidos) if _fornecedores_escolhidos else "",
                        })
                        st.rerun()

                if st.session_state["novos_itens"]:
                    _df_itens_novos = pd.DataFrame(st.session_state["novos_itens"])[
                        ["topico", "descricao", "observacoes", "fornecedor_nomes"]
                    ].rename(columns={
                        "topico": "Tópico", "descricao": "Descrição", "observacoes": "Observações",
                        "fornecedor_nomes": "Fornecedores",
                    })
                    st.table(_df_itens_novos)
                    _remover_idx = st.number_input(
                        "Remover item nº (posição na tabela acima, a contar de 1)",
                        min_value=0, max_value=len(st.session_state["novos_itens"]), value=0, step=1,
                    )
                    if _remover_idx > 0 and st.button("🗑️ Remover"):
                        st.session_state["novos_itens"].pop(_remover_idx - 1)
                        st.rerun()
                else:
                    st.info("Ainda sem itens — adiciona pelo menos um antes de gravar a assistência.")

                st.divider()
                if st.button("💾 Gravar Assistência", type="primary", disabled=not st.session_state["novos_itens"]):
                    _aid = dba.create_assistencia(
                        _conn, processo=_obra_sel.processo, obra_assunto=_obra_sel.assunto,
                        obra_director=_obra_sel.director, cliente=_cliente, contacto=_contacto,
                        observacoes_gerais=_obs_gerais, criado_por=st.session_state["auth_user"],
                    )
                    for _item in st.session_state["novos_itens"]:
                        _iid_novo = dba.add_item(_conn, _aid, _item["topico"], _item["descricao"], _item["observacoes"])
                        if _item.get("fornecedor_ids"):
                            dba.set_item_fornecedores(_conn, _iid_novo, _item["fornecedor_ids"])
                    # Limpa a lista de itens acumulados e volta a correr o
                    # script já com o formulário vazio (sem o rerun, a
                    # tabela de itens acima ainda mostraria os itens já
                    # gravados, de uma fração de segundo antes de serem
                    # limpos — confuso, mesmo com a mensagem de sucesso).
                    st.session_state["novos_itens"] = []
                    st.session_state["ultima_assistencia_criada"] = _aid
                    st.session_state["assistencia_criada_msg"] = (
                        f"Assistência nº {_aid} criada com sucesso, associada à obra {_obra_sel.processo}."
                    )
                    st.rerun()

                if st.session_state.get("assistencia_criada_msg"):
                    st.success(st.session_state.pop("assistencia_criada_msg"))

                if st.session_state.get("ultima_assistencia_criada"):
                    _a = dba.get_assistencia(_conn, st.session_state["ultima_assistencia_criada"])
                    if _a:
                        st.download_button(
                            "🖨️ Imprimir listagem desta assistência (PDF)",
                            data=pdf.gerar_pdf_listagem_assistencia(_a),
                            file_name=f"assistencia_{_a['id']}_{_a['processo']}.pdf",
                            mime="application/pdf",
                        )


# =============================================================================
# "Gestão de Pendências"
# =============================================================================
if "Gestão de Pendências" in _tab_por_nome:
    with _tab_por_nome["Gestão de Pendências"]:
        st.subheader("Gestão de pendências")

        # Sincronizar itens que o cliente preencheu no formulário público
        # (Fase 2 — QR code/link) — puxa-os para esta base local. Botão
        # manual (não automático) porque esta app não está sempre aberta;
        # o Pedro corre isto quando quiser ver o que entrou de novo.
        if st.button("🔄 Sincronizar com o Cliente"):
            try:
                _conn_cloud = db_cloud.get_connection()
                _resultado_sync = sync.sincronizar(_conn, _conn_cloud, _FOTOS_DIR)
                if _resultado_sync.get("assistencias_novas"):
                    st.success(f"{_resultado_sync['assistencias_novas']} assistência(s) nova(s), criada(s) por um DTO na app cloud.")
                if _resultado_sync["sincronizados"]:
                    st.success(f"{_resultado_sync['sincronizados']} tópico(s) novo(s) (cliente/DTO), trazido(s) para a lista abaixo.")
                else:
                    st.info("Sem tópicos novos do cliente/DTO.")
                if _resultado_sync.get("alteracoes_estado"):
                    st.success(f"{_resultado_sync['alteracoes_estado']} mudança(s) de estado aplicada(s) (DTO/colaborador).")
                if _resultado_sync["ignorados"]:
                    st.warning(f"{len(_resultado_sync['ignorados'])} item(ns) cloud ignorado(s) — assistência já não existe localmente.")
                # Publica (ou atualiza) TODAS as assistências na cloud — não só
                # as que já tinham link de cliente gerado — para que cada DTO
                # veja sempre TODAS as suas assistências na app cloud ("As
                # Minhas Assistências"/"Tópicos"), mesmo as que o Pedro ainda
                # não partilhou com nenhum cliente. Gera o token
                # automaticamente se ainda não existir. Também atualiza o
                # snapshot de tópicos (tarefas/descrições) para refletir
                # alterações feitas aqui (ex. "Resolvido") desde a última vez.
                for _a_sync in dba.list_assistencias(_conn):
                    _token_sync = dba.get_or_create_token(_conn, _a_sync["id"])
                    _detalhe_sync = dba.get_assistencia(_conn, _a_sync["id"])
                    db_cloud.publicar_assistencia(
                        _conn_cloud, _token_sync, processo=_detalhe_sync["processo"],
                        obra_assunto=_detalhe_sync["obra_assunto"], obra_director=_detalhe_sync["obra_director"],
                        cliente=_detalhe_sync["cliente"], contacto=_detalhe_sync["contacto"],
                        obra_director_email=dba.get_diretor_email(_conn, _detalhe_sync["obra_director"]),
                    )
                    db_cloud.publicar_itens_leitura(
                        _conn_cloud, _token_sync,
                        _itens_com_foto_e_fornecedores(_conn, _FOTOS_DIR, _detalhe_sync["itens"]),
                    )
            except db_cloud.CloudNaoConfigurado as _erro_cloud:
                st.warning(f"Ligação à cloud ainda não configurada: {_erro_cloud}")

        _itens_todos = dba.list_itens(_conn)
        if not _itens_todos:
            st.info("Ainda não há nenhuma assistência criada.")
        else:
            _df_todos = pd.DataFrame(_itens_todos)
            _diretores = sorted([d for d in _df_todos["obra_director"].dropna().unique() if str(d).strip()])
            _clientes = sorted([c for c in _df_todos["cliente"].dropna().unique() if str(c).strip()])
            _processos = sorted(_df_todos["processo"].dropna().unique())
            _fornecedores_filtro = dba.list_fornecedores(_conn)
            _opcoes_fornecedor_filtro = {f"{f['nome']} ({f['tipo']})": f["id"] for f in _fornecedores_filtro}

            fc1, fc2, fc3, fc4, fc5, fc6 = st.columns(6)
            with fc1:
                _f_cliente = st.selectbox("Cliente", ["Todos"] + _clientes, key="f_cliente_pendencias")
            with fc2:
                _f_diretor = st.selectbox("Diretor de Obra", ["Todos"] + _diretores)
            with fc3:
                _f_processo = st.selectbox("Obra", ["Todas"] + list(_processos))
            with fc4:
                _f_estado = st.selectbox("Estado", ["Todos"] + dba.ESTADOS_ITEM)
            with fc5:
                _f_fornecedor = st.selectbox("Fornecedor", ["Todos"] + list(_opcoes_fornecedor_filtro.keys()))
            with fc6:
                _f_busca = st.text_input("Pesquisa livre", key="busca_pendencias")

            _filtros = {}
            if _f_cliente != "Todos":
                _filtros["cliente"] = _f_cliente
            if _f_diretor != "Todos":
                _filtros["director"] = _f_diretor
            if _f_processo != "Todas":
                _filtros["processo"] = _f_processo
            if _f_estado != "Todos":
                _filtros["estado"] = _f_estado
            if _f_fornecedor != "Todos":
                _filtros["fornecedor_id"] = _opcoes_fornecedor_filtro[_f_fornecedor]
            if _f_busca:
                _filtros["busca"] = _f_busca

            _itens_filtrados = dba.list_itens(_conn, _filtros)
            st.caption(f"{len(_itens_filtrados)} pendência(s) de {len(_itens_todos)} no total")

            if not _itens_filtrados:
                st.info("Nenhum item encontrado com estes filtros.")
            else:
                # Agrupa por assistência (pedido do Pedro: selecionar um
                # Cliente/Contacto e ver, dentro de cada assistência dele,
                # os tópicos/itens como subitens — em vez da tabela plana
                # única de antes). A ordem dos grupos segue a ordem natural
                # de list_itens (item mais recente primeiro), por isso a
                # assistência com atividade mais recente aparece em 1º.
                _grupos = {}
                _ordem_grupos = []
                for _item in _itens_filtrados:
                    _aid_grupo = _item["assistencia_id"]
                    if _aid_grupo not in _grupos:
                        _grupos[_aid_grupo] = []
                        _ordem_grupos.append(_aid_grupo)
                    _grupos[_aid_grupo].append(_item)

                # Com um Cliente específico escolhido, são normalmente
                # poucas assistências — abre logo todas. Com "Todos", fica
                # tudo fechado por omissão para não rebentar a página.
                _expandir_grupo = _f_cliente != "Todos"

                for _aid_grupo in _ordem_grupos:
                    _itens_grupo = _grupos[_aid_grupo]
                    _primeiro = _itens_grupo[0]
                    _n_pend_grupo = sum(1 for i in _itens_grupo if i["estado"] == "Pendente")
                    _cliente_label = _primeiro["cliente"] or "(sem cliente indicado)"
                    _contacto_label = f" · {_primeiro['contacto']}" if _primeiro.get("contacto") else ""
                    _titulo_grupo = (
                        f"👤 {_cliente_label}{_contacto_label} — Nº {_aid_grupo} · {_primeiro['processo']} "
                        f"({_primeiro['obra_assunto']}) — {_n_pend_grupo} pendente(s) de {len(_itens_grupo)}"
                    )
                    with st.expander(_titulo_grupo, expanded=_expandir_grupo):
                        # --- Editar dados da assistência (cliente/contacto,
                        # Diretor de Obra, observações gerais) — pedido do
                        # Pedro: "colocar novos dados de informação do
                        # cliente". `processo`/`obra_assunto` vêm do PHC e
                        # não são editáveis aqui.
                        _a_dados = dba.get_assistencia(_conn, _aid_grupo)
                        with st.expander("✏️ Editar dados da assistência", expanded=False):
                            with st.form(f"form_editar_assist_{_aid_grupo}"):
                                _ed1, _ed2 = st.columns(2)
                                with _ed1:
                                    _novo_cliente = st.text_input("Cliente / Contacto da assistência", value=_a_dados.get("cliente") or "", key=f"ed_cliente_{_aid_grupo}")
                                    _novo_diretor = st.text_input("Diretor de Obra", value=_a_dados.get("obra_director") or "", key=f"ed_diretor_{_aid_grupo}")
                                with _ed2:
                                    _novo_contacto = st.text_input("Telefone / Email", value=_a_dados.get("contacto") or "", key=f"ed_contacto_{_aid_grupo}")
                                _novas_obs = st.text_area("Observações gerais", value=_a_dados.get("observacoes_gerais") or "", key=f"ed_obs_{_aid_grupo}")
                                if st.form_submit_button("💾 Guardar dados da assistência"):
                                    dba.update_assistencia(
                                        _conn, _aid_grupo, cliente=_novo_cliente, contacto=_novo_contacto,
                                        obra_director=_novo_diretor, observacoes_gerais=_novas_obs,
                                    )
                                    st.success("Dados da assistência atualizados.")
                                    st.rerun()

                        # --- Apagar esta assistência inteira — pedido do
                        # Pedro: "apagar... assistencias" na Gestão de
                        # Pendências. Ação irreversível (apaga também todos
                        # os tópicos desta assistência), por isso exige
                        # confirmação explícita antes do botão ficar ativo.
                        with st.expander("🗑️ Apagar esta assistência", expanded=False):
                            st.warning(
                                "Apaga a assistência inteira, incluindo todos os seus tópicos — ação irreversível."
                            )
                            _confirma_apagar_assist = st.checkbox(
                                "Confirmo que quero apagar esta assistência e todos os seus tópicos",
                                key=f"confirma_apagar_assist_{_aid_grupo}",
                            )
                            if st.button(
                                "🗑️ Apagar assistência", key=f"btn_apagar_assist_{_aid_grupo}",
                                disabled=not _confirma_apagar_assist,
                            ):
                                dba.delete_assistencia(_conn, _aid_grupo)
                                st.success(f"Assistência Nº {_aid_grupo} apagada.")
                                st.rerun()

                        for _it in _itens_grupo:
                            _it["fornecedores_nomes"] = ", ".join(f["nome"] for f in _it.get("fornecedores") or [])
                        _df_grupo = pd.DataFrame(_itens_grupo)[
                            ["id", "topico", "descricao", "observacoes", "estado", "origem", "fornecedores_nomes"]
                        ].rename(columns={
                            "id": "ID", "topico": "Tópico", "descricao": "Descrição", "observacoes": "Observações",
                            "estado": "Estado", "origem": "Origem", "fornecedores_nomes": "Fornecedores",
                        })
                        # Tópico/Descrição/Observações/Estado ficam
                        # editáveis diretamente na tabela (pedido do Pedro:
                        # "possível alterar, alterar estado..."); Origem e
                        # Fornecedores não (Fornecedores é uma relação à
                        # parte, geridos no formulário de baixo).
                        _editado_grupo = st.data_editor(
                            _df_grupo, hide_index=True, use_container_width=True,
                            key=f"editor_pendencias_{_aid_grupo}",
                            disabled=["ID", "Origem", "Fornecedores"],
                            column_config={
                                "Estado": st.column_config.SelectboxColumn("Estado", options=dba.ESTADOS_ITEM, required=True),
                            },
                        )
                        _linhas_alteradas = _editado_grupo[
                            (_editado_grupo["Estado"] != _df_grupo["Estado"])
                            | (_editado_grupo["Tópico"] != _df_grupo["Tópico"])
                            | (_editado_grupo["Descrição"] != _df_grupo["Descrição"])
                            | (_editado_grupo["Observações"] != _df_grupo["Observações"])
                        ]
                        if not _linhas_alteradas.empty and st.button("✅ Gravar alterações", key=f"gravar_pendencias_{_aid_grupo}"):
                            _original_por_id = {int(r["ID"]): r for _, r in _df_grupo.iterrows()}
                            for _, _linha in _linhas_alteradas.iterrows():
                                _id_linha = int(_linha["ID"])
                                _orig = _original_por_id[_id_linha]
                                if not _linha["Tópico"] or not str(_linha["Tópico"]).strip():
                                    st.error(f"Item {_id_linha}: o Tópico não pode ficar vazio — alteração ignorada.")
                                    continue
                                if (
                                    _linha["Tópico"] != _orig["Tópico"]
                                    or _linha["Descrição"] != _orig["Descrição"]
                                    or _linha["Observações"] != _orig["Observações"]
                                ):
                                    dba.update_item_dados(_conn, _id_linha, _linha["Tópico"], _linha["Descrição"], _linha["Observações"])
                                if _linha["Estado"] != _orig["Estado"]:
                                    dba.update_item_estado(_conn, _id_linha, _linha["Estado"])
                            st.success(f"{len(_linhas_alteradas)} item(ns) atualizado(s).")
                            st.rerun()

                        # --- Adicionar novo tópico a esta assistência —
                        # pedido do Pedro: "adicionar novos tópicos" sem ter
                        # de passar pelo separador "Nova Assistência".
                        _fornecedores_ativos_grupo = dba.list_fornecedores(_conn, apenas_ativos=True)
                        _opcoes_fornecedor_grupo = {f"{f['nome']} ({f['tipo']})": f["id"] for f in _fornecedores_ativos_grupo}
                        with st.form(f"form_novo_topico_{_aid_grupo}", clear_on_submit=True):
                            st.markdown("**➕ Adicionar novo tópico a esta assistência**")
                            _nt1, _nt2 = st.columns([1, 2])
                            with _nt1:
                                _novo_topico = st.text_input("Tópico *", placeholder="ex.: Infiltração", key=f"novo_topico_{_aid_grupo}")
                            with _nt2:
                                _nova_descricao = st.text_input("Descrição", key=f"nova_descricao_{_aid_grupo}")
                            _novas_observacoes = st.text_input("Observações", key=f"novas_observacoes_{_aid_grupo}")
                            _novos_fornecedores = st.multiselect(
                                "Fornecedores afetos a este tópico (opcional)", list(_opcoes_fornecedor_grupo.keys()),
                                key=f"novos_fornecedores_{_aid_grupo}",
                            )
                            if st.form_submit_button("➕ Adicionar tópico"):
                                if not _novo_topico or not _novo_topico.strip():
                                    st.error("O Tópico é obrigatório.")
                                else:
                                    _iid_novo_topico = dba.add_item(_conn, _aid_grupo, _novo_topico, _nova_descricao, _novas_observacoes)
                                    if _novos_fornecedores:
                                        dba.set_item_fornecedores(_conn, _iid_novo_topico, [_opcoes_fornecedor_grupo[n] for n in _novos_fornecedores])
                                    st.success(f"Tópico \"{_novo_topico.strip()}\" adicionado.")
                                    st.rerun()

                        # --- Apagar tópico(s) desta assistência — pedido do
                        # Pedro: "apagar Topicos" na Gestão de Pendências.
                        # Ação irreversível, por isso exige confirmação
                        # explícita antes do botão ficar ativo.
                        _opcoes_apagar_topico = {
                            f"Nº {i['id']} — {i['topico']}": i["id"] for i in _itens_grupo
                        }
                        with st.expander("🗑️ Apagar tópicos desta assistência", expanded=False):
                            _topicos_a_apagar = st.multiselect(
                                "Tópico(s) a apagar", list(_opcoes_apagar_topico.keys()),
                                key=f"topicos_apagar_{_aid_grupo}",
                            )
                            _confirma_apagar_topico = st.checkbox(
                                "Confirmo que quero apagar o(s) tópico(s) selecionado(s) (ação irreversível)",
                                key=f"confirma_apagar_topico_{_aid_grupo}",
                            )
                            if st.button(
                                "🗑️ Apagar tópico(s) selecionado(s)", key=f"btn_apagar_topico_{_aid_grupo}",
                                disabled=not (_topicos_a_apagar and _confirma_apagar_topico),
                            ):
                                for _label_apagar in _topicos_a_apagar:
                                    dba.delete_item(_conn, _opcoes_apagar_topico[_label_apagar])
                                st.success(f"{len(_topicos_a_apagar)} tópico(s) apagado(s).")
                                st.rerun()

            st.divider()
            st.markdown("##### Imprimir listagem de uma assistência")
            _assistencias = dba.list_assistencias(_conn)
            _opcoes_assist = {
                f"Nº {a['id']} — {a['processo']} ({a['obra_assunto']}) — {a['estado_geral']}": a["id"]
                for a in _assistencias
            }
            if _opcoes_assist:
                _escolha_pdf = st.selectbox("Assistência", list(_opcoes_assist.keys()), key="escolha_pdf")
                _aid_pdf = _opcoes_assist[_escolha_pdf]
                _a_pdf = dba.get_assistencia(_conn, _aid_pdf)
                st.download_button(
                    "🖨️ Gerar PDF",
                    data=pdf.gerar_pdf_listagem_assistencia(_a_pdf),
                    file_name=f"assistencia_{_a_pdf['id']}_{_a_pdf['processo']}.pdf",
                    mime="application/pdf",
                )

            st.divider()
            st.markdown("##### Imprimir listagem de assistências por Obra")
            _opcoes_obra_print = {}
            for _it_obra in _itens_todos:
                _proc = _it_obra.get("processo")
                if _proc and _proc not in _opcoes_obra_print:
                    _opcoes_obra_print[f"{_proc} — {_it_obra.get('obra_assunto') or '—'}"] = _proc
            if not _opcoes_obra_print:
                st.info("Ainda não há nenhuma assistência criada.")
            else:
                _escolha_obra_print = st.selectbox("Obra", sorted(_opcoes_obra_print.keys()), key="escolha_obra_print")
                _processo_print = _opcoes_obra_print[_escolha_obra_print]
                _assunto_print = next(
                    i["obra_assunto"] for i in _itens_todos if i["processo"] == _processo_print
                )
                _itens_obra_print = dba.list_itens(_conn, {"processo": _processo_print})
                st.download_button(
                    "🖨️ Gerar PDF",
                    data=pdf.gerar_pdf_listagem_por_obra(_processo_print, _assunto_print, _itens_obra_print),
                    file_name=f"assistencias_obra_{_processo_print}.pdf",
                    mime="application/pdf",
                    key="btn_pdf_por_obra",
                )

            st.divider()
            st.markdown("##### Imprimir listagem de assistências por Fornecedor")
            if not _fornecedores_filtro:
                st.info("Ainda não há nenhum fornecedor registado (separador \"Conteúdos do Cliente\").")
            else:
                _escolha_fornecedor_print = st.selectbox(
                    "Fornecedor", list(_opcoes_fornecedor_filtro.keys()), key="escolha_fornecedor_print",
                )
                _fornecedor_id_print = _opcoes_fornecedor_filtro[_escolha_fornecedor_print]
                _fornecedor_print = next(f for f in _fornecedores_filtro if f["id"] == _fornecedor_id_print)
                _itens_fornecedor_print = dba.list_itens(_conn, {"fornecedor_id": _fornecedor_id_print})
                st.download_button(
                    "🖨️ Gerar PDF",
                    data=pdf.gerar_pdf_listagem_por_fornecedor(_fornecedor_print, _itens_fornecedor_print),
                    file_name=f"assistencias_fornecedor_{_fornecedor_print['nome']}.pdf",
                    mime="application/pdf",
                    key="btn_pdf_por_fornecedor",
                )

            st.divider()
            st.markdown("##### Link / QR code para o cliente")
            st.caption(
                "O cliente abre o link (ou lê o QR) e preenche os tópicos, descrição, observações e "
                "fotos da assistência — sem precisar de login."
            )
            if not _opcoes_assist:
                st.info("Cria primeiro uma assistência em \"Nova Assistência\".")
            elif not CLIENTE_APP_URL:
                st.warning(
                    "Falta configurar o endereço da app do cliente — define CLIENTE_APP_URL no `.env` "
                    "(ver guia de configuração) depois de publicares a app cliente."
                )
            else:
                _escolha_link = st.selectbox("Assistência", list(_opcoes_assist.keys()), key="escolha_link_cliente")
                _aid_link = _opcoes_assist[_escolha_link]
                if st.button("🔗 Gerar / Ver Link do Cliente"):
                    try:
                        _token = dba.get_or_create_token(_conn, _aid_link)
                        _a_link = dba.get_assistencia(_conn, _aid_link)
                        _conn_cloud = db_cloud.get_connection()
                        db_cloud.publicar_assistencia(
                            _conn_cloud, _token, processo=_a_link["processo"], obra_assunto=_a_link["obra_assunto"],
                            obra_director=_a_link["obra_director"], cliente=_a_link["cliente"], contacto=_a_link["contacto"],
                            obra_director_email=dba.get_diretor_email(_conn, _a_link["obra_director"]),
                        )
                        # Publica também o estado atual dos tópicos já
                        # abertos (para o cliente consultar em "Tarefas
                        # Pendentes") — snapshot só de leitura, nunca
                        # editável do lado do cliente. Inclui foto (reduzida)
                        # e fornecedores de cada tópico, para a app DTO.
                        db_cloud.publicar_itens_leitura(
                            _conn_cloud, _token, _itens_com_foto_e_fornecedores(_conn, _FOTOS_DIR, _a_link["itens"]),
                        )
                        st.session_state["link_cliente_gerado"] = f"{CLIENTE_APP_URL}/?token={_token}"
                    except db_cloud.CloudNaoConfigurado as _erro_cloud:
                        st.warning(f"Ligação à cloud ainda não configurada: {_erro_cloud}")

                if st.session_state.get("link_cliente_gerado"):
                    _link = st.session_state["link_cliente_gerado"]
                    st.text_input("Link para enviar ao cliente", value=_link)
                    st.image(qr.gerar_qr_png(_link), caption="QR code da assistência", width=220)
                    st.download_button(
                        "⬇️ Descarregar QR code (PNG)", data=qr.gerar_qr_png(_link),
                        file_name=f"qr_assistencia_{_aid_link}.png", mime="image/png",
                    )


# =============================================================================
# "Conteúdos do Cliente" — o "menu do cliente" que a CS Construtora
# apresenta na app pública: condições de garantia, fornecedores (máquinas /
# apoio direto) e o email de cada Diretor de Obra para as notificações.
# Tudo isto é global (igual em todas as assistências) — só é publicado para
# a cloud quando o Pedro carrega em "Publicar", nunca automaticamente, para
# ele poder rever o texto antes de ficar visível aos clientes.
# =============================================================================
if "Conteúdos do Cliente" in _tab_por_nome:
    with _tab_por_nome["Conteúdos do Cliente"]:
        st.subheader("Conteúdos do Cliente")
        st.caption(
            "Isto é o que aparece no link/QR code enviado ao cliente — condições de garantia, "
            "fornecedores e o email de cada Diretor de Obra (para a notificação quando o cliente "
            "adiciona um tópico). Grava aqui e depois carrega em \"Publicar para a cloud\" para "
            "ficar visível aos clientes."
        )

        st.markdown("##### 📜 Condições de Garantia")
        st.caption("Texto livre — aparece no início do link, antes do cliente poder usar o resto. Suporta Markdown (ex. **negrito**, listas com \"- \").")
        _garantia_atual = dba.get_config(_conn, "condicoes_garantia")
        _garantia_nova = st.text_area("Texto das condições de garantia", value=_garantia_atual, height=220, key="texto_garantia")
        if st.button("💾 Guardar condições de garantia"):
            dba.set_config(_conn, "condicoes_garantia", _garantia_nova)
            st.success("Guardado localmente. Lembra-te de \"Publicar para a cloud\" (em baixo) para os clientes verem a versão nova.")

        st.divider()
        st.markdown("##### 🏭 Fornecedores")
        st.caption("Aparecem no menu do cliente (por tipo) e ficam disponíveis para associar a tópicos em \"Nova Assistência\".")
        with st.form("form_novo_fornecedor", clear_on_submit=True):
            _nf1, _nf2, _nf3 = st.columns([2, 1, 2])
            with _nf1:
                _novo_forn_nome = st.text_input("Nome")
            with _nf2:
                _novo_forn_tipo = st.selectbox("Tipo", dba.FORNECEDOR_TIPOS)
            with _nf3:
                _novo_forn_contacto = st.text_input("Contacto (telefone/email)")
            if st.form_submit_button("➕ Adicionar fornecedor"):
                if not _novo_forn_nome or not _novo_forn_nome.strip():
                    st.error("O nome é obrigatório.")
                else:
                    dba.add_fornecedor(_conn, _novo_forn_nome, _novo_forn_tipo, _novo_forn_contacto)
                    st.success(f"Fornecedor '{_novo_forn_nome}' adicionado.")
                    st.rerun()

        _fornecedores_existentes = dba.list_fornecedores(_conn)
        if _fornecedores_existentes:
            _df_fornecedores = pd.DataFrame(_fornecedores_existentes)[
                ["id", "nome", "tipo", "contacto", "ativo"]
            ].rename(columns={"id": "ID", "nome": "Nome", "tipo": "Tipo", "contacto": "Contacto", "ativo": "Ativo"})
            _df_fornecedores["Ativo"] = _df_fornecedores["Ativo"].astype(bool)
            _editado_fornecedores = st.data_editor(
                _df_fornecedores, hide_index=True, use_container_width=True, key="editor_fornecedores",
                disabled=["ID"],
                column_config={"Tipo": st.column_config.SelectboxColumn("Tipo", options=dba.FORNECEDOR_TIPOS, required=True)},
            )
            if st.button("✅ Gravar alterações de fornecedores"):
                for _, _linha_f in _editado_fornecedores.iterrows():
                    dba.update_fornecedor(_conn, int(_linha_f["ID"]), _linha_f["Nome"], _linha_f["Tipo"], _linha_f["Contacto"], bool(_linha_f["Ativo"]))
                st.success("Fornecedores atualizados.")
                st.rerun()
            st.caption("Desativa (\"Ativo\" = falso) em vez de apagar — mantém o histórico dos tópicos já associados a esse fornecedor.")
        else:
            st.info("Ainda não há fornecedores — adiciona o primeiro acima.")

        st.divider()
        st.markdown("##### 📧 Email dos Diretores de Obra")
        st.caption("Usado para a notificação automática quando um cliente adiciona um tópico à assistência dele.")
        _diretores_todos = sorted({a["obra_director"] for a in dba.list_assistencias(_conn) if a.get("obra_director")})
        if not _diretores_todos:
            st.info("Ainda não há nenhum Diretor de Obra associado a uma assistência.")
        else:
            _emails_atuais = dba.list_diretores_email(_conn)
            _novos_emails = {}
            for _diretor_nome in _diretores_todos:
                _novos_emails[_diretor_nome] = st.text_input(
                    _diretor_nome, value=_emails_atuais.get(_diretor_nome, ""), key=f"email_diretor_{_diretor_nome}",
                    placeholder="nome.apelido@csconstrutora.com",
                )
            if st.button("💾 Guardar emails dos diretores"):
                for _diretor_nome, _email_novo in _novos_emails.items():
                    dba.set_diretor_email(_conn, _diretor_nome, _email_novo.strip())
                st.success("Emails atualizados.")

        st.divider()
        st.markdown("##### 📤 Publicar para a cloud")
        st.caption(
            "Envia as condições de garantia e a lista de fornecedores ativos para a app do cliente. "
            "Os emails dos diretores vão automaticamente sempre que gerares/atualizares um link de "
            "uma assistência — não é preciso \"publicar\" para isso."
        )
        if st.button("📤 Publicar condições de garantia e fornecedores"):
            try:
                _conn_cloud_conteudos = db_cloud.get_connection()
                db_cloud.publicar_conteudos(
                    _conn_cloud_conteudos,
                    condicoes_garantia=dba.get_config(_conn, "condicoes_garantia"),
                    fornecedores=dba.list_fornecedores(_conn, apenas_ativos=True),
                )
                st.success("Publicado — já visível na app do cliente.")
            except db_cloud.CloudNaoConfigurado as _erro_cloud:
                st.warning(f"Ligação à cloud ainda não configurada: {_erro_cloud}")

        # =========================================================================
        # Fase 4 (14/10/2026) — "App DTO": acesso de cada Diretor de Obra à app
        # cloud (login próprio, 2º modo de entrada lá), e a lista de obras que a
        # app cloud precisa (sem ligação PHC) para o DTO escolher ao criar uma
        # assistência nova de lá.
        # =========================================================================
        st.divider()
        st.markdown("##### 🔑 Diretores de Obra — acesso à app cloud")
        st.caption(
            "Cada Diretor de Obra pode entrar na app do cliente (mesmo link/QR) com o seu próprio "
            "nome + password, para gerir as assistências das suas obras. Define aqui a password de "
            "cada um — fica guardada só na cloud, em hash, nunca em texto simples."
        )
        if not _diretores_todos:
            st.info("Ainda não há nenhum Diretor de Obra associado a uma assistência.")
        else:
            _acessos_atuais = {d["nome"]: d for d in dba.list_diretores_acesso(_conn)}
            for _diretor_nome in _diretores_todos:
                _acesso = _acessos_atuais.get(_diretor_nome)
                _estado_acesso = "🟢 ativo" if _acesso and _acesso["ativo"] else ("⚪ inativo" if _acesso else "— sem acesso configurado")
                with st.expander(f"{_diretor_nome} ({_estado_acesso})"):
                    _c1, _c2, _c3 = st.columns([2, 1, 1])
                    with _c1:
                        _nova_pass_dto = st.text_input(
                            "Password", type="password", key=f"pass_dto_{_diretor_nome}",
                            placeholder="Definir / reiniciar password",
                        )
                        if st.button("💾 Definir / Reiniciar password", key=f"def_dto_{_diretor_nome}"):
                            if not _nova_pass_dto:
                                st.error("Escreve uma password.")
                            else:
                                try:
                                    _conn_cloud_dto = db_cloud.get_connection()
                                    db_cloud.set_diretor_password(_conn_cloud_dto, _diretor_nome, _nova_pass_dto, ativo=True)
                                    dba.registar_diretor_acesso(_conn, _diretor_nome, ativo=True)
                                    st.success(f"Password de '{_diretor_nome}' definida/atualizada.")
                                    st.rerun()
                                except db_cloud.CloudNaoConfigurado as _erro_cloud:
                                    st.warning(f"Ligação à cloud ainda não configurada: {_erro_cloud}")
                    with _c2:
                        if _acesso and _acesso["ativo"]:
                            if st.button("🔒 Desativar acesso", key=f"desativar_dto_{_diretor_nome}"):
                                try:
                                    _conn_cloud_dto = db_cloud.get_connection()
                                    db_cloud.set_diretor_ativo(_conn_cloud_dto, _diretor_nome, False)
                                    dba.set_diretor_acesso_ativo(_conn, _diretor_nome, False)
                                    st.success(f"Acesso de '{_diretor_nome}' desativado.")
                                    st.rerun()
                                except db_cloud.CloudNaoConfigurado as _erro_cloud:
                                    st.warning(f"Ligação à cloud ainda não configurada: {_erro_cloud}")
                    with _c3:
                        if _acesso and not _acesso["ativo"]:
                            if st.button("🔓 Reativar acesso", key=f"reativar_dto_{_diretor_nome}"):
                                try:
                                    _conn_cloud_dto = db_cloud.get_connection()
                                    db_cloud.set_diretor_ativo(_conn_cloud_dto, _diretor_nome, True)
                                    dba.set_diretor_acesso_ativo(_conn, _diretor_nome, True)
                                    st.success(f"Acesso de '{_diretor_nome}' reativado.")
                                    st.rerun()
                                except db_cloud.CloudNaoConfigurado as _erro_cloud:
                                    st.warning(f"Ligação à cloud ainda não configurada: {_erro_cloud}")

        st.divider()
        st.markdown("##### 🏗️ Publicar Obras para a app cloud")
        st.caption(
            "A app cloud não tem ligação ao PHC — por isso, para o DTO conseguir escolher a obra ao "
            "criar uma assistência nova de lá, publica aqui a lista de obras (processo/assunto/"
            "diretor) sempre que houver obras novas no PHC."
        )
        if st.button("🏗️ Publicar lista de Obras"):
            with st.spinner("A carregar obras do PHC…"):
                _obras_para_publicar = obras_mod.carregar_obras()
            if _obras_para_publicar.empty:
                st.error("Não foi possível carregar a lista de obras do PHC.")
            else:
                try:
                    _conn_cloud_obras = db_cloud.get_connection()
                    db_cloud.publicar_obras(
                        _conn_cloud_obras,
                        [
                            {"processo": r.processo, "obra_assunto": r.assunto, "obra_director": r.director}
                            for r in _obras_para_publicar.itertuples(index=False)
                        ],
                    )
                    st.success(f"{len(_obras_para_publicar)} obra(s) publicada(s) — já disponíveis na app cloud.")
                except db_cloud.CloudNaoConfigurado as _erro_cloud:
                    st.warning(f"Ligação à cloud ainda não configurada: {_erro_cloud}")


# =============================================================================
# "Gestão de Utilizadores" (só admins) — mesmo mecanismo do Painel de Obras.
# =============================================================================
if "Gestão de Utilizadores" in _tab_por_nome:
    with _tab_por_nome["Gestão de Utilizadores"]:
        st.subheader("Gestão de utilizadores")
        _users_atual = load_users()

        st.markdown("##### Utilizadores existentes")
        for _uname, _udata in _users_atual.items():
            with st.expander(f"{_uname}" + (" · admin" if _udata.get("is_admin") else "")):
                _novas_tabs = st.multiselect("Separadores", TABS_DISPONIVEIS, default=_udata.get("tabs", []), key=f"tabs_{_uname}")
                _novo_admin = st.checkbox("É administrador", value=_udata.get("is_admin", False), key=f"admin_{_uname}")
                c1, c2, c3 = st.columns(3)
                with c1:
                    if st.button("Guardar", key=f"save_{_uname}"):
                        update_user_tabs(_users_atual, _uname, _novas_tabs, _novo_admin)
                        save_users(_users_atual)
                        st.success("Atualizado.")
                        st.rerun()
                with c2:
                    _nova_pass = st.text_input("Nova password", type="password", key=f"pass_{_uname}")
                    if st.button("Alterar password", key=f"savepass_{_uname}") and _nova_pass:
                        set_password(_users_atual, _uname, _nova_pass)
                        save_users(_users_atual)
                        st.success("Password alterada.")
                with c3:
                    _pode_apagar = not (_udata.get("is_admin") and count_admins(_users_atual) <= 1)
                    if st.button("🗑️ Remover", key=f"del_{_uname}", disabled=not _pode_apagar):
                        remove_user(_users_atual, _uname)
                        save_users(_users_atual)
                        st.rerun()
                    if not _pode_apagar:
                        st.caption("Não é possível remover o último administrador.")

        st.divider()
        st.markdown("##### Novo utilizador")
        with st.form("novo_utilizador"):
            _nu_nome = st.text_input("Utilizador")
            _nu_pass = st.text_input("Password", type="password")
            _nu_tabs = st.multiselect("Separadores", TABS_DISPONIVEIS, default=["Nova Assistência", "Gestão de Pendências"])
            _nu_admin = st.checkbox("É administrador")
            if st.form_submit_button("Criar"):
                if not _nu_nome or not _nu_pass:
                    st.error("Utilizador e password são obrigatórios.")
                elif _nu_nome in _users_atual:
                    st.error("Já existe um utilizador com esse nome.")
                else:
                    add_user(_users_atual, _nu_nome, _nu_pass, _nu_tabs, _nu_admin)
                    save_users(_users_atual)
                    st.success(f"Utilizador '{_nu_nome}' criado.")
                    st.rerun()
