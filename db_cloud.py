"""
db_cloud.py — Ponte entre a app local (PC do Pedro, ligação PHC) e o
formulário público do cliente (app separada, alojada no Streamlit
Community Cloud), 10/10/2026. Fase 2 do pedido original: "gerar um qrcode,
que permite enviar ao cliente, podendo o mesmo [...] inserir fotos,
descrição e campo de observações".

Porquê uma base à parte (Postgres no Supabase) e não a `assistencias.db`
local: o formulário do cliente tem de estar acessível pela internet (é
para isso que serve o QR code), mas a ligação PHC só existe no PC do
Pedro — por isso esta base cloud só guarda o mínimo preciso para o
cliente ver a assistência e enviar os seus itens; os dados "a sério"
continuam só na base local, e são sincronizados a partir daqui com
`sincronizar_itens_cliente` (ver app.py).

Fluxo:
  1. Pedro cria a assistência localmente (como já fazia) -> gera um link
     ("Gerar Link para Cliente"): isto chama `publicar_assistencia`, que
     cria um `token` único e envia para `assistencias_cloud` só os dados
     que o cliente precisa de ver (obra/cliente), nunca a base toda.
  2. O cliente abre o link (`app_cliente.py`, noutra pasta/app, sem
     login), vê a assistência pelo token, e cada item que adicionar vai
     para `itens_cloud` (com a foto em base64, guardada na própria base
     por decisão do Pedro — mais simples, sem contas extra).
  3. Pedro clica "Sincronizar com o Cliente" na app local: isto puxa os
     itens novos de `itens_cloud` (ainda `sincronizado = false`) para a
     base local `assistencias.db` (via `db_assist.add_item`, origem=
     'Cliente'), grava a foto em disco, e marca-os como sincronizados na
     cloud (não apaga — fica histórico).

Se a ligação à cloud ainda não estiver configurada (sem
`SUPABASE_DB_URL`), todas as funções daqui levantam `CloudNaoConfigurado`
com uma mensagem clara — a app local continua a funcionar sem isto, só
não consegue gerar links novos nem sincronizar.
"""
import base64
import hashlib
import os
import secrets
import uuid
from datetime import datetime


class CloudNaoConfigurado(Exception):
    """A ligação à base cloud (Supabase) ainda não está configurada."""


_ITERATIONS = 200_000


def hash_password(password: str, salt: str = None) -> tuple:
    """PBKDF2-HMAC-SHA256, 200 000 iterações, salt aleatório — mesmo
    esquema de `auth.py` (users.json da app local), implementado aqui para
    que este ficheiro (copiado para as duas pastas, local e cloud) seja a
    única fonte desta lógica: a app local usa isto para DEFINIR a password
    de um Diretor de Obra (`set_diretor_password`), a app cloud usa isto
    (via `verificar_login_diretor`/`verificar_partilha_assistencia`) para a
    VERIFICAR — nunca duplicado nos dois lados. Se `salt` não for dado,
    gera um novo (uso: ao definir uma password nova)."""
    salt = salt or secrets.token_hex(16)
    password_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS).hex()
    return salt, password_hash


def verify_password_hash(password: str, salt: str, password_hash: str) -> bool:
    if not password or not salt or not password_hash:
        return False
    _, calculado = hash_password(password, salt)
    return calculado == password_hash


_SCHEMA = """
CREATE TABLE IF NOT EXISTS assistencias_cloud (
    token TEXT PRIMARY KEY,
    processo TEXT,
    obra_assunto TEXT,
    obra_director TEXT,
    obra_director_email TEXT,
    cliente TEXT,
    contacto TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    origem TEXT NOT NULL DEFAULT 'Interno',
    sincronizado_local BOOLEAN NOT NULL DEFAULT TRUE,
    partilha_salt TEXT,
    partilha_password_hash TEXT
);

CREATE TABLE IF NOT EXISTS itens_cloud (
    id BIGSERIAL PRIMARY KEY,
    token TEXT NOT NULL REFERENCES assistencias_cloud(token),
    topico TEXT NOT NULL,
    descricao TEXT,
    observacoes TEXT,
    foto_base64 TEXT,
    foto_nome TEXT,
    origem TEXT NOT NULL DEFAULT 'Cliente',
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    sincronizado BOOLEAN NOT NULL DEFAULT FALSE
);

-- Fase 4 (14/10/2026), pedido do Pedro: "tem de existir uma app associada à
-- listagem de assistências, onde cada DTO entre com uma palavra pass".
-- Login próprio do Diretor de Obra na app cloud (2º modo de entrada, ao
-- lado do link/token do cliente) — nome tem de corresponder exatamente ao
-- `obra_director` usado nas assistências, para ligar as duas coisas. A
-- password só é definida/alterada a partir da app local (Pedro); esta
-- tabela nunca é escrita pela app cloud.
CREATE TABLE IF NOT EXISTS diretores_cloud (
    nome TEXT PRIMARY KEY,
    salt TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    ativo BOOLEAN NOT NULL DEFAULT TRUE,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Lista de obras (processo/assunto/diretor), publicada pela app local a
-- partir do PHC (`obras.carregar_obras()`) — a app cloud/DTO não tem
-- ligação PHC, por isso precisa deste "espelho" mínimo para o DTO poder
-- escolher a obra ao criar uma assistência nova na app cloud. Reescrita
-- por inteiro a cada publicação.
CREATE TABLE IF NOT EXISTS obras_cloud (
    processo TEXT PRIMARY KEY,
    obra_assunto TEXT,
    obra_director TEXT
);

-- Fila de alterações feitas na app cloud (pelo DTO, ou por um colaborador
-- com acesso de partilha a uma assistência) que ainda têm de ser
-- aplicadas na base local — mudar o estado de um tópico, ou marcá-lo como
-- "Assumido". Nunca escreve direto em `itens_leitura_cloud` (que é só
-- espelho de leitura vindo da base local) nem na base local — o Pedro
-- aplica isto ao "Sincronizar", e só depois o espelho é reescrito a
-- partir da base local já atualizada.
CREATE TABLE IF NOT EXISTS alteracoes_estado_cloud (
    id BIGSERIAL PRIMARY KEY,
    token TEXT NOT NULL REFERENCES assistencias_cloud(token),
    item_id INTEGER NOT NULL,
    novo_estado TEXT NOT NULL,
    assumido_por TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    aplicado BOOLEAN NOT NULL DEFAULT FALSE
);

-- Fase 3 (11/10/2026) — "menu do cliente": condições de garantia + lista de
-- fornecedores, geridos pelo Pedro na app local e publicados aqui (conteúdo
-- GLOBAL, igual para todos os clientes — não é por assistência). Linha
-- única (id fixo = 1) para as condições de garantia; fornecedores_cloud
-- reescrita por inteiro a cada publicação (lista costuma ser pequena).
CREATE TABLE IF NOT EXISTS conteudos_cliente (
    id INTEGER PRIMARY KEY DEFAULT 1,
    condicoes_garantia TEXT,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fornecedores_cloud (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL,
    tipo TEXT NOT NULL,
    contacto TEXT
);

-- Snapshot (só leitura) dos tópicos já existentes de uma assistência, para
-- o cliente conseguir consultar "tarefas pendentes já abertas e o seu
-- estado" sem a app cliente alguma vez tocar na base local/PHC. Reescrito
-- por inteiro (apaga + insere) sempre que o Pedro gera o link ou sincroniza.
CREATE TABLE IF NOT EXISTS itens_leitura_cloud (
    token TEXT NOT NULL REFERENCES assistencias_cloud(token),
    item_id INTEGER NOT NULL,
    topico TEXT NOT NULL,
    descricao TEXT,
    estado TEXT,
    origem TEXT,
    criado_em TEXT,
    PRIMARY KEY (token, item_id)
);

-- Registo de que o cliente confirmou "li e tomo conhecimento" das condições
-- de garantia, com data/hora — pedido do Pedro ("onde o cliente toma
-- conhecimento das mesmas").
CREATE TABLE IF NOT EXISTS aceitacoes_garantia (
    token TEXT PRIMARY KEY REFERENCES assistencias_cloud(token),
    aceite_em TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _connection_string() -> str:
    """Lê a connection string do Supabase de `SUPABASE_DB_URL` (variável de
    ambiente / .env local) ou, se correr dentro do Streamlit (app cliente
    na cloud), de `st.secrets["SUPABASE_DB_URL"]`. Nunca vem escrita no
    código — é o Pedro que a cola no `.env`/secrets, ver README/guia de
    setup."""
    valor = os.environ.get("SUPABASE_DB_URL")
    if valor:
        return valor
    try:
        import streamlit as st  # import tardio: só a app cliente/local corre dentro do Streamlit
        return st.secrets.get("SUPABASE_DB_URL")
    except Exception:
        return None


# Corrido uma única vez por processo (não a cada clique) — ver nota em
# get_connection(). Um projeto Supabase gratuito "adormece" ao fim de uns
# dias sem uso e demora mais no primeiro pedido a seguir a acordar; repetir
# esta criação/migração de tabelas em todos os cliques só multiplicava o
# risco de apanhar isso como um "statement timeout".
_schema_pronto = False


def get_connection():
    """Devolve uma ligação psycopg2 à base Supabase. Na primeira vez que
    corre neste processo, cria/migra as tabelas (idempotente — "IF NOT
    EXISTS" em tudo); das vezes seguintes só liga, sem repetir isso.
    Levanta `CloudNaoConfigurado` se a connection string não estiver
    definida, se a ligação falhar (rede/credenciais), ou se a criação das
    tabelas falhar (nunca deixa um erro em bruto do psycopg2 chegar ao
    ecrã)."""
    global _schema_pronto
    dsn = _connection_string()
    if not dsn:
        raise CloudNaoConfigurado(
            "Falta configurar SUPABASE_DB_URL (.env local ou secrets da app cliente) — "
            "ver guia de configuração (GitHub + Supabase + Streamlit Cloud)."
        )
    try:
        import psycopg2
        conn = psycopg2.connect(dsn, connect_timeout=10)
    except ModuleNotFoundError as exc:
        raise CloudNaoConfigurado("Falta instalar psycopg2-binary (está no requirements.txt).") from exc
    except Exception as exc:
        raise CloudNaoConfigurado(f"Não foi possível ligar à base cloud: {exc}") from exc

    if not _schema_pronto:
        try:
            with conn.cursor() as cur:
                # Alarga a janela desta sessão para a criação/migração das
                # tabelas — um projeto Supabase "a acordar" (free tier, ao
                # fim de uns dias sem uso) pode demorar mais do que o
                # limite por omissão num pooler, mesmo para um ALTER TABLE
                # trivial.
                cur.execute("SET statement_timeout = '30000'")  # 30s, só nesta transação
                cur.execute(_SCHEMA)
                # Migração idempotente para bases já publicadas antes da
                # Fase 3/4 (colunas novas em tabelas que já existiam) —
                # Postgres suporta "ADD COLUMN IF NOT EXISTS" nativamente,
                # ao contrário do SQLite.
                cur.execute("ALTER TABLE assistencias_cloud ADD COLUMN IF NOT EXISTS obra_director_email TEXT")
                cur.execute("ALTER TABLE assistencias_cloud ADD COLUMN IF NOT EXISTS origem TEXT NOT NULL DEFAULT 'Interno'")
                cur.execute("ALTER TABLE assistencias_cloud ADD COLUMN IF NOT EXISTS sincronizado_local BOOLEAN NOT NULL DEFAULT TRUE")
                cur.execute("ALTER TABLE assistencias_cloud ADD COLUMN IF NOT EXISTS partilha_salt TEXT")
                cur.execute("ALTER TABLE assistencias_cloud ADD COLUMN IF NOT EXISTS partilha_password_hash TEXT")
                cur.execute("ALTER TABLE itens_cloud ADD COLUMN IF NOT EXISTS foto_nome TEXT")
                cur.execute("ALTER TABLE itens_cloud ADD COLUMN IF NOT EXISTS origem TEXT NOT NULL DEFAULT 'Cliente'")
            conn.commit()
            _schema_pronto = True
        except Exception as exc:
            conn.rollback()
            conn.close()
            raise CloudNaoConfigurado(
                f"A base cloud ligou, mas falhou a preparar as tabelas (tenta outra vez — se o "
                f"projeto Supabase estava inativo, a 1ª tentativa a seguir a acordar é normal falhar): {exc}"
            ) from exc
    return conn


def publicar_assistencia(conn, token: str, processo: str, obra_assunto: str,
                          obra_director: str, cliente: str, contacto: str,
                          obra_director_email: str = "", origem: str = "Interno") -> None:
    """Envia (ou atualiza) os dados mínimos da assistência para a cloud,
    para o cliente conseguir vê-la pelo link. `token` já vem gerado
    localmente (`db_assist.get_or_create_token`) para o caso normal
    (`origem="Interno"` — assistência criada no PC do Pedro). `origem`
    também pode ser `"DTO"`: usado por `criar_assistencia_dto`, quando é a
    app cloud a criar a assistência pela primeira vez — nesse caso
    `sincronizado_local` fica a False (ver essa função) até o Pedro
    sincronizar e ela passar a existir também na base local. Note que o
    `ON CONFLICT` nunca atualiza `origem`/`sincronizado_local` — só o
    INSERT inicial é que os define, para não "reverter" o estado de
    sincronização de uma assistência ao republicá-la por outro motivo (ex.
    ao gerar de novo o QR code). `obra_director_email` é o email para onde
    vai a notificação quando o cliente mexe nesta assistência (lido
    localmente de `db_assist.get_diretor_email`)."""
    sincronizado_local = (origem == "Interno")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO assistencias_cloud
                (token, processo, obra_assunto, obra_director, obra_director_email, cliente, contacto,
                 origem, sincronizado_local)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (token) DO UPDATE SET
                processo = EXCLUDED.processo,
                obra_assunto = EXCLUDED.obra_assunto,
                obra_director = EXCLUDED.obra_director,
                obra_director_email = EXCLUDED.obra_director_email,
                cliente = EXCLUDED.cliente,
                contacto = EXCLUDED.contacto
            """,
            (token, processo, obra_assunto, obra_director, obra_director_email, cliente, contacto,
             origem, sincronizado_local),
        )
    conn.commit()


def criar_assistencia_dto(conn, token: str, processo: str, obra_assunto: str,
                           obra_director: str, cliente: str = "", contacto: str = "") -> None:
    """O DTO cria uma assistência nova diretamente na app cloud (pedido do
    Pedro: "a criação da assistência pode ser feita na app"). É só um atalho
    para `publicar_assistencia(..., origem="DTO")` — fica com
    `sincronizado_local = False` até o Pedro clicar "Sincronizar" na app
    local, altura em que `sync.py` cria o cabeçalho correspondente em
    `assistencias.db` com este MESMO token (para o link/QR já gerado aqui
    continuar a funcionar depois de sincronizado)."""
    publicar_assistencia(
        conn, token, processo=processo, obra_assunto=obra_assunto, obra_director=obra_director,
        cliente=cliente, contacto=contacto, origem="DTO",
    )


def obter_assistencia_publica(conn, token: str) -> dict:
    """Dados da assistência para o formulário do cliente ver (app
    `app_cliente.py`). Devolve None se o token não existir (link
    inválido/expirado)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT token, processo, obra_assunto, obra_director, obra_director_email, cliente, contacto, criado_em "
            "FROM assistencias_cloud WHERE token = %s",
            (token,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        colunas = [c.name for c in cur.description]
        return dict(zip(colunas, row))


def adicionar_item_cliente(conn, token: str, topico: str, descricao: str = "",
                            observacoes: str = "", foto_bytes: bytes = None,
                            foto_nome: str = None, origem: str = "Cliente") -> int:
    """Acrescenta um tópico/item a uma assistência vinda da cloud — tanto o
    cliente (sem login, `origem="Cliente"`, comportamento original) como
    agora o DTO/colaborador a partir do seu dashboard (`origem="DTO"`) usam
    esta mesma função. A foto (se houver) vai em base64 — decisão do Pedro
    de a guardar diretamente na base cloud, sem serviço de armazenamento à
    parte; `foto_nome` (nome original do ficheiro, ex. "foto1.jpg") é
    guardado só para `sync.py` conseguir recriar a extensão certa ao
    gravar o ficheiro localmente — antes da Fase 4 isto vinha sempre
    gravado como ".jpg", mesmo quando a foto era .png."""
    if not topico or not topico.strip():
        raise ValueError("Tópico é obrigatório.")
    foto_base64 = base64.b64encode(foto_bytes).decode("ascii") if foto_bytes else None
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO itens_cloud (token, topico, descricao, observacoes, foto_base64, foto_nome, origem) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (token, topico.strip(), descricao or "", observacoes or "", foto_base64, foto_nome, origem),
        )
        novo_id = cur.fetchone()[0]
    conn.commit()
    return novo_id


def listar_itens_pendentes_sincronizacao(conn, tokens: list) -> list:
    """Itens do cliente/DTO ainda não trazidos para a base local, só para os
    `tokens` passados (as assistências que existem nesta base local —
    nunca puxa tokens de outra instalação por engano)."""
    if not tokens:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT i.id, i.token, i.topico, i.descricao, i.observacoes, i.foto_base64, i.foto_nome, "
            "       i.origem, i.criado_em, a.processo, a.obra_assunto "
            "FROM itens_cloud i JOIN assistencias_cloud a ON a.token = i.token "
            "WHERE i.sincronizado = FALSE AND i.token = ANY(%s) "
            "ORDER BY i.criado_em",
            (list(tokens),),
        )
        colunas = [c.name for c in cur.description]
        return [dict(zip(colunas, row)) for row in cur.fetchall()]


def marcar_sincronizado(conn, item_cloud_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE itens_cloud SET sincronizado = TRUE WHERE id = %s", (item_cloud_id,))
    conn.commit()


def publicar_itens_leitura(conn, token: str, itens: list) -> None:
    """Reescreve o snapshot (só leitura) dos tópicos da assistência, para o
    cliente consultar "tarefas pendentes já abertas e o seu estado" — nunca
    é o cliente a mexer nisto, só o Pedro publica (ao gerar o link ou ao
    sincronizar). `itens`: lista de dicts com id/topico/descricao/estado/
    origem/data_criacao (o formato que `db_assist.list_itens` já devolve)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM itens_leitura_cloud WHERE token = %s", (token,))
        for item in itens:
            cur.execute(
                "INSERT INTO itens_leitura_cloud (token, item_id, topico, descricao, estado, origem, criado_em) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (token, item["id"], item["topico"], item.get("descricao") or "",
                 item.get("estado") or "", item.get("origem") or "", str(item.get("data_criacao") or "")),
            )
    conn.commit()


def listar_itens_leitura(conn, token: str) -> list:
    """Tópicos já existentes desta assistência, para o cliente consultar
    (menu "Tarefas desta assistência") — nunca editável no lado do
    cliente."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT item_id, topico, descricao, estado, origem, criado_em "
            "FROM itens_leitura_cloud WHERE token = %s ORDER BY criado_em DESC, item_id DESC",
            (token,),
        )
        colunas = [c.name for c in cur.description]
        return [dict(zip(colunas, row)) for row in cur.fetchall()]


def publicar_conteudos(conn, condicoes_garantia: str, fornecedores: list) -> None:
    """Publica o "menu do cliente" (condições de garantia + lista de
    fornecedores) — conteúdo global, igual em todas as assistências, gerido
    pelo Pedro numa só vez na app local. `fornecedores`: lista de dicts
    id/nome/tipo/contacto (só os ativos — filtra antes de chamar)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conteudos_cliente (id, condicoes_garantia, atualizado_em)
            VALUES (1, %s, now())
            ON CONFLICT (id) DO UPDATE SET
                condicoes_garantia = EXCLUDED.condicoes_garantia,
                atualizado_em = now()
            """,
            (condicoes_garantia,),
        )
        cur.execute("DELETE FROM fornecedores_cloud")
        for f in fornecedores:
            cur.execute(
                "INSERT INTO fornecedores_cloud (id, nome, tipo, contacto) VALUES (%s, %s, %s, %s)",
                (f["id"], f["nome"], f["tipo"], f.get("contacto") or ""),
            )
    conn.commit()


def obter_conteudos_cliente(conn) -> dict:
    """Condições de garantia + fornecedores, para a app cliente mostrar no
    seu menu. Nunca levanta erro por não existir ainda — devolve vazio (o
    Pedro ainda não publicou nada)."""
    with conn.cursor() as cur:
        cur.execute("SELECT condicoes_garantia FROM conteudos_cliente WHERE id = 1")
        row = cur.fetchone()
        condicoes = row[0] if row and row[0] else ""
        cur.execute("SELECT id, nome, tipo, contacto FROM fornecedores_cloud ORDER BY tipo, nome")
        colunas = [c.name for c in cur.description]
        fornecedores = [dict(zip(colunas, r)) for r in cur.fetchall()]
    return {"condicoes_garantia": condicoes, "fornecedores": fornecedores}


def registar_aceitacao_garantia(conn, token: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO aceitacoes_garantia (token) VALUES (%s) ON CONFLICT (token) DO NOTHING",
            (token,),
        )
    conn.commit()


def obter_aceitacao_garantia(conn, token: str):
    """Devolve a data/hora em que o cliente confirmou as condições de
    garantia para este token, ou None se ainda não confirmou — para a app
    cliente não voltar a pedir se já confirmou numa visita anterior."""
    with conn.cursor() as cur:
        cur.execute("SELECT aceite_em FROM aceitacoes_garantia WHERE token = %s", (token,))
        row = cur.fetchone()
        return row[0] if row else None


def gerar_token() -> str:
    return uuid.uuid4().hex


# =============================================================================
# Fase 4 (14/10/2026) — "app DTO": login dos Diretores de Obra na app cloud,
# lista de obras (para a app DTO criar assistências sem acesso ao PHC), e a
# fila de alterações de estado feitas na cloud (estado/"Assumido") à espera
# de serem aplicadas na base local.
# =============================================================================

def set_diretor_password(conn, nome: str, password: str, ativo: bool = True) -> None:
    """Define/reinicia a password de acesso de um Diretor de Obra à app
    cloud (login do 2º modo, "App DTO"). Só chamado a partir da app local
    (separador "Conteúdos do Cliente" → "Diretores de Obra"); a app cloud
    nunca escreve aqui, só lê para verificar o login. A password em claro
    nunca é guardada — só o hash (ver `hash_password`)."""
    salt, password_hash = hash_password(password)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO diretores_cloud (nome, salt, password_hash, ativo, atualizado_em)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (nome) DO UPDATE SET
                salt = EXCLUDED.salt,
                password_hash = EXCLUDED.password_hash,
                ativo = EXCLUDED.ativo,
                atualizado_em = now()
            """,
            (nome, salt, password_hash, ativo),
        )
    conn.commit()


def set_diretor_ativo(conn, nome: str, ativo: bool) -> None:
    """Ativa/desativa o acesso de um Diretor de Obra à app cloud, sem
    mexer na password já definida — usado para "retirar" o acesso
    temporariamente sem ter de voltar a configurar tudo se for reativado."""
    with conn.cursor() as cur:
        cur.execute("UPDATE diretores_cloud SET ativo = %s, atualizado_em = now() WHERE nome = %s", (ativo, nome))
    conn.commit()


def listar_diretores_cloud(conn, apenas_ativos: bool = False) -> list:
    sql = "SELECT nome, ativo, atualizado_em FROM diretores_cloud"
    if apenas_ativos:
        sql += " WHERE ativo = TRUE"
    sql += " ORDER BY nome"
    with conn.cursor() as cur:
        cur.execute(sql)
        colunas = [c.name for c in cur.description]
        return [dict(zip(colunas, row)) for row in cur.fetchall()]


def verificar_login_diretor(conn, nome: str, password: str) -> bool:
    """Login do DTO na app cloud — nome escolhido de uma lista (nunca
    escrito à mão, para não haver erros de maiúsculas/acentos face ao
    `obra_director` das assistências) + password própria."""
    with conn.cursor() as cur:
        cur.execute("SELECT salt, password_hash, ativo FROM diretores_cloud WHERE nome = %s", (nome,))
        row = cur.fetchone()
    if row is None or not row[2]:
        return False
    return verify_password_hash(password, row[0], row[1])


def publicar_obras(conn, obras: list) -> None:
    """Reescreve por inteiro a lista de obras disponíveis na app cloud
    (para o DTO escolher a obra ao criar uma assistência nova) — publicada
    pela app local a partir do PHC (`obras.carregar_obras()`). `obras`:
    lista de dicts com processo/obra_assunto/obra_director."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM obras_cloud")
        for o in obras:
            cur.execute(
                "INSERT INTO obras_cloud (processo, obra_assunto, obra_director) VALUES (%s, %s, %s) "
                "ON CONFLICT (processo) DO UPDATE SET obra_assunto = EXCLUDED.obra_assunto, "
                "obra_director = EXCLUDED.obra_director",
                (o["processo"], o.get("obra_assunto") or "", o.get("obra_director") or ""),
            )
    conn.commit()


def listar_obras_cloud(conn, obra_director: str = None) -> list:
    """Lista de obras para o picker da app DTO — se `obra_director` for
    dado, mostra só as obras desse diretor (o caso normal: o DTO só cria
    assistências nas suas próprias obras); sem filtro, devolve todas (não
    usado na app DTO, só aqui por completude/testes)."""
    with conn.cursor() as cur:
        if obra_director:
            cur.execute(
                "SELECT processo, obra_assunto, obra_director FROM obras_cloud "
                "WHERE obra_director = %s ORDER BY processo",
                (obra_director,),
            )
        else:
            cur.execute("SELECT processo, obra_assunto, obra_director FROM obras_cloud ORDER BY processo")
        colunas = [c.name for c in cur.description]
        return [dict(zip(colunas, row)) for row in cur.fetchall()]


def listar_assistencias_nao_sincronizadas(conn) -> list:
    """Assistências criadas na app cloud (pelo DTO) que ainda não existem
    na base local — `sync.py` cria o cabeçalho local para cada uma destas
    ANTES de puxar os itens/tópicos, para o `token` já existir localmente e
    os itens ficarem associados à assistência certa."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT token, processo, obra_assunto, obra_director, cliente, contacto, criado_em "
            "FROM assistencias_cloud WHERE origem = 'DTO' AND sincronizado_local = FALSE "
            "ORDER BY criado_em"
        )
        colunas = [c.name for c in cur.description]
        return [dict(zip(colunas, row)) for row in cur.fetchall()]


def marcar_assistencia_sincronizada(conn, token: str) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE assistencias_cloud SET sincronizado_local = TRUE WHERE token = %s", (token,))
    conn.commit()


def listar_assistencias_por_diretor(conn, obra_director: str) -> list:
    """Assistências deste Diretor de Obra, para o dashboard da app DTO
    ("As minhas assistências" — gerar/partilhar link, ver tópicos)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT token, processo, obra_assunto, cliente, contacto, criado_em, "
            "       (partilha_password_hash IS NOT NULL) AS tem_partilha "
            "FROM assistencias_cloud WHERE obra_director = %s ORDER BY criado_em DESC",
            (obra_director,),
        )
        colunas = [c.name for c in cur.description]
        return [dict(zip(colunas, row)) for row in cur.fetchall()]


def listar_itens_leitura_por_diretor(conn, obra_director: str) -> list:
    """Todos os tópicos (já sincronizados da base local) das assistências
    deste Diretor de Obra — para o dashboard da app DTO mostrar os
    "tópicos pendentes, resolvidos e assumidos" entre TODAS as suas
    assistências, não só uma de cada vez."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT l.token, l.item_id, l.topico, l.descricao, l.estado, l.origem, l.criado_em, "
            "       a.processo, a.obra_assunto, a.cliente "
            "FROM itens_leitura_cloud l JOIN assistencias_cloud a ON a.token = l.token "
            "WHERE a.obra_director = %s "
            "ORDER BY l.criado_em DESC, l.item_id DESC",
            (obra_director,),
        )
        colunas = [c.name for c in cur.description]
        return [dict(zip(colunas, row)) for row in cur.fetchall()]


def registar_alteracao_estado(conn, token: str, item_id: int, novo_estado: str, assumido_por: str = None) -> None:
    """O DTO (ou um colaborador com acesso de partilha) muda o estado de um
    tópico já existente, ou marca-o como "Assumido" — fica em fila
    (`alteracoes_estado_cloud`) até o Pedro sincronizar; nunca escreve
    direto na base local nem no espelho de leitura, que só o Pedro
    atualiza."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO alteracoes_estado_cloud (token, item_id, novo_estado, assumido_por) "
            "VALUES (%s, %s, %s, %s)",
            (token, item_id, novo_estado, assumido_por),
        )
    conn.commit()


def listar_alteracoes_estado_pendentes(conn, tokens: list) -> list:
    """Alterações de estado ainda não aplicadas na base local, só para os
    `tokens` desta base local (mesmo cuidado que `listar_itens_pendentes_
    sincronizacao` — nunca mistura instalações)."""
    if not tokens:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, token, item_id, novo_estado, assumido_por, criado_em "
            "FROM alteracoes_estado_cloud WHERE aplicado = FALSE AND token = ANY(%s) "
            "ORDER BY criado_em",
            (list(tokens),),
        )
        colunas = [c.name for c in cur.description]
        return [dict(zip(colunas, row)) for row in cur.fetchall()]


def marcar_alteracao_aplicada(conn, alteracao_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE alteracoes_estado_cloud SET aplicado = TRUE WHERE id = %s", (alteracao_id,))
    conn.commit()


def definir_partilha_assistencia(conn, token: str, password: str) -> None:
    """O DTO dá acesso a esta UMA assistência a outra pessoa (ex. um
    encarregado), protegido por password — pedido do Pedro: "pode dar
    permissão para outra pessoa com uma pass pode aceder e alterar a
    assistência". Acesso total (ver/alterar tópicos), mas só a ESTA
    assistência — a pessoa nunca vê as outras do DTO. Reutiliza o mesmo
    link/token já usado pelo cliente; entrar com a password é que desbloqueia
    o modo de edição (ver `app_cliente.py`)."""
    salt, password_hash = hash_password(password)
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE assistencias_cloud SET partilha_salt = %s, partilha_password_hash = %s WHERE token = %s",
            (salt, password_hash, token),
        )
    conn.commit()


def remover_partilha_assistencia(conn, token: str) -> None:
    """Revoga o acesso de partilha desta assistência (a password deixa de
    funcionar) — o DTO continua a ter o seu próprio acesso, só o acesso
    dado à outra pessoa é que é retirado."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE assistencias_cloud SET partilha_salt = NULL, partilha_password_hash = NULL WHERE token = %s",
            (token,),
        )
    conn.commit()


def verificar_partilha_assistencia(conn, token: str, password: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT partilha_salt, partilha_password_hash FROM assistencias_cloud WHERE token = %s", (token,))
        row = cur.fetchone()
    if row is None or not row[0] or not row[1]:
        return False
    return verify_password_hash(password, row[0], row[1])
