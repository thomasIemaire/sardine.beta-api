"""
Service d'envoi d'emails via l'API Brevo (anciennement Sendinblue).

Usage basique :
    await send_email(
        to=[{"email": "user@example.com", "name": "John Doe"}],
        subject="Bienvenue sur Sardine",
        html_content="<p>Bonjour !</p>",
    )

Usage avec template Brevo :
    await send_email(
        to=[{"email": "user@example.com"}],
        subject="Réinitialisation de mot de passe",
        template_id=1,
        template_params={"reset_link": "https://..."},
    )

Usage avec plusieurs destinataires et CC :
    await send_email(
        to=[{"email": "a@example.com"}, {"email": "b@example.com"}],
        subject="Rapport hebdomadaire",
        html_content="<p>Voici le rapport...</p>",
        cc=[{"email": "manager@example.com"}],
        reply_to={"email": "support@sardine.app"},
        attachments=[
            {
                "name": "rapport.pdf",
                "content": "<base64-string>",  # contenu base64
            }
        ],
    )
"""

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


class EmailError(Exception):
    """Levée quand l'envoi d'email échoue."""


async def send_email(
    to: list[dict],
    subject: str,
    html_content: str | None = None,
    text_content: str | None = None,
    template_id: int | None = None,
    template_params: dict | None = None,
    cc: list[dict] | None = None,
    bcc: list[dict] | None = None,
    reply_to: dict | None = None,
    attachments: list[dict] | None = None,
    sender_email: str | None = None,
    sender_name: str | None = None,
) -> dict:
    """
    Envoie un email via l'API Brevo.

    Paramètres :
        to              : liste de destinataires [{"email": str, "name": str (optionnel)}]
        subject         : sujet de l'email
        html_content    : corps HTML (requis si pas de template_id)
        text_content    : corps texte brut (optionnel, fallback HTML)
        template_id     : ID d'un template Brevo (remplace html_content)
        template_params : variables à injecter dans le template Brevo
        cc              : liste de destinataires en copie
        bcc             : liste de destinataires en copie cachée
        reply_to        : adresse de réponse {"email": str, "name": str (optionnel)}
        attachments     : pièces jointes [{"name": str, "content": str (base64)}]
        sender_email    : expéditeur (défaut : BREVO_SENDER_EMAIL)
        sender_name     : nom expéditeur (défaut : BREVO_SENDER_NAME)

    Retourne le dict de réponse Brevo (contient "messageId").
    Lève EmailError si l'envoi échoue.
    """
    if not settings.BREVO_API_KEY:
        raise EmailError("BREVO_API_KEY non configurée")

    if not to:
        raise EmailError("Au moins un destinataire requis")

    if not template_id and not html_content and not text_content:
        raise EmailError("html_content, text_content ou template_id requis")

    payload: dict = {
        "sender": {
            "email": sender_email or settings.BREVO_SENDER_EMAIL,
            "name": sender_name or settings.BREVO_SENDER_NAME,
        },
        "to": to,
        "subject": subject,
    }

    if template_id:
        payload["templateId"] = template_id
        if template_params:
            payload["params"] = template_params
    else:
        if html_content:
            payload["htmlContent"] = html_content
        if text_content:
            payload["textContent"] = text_content

    if cc:
        payload["cc"] = cc
    if bcc:
        payload["bcc"] = bcc
    if reply_to:
        payload["replyTo"] = reply_to
    if attachments:
        payload["attachment"] = attachments

    headers = {
        "api-key": settings.BREVO_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(BREVO_API_URL, json=payload, headers=headers)
        except httpx.RequestError as exc:
            logger.error("Brevo: erreur réseau — %s", exc)
            raise EmailError(f"Erreur réseau lors de l'envoi de l'email : {exc}") from exc

    if response.status_code not in (200, 201):
        logger.error(
            "Brevo: échec envoi — status=%d body=%s",
            response.status_code,
            response.text,
        )
        raise EmailError(
            f"Brevo a retourné une erreur {response.status_code} : {response.text}"
        )

    result = response.json()
    logger.info(
        "Email envoyé via Brevo — to=%s subject=%r messageId=%s",
        [r.get("email") for r in to],
        subject,
        result.get("messageId"),
    )
    return result
