"""WhatsApp (Meta Cloud API) and email (SMTP) senders. With DRY_RUN on, nothing leaves the machine."""
import smtplib
from email.message import EmailMessage

import httpx

import config


def send_whatsapp_template(phone, template, params, lang=None):
    """Business-initiated WhatsApp messages must use a template pre-approved in Meta Business Manager.
    `params` fill the template body's {{1}}, {{2}}, ... in order."""
    body = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": lang or config.WA_TEMPLATE_LANG},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in params],
            }],
        },
    }
    if config.DRY_RUN:
        return {"dry_run": True, "request": body}

    url = (f"https://graph.facebook.com/{config.WHATSAPP_API_VERSION}/"
           f"{config.WHATSAPP_PHONE_NUMBER_ID}/messages")
    r = httpx.post(url, json=body, timeout=20,
                   headers={"Authorization": f"Bearer {config.WHATSAPP_TOKEN}"})
    r.raise_for_status()
    return r.json()


def send_email(to, subject, text):
    if config.DRY_RUN:
        return {"dry_run": True, "to": to, "subject": subject, "body": text}

    msg = EmailMessage()
    msg["From"] = config.EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
        s.starttls()
        s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(msg)
    return {"sent": True, "to": to}
