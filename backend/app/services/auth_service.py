import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path

STORAGE_DIR = Path(str(next((p for p in Path(__file__).resolve().parents if (p / "frontend").exists()), Path(__file__).resolve().parents[2]) / "storage"))
DB_PATH = STORAGE_DIR / "app.db"
CONFIG_PATH = STORAGE_DIR / "runtime_config.json"
SECRET_PATH = STORAGE_DIR / ".session_secret"

DEFAULT_CONFIG = {
    "ollama_model": "qwen3.5:4b",
    "whisper_model": "base",
    "max_clips": 3,
    "allow_registration": True,
    "allow_url_download": True,
}


def _get_secret_key() -> bytes:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    if not SECRET_PATH.exists():
        SECRET_PATH.write_text(secrets.token_hex(32), encoding="utf-8")
    return SECRET_PATH.read_text(encoding="utf-8").strip().encode("utf-8")


def get_runtime_config() -> dict:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return dict(DEFAULT_CONFIG)
    try:
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return {**DEFAULT_CONFIG, **loaded}
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_runtime_config(updates: dict) -> dict:
    current = get_runtime_config()
    if "ollama_model" in updates and str(updates["ollama_model"]).strip():
        current["ollama_model"] = str(updates["ollama_model"]).strip()
    if "whisper_model" in updates and updates["whisper_model"] in ("tiny", "base", "small", "medium"):
        current["whisper_model"] = updates["whisper_model"]
    if "max_clips" in updates:
        current["max_clips"] = max(1, min(5, int(updates["max_clips"])))
    if "allow_registration" in updates:
        current["allow_registration"] = bool(updates["allow_registration"])
    if "allow_url_download" in updates:
        current["allow_url_download"] = bool(updates["allow_url_download"])

    CONFIG_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, _ = stored_hash.split("$", 1)
        candidate = hash_password(password, salt)
        return hmac.compare_digest(candidate, stored_hash)
    except Exception:
        return False


def _connect() -> sqlite3.Connection:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at REAL NOT NULL
            )
            """
        )
        try:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        except sqlite3.OperationalError:
            pass

        conn.execute(
            "UPDATE users SET email = LOWER(username) || '@aiclipping.local' WHERE email IS NULL OR TRIM(email) = ''"
        )
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_owners (
                project_id TEXT PRIMARY KEY,
                owner_username TEXT NOT NULL,
                source_label TEXT DEFAULT '',
                created_at REAL NOT NULL
            )
            """
        )
        admin_row = conn.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        if not admin_row:
            conn.execute(
                "INSERT INTO users (username, email, password_hash, role, created_at) VALUES (?, ?, ?, 'admin', ?)",
                ("admin", "admin@aiclipping.local", hash_password("admin1234"), time.time()),
            )
        creator_row = conn.execute("SELECT id FROM users WHERE username = 'creator' LIMIT 1").fetchone()
        if not creator_row:
            conn.execute(
                "INSERT INTO users (username, email, password_hash, role, created_at) VALUES (?, ?, ?, 'user', ?)",
                ("creator", "creator@aiclipping.local", hash_password("user1234"), time.time()),
            )


def _normalize_email(email: str) -> str:
    clean = email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", clean):
        raise ValueError("Please enter a valid email address.")
    return clean


def authenticate_user(email: str, password: str) -> dict | None:
    clean_login = email.strip().lower()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(email) = ? OR LOWER(username) = ?",
            (clean_login, clean_login),
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        return {
            "id": row["id"],
            "username": row["username"],
            "email": row["email"],
            "role": row["role"],
        }


def create_user(email: str, password: str, role: str = "user") -> dict:
    clean_email = _normalize_email(email)
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    safe_role = "admin" if role == "admin" else "user"

    local_part = clean_email.split("@", 1)[0]
    base_username = re.sub(r"[^a-z0-9]", "", local_part)[:16] or "creator"

    with _connect() as conn:
        existing_email = conn.execute("SELECT id FROM users WHERE LOWER(email) = ?", (clean_email,)).fetchone()
        if existing_email:
            raise ValueError("An account with this email already exists.")

        candidate_user = base_username
        suffix = 1
        while conn.execute("SELECT id FROM users WHERE username = ?", (candidate_user,)).fetchone():
            candidate_user = f"{base_username}{suffix}"
            suffix += 1

        conn.execute(
            "INSERT INTO users (username, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (candidate_user, clean_email, hash_password(password), safe_role, time.time()),
        )
        row = conn.execute(
            "SELECT id, username, email, role FROM users WHERE LOWER(email) = ?",
            (clean_email,),
        ).fetchone()
        return dict(row)


def list_users() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT u.id, u.username, u.email, u.role, u.created_at,
                   COUNT(p.project_id) AS project_count
            FROM users u
            LEFT JOIN project_owners p ON p.owner_username = u.username
            GROUP BY u.id
            ORDER BY u.id ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def update_user_role(user_id: int, role: str) -> None:
    safe_role = "admin" if role == "admin" else "user"
    with _connect() as conn:
        if safe_role == "user":
            admins = conn.execute("SELECT COUNT(*) AS cnt FROM users WHERE role = 'admin'").fetchone()["cnt"]
            target = conn.execute("SELECT role FROM users WHERE id = ?", (user_id,)).fetchone()
            if target and target["role"] == "admin" and admins <= 1:
                raise ValueError("Cannot demote the last remaining administrator.")
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (safe_role, user_id))


def reset_user_password(user_id: int, new_password: str) -> None:
    if len(new_password) < 6:
        raise ValueError("New password must be at least 6 characters.")
    with _connect() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new_password), user_id))


def assign_project_owner(project_id: str, owner_username: str, source_label: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO project_owners (project_id, owner_username, source_label, created_at) VALUES (?, ?, ?, ?)",
            (project_id, owner_username, source_label, time.time()),
        )


def get_project_owner_map() -> dict[str, dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT project_id, owner_username, source_label FROM project_owners").fetchall()
        return {r["project_id"]: dict(r) for r in rows}


def create_session_token(user: dict, ttl_seconds: int = 86400 * 7) -> str:
    payload = json.dumps(
        {
            "username": user["username"],
            "email": user.get("email", f"{user['username']}@aiclipping.local"),
            "role": user["role"],
            "exp": int(time.time()) + ttl_seconds,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    b64 = base64.urlsafe_b64encode(payload).decode("ascii")
    sig = hmac.new(_get_secret_key(), b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def decode_session_token(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    try:
        b64, sig = token.rsplit(".", 1)
        expected = hmac.new(_get_secret_key(), b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(b64.encode("ascii")).decode("utf-8"))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return {
            "username": data["username"],
            "email": data.get("email", f"{data['username']}@aiclipping.local"),
            "role": data["role"],
        }
    except Exception:
        return None

def _ensure_reset_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            expires_at REAL NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        )
        """
    )


def create_password_reset_code(email_or_user: str) -> dict:
    clean = email_or_user.strip().lower()
    if not clean:
        raise ValueError("Please enter your account email or username.")
    with _connect() as conn:
        _ensure_reset_table(conn)
        row = conn.execute(
            "SELECT id, username, email, role FROM users WHERE LOWER(email) = ? OR LOWER(username) = ?",
            (clean, clean),
        ).fetchone()
        if not row:
            raise ValueError("No account found with that email or username.")

        conn.execute("UPDATE password_resets SET used = 1 WHERE user_id = ?", (row["id"],))
        code = f"{secrets.randbelow(900000) + 100000}"
        now = time.time()
        conn.execute(
            """
            INSERT INTO password_resets (user_id, email, code_hash, expires_at, used, created_at)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (row["id"], row["email"], hash_password(code), now + 600, now),
        )
        temp_file = STORAGE_DIR / "temp" / "last_reset_code.txt"
        temp_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file.write_text(f"{row['email']}: {code}\n", encoding="utf-8")
        print(f"[auth_service] Password reset code for {row['email']}: {code}")
        return {
            "ok": True,
            "email": row["email"],
            "reset_code": code,
            "expires_in_sec": 600,
        }


def complete_password_reset(email_or_user: str, code: str, new_password: str) -> dict:
    clean = email_or_user.strip().lower()
    clean_code = code.strip()
    if not clean:
        raise ValueError("Email or username is required.")
    if len(clean_code) != 6 or not clean_code.isdigit():
        raise ValueError("Enter the 6-digit verification code.")
    if len(new_password) < 6:
        raise ValueError("New password must be at least 6 characters.")

    with _connect() as conn:
        _ensure_reset_table(conn)
        user_row = conn.execute(
            "SELECT id, username, email, role FROM users WHERE LOWER(email) = ? OR LOWER(username) = ?",
            (clean, clean),
        ).fetchone()
        if not user_row:
            raise ValueError("Account not found.")

        reset_row = conn.execute(
            """
            SELECT id, code_hash, expires_at
            FROM password_resets
            WHERE user_id = ? AND used = 0
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_row["id"],),
        ).fetchone()
        if not reset_row:
            raise ValueError("No active reset code found. Request a new code first.")
        if time.time() > float(reset_row["expires_at"]):
            conn.execute("UPDATE password_resets SET used = 1 WHERE id = ?", (reset_row["id"],))
            raise ValueError("Reset code expired. Please request a new 6-digit code.")
        if not verify_password(clean_code, reset_row["code_hash"]):
            raise ValueError("Invalid 6-digit verification code.")

        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), user_row["id"]),
        )
        conn.execute("UPDATE password_resets SET used = 1 WHERE id = ?", (reset_row["id"],))
        return {
            "id": user_row["id"],
            "username": user_row["username"],
            "email": user_row["email"],
            "role": user_row["role"],
        }
