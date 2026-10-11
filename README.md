# Assistências Pós-Obra CS — Formulário do Cliente

App separada e pública (sem login), para o cliente preencher a sua
assistência pós-obra a partir do link/QR code que a CS Construtora envia.
Não tem ligação ao PHC nem à base local do Pedro — só à base partilhada
no Supabase (ver `SUPABASE_DB_URL`), que é também usada pela app principal
(`APP_CS_ASSISTENCIAS`) para publicar o link e depois sincronizar o que o
cliente enviou.

Pensada para correr no **Streamlit Community Cloud** (gratuito) — ver o
guia de configuração completo (GitHub + Supabase + Streamlit Cloud) no
ficheiro do projeto.

## Resumo rápido

1. Este código fica num repositório GitHub (pode ser privado).
2. No Streamlit Community Cloud, cria uma app a apontar para este
   repositório, ficheiro principal `app_cliente.py`.
3. Em "Settings -> Secrets" dessa app, cola o conteúdo de
   `.streamlit/secrets.toml.example` com a connection string real do
   Supabase.
4. O endereço que o Streamlit Cloud te der (ex.
   `https://cs-assistencias-cliente.streamlit.app`) é o que entra na app
   principal, variável `CLIENTE_APP_URL` no `.env`.

## Ficheiros

- `app_cliente.py` — interface do formulário.
- `db_cloud.py` — ligação à base partilhada (cópia do mesmo ficheiro da
  app principal — independentes, por isso sem import cruzado).
- `Logos/logo2.png` — logótipo CS.
- `requirements.txt`, `.streamlit/secrets.toml.example`.
