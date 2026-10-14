"""
app_cliente.py — Formulário público do cliente, Assistências Pós-Obra CS.

Fase 2 (10/10/2026): link/QR code, cliente acrescenta tópicos (com foto),
sem login.

Fase 3 (11/10/2026), pedido do Pedro: condições de garantia no início (o
cliente tem de confirmar que tomou conhecimento antes de poder usar o
resto), consulta das tarefas já abertas e o seu estado, menu de
fornecedores (máquinas / apoio direto) com contactos, e notificação por
email ao Diretor de Obra sempre que o cliente adiciona tópicos.

Fase 4 (14/10/2026), pedido do Pedro: "tem de existir uma app associada à
listagem de assistências, onde cada DTO entre com uma palavra pass e acede
às assistências dele". Esta MESMA app ganha um 2º modo de entrada, sem
`?token=` na URL — login do Diretor de Obra (nome da lista + password),
com um dashboard onde ele cria assistências novas (gera o link/QR, tal
como já fazia o Pedro na app local), vê/gere os tópicos de TODAS as suas
assistências (pendentes/assumidos/em orçamento/resolvidos), e pode dar
acesso de colaborador (password própria, acesso total mas só àquela
assistência) a outra pessoa — nova 4ª aba "🔑 Colaborador" no ecrã normal
do link/token, ao lado das 3 já existentes.

O cliente abre `<esta app>/?token=xxxx`, vê os dados básicos da sua
assistência (sem conseguir ver nem adivinhar as de outros clientes — o
token é um uuid4, nunca um id sequencial). Cada item enviado fica em
`itens_cloud` no Supabase; o Pedro traz isso para a base local quando
quiser, com o botão "Sincronizar com o Cliente" na app principal — esta
app aqui nunca toca na base local nem no PHC. O mesmo vale para tudo o que
o DTO/colaborador fizer aqui: mudanças de estado ficam em fila
(`alteracoes_estado_cloud`) e assistências/tópicos novos em
`assistencias_cloud`/`itens_cloud`, tudo só aplicado à base local quando o
Pedro sincronizar.
"""
import os

import streamlit as st

import db_cloud
import email_cliente
import qr

# Mesma lista/ordem de `db_assist.ESTADOS_ITEM` (app local) — duplicada
# aqui de propósito: esta app cloud é independente, nunca importa nada da
# app local (nem tem acesso ao PHC). Se um dia a lista mudar lá, tem de
# mudar aqui também.
ESTADOS_ITEM = ["Pendente", "Assumido", "Em Orçamento", "Resolvido"]
_ICONE_ESTADO = {"Pendente": "🟡", "Assumido": "🔵", "Em Orçamento": "🟠", "Resolvido": "🟢"}

st.set_page_config(page_title="Assistência Pós-Obra — CS Construtora", page_icon="🛠️", layout="centered")


def _url_base_app() -> str:
    """Endereço público desta própria app (Streamlit Community Cloud) —
    para o dashboard do DTO conseguir montar o link/QR de uma assistência
    nova sem o Pedro ter de o escrever à mão. Lido de `st.secrets`
    (`APP_URL`, ver `.streamlit/secrets.toml.example`) — mesmo princípio
    de `CLIENTE_APP_URL` na app local."""
    try:
        valor = st.secrets.get("APP_URL")
    except Exception:
        valor = None
    return (valor or os.environ.get("APP_URL") or "").rstrip("/")


def _link_assistencia(token: str) -> str:
    base = _url_base_app()
    if not base:
        return f"?token={token}"
    return f"{base}/?token={token}"


# =============================================================================
# Fase 4 — "App DTO": login do Diretor de Obra + dashboard (criar
# assistência nova, gerar link/QR, ver e mudar o estado dos tópicos de
# TODAS as suas assistências, dar acesso de colaborador a outra pessoa).
# =============================================================================
def _pagina_dto(conn) -> None:
    if st.session_state.get("dto_autenticado"):
        _dashboard_dto(conn, st.session_state["dto_autenticado"])
        return

    _diretores_ativos = db_cloud.listar_diretores_cloud(conn, apenas_ativos=True)
    _nomes = [d["nome"] for d in _diretores_ativos]
    if not _nomes:
        st.info(
            "Ainda não há nenhum Diretor de Obra com acesso configurado. Pede à CS Construtora "
            "para configurar o teu acesso na app local (separador \"Conteúdos do Cliente\")."
        )
        return

    with st.form("form_login_dto"):
        _nome_escolhido = st.selectbox("O teu nome", _nomes)
        _password_dto = st.text_input("Password", type="password")
        _submeteu_login = st.form_submit_button("Entrar", type="primary")
    if _submeteu_login:
        if db_cloud.verificar_login_diretor(conn, _nome_escolhido, _password_dto):
            st.session_state["dto_autenticado"] = _nome_escolhido
            st.rerun()
        else:
            st.error("Nome ou password incorretos.")


def _dashboard_dto(conn, nome: str) -> None:
    _col_user, _col_sair = st.columns([4, 1])
    with _col_user:
        st.success(f"👤 {nome} — Diretor de Obra")
    with _col_sair:
        if st.button("Sair"):
            del st.session_state["dto_autenticado"]
            st.rerun()

    _tab_nova, _tab_assist, _tab_topicos = st.tabs([
        "➕ Nova Assistência", "📋 As Minhas Assistências", "🗂️ Tópicos",
    ])

    with _tab_nova:
        _obras = db_cloud.listar_obras_cloud(conn, obra_director=nome)
        if not _obras:
            st.info(
                "Ainda não há obras publicadas para o teu nome. Pede à CS Construtora para "
                "publicar a lista de obras (separador \"Conteúdos do Cliente\" → \"Publicar Obras\")."
            )
        else:
            _opcoes_obra = {f"{o['processo']} — {o['obra_assunto'] or 'sem assunto'}": o for o in _obras}
            with st.form("form_nova_assistencia_dto"):
                _escolha_obra = st.selectbox("Obra", list(_opcoes_obra.keys()))
                _cliente_novo = st.text_input("Cliente / Contacto da assistência")
                _contacto_novo = st.text_input("Telefone / Email")
                _criar_submit = st.form_submit_button("💾 Criar assistência", type="primary")
            if _criar_submit:
                _obra_sel = _opcoes_obra[_escolha_obra]
                _token_novo = db_cloud.gerar_token()
                db_cloud.criar_assistencia_dto(
                    conn, _token_novo, processo=_obra_sel["processo"], obra_assunto=_obra_sel["obra_assunto"],
                    obra_director=nome, cliente=_cliente_novo, contacto=_contacto_novo,
                )
                st.session_state["dto_ultimo_token"] = _token_novo
                st.rerun()

            if st.session_state.get("dto_ultimo_token"):
                _token_novo = st.session_state["dto_ultimo_token"]
                _link_novo = _link_assistencia(_token_novo)
                st.success("Assistência criada! Fica disponível na base principal do Pedro na próxima sincronização.")
                st.text_input("Link para enviar ao cliente", value=_link_novo, key="link_dto_novo")
                if not _url_base_app():
                    st.warning(
                        "Falta configurar o endereço público desta app (`APP_URL` nos secrets) — o "
                        "link acima está incompleto até isso ficar configurado."
                    )
                st.image(qr.gerar_qr_png(_link_novo), caption="QR code da assistência", width=200)
                st.download_button(
                    "⬇️ Descarregar QR code (PNG)", data=qr.gerar_qr_png(_link_novo),
                    file_name=f"qr_{_token_novo}.png", mime="image/png",
                )

    with _tab_assist:
        _assistencias_dto = db_cloud.listar_assistencias_por_diretor(conn, nome)
        if not _assistencias_dto:
            st.info("Ainda não tens nenhuma assistência.")
        else:
            for _a in _assistencias_dto:
                with st.expander(f"{_a['processo']} — {_a.get('cliente') or 'sem cliente'}"):
                    _link_a = _link_assistencia(_a["token"])
                    st.text_input("Link / QR code", value=_link_a, key=f"link_{_a['token']}")
                    st.image(qr.gerar_qr_png(_link_a), width=160)

                    _email_default_a = _a.get("contacto") or ""
                    if "@" not in _email_default_a:
                        _email_default_a = ""
                    _col_email_a, _col_btn_email_a = st.columns([3, 1])
                    with _col_email_a:
                        _email_cliente_a = st.text_input(
                            "Email do cliente", value=_email_default_a, key=f"email_cliente_{_a['token']}",
                        )
                    with _col_btn_email_a:
                        st.write("")
                        st.write("")
                        _enviar_email_a = st.button("✉️ Enviar", key=f"btn_email_{_a['token']}")
                    if _enviar_email_a:
                        if not _email_cliente_a or "@" not in _email_cliente_a:
                            st.error("Indica um email válido.")
                        elif email_cliente.enviar_link_cliente(
                            _email_cliente_a, _link_a, _a["processo"], _a.get("obra_assunto") or "",
                        ):
                            st.success(f"Email enviado para {_email_cliente_a}.")
                        else:
                            st.error(
                                "Não foi possível enviar o email — confirma se NOTIF_EMAIL_USER/"
                                "NOTIF_EMAIL_PASSWORD estão configurados nos Secrets desta app."
                            )

                    st.divider()
                    st.markdown("###### 🗂️ Tópicos desta assistência")
                    _itens_a = db_cloud.listar_itens_leitura(conn, _a["token"])
                    if not _itens_a:
                        st.caption("Ainda sem tópicos.")
                    else:
                        for _it_a in _itens_a:
                            _icone_a = _ICONE_ESTADO.get(_it_a["estado"], "⚪")
                            st.markdown(f"{_icone_a} **{_it_a['topico']}** — {_it_a['estado'] or 'Pendente'}")
                            if _it_a.get("descricao"):
                                st.caption(_it_a["descricao"])

                    st.divider()
                    st.markdown("###### ➕ Adicionar tópico a esta assistência")
                    with st.form(f"form_novo_topico_dto_{_a['token']}", clear_on_submit=True):
                        _topico_dto = st.text_input("Assunto / Tópico *")
                        _descricao_dto = st.text_area("Descrição")
                        _foto_dto = st.file_uploader("Foto (opcional)", type=["jpg", "jpeg", "png"])
                        if st.form_submit_button("Adicionar"):
                            if not _topico_dto or not _topico_dto.strip():
                                st.error("O assunto/tópico é obrigatório.")
                            else:
                                db_cloud.adicionar_item_cliente(
                                    conn, _a["token"], topico=_topico_dto, descricao=_descricao_dto,
                                    foto_bytes=_foto_dto.getvalue() if _foto_dto else None,
                                    foto_nome=_foto_dto.name if _foto_dto else None, origem="DTO",
                                )
                                st.success("Tópico adicionado.")
                                st.rerun()

                    st.divider()
                    st.markdown("###### 🤝 Partilhar esta assistência")
                    st.caption(
                        "Dá acesso total (ver e alterar tópicos), mas só a ESTA assistência, a outra "
                        "pessoa — ex. um encarregado. Define uma password e envia-lha junto com o "
                        "link acima; ela entra na aba \"🔑 Colaborador\"."
                    )
                    if _a.get("tem_partilha"):
                        st.caption("🔓 Já tem uma password de partilha ativa.")
                        if st.button("Revogar acesso de partilha", key=f"revogar_{_a['token']}"):
                            db_cloud.remover_partilha_assistencia(conn, _a["token"])
                            st.rerun()
                    _nova_pass_partilha = st.text_input(
                        "Nova password de partilha", type="password", key=f"pass_partilha_{_a['token']}",
                    )
                    if st.button("Definir / Atualizar password", key=f"def_partilha_{_a['token']}"):
                        if not _nova_pass_partilha:
                            st.error("Escreve uma password.")
                        else:
                            db_cloud.definir_partilha_assistencia(conn, _a["token"], _nova_pass_partilha)
                            st.success("Password de partilha definida.")
                            st.rerun()

    with _tab_topicos:
        _itens_dto = db_cloud.listar_itens_leitura_por_diretor(conn, nome)
        if not _itens_dto:
            st.info("Ainda não há tópicos nas tuas assistências.")
        else:
            _filtro_estado_dto = st.radio(
                "Estado", ["Todos"] + ESTADOS_ITEM, horizontal=True, key="dto_filtro_estado",
            )
            _itens_filtrados_dto = (
                _itens_dto if _filtro_estado_dto == "Todos"
                else [i for i in _itens_dto if (i["estado"] or "Pendente") == _filtro_estado_dto]
            )
            st.caption(f"{len(_itens_filtrados_dto)} tópico(s)")
            for _item in _itens_filtrados_dto:
                _icone = _ICONE_ESTADO.get(_item["estado"], "⚪")
                with st.container(border=True):
                    st.markdown(
                        f"**{_item['topico']}** — {_item['processo']} ({_item['obra_assunto']}) "
                        f"{_icone} {_item['estado'] or 'Pendente'}"
                    )
                    if _item.get("descricao"):
                        st.caption(_item["descricao"])
                    _c_estado, _c_btn = st.columns([2, 1])
                    with _c_estado:
                        _indice_atual = ESTADOS_ITEM.index(_item["estado"]) if _item["estado"] in ESTADOS_ITEM else 0
                        _novo_estado_dto = st.selectbox(
                            "Novo estado", ESTADOS_ITEM, index=_indice_atual,
                            key=f"estado_{_item['token']}_{_item['item_id']}", label_visibility="collapsed",
                        )
                    with _c_btn:
                        if st.button("Atualizar", key=f"upd_{_item['token']}_{_item['item_id']}"):
                            db_cloud.registar_alteracao_estado(
                                conn, _item["token"], _item["item_id"], _novo_estado_dto,
                                assumido_por=(nome if _novo_estado_dto == "Assumido" else None),
                            )
                            st.success("Atualizado — fica visível na app local depois do Pedro sincronizar.")


# =============================================================================
# Fase 4 — aba "🔑 Colaborador": acesso total (ver + alterar tópicos), mas
# só a UMA assistência, protegido por uma password própria que o DTO
# define (ver _dashboard_dto acima). Reutiliza o MESMO link/token do
# cliente — a password é só o que desbloqueia o modo de edição.
# =============================================================================
def _tab_colaborador_conteudo(conn, token: str) -> None:
    _chave_sessao = f"colab_autenticado_{token}"
    if not st.session_state.get(_chave_sessao):
        st.caption(
            "Se o Diretor de Obra te deu uma password de acesso a esta assistência, entra aqui "
            "para ver e alterar os tópicos."
        )
        with st.form(f"form_login_colab_{token}"):
            _pass_colab = st.text_input("Password de acesso", type="password")
            _entrar_colab = st.form_submit_button("Entrar")
        if _entrar_colab:
            if db_cloud.verificar_partilha_assistencia(conn, token, _pass_colab):
                st.session_state[_chave_sessao] = True
                st.rerun()
            else:
                st.error("Password incorreta (ou ainda não foi configurado acesso de colaborador para esta assistência).")
        return

    st.success("✅ Acesso de colaborador ativo — podes ver e alterar todos os tópicos desta assistência.")
    _nome_colab = st.text_input(
        "O teu nome (fica registado quando assumires um tópico)", key=f"colab_nome_{token}",
    )

    st.markdown("##### Tópicos")
    _itens_leitura_colab = db_cloud.listar_itens_leitura(conn, token)
    if not _itens_leitura_colab:
        st.info("Ainda não há tópicos nesta assistência.")
    else:
        for _item in _itens_leitura_colab:
            _icone = _ICONE_ESTADO.get(_item["estado"], "⚪")
            with st.container(border=True):
                st.markdown(f"**{_item['topico']}** — {_icone} {_item['estado'] or 'Pendente'}")
                if _item.get("descricao"):
                    st.caption(_item["descricao"])
                _c1, _c2 = st.columns([2, 1])
                with _c1:
                    _indice_atual = ESTADOS_ITEM.index(_item["estado"]) if _item["estado"] in ESTADOS_ITEM else 0
                    _novo_estado_colab = st.selectbox(
                        "Novo estado", ESTADOS_ITEM, index=_indice_atual,
                        key=f"colab_estado_{token}_{_item['item_id']}", label_visibility="collapsed",
                    )
                with _c2:
                    if st.button("Atualizar", key=f"colab_upd_{token}_{_item['item_id']}"):
                        db_cloud.registar_alteracao_estado(
                            conn, token, _item["item_id"], _novo_estado_colab,
                            assumido_por=(_nome_colab.strip() or None) if _novo_estado_colab == "Assumido" else None,
                        )
                        st.success("Atualizado — fica visível na app local depois do Pedro sincronizar.")

    st.divider()
    st.markdown("##### ➕ Adicionar novo tópico")
    with st.form(f"form_novo_topico_colab_{token}", clear_on_submit=True):
        _topico_colab = st.text_input("Assunto / Tópico *")
        _descricao_colab = st.text_area("Descrição")
        _observacoes_colab = st.text_area("Observações (opcional)")
        _foto_colab = st.file_uploader("Foto (opcional)", type=["jpg", "jpeg", "png"])
        if st.form_submit_button("➕ Adicionar"):
            if not _topico_colab or not _topico_colab.strip():
                st.error("O assunto/tópico é obrigatório.")
            else:
                db_cloud.adicionar_item_cliente(
                    conn, token, topico=_topico_colab, descricao=_descricao_colab, observacoes=_observacoes_colab,
                    foto_bytes=_foto_colab.getvalue() if _foto_colab else None,
                    foto_nome=_foto_colab.name if _foto_colab else None, origem="Colaborador",
                )
                st.success("Tópico adicionado.")
                st.rerun()

    if st.button("Sair do modo colaborador", key=f"colab_sair_{token}"):
        del st.session_state[_chave_sessao]
        st.rerun()

_col_logo, _col_titulo = st.columns([1, 4])
with _col_logo:
    try:
        st.image("Logos/logo2.png", width=90)
    except Exception:
        pass
with _col_titulo:
    st.title("Assistência Pós-Obra")
    st.caption("CS Construtora")

try:
    _conn = db_cloud.get_connection()
except db_cloud.CloudNaoConfigurado as _erro:
    st.error(f"Não foi possível ligar ao servidor: {_erro}")
    st.stop()

_token = st.query_params.get("token", "").strip()

if not _token:
    # Sem token na URL -> não é o link de um cliente, é alguém a abrir a
    # app diretamente. Fase 4: login do Diretor de Obra (2º modo de
    # entrada desta mesma app).
    st.markdown("### 🔑 Acesso de Diretor de Obra")
    st.caption(
        "Este link costuma ser enviado a um cliente para uma assistência específica. Se és "
        "Diretor de Obra, entra abaixo para criar e gerir as tuas assistências."
    )
    _pagina_dto(_conn)
    st.stop()

_assistencia = db_cloud.obter_assistencia_publica(_conn, _token)
if _assistencia is None:
    st.error("Não encontrámos nenhuma assistência com este link. Verifica se o copiaste por inteiro, ou contacta a CS Construtora.")
    st.stop()

st.success(f"Assistência da obra **{_assistencia['obra_assunto']}** (processo {_assistencia['processo']})")
if _assistencia.get("cliente"):
    st.caption(f"Cliente: {_assistencia['cliente']}")

_conteudos = db_cloud.obter_conteudos_cliente(_conn)

# =============================================================================
# Condições de Garantia — o cliente tem de confirmar que tomou conhecimento
# antes de poder ver/usar o resto da app (pedido do Pedro). Se já confirmou
# numa visita anterior (fica registado por token na cloud), não volta a
# pedir — só mostra a confirmação.
# =============================================================================
_aceite_em = db_cloud.obter_aceitacao_garantia(_conn, _token)

st.markdown("### 📜 Condições de Garantia")
if _conteudos.get("condicoes_garantia"):
    st.markdown(_conteudos["condicoes_garantia"])
else:
    st.caption("(A CS Construtora ainda não publicou o texto das condições de garantia.)")

if _aceite_em:
    st.success(f"✅ Confirmaste que tomaste conhecimento destas condições em {_aceite_em:%d/%m/%Y %H:%M}.")
else:
    _confirmou = st.checkbox("Li e tomo conhecimento das condições de garantia acima.")
    if not _confirmou:
        st.info("Confirma que leste as condições de garantia para continuares.")
        st.stop()
    if st.button("Confirmar e continuar", type="primary"):
        db_cloud.registar_aceitacao_garantia(_conn, _token)
        st.rerun()
    st.stop()

st.divider()

# =============================================================================
# Menu do cliente — preencher a assistência / ver tarefas pendentes / ver
# fornecedores. Só aparece depois de confirmadas as condições de garantia.
# =============================================================================
_tab_form, _tab_tarefas, _tab_fornecedores, _tab_colaborador = st.tabs([
    "📝 Reportar Assunto", "📋 Tarefas Pendentes", "🏭 Fornecedores", "🔑 Colaborador",
])

with _tab_form:
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
            # Notificação por email ao Diretor de Obra (best-effort — nunca
            # impede a confirmação de envio ao cliente, mesmo que falhe).
            email_cliente.notificar_novos_topicos(
                director_email=_assistencia.get("obra_director_email"),
                processo=_assistencia["processo"], obra_assunto=_assistencia["obra_assunto"],
                cliente=_assistencia.get("cliente"), itens=st.session_state["itens_cliente"],
            )
            st.session_state["itens_cliente"] = []
            st.session_state["envio_confirmado"] = True
            st.rerun()
    else:
        st.info("Ainda sem itens — adiciona pelo menos um assunto antes de enviar.")

    if st.session_state.get("envio_confirmado"):
        st.success("Obrigado! A CS Construtora foi notificada e vai tratar do assunto.")
        st.session_state["envio_confirmado"] = False

with _tab_tarefas:
    st.caption("Assuntos já abertos nesta assistência e o respetivo estado (atualizado quando a CS Construtora sincroniza).")
    _itens_leitura = db_cloud.listar_itens_leitura(_conn, _token)
    if not _itens_leitura:
        st.info("Ainda não há tarefas registadas nesta assistência.")
    else:
        for _item in _itens_leitura:
            _icone_estado = _ICONE_ESTADO.get(_item["estado"], "⚪")
            with st.container(border=True):
                st.markdown(f"**{_item['topico']}** — {_icone_estado} {_item['estado'] or 'Pendente'}")
                if _item.get("descricao"):
                    st.caption(_item["descricao"])

with _tab_fornecedores:
    st.caption("Contactos dos fornecedores de apoio a esta obra, disponibilizados pela CS Construtora.")
    _fornecedores = _conteudos.get("fornecedores") or []
    if not _fornecedores:
        st.info("A CS Construtora ainda não publicou fornecedores.")
    else:
        for _tipo in ["Máquinas", "Apoio Direto"]:
            _do_tipo = [f for f in _fornecedores if f["tipo"] == _tipo]
            if not _do_tipo:
                continue
            st.markdown(f"##### {_tipo}")
            for _f in _do_tipo:
                st.write(f"**{_f['nome']}** — {_f.get('contacto') or 'sem contacto indicado'}")

with _tab_colaborador:
    _tab_colaborador_conteudo(_conn, _token)
