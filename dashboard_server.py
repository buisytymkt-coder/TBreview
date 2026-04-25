#!/usr/bin/env python3
import json
import os
import sqlite3
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv
from flask import Flask, redirect, request

load_dotenv()

app = Flask(__name__)
APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "seen_reviews.db"
COOKIES_PATH = Path(os.getenv("TIKTOK_COOKIES_FILE", "cookies.json") or "cookies.json")
if not COOKIES_PATH.is_absolute():
    COOKIES_PATH = APP_DIR / COOKIES_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def parse_json_cookie_text(raw_text: str) -> list[dict]:
    raw = json.loads(raw_text)
    if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
        items = raw["cookies"]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("Cookie JSON must be a list or object with 'cookies'.")

    cookies: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        value = str(item.get("value", "")).strip()
        if not name:
            continue

        cookie: dict = {"name": name, "value": value}
        domain = str(item.get("domain", "")).strip()
        url = str(item.get("url", "")).strip()
        if domain:
            cookie["domain"] = domain
            cookie["path"] = str(item.get("path", "/")) or "/"
        elif url:
            cookie["url"] = url
        else:
            continue

        expires = item.get("expires", item.get("expirationDate"))
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expires"] = float(expires)
        if "secure" in item:
            cookie["secure"] = bool(item.get("secure"))
        if "httpOnly" in item:
            cookie["httpOnly"] = bool(item.get("httpOnly"))
        same_site = str(item.get("sameSite", "")).lower()
        if same_site in {"lax", "strict", "none"}:
            cookie["sameSite"] = same_site.capitalize()

        cookies.append(cookie)

    if not cookies:
        raise ValueError("No valid cookie records found in JSON.")
    return cookies


def parse_netscape_cookie_text(raw_text: str) -> list[dict]:
    cookies: list[dict] = []
    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue

        parts = raw.rstrip("\n").split("\t")
        if len(parts) != 7:
            continue
        domain_raw, _, path, secure_flag, expires_raw, name, value = parts
        if not name:
            continue

        http_only = False
        domain = domain_raw
        if domain.startswith("#HttpOnly_"):
            http_only = True
            domain = domain.replace("#HttpOnly_", "", 1)

        cookie: dict = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path or "/",
            "secure": secure_flag.strip().upper() == "TRUE",
            "httpOnly": http_only,
            "sameSite": "Lax",
        }
        try:
            exp_int = int(expires_raw)
            if exp_int > 0:
                cookie["expires"] = float(exp_int)
        except ValueError:
            pass

        cookies.append(cookie)

    if not cookies:
        raise ValueError("No valid cookies parsed from cookies.txt format.")
    return cookies


def write_cookie_file(cookies: list[dict]) -> None:
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOKIES_PATH.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": datetime.utcnow().isoformat() + "Z"}


@app.post("/cookies/update")
def update_cookies():
    raw_cookie_text = request.form.get("cookie_text", "").strip()
    cookie_format = request.form.get("cookie_format", "auto").strip().lower()

    if not raw_cookie_text:
        return redirect("/?msg=Cookie+input+is+empty.&ok=0")

    try:
        if cookie_format == "json":
            cookies = parse_json_cookie_text(raw_cookie_text)
        elif cookie_format == "netscape":
            cookies = parse_netscape_cookie_text(raw_cookie_text)
        else:
            # auto: try JSON first, fallback to Netscape
            try:
                cookies = parse_json_cookie_text(raw_cookie_text)
            except Exception:
                cookies = parse_netscape_cookie_text(raw_cookie_text)
        write_cookie_file(cookies)
        msg = quote_plus(f"Saved {len(cookies)} cookies to {COOKIES_PATH}")
        return redirect(f"/?msg={msg}&ok=1")
    except Exception as exc:
        msg = quote_plus(str(exc))
        return redirect(f"/?msg={msg}&ok=0")


@app.get("/")
def index():
    rows = []
    last_success = None
    last_error = None
    error_rows = []
    message = request.args.get("msg", "")
    msg_ok = request.args.get("ok", "")

    if DB_PATH.exists():
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT run_at, status, fetched, new_count, error_message
                FROM run_history
                ORDER BY id DESC
                LIMIT 200
                """
            ).fetchall()
            last_success = conn.execute(
                """
                SELECT run_at, fetched, new_count
                FROM run_history
                WHERE status = 'success'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            last_error = conn.execute(
                """
                SELECT run_at, error_message
                FROM run_history
                WHERE status = 'error'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            error_rows = conn.execute(
                """
                SELECT run_at, error_message
                FROM run_history
                WHERE status = 'error'
                ORDER BY id DESC
                LIMIT 20
                """
            ).fetchall()
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    cookie_exists = COOKIES_PATH.exists()
    cookie_mtime = (
        datetime.utcfromtimestamp(COOKIES_PATH.stat().st_mtime).isoformat() + "Z"
        if cookie_exists
        else "N/A"
    )

    def row_to_html(r):
        status_color = "#0a7" if r["status"] == "success" else "#d33"
        err = escape(r["error_message"] or "")
        return (
            f"<tr>"
            f"<td>{escape(r['run_at'])}</td>"
            f"<td style='color:{status_color};font-weight:600'>{escape(r['status'])}</td>"
            f"<td>{r['fetched'] if r['fetched'] is not None else ''}</td>"
            f"<td>{r['new_count'] if r['new_count'] is not None else ''}</td>"
            f"<td style='max-width:680px;word-break:break-word'>{err}</td>"
            f"</tr>"
        )

    def err_to_html(r):
        return (
            f"<tr>"
            f"<td>{escape(r['run_at'])}</td>"
            f"<td style='max-width:900px;word-break:break-word'>{escape(r['error_message'] or '')}</td>"
            f"</tr>"
        )

    rows_html = "\n".join(row_to_html(r) for r in rows)
    errors_html = "\n".join(err_to_html(r) for r in error_rows)
    success_text = (
        f"{last_success['run_at']} | fetched={last_success['fetched']} | new={last_success['new_count']}"
        if last_success
        else "N/A"
    )
    error_text = f"{last_error['run_at']} | {last_error['error_message']}" if last_error else "N/A"
    msg_box = ""
    if message:
        color = "#0a7" if msg_ok == "1" else "#d33"
        msg_box = f"<div class='msg' style='border-left:4px solid {color}'>{escape(message)}</div>"

    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TikTok Review Bot - Dashboard</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background:#f5f7fb; margin:0; }}
    .wrap {{ max-width:1300px; margin:24px auto; padding:0 16px; }}
    .card {{ background:#fff; border-radius:12px; box-shadow:0 2px 14px rgba(0,0,0,0.08); padding:16px; margin-bottom:16px; }}
    h1 {{ margin:0 0 12px; font-size:24px; }}
    h2 {{ margin:0 0 10px; font-size:18px; }}
    table {{ border-collapse: collapse; width:100%; font-size:14px; }}
    th, td {{ border-bottom:1px solid #e8edf4; text-align:left; padding:8px; vertical-align:top; }}
    th {{ background:#fafcff; position:sticky; top:0; }}
    .meta {{ color:#34495e; font-size:14px; line-height:1.7; }}
    .small {{ color:#6b7280; font-size:12px; }}
    .grid {{ display:grid; grid-template-columns: 1fr 1fr; gap:16px; }}
    .msg {{ margin: 0 0 12px; padding:10px 12px; background:#f9fbff; border-radius:8px; }}
    textarea {{ width:100%; min-height:180px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }}
    .row {{ display:flex; gap:10px; align-items:center; margin-bottom:10px; flex-wrap:wrap; }}
    button {{ background:#0ea5e9; color:#fff; border:0; border-radius:8px; padding:8px 14px; cursor:pointer; }}
    @media (max-width: 980px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>TikTok Review Bot - Dashboard</h1>
      <div class="meta">
        <div><b>Now (UTC):</b> {datetime.utcnow().isoformat()}Z</div>
        <div><b>Last success:</b> {escape(success_text)}</div>
        <div><b>Last error:</b> {escape(error_text)}</div>
        <div><b>Cookie file:</b> {escape(str(COOKIES_PATH))}</div>
        <div><b>Cookie updated:</b> {escape(cookie_mtime)}</div>
      </div>
      <div class="small">Refresh page to see latest runs. Bot reads cookies from file at each cycle.</div>
    </div>
    <div class="grid">
      <div class="card">
        <h2>Update Cookie</h2>
        {msg_box}
        <form method="post" action="/cookies/update">
          <div class="row">
            <label><b>Format:</b></label>
            <select name="cookie_format">
              <option value="auto">Auto detect</option>
              <option value="json">JSON</option>
              <option value="netscape">cookies.txt (Netscape)</option>
            </select>
            <button type="submit">Save Cookies</button>
          </div>
          <textarea name="cookie_text" placeholder="Paste cookie JSON or cookies.txt content here..."></textarea>
        </form>
      </div>
      <div class="card">
        <h2>Recent Errors (20)</h2>
        <table>
          <thead><tr><th>run_at (UTC)</th><th>error_message</th></tr></thead>
          <tbody>{errors_html}</tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <h2>Run History (200)</h2>
      <table>
        <thead>
          <tr>
            <th>run_at (UTC)</th>
            <th>status</th>
            <th>fetched</th>
            <th>new</th>
            <th>error_message</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""


if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    app.run(host=host, port=port)
