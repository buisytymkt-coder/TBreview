#!/usr/bin/env python3
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
    url: str, user_data_dir: Path, max_rows: int, headless: bool, login_wait_seconds: int
) -> List[Review]:
    reviews: List[Review] = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=headless,
            viewport={"width": 1600, "height": 1200},
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            try:
                page.wait_for_selector("text=Product Ratings", timeout=40000)
            except PlaywrightTimeoutError:
                pass

            page.wait_for_selector("text=Order ID:", timeout=login_wait_seconds * 1000)

            cards = page.locator(
                "xpath=//*[starts-with(normalize-space(.), 'Order ID:') and contains(normalize-space(.), 'Product ID:')]"
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
) -> dict:
    conn = ensure_db(db_path)
    try:
        reviews = fetch_reviews_ui(
            rating_url,
            user_data_dir,
            max_rows=max_rows,
            headless=headless,
            login_wait_seconds=login_wait_seconds,
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
        send_startup = as_bool(get_env("SEND_STARTUP_MESSAGE", required=False, default="false"))
    except Exception as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    app_dir = Path(__file__).resolve().parent
    db_path = app_dir / "seen_reviews.db"
    user_data_dir = app_dir / "browser_profile"
    user_data_dir.mkdir(parents=True, exist_ok=True)

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
