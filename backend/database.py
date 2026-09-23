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
        g.db = connection
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db():
    get_db().executescript(
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
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email COLLATE NOCASE);

        CREATE TABLE IF NOT EXISTS business_clients (
            id INTEGER PRIMARY KEY,
            company_name TEXT NOT NULL,
            tax_id TEXT NOT NULL UNIQUE COLLATE NOCASE,
            contact_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            phone TEXT NOT NULL DEFAULT '',
            address TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'suspended')),
            verified_at TEXT,
            verified_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS business_members (
            business_id INTEGER NOT NULL REFERENCES business_clients(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            member_role TEXT NOT NULL DEFAULT 'buyer'
                CHECK (member_role IN ('owner', 'buyer')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (business_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            reference TEXT NOT NULL UNIQUE,
            business_id INTEGER NOT NULL REFERENCES business_clients(id),
            submitted_by INTEGER NOT NULL REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'submitted'
                CHECK (status IN ('submitted', 'confirmed', 'processing', 'shipped', 'completed', 'cancelled')),
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

        CREATE INDEX IF NOT EXISTS idx_business_clients_status ON business_clients(status);
        CREATE INDEX IF NOT EXISTS idx_business_members_business ON business_members(business_id);
        CREATE INDEX IF NOT EXISTS idx_orders_business ON orders(business_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
        """
    )


def init_app(app):
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
