"""Envio de e-mail. Sem servidor SMTP configurado, grava a mensagem na caixa de saída local
(instance/mail_outbox), útil apenas em desenvolvimento. Nada é enviado de forma simulada."""
import os
import smtplib
import uuid
from email.message import EmailMessage

from flask import current_app

from app.models.base import utcnow


def email_configurado():
    return bool(current_app.config.get("MAIL_SERVER"))


def enviar_email(destino, assunto, corpo):
    cfg = current_app.config
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = destino
    msg.set_content(corpo)

    if email_configurado():
        try:
            with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15) as smtp:
                if cfg["MAIL_USE_TLS"]:
                    smtp.starttls()
                if cfg["MAIL_USERNAME"]:
                    smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
                smtp.send_message(msg)
            return True
        except (smtplib.SMTPException, OSError):
            current_app.logger.exception("Falha ao enviar e-mail para destinatário (assunto: %s)", assunto)
            return False

    if cfg.get("TESTING") or cfg.get("ENV_MODE") != "production":
        pasta = os.path.join(current_app.instance_path, "mail_outbox")
        os.makedirs(pasta, exist_ok=True)
        nome = f"{utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.eml"
        with open(os.path.join(pasta, nome), "wb") as fh:
            fh.write(bytes(msg))
        current_app.logger.info("E-mail gravado na caixa de saída local: %s", nome)
        return True
    current_app.logger.warning("E-mail não enviado: servidor SMTP não configurado.")
    return False
