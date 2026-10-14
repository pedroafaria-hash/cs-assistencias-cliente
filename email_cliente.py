"""
email_cliente.py — notificação por email ao Diretor de Obra (gestor da
assistência) sempre que o cliente adiciona tópicos, Fase 3 (11/10/2026),
pedido do Pedro: "Sempre que o cliente faça alterações e adicione tópicos,
é enviada uma notificação por email para o gestor da assistência".

Usa SMTP do Gmail/Google Workspace (smtp.gmail.com:587, STARTTLS) com uma
"Password de Aplicação" da conta pedro.faria@csconstrutora.com — nunca a
password normal da conta. Credenciais só nos Secrets desta app no Streamlit
Community Cloud (nunca escritas no código):

    NOTIF_EMAIL_USER = "pedro.faria@csconstrutora.com"
    NOTIF_EMAIL_PASSWORD = "xxxx xxxx xxxx xxxx"   # Password de Aplicação

Como criar a Password de Aplicação (conta Google Workspace):
  1. myaccount.google.com -> Segurança -> ativar "Verificação em 2 passos"
     (obrigatório para poder criar passwords de aplicação).
  2. Na mesma página, "Passwords de aplicação" -> criar uma nova (nome
     sugerido: "Assistencias CS") -> copiar a password de 16 caracteres.
  3. Colar essa password (não a password normal da conta) no Secrets acima.

Se a conta for gerida pela TI da CS Construtora (Google Workspace), pode
ser preciso o administrador ativar "Passwords de aplicação" para a
organização — se o passo 2 não aparecer, é esse o motivo.

Nunca bloqueia nem mostra erro ao cliente: o email é um extra, nunca pode
impedir o envio do tópico em si. Falha silenciosamente (devolve False) se
as credenciais não estiverem configuradas ou o envio falhar — o Pedro vê o
tópico na mesma, só não recebe o email, até configurar isto.
"""
import os
import smtplib
from email.mime.text import MIMEText


def _credenciais():
    user = os.environ.get("NOTIF_EMAIL_USER")
    password = os.environ.get("NOTIF_EMAIL_PASSWORD")
    if user and password:
        return user, password
    try:
        import streamlit as st  # import tardio: só a app cliente corre dentro do Streamlit
        user = user or st.secrets.get("NOTIF_EMAIL_USER")
        password = password or st.secrets.get("NOTIF_EMAIL_PASSWORD")
    except Exception:
        pass
    return user, password


def notificar_novos_topicos(director_email: str, processo: str, obra_assunto: str,
                             cliente: str, itens: list) -> bool:
    """Envia um único email ao Diretor de Obra com todos os tópicos que o
    cliente acabou de enviar numa só submissão. `itens`: lista de dicts com
    'topico' e 'descricao'. Best-effort — nunca levanta exceção, devolve
    True/False só para testes/registo."""
    if not director_email or not itens:
        return False
    user, password = _credenciais()
    if not user or not password:
        return False
    try:
        linhas_itens = "\n".join(
            f"  - {i['topico']}: {i.get('descricao') or '(sem descrição)'}" for i in itens
        )
        plural = "tópicos" if len(itens) > 1 else "tópico"
        corpo = (
            f"O cliente {cliente or '(sem nome indicado)'} acrescentou {len(itens)} {plural} "
            f"à assistência da obra {obra_assunto} (processo {processo}):\n\n"
            f"{linhas_itens}\n\n"
            "Consulta a app de Assistências Pós-Obra para mais detalhes e fotos."
        )
        msg = MIMEText(corpo, "plain", "utf-8")
        msg["Subject"] = f"Nova(s) pendência(s) do cliente — {processo} ({obra_assunto})"
        msg["From"] = user
        msg["To"] = director_email
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(user, [director_email], msg.as_string())
        return True
    except Exception:
        return False
