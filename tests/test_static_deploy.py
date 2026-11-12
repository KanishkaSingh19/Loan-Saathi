"""The dashboard as a static site: the API answers cross-origin requests from ALLOWED_ORIGINS (and only
those), /health is cheap to ping, the deploy configs agree with each other, and /dashboard/ still works when
the API serves the dashboard itself.

Run with `python -m pytest tests/test_static_deploy.py`.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

PROBE = r"""
import json, os, sys
sys.path.insert(0, "src")
import server
from fastapi.testclient import TestClient
c = TestClient(server.app)
pre = lambda origin: c.options("/v1/converse", headers={
    "Origin": origin, "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type,x-call-session,x-call-token"})
ok, bad = pre("https://loan-saathi-web.onrender.com"), pre("https://evil.example")
h = c.get("/health", headers={"Origin": "https://loan-saathi-web.onrender.com"})
print(json.dumps({"ok_status": ok.status_code, "ok_allow": ok.headers.get("access-control-allow-origin"),
                  "ok_headers": ok.headers.get("access-control-allow-headers", "").lower(),
                  "bad_allow": bad.headers.get("access-control-allow-origin"),
                  "health": h.json(), "health_allow": h.headers.get("access-control-allow-origin")}))
"""


def _probe():
    env = {**os.environ, "ALLOWED_ORIGINS": "https://loan-saathi-web.onrender.com, https://x.netlify.app/",
           "WEBHOOK_KEY": "static-test-key", "DATABASE_URL": "",
           "DB_PATH": str(Path(tempfile.mkdtemp()) / "t.db"), "GEMINI_API_KEY": "", "GROQ_API_KEY": ""}
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", PROBE], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_cors_allows_the_static_site_and_nobody_else():
    p = _probe()
    assert p["ok_status"] == 200 and p["ok_allow"] == "https://loan-saathi-web.onrender.com", p
    for h in ("content-type", "x-call-session", "x-call-token"):
        assert h in p["ok_headers"], p
    assert p["bad_allow"] is None, p
    assert p["health"] == {"ok": True} and p["health_allow"] == "https://loan-saathi-web.onrender.com", p


def test_render_blueprint_static_site_points_at_the_api_and_is_allowed():
    bp = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    by_name = {s["name"]: s for s in bp["services"]}
    api, web = by_name["loan-saathi"], by_name["loan-saathi-web"]
    assert web["runtime"] == "static" and web["rootDir"] == "dashboard" and web["staticPublishPath"] == "dist"
    web_env = {e["key"]: e.get("value") for e in web["envVars"]}
    api_env = {e["key"]: e.get("value") for e in api["envVars"]}
    assert web_env["STATIC_SITE"] == "1"
    assert web_env["VITE_API_BASE"] == "https://loan-saathi.onrender.com" and not web_env["VITE_API_BASE"].endswith("/")
    assert "https://loan-saathi-web.onrender.com" in api_env["ALLOWED_ORIGINS"].split(",")
    assert api["healthCheckPath"] == "/health"


def test_static_hosts_build_for_the_site_root_and_the_api_keeps_dashboard_path():
    vite = (ROOT / "dashboard" / "vite.config.js").read_text(encoding="utf-8")
    assert 'process.env.VERCEL || process.env.NETLIFY || process.env.STATIC_SITE ? "/" : "/dashboard/"' in vite
    assert 'publish = "dist"' in (ROOT / "dashboard" / "netlify.toml").read_text(encoding="utf-8")
    assert json.loads((ROOT / "dashboard" / "vercel.json").read_text(encoding="utf-8"))["outputDirectory"] == "dist"
    server_src = (ROOT / "src" / "server.py").read_text(encoding="utf-8")
    assert '"/dashboard"' in server_src or "'/dashboard'" in server_src


def test_deploy_doc_covers_the_static_site():
    doc = (ROOT / "deploy" / "DEPLOY.md").read_text(encoding="utf-8")
    for needle in ("VITE_API_BASE", "ALLOWED_ORIGINS", "loan-saathi-web", "/health", "Netlify", "/dashboard/"):
        assert needle in doc, needle
