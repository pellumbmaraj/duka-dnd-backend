import json
import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db():
    if "db" not in g:
        path = Path(current_app.config["DATABASE"])
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        g.db = connection
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def _columns(db, table):
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}


def _ensure_column(db, table, name, definition):
    if name not in _columns(db, table):
        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _migrate_business_members(db):
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'business_members'"
    ).fetchone()
    if not row or "user_id INTEGER NOT NULL UNIQUE" not in row["sql"]:
        return
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.executescript(
            """
            BEGIN IMMEDIATE;
            ALTER TABLE business_members RENAME TO business_members_legacy;
            CREATE TABLE business_members (
                business_id INTEGER NOT NULL REFERENCES business_clients(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                member_role TEXT NOT NULL DEFAULT 'buyer' CHECK (member_role IN ('owner', 'buyer')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (business_id, user_id)
            );
            INSERT INTO business_members(business_id, user_id, member_role, created_at)
            SELECT business_id, user_id, member_role, created_at FROM business_members_legacy;
            DROP TABLE business_members_legacy;
            COMMIT;
            """
        )
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def _migrate_business_email_uniqueness(db):
    """Allow one customer to reuse a contact email across their businesses."""
    row = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='business_clients'"
    ).fetchone()
    if not row or "email TEXT NOT NULL UNIQUE COLLATE NOCASE" not in row["sql"]:
        return
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE business_clients_new (
                id INTEGER PRIMARY KEY,
                company_name TEXT NOT NULL,
                tax_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
                contact_name TEXT NOT NULL,
                email TEXT NOT NULL COLLATE NOCASE,
                phone TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
                verified_at TEXT,
                verified_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                price_tier TEXT NOT NULL DEFAULT 'standard'
            );
            INSERT INTO business_clients_new(
                id,company_name,tax_id,contact_name,email,phone,address,status,verified_at,
                verified_by,created_at,updated_at,price_tier
            )
            SELECT id,company_name,tax_id,contact_name,email,phone,address,status,verified_at,
                   verified_by,created_at,updated_at,price_tier
            FROM business_clients;
            DROP TABLE business_clients;
            ALTER TABLE business_clients_new RENAME TO business_clients;
            CREATE INDEX IF NOT EXISTS idx_business_clients_status ON business_clients(status);
            COMMIT;
            """
        )
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def _seed_catalog(db):
    if db.execute("SELECT COUNT(*) FROM products").fetchone()[0]:
        return
    seed_path = Path(__file__).with_name("catalog_seed.json")
    if not seed_path.exists():
        return
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    db.execute("BEGIN IMMEDIATE")
    try:
        for category in seed["categories"]:
            db.execute(
                "INSERT INTO categories(id, slug, name_sq, name_en, image_url) VALUES (?, ?, ?, ?, ?)",
                (category["id"], category["id"], category["name"]["sq"], category["name"].get("en"), category.get("image")),
            )
        for brand in seed["brands"]:
            db.execute(
                "INSERT INTO brands(id, slug, name, description_sq, description_en, logo_url) VALUES (?, ?, ?, ?, ?, ?)",
                (brand["id"], brand["id"], brand["name"], brand.get("description", {}).get("sq"), brand.get("description", {}).get("en"), brand.get("image")),
            )
        for index, product in enumerate(seed["products"], start=1):
            base_cents = round(product["price"] * 10_000)
            has_offer = "offer" in product["badges"]
            current_cents = round(base_cents * 0.90) if has_offer else base_cents
            available_units = 60 + ((index * 17) % 141)
            db.execute(
                """INSERT INTO products(
                    id, sku, name_sq, name_en, category_id, brand_id, image_url, image_alt_sq,
                    image_alt_en, unit_price_cents, original_unit_price_cents, vat_basis_points,
                    case_size, minimum_order_units, maximum_order_units, available_units, availability, badges,
                    offer_label_sq, offer_label_en, offer_discount_basis_points, monthly_units_sold
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    product["id"], product["sku"], product["name"]["sq"], product["name"].get("en"),
                    product["categoryId"], product["brandId"], product["image"], product["name"]["sq"],
                    product["name"].get("en"), current_cents, base_cents if has_offer else None,
                    product["vatBasisPoints"], product["caseSize"], product["minimumOrderUnits"],
                    product["maximumOrderUnits"], available_units, "in_stock", ",".join(product["badges"]),
                    "Ofertë", "Offer", 1000 if has_offer else None, product.get("sold", 0),
                ),
            )
            db.executemany(
                "INSERT INTO product_volume_prices(product_id, minimum_units, unit_price_cents) VALUES (?, ?, ?)",
                [(product["id"], band["minimumUnits"], band["unitPriceCents"] * 100) for band in product["volumePrices"]],
            )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


def _migrate_currency_to_lek(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    migration = "2026-09-catalog-prices-to-all"
    if db.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (migration,)).fetchone():
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(
            "UPDATE products SET unit_price_cents = unit_price_cents * 100, "
            "original_unit_price_cents = CASE WHEN original_unit_price_cents IS NULL THEN NULL ELSE original_unit_price_cents * 100 END"
        )
        db.execute("UPDATE product_volume_prices SET unit_price_cents = unit_price_cents * 100")
        db.execute("UPDATE order_items SET unit_price_cents = unit_price_cents * 100, line_total_cents = line_total_cents * 100")
        db.execute(
            "UPDATE orders SET server_total_cents = server_total_cents * 100, subtotal_cents = subtotal_cents * 100, "
            "vat_cents = vat_cents * 100, total_cents = total_cents * 100, currency = 'ALL'"
        )
        db.execute("UPDATE quote_items SET unit_price_cents = unit_price_cents * 100, line_total_cents = line_total_cents * 100")
        db.execute("UPDATE quotes SET estimated_total_cents = estimated_total_cents * 100, currency = 'ALL'")
        db.execute("INSERT INTO schema_migrations(name) VALUES (?)", (migration,))
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise


def init_db():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer', 'staff', 'admin')),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            auth_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS auth_attempts (
            bucket_key TEXT PRIMARY KEY,
            window_started INTEGER NOT NULL,
            attempts INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS staff_profiles (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            operational_role TEXT NOT NULL UNIQUE
                CHECK (operational_role IN ('packing', 'delivery')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS business_applications (
            id INTEGER PRIMARY KEY,
            company_name TEXT NOT NULL,
            tax_id TEXT NOT NULL COLLATE NOCASE,
            contact_name TEXT NOT NULL,
            email TEXT NOT NULL COLLATE NOCASE,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new'
                CHECK (status IN ('new', 'verification', 'approved', 'rejected')),
            opened_at TEXT,
            opened_by INTEGER REFERENCES users(id),
            verification_started_at TEXT,
            verification_started_by INTEGER REFERENCES users(id),
            decided_at TEXT,
            decided_by INTEGER REFERENCES users(id),
            business_id INTEGER REFERENCES business_clients(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS business_clients (
            id INTEGER PRIMARY KEY,
            company_name TEXT NOT NULL,
            tax_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
            contact_name TEXT NOT NULL,
            email TEXT NOT NULL COLLATE NOCASE,
            phone TEXT NOT NULL DEFAULT '',
            address TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
            verified_at TEXT,
            verified_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS business_members (
            business_id INTEGER NOT NULL REFERENCES business_clients(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            member_role TEXT NOT NULL DEFAULT 'buyer' CHECK (member_role IN ('owner', 'buyer')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (business_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            reference TEXT NOT NULL UNIQUE,
            business_id INTEGER NOT NULL REFERENCES business_clients(id),
            submitted_by INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'confirmed', 'processing', 'shipped', 'completed', 'cancelled')),
            delivery_address TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT,
            server_total_cents INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (business_id, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL,
            quantity INTEGER NOT NULL CHECK (quantity > 0 AND quantity <= 9999),
            unit_price_cents INTEGER,
            line_total_cents INTEGER
        );
        CREATE TABLE IF NOT EXISTS order_status_events (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN ('submitted', 'confirmed', 'processing', 'shipped', 'completed', 'cancelled')),
            changed_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS order_change_requests (
            id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            requested_by INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
            subtotal_cents INTEGER NOT NULL,
            vat_cents INTEGER NOT NULL,
            total_cents INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'ALL',
            decided_by INTEGER REFERENCES users(id),
            decided_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS order_change_request_items (
            id INTEGER PRIMARY KEY,
            request_id INTEGER NOT NULL REFERENCES order_change_requests(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL REFERENCES products(id),
            quantity INTEGER NOT NULL CHECK (quantity > 0 AND quantity <= 9999),
            unit_price_cents INTEGER NOT NULL,
            line_total_cents INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email COLLATE NOCASE);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_open_application_tax
            ON business_applications(tax_id) WHERE status IN ('new', 'verification');
        CREATE UNIQUE INDEX IF NOT EXISTS idx_open_application_email
            ON business_applications(email) WHERE status IN ('new', 'verification');
        CREATE INDEX IF NOT EXISTS idx_applications_status ON business_applications(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_business_clients_status ON business_clients(status);
        CREATE INDEX IF NOT EXISTS idx_orders_business ON orders(business_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_order_status_events_order ON order_status_events(order_id, id DESC);
        """
    )

    _ensure_column(db, "users", "phone", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(db, "users", "must_change_password", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(db, "business_clients", "price_tier", "TEXT NOT NULL DEFAULT 'standard'")
    _ensure_column(db, "orders", "address_id", "INTEGER")
    _ensure_column(db, "orders", "payment_method", "TEXT NOT NULL DEFAULT 'invoice'")
    _ensure_column(db, "orders", "subtotal_cents", "INTEGER")
    _ensure_column(db, "orders", "vat_cents", "INTEGER")
    _ensure_column(db, "orders", "total_cents", "INTEGER")
    _ensure_column(db, "orders", "currency", "TEXT NOT NULL DEFAULT 'ALL'")
    _migrate_business_members(db)
    _migrate_business_email_uniqueness(db)

    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS business_addresses (
            id INTEGER PRIMARY KEY,
            business_id INTEGER NOT NULL REFERENCES business_clients(id) ON DELETE CASCADE,
            label TEXT NOT NULL DEFAULT 'Primary',
            line1 TEXT NOT NULL,
            line2 TEXT NOT NULL DEFAULT '',
            city TEXT NOT NULL DEFAULT '',
            postal_code TEXT NOT NULL DEFAULT '',
            country_code TEXT NOT NULL DEFAULT 'AL',
            is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
            tax_id TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_default_business_address
        ON business_addresses(business_id) WHERE is_default = 1;

        CREATE TABLE IF NOT EXISTS categories (
            id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name_sq TEXT NOT NULL, name_en TEXT,
            description_sq TEXT, description_en TEXT, image_url TEXT, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS brands (
            id TEXT PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
            description_sq TEXT, description_en TEXT, logo_url TEXT, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY, sku TEXT NOT NULL UNIQUE COLLATE NOCASE, name_sq TEXT NOT NULL, name_en TEXT,
            description_sq TEXT, description_en TEXT, category_id TEXT NOT NULL REFERENCES categories(id),
            brand_id TEXT NOT NULL REFERENCES brands(id), image_url TEXT NOT NULL, image_alt_sq TEXT NOT NULL,
            image_alt_en TEXT, unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
            original_unit_price_cents INTEGER, vat_basis_points INTEGER NOT NULL DEFAULT 1300,
            case_size INTEGER NOT NULL DEFAULT 1, minimum_order_units INTEGER NOT NULL DEFAULT 1,
            maximum_order_units INTEGER NOT NULL DEFAULT 9999, available_units INTEGER,
            availability TEXT NOT NULL DEFAULT 'in_stock' CHECK (availability IN ('in_stock','low_stock','out_of_stock','preorder')),
            badges TEXT NOT NULL DEFAULT '', offer_label_sq TEXT, offer_label_en TEXT,
            offer_discount_basis_points INTEGER, offer_ends_at TEXT, ingredients_sq TEXT, ingredients_en TEXT,
            allergens_sq TEXT, allergens_en TEXT, storage_sq TEXT, storage_en TEXT,
            monthly_rank INTEGER, monthly_units_sold INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS product_volume_prices (
            product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            minimum_units INTEGER NOT NULL CHECK (minimum_units > 0),
            unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
            PRIMARY KEY (product_id, minimum_units)
        );
        CREATE TABLE IF NOT EXISTS cart_items (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            quantity INTEGER NOT NULL CHECK (quantity > 0 AND quantity <= 9999),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, product_id)
        );
        CREATE TABLE IF NOT EXISTS saved_lists (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS saved_list_items (
            list_id INTEGER NOT NULL REFERENCES saved_lists(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL CHECK (quantity > 0 AND quantity <= 9999),
            PRIMARY KEY (list_id, product_id)
        );
        CREATE TABLE IF NOT EXISTS comparisons (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            position INTEGER NOT NULL CHECK (position BETWEEN 0 AND 3),
            PRIMARY KEY (user_id, product_id), UNIQUE (user_id, position)
        );
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY, reference TEXT NOT NULL UNIQUE,
            business_id INTEGER NOT NULL REFERENCES business_clients(id), address_id INTEGER NOT NULL REFERENCES business_addresses(id),
            submitted_by INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL DEFAULT 'submitted'
                CHECK (status IN ('draft','submitted','reviewing','accepted','rejected','expired')),
            note TEXT NOT NULL DEFAULT '', estimated_total_cents INTEGER, currency TEXT NOT NULL DEFAULT 'ALL',
            idempotency_key TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE (business_id, idempotency_key)
        );
        CREATE TABLE IF NOT EXISTS quote_items (
            id INTEGER PRIMARY KEY, quote_id INTEGER NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
            product_id TEXT NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL,
            unit_price_cents INTEGER NOT NULL, line_total_cents INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS delivery_options (
            id INTEGER PRIMARY KEY, business_id INTEGER REFERENCES business_clients(id) ON DELETE CASCADE,
            address_id INTEGER REFERENCES business_addresses(id) ON DELETE CASCADE,
            service_area TEXT NOT NULL, next_available_date TEXT, cutoff_time TEXT,
            schedule_json TEXT NOT NULL DEFAULT '[]', available INTEGER NOT NULL DEFAULT 1,
            message_sq TEXT, message_en TEXT
        );
        CREATE TABLE IF NOT EXISTS account_activation_otps (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            otp_hash TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS admin_notifications (
            id INTEGER PRIMARY KEY,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            target_url TEXT NOT NULL DEFAULT '/admin',
            actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            email_status TEXT NOT NULL DEFAULT 'pending' CHECK (email_status IN ('pending','sent','failed','skipped')),
            email_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_addresses_business ON business_addresses(business_id);
        CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id, active);
        CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand_id, active);
        CREATE INDEX IF NOT EXISTS idx_cart_user ON cart_items(user_id);
        CREATE INDEX IF NOT EXISTS idx_saved_lists_user ON saved_lists(user_id);
        CREATE INDEX IF NOT EXISTS idx_quotes_business ON quotes(business_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
        CREATE INDEX IF NOT EXISTS idx_order_change_requests_order ON order_change_requests(order_id, id DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_order_change
            ON order_change_requests(order_id) WHERE status = 'pending';
        CREATE INDEX IF NOT EXISTS idx_order_change_request_items_request ON order_change_request_items(request_id);
        CREATE INDEX IF NOT EXISTS idx_admin_notifications_created ON admin_notifications(id DESC);
        """
    )

    _ensure_column(db, "business_addresses", "tax_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(db, "business_addresses", "email", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(db, "business_addresses", "phone", "TEXT NOT NULL DEFAULT ''")
    db.execute(
        "UPDATE business_addresses SET tax_id = COALESCE(NULLIF(tax_id,''), "
        "(SELECT tax_id FROM business_clients WHERE id = business_addresses.business_id)), "
        "email = COALESCE(NULLIF(email,''), (SELECT email FROM business_clients WHERE id = business_addresses.business_id)), "
        "phone = COALESCE(NULLIF(phone,''), (SELECT phone FROM business_clients WHERE id = business_addresses.business_id)) "
        "WHERE tax_id = '' OR email = '' OR phone = ''"
    )

    db.execute(
        "INSERT INTO business_addresses(business_id, label, line1, is_default) "
        "SELECT id, 'Primary', address, 1 FROM business_clients b WHERE trim(address) <> '' "
        "AND NOT EXISTS (SELECT 1 FROM business_addresses a WHERE a.business_id = b.id)"
    )
    db.execute(
        "INSERT INTO order_status_events(order_id,status,changed_by,created_at) "
        "SELECT o.id,o.status,o.submitted_by,o.created_at FROM orders o "
        "WHERE NOT EXISTS (SELECT 1 FROM order_status_events e WHERE e.order_id=o.id)"
    )
    _migrate_currency_to_lek(db)
    _seed_catalog(db)
    db.execute(
        "UPDATE products SET available_units = 60 + ((rowid * 17) % 141) WHERE available_units IS NULL"
    )
    db.execute(
        "UPDATE products SET availability = CASE WHEN available_units <= 0 THEN 'out_of_stock' "
        "WHEN available_units <= 20 THEN 'low_stock' ELSE 'in_stock' END WHERE available_units IS NOT NULL"
    )


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
