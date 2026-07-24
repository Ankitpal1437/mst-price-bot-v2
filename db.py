"""
Turso (libSQL) persistent storage for the bot.
Stores each dataset (users_data, customers_data) as a JSON blob in a
key-value table. Simple, reliable, and survives Render redeploys.
"""
import json
import os
import libsql_client

TURSO_URL = os.environ.get("TURSO_URL", "")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

_client = None

def get_client():
    global _client
    if _client is None:
        if not TURSO_URL or not TURSO_AUTH_TOKEN:
            raise RuntimeError("TURSO_URL / TURSO_AUTH_TOKEN environment variables missing")
        _client = libsql_client.create_client(url=TURSO_URL, auth_token=TURSO_AUTH_TOKEN)
    return _client

async def init_db():
    client = get_client()
    await client.execute(
        "CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT)"
    )

async def load_json(key: str, default):
    client = get_client()
    rs = await client.execute("SELECT value FROM kv_store WHERE key = ?", [key])
    if rs.rows:
        try:
            return json.loads(rs.rows[0][0])
        except Exception:
            return default
    return default

async def save_json(key: str, data):
    client = get_client()
    await client.execute(
        "INSERT INTO kv_store (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [key, json.dumps(data)],
    )

async def close_db():
    global _client
    if _client is not None:
        await _client.close()
        _client = None
