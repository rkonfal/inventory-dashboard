#!/usr/bin/env python3
"""Simple email capture service for a landing page form.

Usage:
  python3 save_email.py
  curl -X POST http://127.0.0.1:8000/save-email \
    -H 'Content-Type: application/json' \
    -d '{"email":"test@example.com","consent":true}'
"""

from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parent
HOST = os.environ.get("SAVE_EMAIL_HOST", "0.0.0.0")
PORT = int(os.environ.get("SAVE_EMAIL_PORT", "8000"))
EMAILS_CSV_PATH = Path(
    os.environ.get(
        "EMAILS_CSV_PATH",
        str(BASE_DIR / "emails.csv"),
    )
)
EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}$", re.IGNORECASE)
FILE_LOCK = threading.Lock()
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("SAVE_EMAIL_RATE_LIMIT_WINDOW", "60"))
RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("SAVE_EMAIL_RATE_LIMIT_MAX", "12"))
MAX_BODY_BYTES = int(os.environ.get("SAVE_EMAIL_MAX_BODY_BYTES", "16384"))
RETENTION_DAYS = int(os.environ.get("SAVE_EMAIL_RETENTION_DAYS", "365"))
ALLOWED_ORIGINS = {
    origin.strip()
    for origin in os.environ.get(
        "SAVE_EMAIL_ALLOWED_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000,null",
    ).split(",")
    if origin.strip()
}
TRUST_PROXY_HEADERS = os.environ.get("SAVE_EMAIL_TRUST_PROXY", "").strip().lower() in {"1", "true", "yes"}
ALLOWED_INTERESTS = {
    "Hydratace",
    "Rozjasnění",
    "Zklidnění citlivé pleti",
    "Kompletní rutina",
}
CONSENT_VERSION = "marketing-consent-v1"
PRIVACY_VERSION = "privacy-policy-v1"
REQUEST_LOG: dict[str, deque[float]] = {}


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_PATTERN.fullmatch(value))


def normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def sanitize_text(value: Any, limit: int = 120) -> str:
    text = str(value).strip()
    return " ".join(text.split())[:limit]


def sanitize_csv_cell(value: str) -> str:
    if value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def load_existing_emails(path: Path) -> set[str]:
    if not path.exists():
        return set()

    existing: set[str] = set()
    with path.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.reader(csv_file)
        for row in reader:
            if not row:
                continue
            email = row[0].strip().lower()
            if email == "email":
                continue
            existing.add(email)
    return existing


def is_row_retained(row: dict[str, str]) -> bool:
    submitted_at = row.get("submitted_at", "").strip()
    if not submitted_at:
        return True
    try:
        timestamp = datetime.fromisoformat(submitted_at)
    except ValueError:
        return True
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp >= datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)


def prune_expired_rows(path: Path) -> set[str]:
    if not path.exists():
        return set()

    with path.open("r", newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        rows = list(reader)
        fieldnames = reader.fieldnames or [
            "email",
            "name",
            "interest",
            "consent",
            "consent_version",
            "privacy_version",
            "submitted_at",
            "source",
        ]

    retained_rows = [row for row in rows if is_row_retained(row)]
    if len(retained_rows) != len(rows):
        with path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(retained_rows)

    return {
        str(row.get("email", "")).strip().lower()
        for row in retained_rows
        if str(row.get("email", "")).strip()
    }


def save_email(path: Path, email: str, record: dict[str, str]) -> bool:
    normalized_email = email.strip().lower()
    path.parent.mkdir(parents=True, exist_ok=True)

    with FILE_LOCK:
        existing = prune_expired_rows(path)
        if normalized_email in existing:
            return False

        file_exists = path.exists()
        with path.open("a", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            if not file_exists or path.stat().st_size == 0:
                writer.writerow(
                    [
                        "email",
                        "name",
                        "interest",
                        "consent",
                        "consent_version",
                        "privacy_version",
                        "submitted_at",
                        "source",
                    ]
                )
            writer.writerow(
                [
                    normalized_email,
                    sanitize_csv_cell(record["name"]),
                    sanitize_csv_cell(record["interest"]),
                    "true",
                    record["consent_version"],
                    record["privacy_version"],
                    record["submitted_at"],
                    sanitize_csv_cell(record["source"]),
                ]
            )
        return True


class SaveEmailHandler(BaseHTTPRequestHandler):
    server_version = "SaveEmailHTTP/1.0"

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != "/save-email":
            self.send_json(
                HTTPStatus.NOT_FOUND,
                {"ok": False, "error": "Endpoint not found."},
            )
            return

        if not self.is_origin_allowed():
            self.send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "Origin is not allowed."},
            )
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_BODY_BYTES:
            self.send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"ok": False, "error": "Payload too large."},
            )
            return

        try:
            payload = self.parse_request_body()
        except ValueError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})
            return

        client_ip = self.get_client_ip()
        if self.is_rate_limited(client_ip):
            self.send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"ok": False, "error": "Too many requests. Try again later."},
            )
            return

        email = str(payload.get("email", "")).strip()
        if not email:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Missing email field."},
            )
            return

        if not is_valid_email(email):
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Invalid email address."},
            )
            return

        consent = normalize_bool(payload.get("consent", False))
        if not consent:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Consent is required."},
            )
            return

        interest = sanitize_text(payload.get("interest", ""))
        if interest and interest not in ALLOWED_INTERESTS:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "Invalid interest value."},
            )
            return

        record = {
            "name": sanitize_text(payload.get("name", "")),
            "interest": interest,
            "consent_version": CONSENT_VERSION,
            "privacy_version": PRIVACY_VERSION,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "source": self.headers.get("Origin", "direct"),
        }

        created = save_email(EMAILS_CSV_PATH, email, record)
        self.send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "saved": created,
                "message": "Email saved." if created else "Email already exists.",
            },
        )

    def parse_request_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > MAX_BODY_BYTES:
            raise ValueError("Payload too large.")
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()

        if content_type == "application/json":
            if not raw_body:
                raise ValueError("Request body is empty.")
            try:
                data = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Invalid JSON body.") from exc
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object.")
            return data

        if content_type == "application/x-www-form-urlencoded":
            form_data = parse_qs(raw_body.decode("utf-8"), keep_blank_values=True)
            return {key: values[0] if values else "" for key, values in form_data.items()}

        raise ValueError("Unsupported Content-Type. Use JSON or form-urlencoded.")

    def get_client_ip(self) -> str:
        forwarded = self.headers.get("X-Forwarded-For", "")
        if TRUST_PROXY_HEADERS and forwarded:
            return forwarded.split(",", 1)[0].strip()
        return self.client_address[0]

    def is_origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        if origin == "null":
            return origin in ALLOWED_ORIGINS
        if self.is_same_origin(origin):
            return True
        return origin in ALLOWED_ORIGINS

    def is_same_origin(self, origin: str) -> bool:
        parsed_origin = urlparse(origin)
        if not parsed_origin.scheme or not parsed_origin.netloc:
            return False
        expected_host = self.headers.get("Host", "")
        return parsed_origin.netloc == expected_host

    def is_rate_limited(self, client_ip: str) -> bool:
        now = time.time()
        with FILE_LOCK:
            bucket = REQUEST_LOG.setdefault(client_ip, deque())
            while bucket and now - bucket[0] > RATE_LIMIT_WINDOW_SECONDS:
                bucket.popleft()
            if len(bucket) >= RATE_LIMIT_MAX_REQUESTS:
                return True
            bucket.append(now)
        return False

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        allowed_origin = "null"
        if origin:
            if origin == "null" and origin in ALLOWED_ORIGINS:
                allowed_origin = "null"
            elif self.is_same_origin(origin) or origin in ALLOWED_ORIGINS:
                allowed_origin = origin
        self.send_header("Access-Control-Allow-Origin", allowed_origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Vary", "Origin")

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), SaveEmailHandler)
    print(f"Listening on http://{HOST}:{PORT}")
    print(f"Saving emails to {EMAILS_CSV_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
