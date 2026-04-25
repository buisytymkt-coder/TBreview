#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def parse_netscape_cookie_line(line: str):
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 7:
        return None

    domain_raw, _, path, secure_flag, expires_raw, name, value = parts

    http_only = False
    domain = domain_raw
    if domain.startswith("#HttpOnly_"):
        http_only = True
        domain = domain.replace("#HttpOnly_", "", 1)

    secure = secure_flag.strip().upper() == "TRUE"
    expires = None
    try:
        exp_int = int(expires_raw)
        if exp_int > 0:
            expires = float(exp_int)
    except ValueError:
        expires = None

    cookie = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": path or "/",
        "secure": secure,
        "httpOnly": http_only,
        "sameSite": "Lax",
    }

    if expires is not None:
        cookie["expires"] = expires

    return cookie


def convert(input_path: Path, output_path: Path):
    cookies = []
    for raw in input_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue

        item = parse_netscape_cookie_line(raw)
        if item and item.get("name"):
            cookies.append(item)

    if not cookies:
        raise SystemExit("No valid cookies parsed from cookies.txt")

    output_path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(cookies)} cookies -> {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Convert Netscape cookies.txt to Playwright JSON cookie list")
    parser.add_argument("input", help="Path to cookies.txt")
    parser.add_argument("output", nargs="?", default="cookies.json", help="Output JSON path (default: cookies.json)")
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")

    convert(input_path, output_path)


if __name__ == "__main__":
    main()
