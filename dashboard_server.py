#!/usr/bin/env python3
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Flask
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "seen_reviews.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": datetime.utcnow().isoformat() + "Z"}


@app.get("/")
def index():
    rows = []
    last_success = None
    last_error = None

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
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()

    def row_to_html(r):
        status_color = "#0a7" if r["status"] == "success" else "#d33"
        err = (r["error_message"] or "").replace("<", "&lt;").replace(">", "&gt;")
        return (
            f"<tr>"
            f"<td>{r['run_at']}</td>"
            f"<td style='color:{status_color};font-weight:600'>{r['status']}</td>"
            f"<td>{r['fetched'] if r['fetched'] is not None else ''}</td>"
            f"<td>{r['new_count'] if r['new_count'] is not None else ''}</td>"
            f"<td style='max-width:680px;word-break:break-word'>{err}</td>"
            f"</tr>"
        )

    rows_html = "\n".join(row_to_html(r) for r in rows)

    success_text = (
        f"{last_success['run_at']} | fetched={last_success['fetched']} | new={last_success['new_count']}"
        if last_success
        else "N/A"
    )
    error_text = (
        f"{last_error['run_at']} | {last_error['error_message']}"
        if last_error
        else "N/A"
    )

    return f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>TikTok Review Bot - Run History</title>
  <style>
    body {{ font-family: -apple-system, Segoe UI, Arial, sans-serif; background:#f5f7fb; margin:0; }}
    .wrap {{ max-width:1200px; margin:24px auto; padding:0 16px; }}
    .card {{ background:#fff; border-radius:12px; box-shadow:0 2px 14px rgba(0,0,0,0.08); padding:16px; margin-bottom:16px; }}
    h1 {{ margin:0 0 12px; font-size:24px; }}
    table {{ border-collapse: collapse; width:100%; font-size:14px; }}
    th, td {{ border-bottom:1px solid #e8edf4; text-align:left; padding:8px; vertical-align:top; }}
    th {{ background:#fafcff; position:sticky; top:0; }}
    .meta {{ color:#34495e; font-size:14px; line-height:1.7; }}
    .small {{ color:#6b7280; font-size:12px; }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"card\">
      <h1>TikTok Review Bot - Run History</h1>
      <div class=\"meta\">
        <div><b>Now (UTC):</b> {datetime.utcnow().isoformat()}Z</div>
        <div><b>Last success:</b> {success_text}</div>
        <div><b>Last error:</b> {error_text}</div>
      </div>
      <div class=\"small\">Refresh this page to get latest updates.</div>
    </div>
    <div class=\"card\">
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
