# Guia — publicar o formulário do cliente na cloud

Este guia é só para a parte pública (o formulário que o cliente preenche
pelo QR code/link). A app principal (`APP_CS_ASSISTENCIAS`) continua a
correr no teu PC, como já corre hoje — não precisa de nada disto para
continuar a funcionar.

São 3 contas gratuitas, criadas uma vez: **GitHub** (guarda o código),
**Supabase** (a base de dados partilhada entre as duas apps) e
**Streamlit Community Cloud** (onde o formulário fica mesmo a correr,
acessível pela internet). Demora uns 15-20 minutos a primeira vez; depois
disto feito, não tens de repetir nada — só atualizar ficheiros se um dia
eu mudar o formulário.

## 1. Criar conta GitHub

1. Vai a **github.com** → "Sign up" → segue os passos (email, password,
   nome de utilizador).
2. Depois de entrares, clica no **+** no canto superior direito →
   **"New repository"**.
3. Nome do repositório: `cs-assistencias-cliente` (ou outro nome, não
   importa). Marca **"Private"**. Não marques nenhuma opção de
   inicialização (README, .gitignore, etc.). Clica **"Create repository"**.

## 2. Enviar os ficheiros para o repositório

Na página do repositório recém-criado:

1. Clica em **"uploading an existing file"** (ou "Add file" →
   "Upload files").
2. Arrasta para lá **todos os ficheiros e pastas** desta pasta
   (`APP_CS_ASSISTENCIAS_CLIENTE`): `app_cliente.py`, `db_cloud.py`,
   `requirements.txt`, `README.md`, e a pasta `Logos` (com o
   `logo2.png` lá dentro) — o GitHub aceita arrastar uma pasta inteira e
   mantém a estrutura.
3. **Não envies** o ficheiro `.streamlit/secrets.toml.example` nem a pasta
   `.streamlit` — isso é só para referência, os segredos a sério vão
   diretamente para o Streamlit Cloud no passo 4, nunca para o GitHub.
4. Em baixo, escreve uma mensagem qualquer (ex. "Primeira versão") e
   clica **"Commit changes"**.

## 3. Criar conta e projeto Supabase

1. Vai a **supabase.com** → "Start your project" → entra com a conta
   GitHub que acabaste de criar (mais simples) ou com email.
2. **"New project"**: escolhe um nome (ex. `cs-assistencias`), define uma
   password forte para a base de dados (guarda-a nalgum lado — não é a
   mesma coisa que a connection string do passo seguinte) e escolhe a
   região mais próxima (Europe). Clica **"Create new project"** — demora
   1-2 minutos a ficar pronto.
3. Já dentro do projeto: menu lateral → **Project Settings** (ícone de
   engrenagem) → **Database**. Procura **"Connection string"** → separador
   **"URI"**. Copia esse texto (começa por `postgresql://postgres:...`).
   Esta é a `SUPABASE_DB_URL` que vais usar nos dois sítios a seguir.

Não precisas de criar tabelas manualmente — a app cria-as sozinha (de
forma segura, só se não existirem) na primeira vez que qualquer uma das
duas apps se liga.

## 4. Publicar a app no Streamlit Community Cloud

1. Vai a **share.streamlit.io** → "Sign in" → entra com a conta GitHub.
2. **"New app"** → escolhe o repositório `cs-assistencias-cliente`, o
   branch `main`, e em "Main file path" escreve `app_cliente.py`.
3. Antes de publicar, clica em **"Advanced settings"** → campo
   **"Secrets"** → cola isto (substituindo pela tua connection string real
   do passo 3):
   ```
   SUPABASE_DB_URL = "postgresql://postgres:XXXXX@db.xxxxxxxx.supabase.co:5432/postgres"
   ```
4. Clica **"Deploy"**. Fica pronto em 1-2 minutos. O endereço final
   aparece no topo do browser (algo como
   `https://cs-assistencias-cliente.streamlit.app`) — é esse endereço que
   precisas a seguir.

## 5. Ligar a app do teu PC a esta configuração

Na pasta `APP_CS_ASSISTENCIAS` (a app principal, no teu PC):

1. Faz uma cópia do ficheiro `.env.example` e chama-lhe `.env` (sem
   ".example").
2. Dentro desse `.env`, acrescenta (ou descomenta e preenche) estas duas
   linhas:
   ```
   SUPABASE_DB_URL=postgresql://postgres:XXXXX@db.xxxxxxxx.supabase.co:5432/postgres
   CLIENTE_APP_URL=https://cs-assistencias-cliente.streamlit.app
   ```
   (a mesma connection string do passo 3, e o endereço que o Streamlit
   Cloud te deu no passo 4).
3. Fecha a app (botão "🔴 Fechar Assistências") e volta a abrir com o
   `iniciar.bat` — para ela ler o novo `.env`.

## 6. Testar

1. Na app local, separador **Gestão de Pendências**, escolhe uma
   assistência e clica **"Gerar / Ver Link do Cliente"** — deve aparecer
   o link e o QR code.
2. Abre esse link no telemóvel (ou noutro browser), preenche um item de
   teste com foto, e envia.
3. Volta à app local e clica **"Sincronizar com o Cliente"** — o item de
   teste deve aparecer na listagem, com "Origem" = Cliente.

Se algum destes passos der erro, tira print e envia-me — digo-te
exatamente o que ajustar.
