#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

import requests
from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


@dataclass
class Review:
    dedupe_id: str
    stars: int
    review_date: str
    username: str
    order_id: str
    product_name: str
    review_snippet: str


def as_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_env(name: str, required: bool = True, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    if required and not value:
        raise ValueError(f"Missing required env var: {name}")
    return value


def ensure_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_reviews (
            dedupe_id TEXT PRIMARY KEY,
            seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_order_ids (
            order_id TEXT PRIMARY KEY,
            seen_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS run_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT NOT NULL,
            status TEXT NOT NULL,
            fetched INTEGER,
            new_count INTEGER,
            error_message TEXT
        )
        """
    )
    conn.commit()
    return conn


def has_seen(conn: sqlite3.Connection, dedupe_id: str, order_id: str) -> bool:
    if order_id:
        row = conn.execute(
            "SELECT 1 FROM seen_order_ids WHERE order_id = ? LIMIT 1", (order_id,)
        ).fetchone()
        if row is not None:
            return True

    row = conn.execute(
        "SELECT 1 FROM seen_reviews WHERE dedupe_id = ? LIMIT 1", (dedupe_id,)
    ).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, dedupe_id: str, order_id: str) -> None:
    now = datetime.utcnow().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO seen_reviews(dedupe_id, seen_at) VALUES(?, ?)",
        (dedupe_id, now),
    )
    if order_id:
        conn.execute(
            "INSERT OR IGNORE INTO seen_order_ids(order_id, seen_at) VALUES(?, ?)",
            (order_id, now),
        )
    conn.commit()


def record_run(
    conn: sqlite3.Connection,
    status: str,
    fetched: int | None = None,
    new_count: int | None = None,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO run_history(run_at, status, fetched, new_count, error_message)
        VALUES(?, ?, ?, ?, ?)
        """,
        (datetime.utcnow().isoformat(), status, fetched, new_count, error_message),
    )
    conn.commit()


def send_telegram(token: str, chat_id: str, message: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=15)
    if not resp.ok:
        raise RuntimeError(f"Telegram send failed: {resp.status_code} {resp.text}")


def clean_lines(text: str) -> List[str]:
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line]


def count_stars_in_text(text: str) -> int:
    return text.count("★")


def build_dedupe_id(parts: List[str]) -> str:
    key = "|".join(parts)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def load_cookies_from_file(cookies_file: Path) -> list[dict]:
    if not cookies_file.exists():
        raise FileNotFoundError(f"Cookie file not found: {cookies_file}")

    raw = json.loads(cookies_file.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
        items = raw["cookies"]
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("Cookie file must be a list or object with 'cookies' list.")

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
        if "httpOnly" in item:
            cookie["httpOnly"] = bool(item.get("httpOnly"))
        if "secure" in item:
            cookie["secure"] = bool(item.get("secure"))
        same_site = str(item.get("sameSite", "")).lower()
        if same_site in {"lax", "strict", "none"}:
            cookie["sameSite"] = same_site.capitalize()

        cookies.append(cookie)

    if not cookies:
        raise ValueError("No valid cookies found in cookie file.")
    return cookies


def parse_row_text(row_text: str, product_block_text: str, stars_from_dom: int, username_from_dom: str) -> Review:
    row_lines = clean_lines(row_text)
    row_all_text = " ".join(row_lines)
    product_lines = clean_lines(product_block_text)
    product_all_text = " ".join(product_lines)

    stars = stars_from_dom if stars_from_dom > 0 else count_stars_in_text(row_all_text)
    if stars == 0:
        star_match = re.search(r"([1-5])\s*[- ]?star", row_all_text, flags=re.IGNORECASE)
        if star_match:
            stars = int(star_match.group(1))

    order_id = ""
    review_date = ""
    username = ""
    product_name = ""

    order_match = re.search(r"Order ID:\s*([0-9]+)", product_all_text, flags=re.IGNORECASE)
    if order_match:
        order_id = order_match.group(1).strip()

    date_match = re.search(
        r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s+\d{4}",
        row_all_text,
        flags=re.IGNORECASE,
    )
    if date_match:
        review_date = date_match.group(0)

    line_user = ""
    for line in row_lines:
        if "@" in line and not line.lower().startswith(("order id:", "product id:")):
            line_user = line.strip()
            break

    if line_user:
        username = line_user
    elif username_from_dom:
        username = username_from_dom.strip()
    else:
        user_match = re.search(r"@[^\s]+", row_all_text)
        if user_match:
            username = user_match.group(0)

    for idx, line in enumerate(product_lines):
        if line.lower().startswith("product id:"):
            for j in range(idx + 1, len(product_lines)):
                candidate = product_lines[j]
                if not candidate:
                    continue
                if candidate.lower().startswith(("reply", "review details", "order id:", "product id:")):
                    continue
                product_name = candidate
                break
            if product_name:
                break

    if not product_name:
        for candidate in product_lines:
            c = candidate.lower()
            if c.startswith(("order id:", "product id:", "reply", "review details")):
                continue
            product_name = candidate
            break

    if order_id:
        dedupe_id = build_dedupe_id(["order_id", order_id])
    else:
        dedupe_id = build_dedupe_id([username, review_date, str(stars), product_name, row_all_text[:500]])

    return Review(
        dedupe_id=dedupe_id,
        stars=stars,
        review_date=review_date,
        username=username,
        order_id=order_id,
        product_name=product_name,
        review_snippet="",
    )


def fetch_reviews_ui(
    url: str,
    user_data_dir: Path,
    max_rows: int,
    headless: bool,
    login_wait_seconds: int,
    network_error_retry_count: int,
    network_error_retry_delay_seconds: int,
    proxy: dict | None = None,
    cookies_file: Path | None = None,
) -> List[Review]:
    reviews: List[Review] = []
    chromium_args = [
        "--disable-gpu",
        "--disable-software-rasterizer",
        "--no-zygote",
        "--disable-features=VizDisplayCompositor,IsolateOrigins,site-per-process",
        "--renderer-process-limit=1",
    ]

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=headless,
            viewport={"width": 1366, "height": 900},
            args=chromium_args,
            proxy=proxy,
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()
            if cookies_file:
                cookies = load_cookies_from_file(cookies_file)
                context.add_cookies(cookies)
            selectors = [
                "text=Order ID:",
                "text=Review details",
                "text=Product ID:",
            ]

            def get_body_preview() -> str:
                try:
                    return " ".join(clean_lines(page.inner_text("body")))[:1200]
                except Exception:
                    return ""

            def has_visible_text(selector: str) -> bool:
                try:
                    loc = page.locator(selector)
                    n = min(loc.count(), 8)
                    for idx in range(n):
                        if loc.nth(idx).is_visible():
                            return True
                except Exception:
                    return False
                return False

            def has_network_error() -> bool:
                try:
                    net_loc = page.locator("text=/Network error/i")
                    n = min(net_loc.count(), 5)
                    for idx in range(n):
                        if net_loc.nth(idx).is_visible():
                            return True
                except Exception:
                    pass
                body = get_body_preview().lower()
                return "network error" in body

            last_diag = "Unknown failure while opening review table."
            for attempt in range(network_error_retry_count + 1):
                if attempt == 0:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                else:
                    page.reload(wait_until="domcontentloaded", timeout=60000)

                try:
                    page.wait_for_selector("text=Product Ratings", timeout=40000)
                except PlaywrightTimeoutError:
                    pass

                if has_network_error():
                    if attempt < network_error_retry_count:
                        page.wait_for_timeout(network_error_retry_delay_seconds * 1000)
                        continue
                    raise RuntimeError(
                        f"TikTok returned Network error after {attempt + 1} attempts."
                    )

                # On some layouts, review rows are below the fold. Probe + auto-scroll
                # before concluding session/login failure.
                ready = False
                deadline = time.time() + login_wait_seconds
                while time.time() < deadline:
                    for sel in selectors:
                        if has_visible_text(sel):
                            ready = True
                            break
                    if ready:
                        break

                    if has_network_error():
                        break

                    try:
                        if page.locator("text=Respond to reviews").count() > 0:
                            page.locator("text=Respond to reviews").first.scroll_into_view_if_needed(timeout=1500)
                    except Exception:
                        pass

                    page.evaluate("window.scrollBy(0, Math.max(800, window.innerHeight));")
                    page.wait_for_timeout(900)

                if ready:
                    break

                current_url = page.url
                title = ""
                selector_counts = {}
                try:
                    title = page.title()
                except Exception:
                    title = "N/A"
                body_preview = get_body_preview()
                for sel in selectors:
                    try:
                        selector_counts[sel] = page.locator(sel).count()
                        selector_counts[f"{sel} (visible)"] = int(has_visible_text(sel))
                    except Exception:
                        selector_counts[sel] = -1

                last_diag = (
                    "Timeout waiting review table. "
                    f"url={current_url} title={title} selectors={selector_counts} body={body_preview}"
                )

                if has_network_error() and attempt < network_error_retry_count:
                    page.wait_for_timeout(network_error_retry_delay_seconds * 1000)
                    continue

                raise RuntimeError(last_diag)
            else:
                raise RuntimeError(last_diag)

            cards = page.locator(
                "xpath=//*[contains(normalize-space(.), 'Order ID:') and contains(normalize-space(.), 'Product ID:')]"
            )
            seen_order_ids = set()

            for i in range(cards.count()):
                product_block_text = cards.nth(i).inner_text(timeout=5000).strip()
                if not product_block_text:
                    continue

                order_match = re.search(r"Order ID:\s*([0-9]+)", product_block_text, flags=re.IGNORECASE)
                if not order_match:
                    continue
                order_id = order_match.group(1).strip()
                if order_id in seen_order_ids:
                    continue

                row_data = cards.nth(i).evaluate(
                    """(el, oid) => {
                        let cur = el;
                        while (cur) {
                            const txt = (cur.innerText || '').trim();
                            if (
                                txt.includes('Order ID:' + oid) &&
                                txt.includes('Review details') &&
                                txt.length < 2600
                            ) {
                                const stars = cur.querySelectorAll('[class*="activeStar"]').length;
                                const userNode = cur.querySelector('[class*="username"]');
                                return {
                                    text: txt,
                                    stars,
                                    username: userNode ? (userNode.innerText || '').trim() : ''
                                };
                            }
                            cur = cur.parentElement;
                        }
                        return {
                            text: (el.innerText || '').trim(),
                            stars: 0,
                            username: ''
                        };
                    }""",
                    order_id,
                )

                review = parse_row_text(
                    row_data.get("text", ""),
                    product_block_text,
                    int(row_data.get("stars", 0)),
                    row_data.get("username", ""),
                )
                if review.order_id:
                    reviews.append(review)
                    seen_order_ids.add(review.order_id)

                if len(reviews) >= max_rows:
                    break
        finally:
            context.close()

    return reviews


def format_message(review: Review) -> str:
    stars_text = "⭐" * review.stars if review.stars > 0 else "(không xác định sao)"
    parts = [
        "<b>TikTok Shop US - Review mới</b>",
        f"- Sao: {stars_text}",
        f"- User: {review.username or 'N/A'}",
        f"- Ngày: {review.review_date or 'N/A'}",
        f"- Order ID: {review.order_id or 'N/A'}",
        f"- Sản phẩm: {review.product_name or 'N/A'}",
    ]
    return "\n".join(parts)


def format_error_message(error_text: str) -> str:
    short_error = error_text.strip().replace("\n", " ")
    if len(short_error) > 500:
        short_error = short_error[:500] + "..."
    return "\n".join(
        [
            "<b>TikTok Shop US - Cảnh báo</b>",
            "- Không thể truy cập trang review hoặc session login bị lỗi.",
            f"- Chi tiết: {short_error or 'Không rõ lỗi'}",
            "- Hành động: mở lại script không headless để login lại nếu cần.",
        ]
    )


def run_once(
    token: str,
    chat_id: str,
    rating_url: str,
    db_path: Path,
    user_data_dir: Path,
    max_rows: int,
    headless: bool,
    login_wait_seconds: int,
    network_error_retry_count: int,
    network_error_retry_delay_seconds: int,
    proxy: dict | None = None,
    cookies_file: Path | None = None,
) -> dict:
    conn = ensure_db(db_path)
    try:
        reviews = fetch_reviews_ui(
            rating_url,
            user_data_dir,
            max_rows=max_rows,
            headless=headless,
            login_wait_seconds=login_wait_seconds,
            network_error_retry_count=network_error_retry_count,
            network_error_retry_delay_seconds=network_error_retry_delay_seconds,
            proxy=proxy,
            cookies_file=cookies_file,
        )
        new_count = 0

        for review in reversed(reviews):
            if has_seen(conn, review.dedupe_id, review.order_id):
                continue
            send_telegram(token, chat_id, format_message(review))
            mark_seen(conn, review.dedupe_id, review.order_id)
            new_count += 1

        result = {"fetched": len(reviews), "new": new_count}
        record_run(conn, status="success", fetched=result["fetched"], new_count=result["new"])
        return result
    finally:
        conn.close()


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description="TikTok Shop review -> Telegram notifier")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless. First login should be done without this flag.",
    )
    args = parser.parse_args()

    try:
        token = get_env("TELEGRAM_BOT_TOKEN")
        chat_id = get_env("TELEGRAM_CHAT_ID")
        rating_url = get_env(
            "TIKTOK_RATING_URL",
            required=False,
            default="https://seller-us.tiktok.com/product/rating?shop_region=US",
        )
        poll_seconds = int(get_env("POLL_SECONDS", required=False, default="300"))
        max_rows = int(get_env("MAX_ROWS", required=False, default="30"))
        login_wait_seconds = int(get_env("LOGIN_WAIT_SECONDS", required=False, default="300"))
        network_error_retry_count = int(
            get_env("NETWORK_ERROR_RETRY_COUNT", required=False, default="4")
        )
        network_error_retry_delay_seconds = int(
            get_env("NETWORK_ERROR_RETRY_DELAY_SECONDS", required=False, default="5")
        )
        proxy_server = get_env("TIKTOK_PROXY_SERVER", required=False, default="")
        proxy_username = get_env("TIKTOK_PROXY_USERNAME", required=False, default="")
        proxy_password = get_env("TIKTOK_PROXY_PASSWORD", required=False, default="")
        send_startup = as_bool(get_env("SEND_STARTUP_MESSAGE", required=False, default="false"))
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    app_dir = Path(__file__).resolve().parent
    db_path = app_dir / "seen_reviews.db"
    user_data_dir = app_dir / "browser_profile"
    user_data_dir.mkdir(parents=True, exist_ok=True)
    cookies_file_env = get_env("TIKTOK_COOKIES_FILE", required=False, default="")
    cookies_file = None
    if cookies_file_env:
        cookies_file = Path(cookies_file_env)
        if not cookies_file.is_absolute():
            cookies_file = app_dir / cookies_file
    proxy = None
    if proxy_server:
        proxy = {"server": proxy_server}
        if proxy_username:
            proxy["username"] = proxy_username
        if proxy_password:
            proxy["password"] = proxy_password

    if send_startup:
        try:
            send_telegram(token, chat_id, "TikTok review notifier started.")
        except Exception as exc:
            print(f"Warning: startup message failed: {exc}", file=sys.stderr)

    if args.once:
        try:
            result = run_once(
                token,
                chat_id,
                rating_url,
                db_path,
                user_data_dir,
                max_rows=max_rows,
                headless=args.headless,
                login_wait_seconds=login_wait_seconds,
                network_error_retry_count=network_error_retry_count,
                network_error_retry_delay_seconds=network_error_retry_delay_seconds,
                proxy=proxy,
                cookies_file=cookies_file,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except Exception as exc:
            conn = ensure_db(db_path)
            try:
                record_run(conn, status="error", error_message=str(exc))
            finally:
                conn.close()
            try:
                send_telegram(token, chat_id, format_error_message(str(exc)))
            except Exception:
                pass
            print(f"Run failed: {exc}", file=sys.stderr)
            return 1

    last_error_signature = ""
    while True:
        started = time.time()
        try:
            result = run_once(
                token,
                chat_id,
                rating_url,
                db_path,
                user_data_dir,
                max_rows=max_rows,
                headless=args.headless,
                login_wait_seconds=login_wait_seconds,
                network_error_retry_count=network_error_retry_count,
                network_error_retry_delay_seconds=network_error_retry_delay_seconds,
                proxy=proxy,
                cookies_file=cookies_file,
            )
            last_error_signature = ""
            print(f"[{datetime.now().isoformat(timespec='seconds')}] fetched={result['fetched']} new={result['new']}")
        except Exception as exc:
            conn = ensure_db(db_path)
            try:
                record_run(conn, status="error", error_message=str(exc))
            finally:
                conn.close()
            signature = str(exc).strip()
            if signature != last_error_signature:
                try:
                    send_telegram(token, chat_id, format_error_message(signature))
                except Exception:
                    pass
                last_error_signature = signature
            print(f"[{datetime.now().isoformat(timespec='seconds')}] cycle failed: {exc}", file=sys.stderr)

        elapsed = time.time() - started
        time.sleep(max(5, poll_seconds - int(elapsed)))


if __name__ == "__main__":
    raise SystemExit(main())
