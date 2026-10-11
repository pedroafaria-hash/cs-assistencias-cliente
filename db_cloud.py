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
import os
import uuid
from datetime import datetime


class CloudNaoConfigurado(Exception):
    """A ligação à base cloud (Supabase) ainda não está configurada."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS assistencias_cloud (
    token TEXT PRIMARY KEY,
    processo TEXT,
    obra_assunto TEXT,
    obra_director TEXT,
    cliente TEXT,
    contacto TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS itens_cloud (
    id BIGSERIAL PRIMARY KEY,
    token TEXT NOT NULL REFERENCES assistencias_cloud(token),
    topico TEXT NOT NULL,
    descricao TEXT,
    observacoes TEXT,
    foto_base64 TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    sincronizado BOOLEAN NOT NULL DEFAULT FALSE
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


def get_connection():
    """Devolve uma ligação psycopg2 à base Supabase, com `init_cloud_db`
    já corrido (criação das tabelas, idempotente). Levanta
    `CloudNaoConfigurado` se a connection string não estiver definida, ou
    se a ligação falhar (rede/credenciais)."""
    dsn = _connection_string()
    if not dsn:
        raise CloudNaoConfigurado(
            "Falta configurar SUPABASE_DB_URL (.env local ou secrets da app cliente) — "
            "ver guia de configuração (GitHub + Supabase + Streamlit Cloud)."
        )
    try:
        import psycopg2
        conn = psycopg2.connect(dsn, connect_timeout=8)
    except ModuleNotFoundError as exc:
        raise CloudNaoConfigurado("Falta instalar psycopg2-binary (está no requirements.txt).") from exc
    except Exception as exc:
        raise CloudNaoConfigurado(f"Não foi possível ligar à base cloud: {exc}") from exc
    with conn.cursor() as cur:
        cur.execute(_SCHEMA)
    conn.commit()
    return conn


def publicar_assistencia(conn, token: str, processo: str, obra_assunto: str,
                          obra_director: str, cliente: str, contacto: str) -> None:
    """Envia (ou atualiza) os dados mínimos da assistência para a cloud,
    para o cliente conseguir vê-la pelo link. `token` já vem gerado
    localmente (`db_assist.get_or_create_token`)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO assistencias_cloud (token, processo, obra_assunto, obra_director, cliente, contacto)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (token) DO UPDATE SET
                processo = EXCLUDED.processo,
                obra_assunto = EXCLUDED.obra_assunto,
                obra_director = EXCLUDED.obra_director,
                cliente = EXCLUDED.cliente,
                contacto = EXCLUDED.contacto
            """,
            (token, processo, obra_assunto, obra_director, cliente, contacto),
        )
    conn.commit()


def obter_assistencia_publica(conn, token: str) -> dict:
    """Dados da assistência para o formulário do cliente ver (app
    `app_cliente.py`). Devolve None se o token não existir (link
    inválido/expirado)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT token, processo, obra_assunto, obra_director, cliente, contacto, criado_em "
            "FROM assistencias_cloud WHERE token = %s",
            (token,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        colunas = [c.name for c in cur.description]
        return dict(zip(colunas, row))


def adicionar_item_cliente(conn, token: str, topico: str, descricao: str = "",
                            observacoes: str = "", foto_bytes: bytes = None) -> int:
    """O cliente acrescenta um tópico/item à assistência (sem login). A
    foto (se houver) vai em base64 — decisão do Pedro de a guardar
    diretamente na base cloud, sem serviço de armazenamento à parte."""
    if not topico or not topico.strip():
        raise ValueError("Tópico é obrigatório.")
    foto_base64 = base64.b64encode(foto_bytes).decode("ascii") if foto_bytes else None
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO itens_cloud (token, topico, descricao, observacoes, foto_base64) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (token, topico.strip(), descricao or "", observacoes or "", foto_base64),
        )
        novo_id = cur.fetchone()[0]
    conn.commit()
    return novo_id


def listar_itens_pendentes_sincronizacao(conn, tokens: list) -> list:
    """Itens do cliente ainda não trazidos para a base local, só para os
    `tokens` passados (as assistências que existem nesta base local —
    nunca puxa tokens de outra instalação por engano)."""
    if not tokens:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT i.id, i.token, i.topico, i.descricao, i.observacoes, i.foto_base64, i.criado_em, "
            "       a.processo, a.obra_assunto "
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


def gerar_token() -> str:
    return uuid.uuid4().hex
