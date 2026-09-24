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
