import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from flask import current_app


def send_activation_email(recipient, contact_name, company_name, otp):
    """Send a single-use activation code through the configured SMTP account."""
    config = current_app.config
    if config.get("MAIL_SUPPRESS_SEND"):
        current_app.extensions.setdefault("mail_outbox", []).append(
            {"to": recipient, "company": company_name, "otp": otp}
        )
        return
    host = config.get("SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SMTP_HOST is not configured.")

    minutes = config["ACTIVATION_OTP_MINUTES"]
    login_url = f"{config['FRONTEND_PUBLIC_URL']}/login"
    message = EmailMessage()
    message["Subject"] = "Your DUKA Group business account is approved"
    message["From"] = formataddr((config["MAIL_FROM_NAME"], config["MAIL_FROM"]))
    message["To"] = recipient
    message.set_content(
        f"Hello {contact_name},\n\n"
        f"The business account request for {company_name} has been verified and approved.\n\n"
        f"Your one-time login code is: {otp}\n\n"
        f"Open {login_url} and use your business email with this code in the password field. "
        f"You will then be asked to create your permanent password. The code expires in {minutes} minutes "
        "and is deleted after it is used.\n\n"
        "DUKA Group"
    )

    smtp_class = smtplib.SMTP_SSL if config["SMTP_USE_SSL"] else smtplib.SMTP
    with smtp_class(host, config["SMTP_PORT"], timeout=10) as connection:
        if config["SMTP_USE_TLS"] and not config["SMTP_USE_SSL"]:
            connection.starttls()
        if config["SMTP_USERNAME"]:
            connection.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
        connection.send_message(message)


def send_business_approval_email(recipient, contact_name, company_name, primary_email):
    """Notify an existing customer that an additional business was approved."""
    config = current_app.config
    if config.get("MAIL_SUPPRESS_SEND"):
        current_app.extensions.setdefault("mail_outbox", []).append(
            {"to": recipient, "company": company_name, "kind": "business_approved"}
        )
        return
    host = config.get("SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SMTP_HOST is not configured.")
    account_url = f"{config['FRONTEND_PUBLIC_URL']}/"
    message = EmailMessage()
    message["Subject"] = "Biznesi juaj u miratua nga DUKA Group"
    message["From"] = formataddr((config["MAIL_FROM_NAME"], config["MAIL_FROM"]))
    message["To"] = recipient
    message.set_content(
        f"Përshëndetje {contact_name},\n\n"
        f"Biznesi {company_name} u verifikua dhe u miratua. Tani mund ta përdorni në llogarinë tuaj.\n\n"
        f"Emaili aktual për hyrje është: {primary_email}\n"
        f"Mund të zgjidhni një email tjetër kryesor nga Llogaria ime pasi të hyni në {account_url}.\n\n"
        "DUKA Group"
    )
    smtp_class = smtplib.SMTP_SSL if config["SMTP_USE_SSL"] else smtplib.SMTP
    with smtp_class(host, config["SMTP_PORT"], timeout=10) as connection:
        if config["SMTP_USE_TLS"] and not config["SMTP_USE_SSL"]:
            connection.starttls()
        if config["SMTP_USERNAME"]:
            connection.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
        connection.send_message(message)


def send_admin_notification_email(recipients, subject, body):
    """Send an operational event to every configured administrator."""
    config = current_app.config
    recipients = sorted({value.strip().casefold() for value in recipients if value and value.strip()})
    if not recipients:
        return "skipped"
    if config.get("MAIL_SUPPRESS_SEND"):
        current_app.extensions.setdefault("mail_outbox", []).append(
            {"to": recipients, "subject": subject, "body": body, "kind": "admin_notification"}
        )
        return "sent"
    host = config.get("SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SMTP_HOST is not configured.")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((config["MAIL_FROM_NAME"], config["MAIL_FROM"]))
    message["To"] = ", ".join(recipients)
    message.set_content(body)
    smtp_class = smtplib.SMTP_SSL if config["SMTP_USE_SSL"] else smtplib.SMTP
    with smtp_class(host, config["SMTP_PORT"], timeout=10) as connection:
        if config["SMTP_USE_TLS"] and not config["SMTP_USE_SSL"]:
            connection.starttls()
        if config["SMTP_USERNAME"]:
            connection.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
        connection.send_message(message)
    return "sent"
