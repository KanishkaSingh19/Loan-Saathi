"""Checks the server against the real Postgres in DATABASE_URL (e.g. the free Neon database).

    python -I -X utf8 -W ignore src/test_postgres.py

Writes one pre-screen and one call, reads them back, re-imports the server (a fresh process
state, like a restart of the Space), reads them again, then deletes exactly those two rows.
Other rows in the database are left alone.
"""
import importlib
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ["WEBHOOK_KEY"] = "pg-test-key-1357"  # overrides .env for this process only

import server  # noqa: E402  (loads .env, including DATABASE_URL)
from fastapi.testclient import TestClient  # noqa: E402

if not server.DATABASE_URL:
    sys.exit("DATABASE_URL is not set in .env or the environment; nothing to test.")

H = {"X-Webhook-Key": "pg-test-key-1357"}
marker = f"pg-test-{int(time.time())}"


def ids(client):
    data = client.get("/v1/applications?limit=500", headers=H).json()
    return {p["id"] for p in data["prescreens"]}, {c["id"] for c in data["calls"]}


t0 = time.time()
c = TestClient(server.app)
r = c.post("/v1/prescreen", headers=H,
           json={"monthly_income": "30 hazaar", "loan_amount": "50 hazaar", "work": "kirana ki dukaan",
                 "note": marker})
assert r.status_code == 200, r.text
pid = r.json()["prescreen_id"]
print(f"prescreen stored: {r.json()['decision']} ({time.time() - t0:.1f}s incl. connect)")
c.post("/v1/post-call", headers=H, json={"callId": marker})

pres, calls = ids(c)
assert pid in pres, "prescreen not found after write"
cid = next(row["id"] for row in c.get("/v1/applications?limit=500", headers=H).json()["calls"]
           if row["payload"].get("callId") == marker)
print("read back OK")

server = importlib.reload(server)  # fresh module state: new connections, schema check again
c2 = TestClient(server.app)
pres, calls = ids(c2)
assert pid in pres and cid in calls, "rows missing after reload"
print("still there after a fresh start OK")

t1 = time.time()
ids(c2)
print(f"one dashboard read: {time.time() - t1:.2f}s")

server._run("DELETE FROM prescreens WHERE id = ?", (pid,))
server._run("DELETE FROM calls WHERE id = ?", (cid,))
pres, calls = ids(c2)
assert pid not in pres and cid not in calls
print("test rows removed. Postgres OK.")
