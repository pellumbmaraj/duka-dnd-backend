from flask import current_app, request, session

from .database import get_db
from .mailer import send_admin_notification_email


EVENTS = {
    "commerce.submit_business_application": ("account_application", "Kërkesë e re për llogari", "/admin/applications"),
    "commerce.account_update": ("account_updated", "Të dhënat e llogarisë u ndryshuan", "/admin/clients"),
    "commerce.account_password": ("password_changed", "Fjalëkalimi i klientit u ndryshua", "/admin/clients"),
    "commerce.businesses_create": ("business_created", "Biznes i ri për verifikim", "/admin/clients"),
    "commerce.business_item": ("business_updated", "Biznesi i klientit u ndryshua", "/admin/clients"),
    "commerce.address_create": ("address_created", "Adresë e re biznesi", "/admin/clients"),
    "commerce.address_item": ("address_updated", "Adresa e biznesit u ndryshua", "/admin/clients"),
    "commerce.cart_replace_or_clear": ("cart_updated", "Shporta e klientit u ndryshua", "/admin"),
    "commerce.cart_item": ("cart_updated", "Shporta e klientit u ndryshua", "/admin"),
    "commerce.saved_lists": ("saved_list_created", "Listë e re e ruajtur", "/admin"),
    "commerce.saved_list_item": ("saved_list_updated", "Lista e ruajtur u ndryshua", "/admin"),
    "commerce.orders": ("order_created", "Porosi e re", "/admin/orders"),
    "commerce.order_update": ("order_updated", "Porosia u ndryshua nga klienti", "/admin/orders"),
    "commerce.order_reorder": ("order_reordered", "Porosia u shtua përsëri në shportë", "/admin/orders"),
    "commerce.quotes": ("quote_created", "Kërkesë e re për ofertë", "/admin/quotes"),
    "commerce.comparison": ("comparison_updated", "Krahasimi i produkteve u ndryshua", "/admin"),
    "auth.login": ("client_login", "Klienti hyri në dyqan", "/admin/clients"),
    "auth.logout": ("client_logout", "Klienti doli nga dyqani", "/admin/clients"),
    "auth.complete_activation": ("account_activated", "Llogaria e klientit u aktivizua", "/admin/clients"),
}


def _actor_description():
    db = get_db()
    user_id = session.get("user_id")
    if user_id:
        user = db.execute("SELECT display_name,email FROM users WHERE id=?", (user_id,)).fetchone()
        if user:
            return user_id, f"{user['display_name']} ({user['email']})"
    payload = request.get_json(silent=True) if request.is_json else None
    if isinstance(payload, dict):
        name = payload.get("contactName") or payload.get("companyName") or payload.get("email")
        if isinstance(name, str) and name.strip():
            return None, name.strip()[:200]
    return None, "Një klient"


def record_storefront_mutation(response):
    """Persist and email successful customer-facing state changes without affecting their response."""
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"} or not 200 <= response.status_code < 300:
        return
    if request.blueprint not in {"commerce", "auth"}:
        return
    event_type, title, target_url = EVENTS.get(
        request.endpoint,
        ("storefront_change", "Ndryshim i ri nga dyqani", "/admin"),
    )
    if event_type == "order_created" and response.status_code != 201:
        return
    db = get_db()
    actor_user_id, actor = _actor_description()
    action = {"POST": "dërgoi", "PUT": "përditësoi", "PATCH": "ndryshoi", "DELETE": "fshiu"}[request.method]
    message = f"{actor} {action} të dhëna në {request.path}."
    if event_type == "business_created":
        payload = request.get_json(silent=True) or {}
        response_payload = response.get_json(silent=True) or {}
        business = response_payload.get("business") if isinstance(response_payload, dict) else None
        company_name = payload.get("name") if isinstance(payload, dict) else None
        if isinstance(company_name, str) and company_name.strip():
            company_name = company_name.strip()[:200]
            title = f"Biznes i ri: {company_name}"
            message = f"{actor} kërkoi të shtojë biznesin {company_name}."
        if isinstance(business, dict) and str(business.get("id", "")).isdigit():
            target_url = f"/admin/clients#client-{business['id']}"
    elif event_type == "order_created":
        response_payload = response.get_json(silent=True) or {}
        order = response_payload.get("order") if isinstance(response_payload, dict) else None
        if isinstance(order, dict):
            order_id = str(order.get("id", ""))
            reference = str(order.get("reference", "")).strip()[:100]
            business_id = str(order.get("businessId", ""))
            items = order.get("items") if isinstance(order.get("items"), list) else []
            units = sum(item.get("quantity", 0) for item in items if isinstance(item, dict) and isinstance(item.get("quantity"), int))
            total_cents = order.get("totalCents", 0) if isinstance(order.get("totalCents"), int) else 0
            company = db.execute(
                "SELECT company_name FROM business_clients WHERE id=?", (business_id,)
            ).fetchone() if business_id.isdigit() else None
            company_name = company["company_name"] if company else actor
            title = f"Porosi e re: {reference}" if reference else "Porosi e re"
            message = f"{company_name} dërgoi një porosi me {units} njësi, me vlerë {total_cents / 100:,.2f} lekë."
            if order_id.isdigit():
                target_url = f"/admin/orders/{order_id}"
    cursor = db.execute(
        "INSERT INTO admin_notifications(event_type,title,message,target_url,actor_user_id) VALUES (?,?,?,?,?)",
        (event_type, title, message, target_url, actor_user_id),
    )
    configured = [value for value in current_app.config.get("ADMIN_NOTIFICATION_EMAILS", "").split(",") if value.strip()]
    administrators = [row["email"] for row in db.execute("SELECT email FROM users WHERE role='admin' AND is_active=1")]
    try:
        status = send_admin_notification_email(
            [*configured, *administrators],
            f"D&D Admin — {title}",
            f"{title}\n\n{message}\n\nHap panelin: {request.host_url.rstrip('/')}{target_url}",
        )
        db.execute("UPDATE admin_notifications SET email_status=? WHERE id=?", (status, cursor.lastrowid))
    except Exception as error:
        current_app.logger.exception("Admin notification email failed")
        db.execute(
            "UPDATE admin_notifications SET email_status='failed',email_error=? WHERE id=?",
            (str(error)[:500], cursor.lastrowid),
        )
