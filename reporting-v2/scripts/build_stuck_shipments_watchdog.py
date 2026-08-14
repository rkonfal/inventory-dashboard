#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIR = ROOT / "data" / "inbox" / "stuck-shipments"
DEFAULT_OUTPUT_JSON = ROOT / "data" / "current" / "stuck_shipments_watchdog.json"
DEFAULT_OUTPUT_TEXT = ROOT / "data" / "current" / "stuck_shipments_watchdog_telegram.txt"
DEFAULT_STATE = ROOT / "data" / "current" / "stuck_shipments_watchdog_state.json"
PRAGUE_TZ = ZoneInfo("Europe/Prague")
SEVERITY_RANK = {"warn": 1, "alert": 2, "critical": 3}
DEFAULT_WPJ_LOOKBACK_DAYS = 21
PICKUP_POINT_KEYWORDS = (
    "z-box",
    "zbox",
    "vydej",
    "výdej",
    "ulozenka",
    "uloženka",
    "pickup",
    "packeta",
    "parcelshop",
    "osobni odber",
    "osobní odběr",
    "box",
)
PICKUP_PROVIDER_KEYWORDS = (
    "zasilkovna",
    "zásilkovna",
    "balikovna",
    "balíkovna",
)
NON_PICKUP_HINTS = ("na adresu", "kuryr", "kurýr", "kuryr")
CESKA_POSTA_TRACKING_BASE = "https://www.postaonline.cz/trackandtrace/-/zasilka/cislo?parcelNumbers="
PACKETA_TRACKING_BASE = "https://tracking.packeta.com"
DPD_GEOAPI_BASE = "https://geoapi.dpd.cz/v2/parcels"
DIRECT_CARRIER_USER_AGENT = "reporting-v2-stuck-watchdog/1.0"
DIRECT_CARRIER_TIMEOUT = 30
RENDERED_DOM_TIMEOUT = 45
CHROME_BIN = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
DIRECT_CARRIER_CACHE: dict[str, dict[str, Any]] = {}
FOURPX_TRACKING_CACHE: dict[str, dict[str, Any]] = {}
DPD_GEOAPI_CACHE: dict[str, dict[str, Any]] = {}
REFRESH_DATA_MODULE = None


@dataclass
class Thresholds:
    warn_hours: float
    alert_hours: float
    critical_hours: float


def build_default_direct_carrier() -> dict[str, Any]:
    return {
        "provider": None,
        "status": "unavailable",
        "statusLabel": "bez přímého carrier checku",
        "trackingUrl": None,
        "packageNumber": None,
        "latestMovementAt": None,
        "hoursWithoutMovement": None,
        "currentState": None,
        "latestEvent": None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("csv", "wpj"), default="csv")
    parser.add_argument("--input", help="Specific CSV export path to process.")
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_JSON))
    parser.add_argument("--output-text", default=str(DEFAULT_OUTPUT_TEXT))
    parser.add_argument("--state", default=str(DEFAULT_STATE))
    parser.add_argument("--warn-hours", type=float, default=48.0)
    parser.add_argument("--alert-hours", type=float, default=72.0)
    parser.add_argument("--critical-hours", type=float, default=120.0)
    parser.add_argument("--wpj-lookback-days", type=int, default=DEFAULT_WPJ_LOOKBACK_DAYS)
    parser.add_argument("--skip-wpj", action="store_true")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", help="Override current timestamp, ISO 8601.")
    return parser.parse_args()


def parse_local_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip().strip('"')
    if not raw:
        return None
    return datetime.strptime(raw, "%d.%m.%Y %H:%M:%S").replace(tzinfo=PRAGUE_TZ)


def load_rows(path: Path, now_dt: datetime, thresholds: Thresholds) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for raw_row in reader:
            created_at = parse_local_dt(raw_row.get("Datum"))
            last_movement_at = parse_local_dt(raw_row.get("Poslední pohyb"))
            if not last_movement_at:
                continue
            hours_without_movement = round((now_dt - last_movement_at).total_seconds() / 3600, 1)
            if hours_without_movement < thresholds.warn_hours:
                continue
            price_amount, currency = parse_money(raw_row.get("Cena"))
            phone = clean_string(raw_row.get("Telefon"))
            tiande_id = clean_string(raw_row.get("TiandeID"))
            sponsor_id = clean_string(raw_row.get("ID sponsora"))
            email = clean_string(raw_row.get("E-mail"))
            order_code = clean_string(raw_row.get("Kód")) or clean_string(raw_row.get("ID"))
            severity = classify_severity(hours_without_movement, thresholds)
            row = {
                "id": clean_string(raw_row.get("ID")),
                "orderCode": order_code,
                "createdAt": created_at.isoformat() if created_at else None,
                "lastMovementAt": last_movement_at.isoformat(),
                "hoursWithoutMovement": hours_without_movement,
                "daysWithoutMovement": round(hours_without_movement / 24, 2),
                "severity": severity,
                "severityRank": SEVERITY_RANK[severity],
                "email": email,
                "phone": phone,
                "tiandeId": tiande_id,
                "status": clean_string(raw_row.get("Stav")) or "–",
                "price": {
                    "amount": price_amount,
                    "currency": currency,
                    "display": clean_string(raw_row.get("Cena")) or "0",
                },
                "sponsorId": sponsor_id,
                "country": infer_country(phone=phone, currency=currency, email=email),
                "isZeroValue": price_amount == 0.0,
                "missingTiandeId": not bool(tiande_id),
                "missingSponsorId": not bool(sponsor_id),
                "directCarrier": build_default_direct_carrier(),
                "carrierEscalationCandidate": True,
            }
            rows.append(row)
    rows.sort(
        key=lambda item: (
            -item["severityRank"],
            -item["hoursWithoutMovement"],
            -item["price"]["amount"],
            item["orderCode"] or "",
        )
    )
    return rows


def clean_string(value: Any) -> str:
    return str(value or "").strip()


def parse_money(value: Any) -> tuple[float, str]:
    raw = clean_string(value)
    if not raw:
        return 0.0, ""
    pieces = raw.replace("\xa0", " ").split()
    if not pieces:
        return 0.0, ""
    amount_raw = pieces[0].replace(",", ".")
    try:
        amount = round(float(amount_raw), 2)
    except ValueError:
        amount = 0.0
    currency = pieces[1] if len(pieces) > 1 else ""
    return amount, currency


def infer_country(*, phone: str, currency: str, email: str) -> str:
    if currency.upper() == "EUR" or phone.startswith("+421"):
        return "SK"
    if currency.upper() == "CZK" or phone.startswith("+420"):
        return "CZ"
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    if domain.endswith(".sk"):
        return "SK"
    if domain.endswith(".cz"):
        return "CZ"
    return "unknown"


def classify_severity(hours_without_movement: float, thresholds: Thresholds) -> str:
    if hours_without_movement >= thresholds.critical_hours:
        return "critical"
    if hours_without_movement >= thresholds.alert_hours:
        return "alert"
    return "warn"


def build_payload(source_meta: dict[str, Any], rows: list[dict[str, Any]], now_dt: datetime, thresholds: Thresholds) -> dict[str, Any]:
    currencies: dict[str, float] = {}
    by_country: dict[str, int] = {}
    by_severity = {"warn": 0, "alert": 0, "critical": 0}
    carrier_status_counts: dict[str, int] = {}
    direct_carrier_counts: dict[str, int] = {}
    for row in rows:
        currency = row["price"]["currency"] or "unknown"
        currencies[currency] = round(currencies.get(currency, 0.0) + float(row["price"]["amount"] or 0.0), 2)
        by_country[row["country"]] = by_country.get(row["country"], 0) + 1
        by_severity[row["severity"]] += 1
        carrier_status = (((row.get("wpj") or {}).get("carrierCheck") or {}).get("status") or "unchecked")
        carrier_status_counts[carrier_status] = carrier_status_counts.get(carrier_status, 0) + 1
        direct_status = ((row.get("directCarrier") or {}).get("status") or "unavailable")
        direct_carrier_counts[direct_status] = direct_carrier_counts.get(direct_status, 0) + 1

    oldest = rows[0]["lastMovementAt"] if rows else None
    newest = rows[-1]["lastMovementAt"] if rows else None
    max_hours = round(max((row["hoursWithoutMovement"] for row in rows), default=0.0), 1)
    avg_hours = round(sum(row["hoursWithoutMovement"] for row in rows) / len(rows), 1) if rows else 0.0
    payload = {
        "generatedAt": now_dt.isoformat(),
        "source": source_meta,
        "thresholds": {
            "warnHours": thresholds.warn_hours,
            "alertHours": thresholds.alert_hours,
            "criticalHours": thresholds.critical_hours,
        },
        "summary": {
            "total": len(rows),
            "warnCount": by_severity["warn"],
            "alertCount": by_severity["alert"],
            "criticalCount": by_severity["critical"],
            "zeroValueCount": len([row for row in rows if row["isZeroValue"]]),
            "missingTiandeIdCount": len([row for row in rows if row["missingTiandeId"]]),
            "missingSponsorIdCount": len([row for row in rows if row["missingSponsorId"]]),
            "avgHoursWithoutMovement": avg_hours,
            "maxHoursWithoutMovement": max_hours,
            "oldestMovementAt": oldest,
            "newestMovementAt": newest,
            "byCountry": dict(sorted(by_country.items())),
            "currencyTotals": dict(sorted(currencies.items())),
            "carrierCheck": dict(sorted(carrier_status_counts.items())),
            "directCarrierCheck": dict(sorted(direct_carrier_counts.items())),
            "needsCarrierEscalationCount": len([row for row in rows if is_carrier_escalation_candidate(row)]),
        },
        "top": rows[:12],
        "rows": rows,
    }
    return payload


def format_hours(hours: float) -> str:
    days = hours / 24
    if days >= 2:
        return f"{days:.1f} dne"
    if hours >= 24:
        return f"{days:.1f} dne"
    return f"{hours:.0f} h"


def format_currency_totals(currency_totals: dict[str, float]) -> str:
    parts = []
    for currency, amount in currency_totals.items():
        if currency == "unknown":
            parts.append(f"{amount:.2f}")
            continue
        if currency == "CZK":
            display_amount = f"{amount:,.0f}".replace(",", " ")
        else:
            display_amount = f"{amount:,.2f}".replace(",", " ").replace(".", ",")
        parts.append(f"{display_amount} {currency}".strip())
    return " + ".join(parts) if parts else "0"


def format_telegram_text(payload: dict[str, Any], delta: dict[str, Any] | None = None) -> str:
    summary = payload["summary"]
    by_country = summary.get("byCountry") or {}
    country_bits = []
    if by_country.get("CZ"):
        country_bits.append(f"CZ {by_country['CZ']}")
    if by_country.get("SK"):
        country_bits.append(f"SK {by_country['SK']}")
    if by_country.get("unknown"):
        country_bits.append(f"Neurčeno {by_country['unknown']}")

    lines = [
        "⚠️ Zásilky 48h+ bez pohybu",
        f"• Celkem: {summary['total']}",
        f"• 72h+: {summary['alertCount'] + summary['criticalCount']} | 120h+: {summary['criticalCount']}",
        f"• Trhy: {', '.join(country_bits) if country_bits else '–'}",
        f"• Hodnota: {format_currency_totals(summary.get('currencyTotals') or {})}",
    ]
    carrier_check = summary.get("carrierCheck") or {}
    if carrier_check:
        lines.append(
            "• Dopravce: "
            + ", ".join(
                part
                for part in [
                    f"převzato {carrier_check.get('picked_up', 0)}" if carrier_check.get('picked_up') else "",
                    f"doručeno {carrier_check.get('delivered', 0)}" if carrier_check.get('delivered') else "",
                    f"k vyzvednutí {carrier_check.get('ready_to_pickup', 0)}" if carrier_check.get('ready_to_pickup') else "",
                    f"k prověření {summary.get('needsCarrierEscalationCount', 0)}",
                ]
                if part
            )
        )
    direct_carrier = summary.get("directCarrierCheck") or {}
    if direct_carrier:
        lines.append(
            "• Přímý tracking: "
            + ", ".join(
                part
                for part in [
                    f"pohyb {direct_carrier.get('moving', 0)}" if direct_carrier.get("moving") else "",
                    f"výdejní místo {direct_carrier.get('pickup_point', 0)}" if direct_carrier.get("pickup_point") else "",
                    f"doručeno {direct_carrier.get('delivered', 0)}" if direct_carrier.get("delivered") else "",
                    f"jen založeno {direct_carrier.get('info_received', 0)}" if direct_carrier.get("info_received") else "",
                ]
                if part
            )
        )
    if summary.get("missingTiandeIdCount") or summary.get("missingSponsorIdCount"):
        lines.append(
            f"• Bez TianDe ID: {summary['missingTiandeIdCount']} | bez sponsora: {summary['missingSponsorIdCount']}"
        )
    if summary.get("zeroValueCount"):
        lines.append(f"• Nulová hodnota: {summary['zeroValueCount']}")
    if delta:
        delta_bits = []
        if delta["new"]:
            delta_bits.append(f"nové {len(delta['new'])}")
        if delta["escalated"]:
            delta_bits.append(f"zhoršené {len(delta['escalated'])}")
        if delta["resolved"]:
            delta_bits.append(f"vyřešené {len(delta['resolved'])}")
        if delta_bits:
            lines.append(f"• Změna: {', '.join(delta_bits)}")

    lines.append("")
    lines.append("Prioritní případy:")
    sorted_rows = sorted(
        filter_open_rows(payload.get("rows") or []),
        key=lambda row: (
            0 if is_carrier_escalation_candidate(row) else 1,
            -row["severityRank"],
            -row["hoursWithoutMovement"],
        ),
    )
    for row in sorted_rows[:5]:
        price = row["price"]["display"] or "0"
        carrier_status = (
            ((row.get("directCarrier") or {}).get("statusLabel"))
            or (((row.get("wpj") or {}).get("carrierCheck") or {}).get("statusLabel"))
            or "bez carrier kontroly"
        )
        lines.append(
            f"• {row['orderCode']} · {format_hours(row['hoursWithoutMovement'])} · {carrier_status} · {price}"
        )
    return "\n".join(lines)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"orders": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"orders": {}}


def build_delta(rows: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, list[str]]:
    previous_orders = state.get("orders") or {}
    current_orders = {row["orderCode"]: row for row in rows if row.get("orderCode")}
    new_codes = sorted(code for code in current_orders if code not in previous_orders)
    resolved_codes = sorted(code for code in previous_orders if code not in current_orders)
    escalated_codes = sorted(
        code
        for code, row in current_orders.items()
        if code in previous_orders
        and SEVERITY_RANK.get(row["severity"], 0) > SEVERITY_RANK.get(previous_orders[code].get("severity"), 0)
    )
    return {"new": new_codes, "resolved": resolved_codes, "escalated": escalated_codes}


def update_state(path: Path, payload: dict[str, Any], delta: dict[str, list[str]]) -> None:
    orders = {
        row["orderCode"]: {
            "severity": row["severity"],
            "lastMovementAt": row["lastMovementAt"],
            "hoursWithoutMovement": row["hoursWithoutMovement"],
        }
        for row in payload.get("rows") or []
        if row.get("orderCode")
    }
    state_payload = {
        "updatedAt": payload["generatedAt"],
        "sourceKind": (payload.get("source") or {}).get("kind"),
        "source": payload["source"],
        "summary": payload["summary"],
        "delta": delta,
        "orders": orders,
    }
    write_json(path, state_payload)


def should_notify(payload: dict[str, Any], delta: dict[str, list[str]], previous_state: dict[str, Any]) -> bool:
    if payload["summary"]["total"] == 0:
        return bool((previous_state.get("orders") or {}))
    return bool(delta["new"] or delta["escalated"] or delta["resolved"] or not (previous_state.get("orders") or {}))


def is_carrier_escalation_candidate(row: dict[str, Any]) -> bool:
    return bool(row.get("carrierEscalationCandidate", True))


def filter_open_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if is_carrier_escalation_candidate(row)]


def load_refresh_data_module():
    global REFRESH_DATA_MODULE
    if REFRESH_DATA_MODULE is not None:
        return REFRESH_DATA_MODULE
    scripts_dir = ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    module_path = scripts_dir / "refresh_data.py"
    spec = importlib.util.spec_from_file_location("reporting_refresh_data", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load refresh_data.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.load_env_file(module.ENV_FILE)
    REFRESH_DATA_MODULE = module
    return module


def extract_first_match(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return (match.group(1) or "").strip() or None


def extract_tracking_url(text: str) -> str | None:
    return extract_first_match(r'href="([^"]+)"', text) or extract_first_match(r'(https?://\S+)', text)


def summarize_order_history(order: dict[str, Any]) -> dict[str, Any]:
    history = order.get("history") or []
    statuses: list[str] = []
    tracking_number = None
    package_number = None
    fourpx_consignment_id = None
    tracking_url = None
    raw_hits = []
    latest_history_at = None
    latest_carrier_signal_at = None
    for item in history:
        comment = str(item.get("comment") or "").strip()
        item_dt = parse_iso_dt(item.get("date"))
        if item_dt and (latest_history_at is None or item_dt > latest_history_at):
            latest_history_at = item_dt
        if not comment:
            continue
        if "[4PX]" in comment or "tracking." in comment or "gls-group" in comment or "dopravce" in comment or "dopravca" in comment:
            raw_hits.append(comment)
            if item_dt and (latest_carrier_signal_at is None or item_dt > latest_carrier_signal_at):
                latest_carrier_signal_at = item_dt
        if fourpx_consignment_id is None:
            fourpx_consignment_id = extract_first_match(r'\[4PX\]\s+Objednávka byla vytvořena;\s+ID:\s*([^\s]+)', comment)
        if tracking_number is None:
            tracking_number = extract_first_match(r'tracking:\s*([A-Za-z0-9]+)', comment)
        if package_number is None:
            package_number = extract_first_match(r'\[4PX\]\s+Číslo balíku:\s*([A-Za-z0-9]+)', comment)
        status = extract_first_match(r'\[4PX\]\s+Změna stavu:\s*([a-z_]+)', comment)
        if status:
            statuses.append(status.lower())
        if tracking_url is None:
            tracking_url = extract_tracking_url(comment)

    status = "no_signal"
    status_label = "bez carrier signálu"
    if "delivered" in statuses:
        status = "delivered"
        status_label = "doručeno"
    elif "ready_to_pickup" in statuses:
        status = "ready_to_pickup"
        status_label = "připraveno k vyzvednutí"
    elif "carrier_picked_up" in statuses:
        status = "picked_up"
        status_label = "převzato dopravcem"
    elif tracking_number or package_number or tracking_url:
        status = "tracking_created"
        status_label = "máme tracking, ale ne pickup"

    return {
        "status": status,
        "statusLabel": status_label,
        "trackingNumber4px": tracking_number,
        "packageNumberCarrier": package_number,
        "trackingUrl": tracking_url,
        "fourpxConsignmentId": fourpx_consignment_id,
        "historySignals": statuses,
        "historyHits": raw_hits[:8],
        "latestHistoryAt": latest_history_at.isoformat() if latest_history_at else None,
        "latestCarrierSignalAt": latest_carrier_signal_at.isoformat() if latest_carrier_signal_at else None,
    }


def enrich_rows_with_wpj(rows: list[dict[str, Any]], now_dt: datetime) -> list[dict[str, Any]]:
    if not rows:
        return rows
    rd = load_refresh_data_module()
    if not rd.wpj_endpoint() or not rd.SETTINGS.wpj_access_token:
        for row in rows:
            row["wpj"] = {
                "carrierCheck": {
                    "status": "unchecked",
                    "statusLabel": "WPJ není připojené",
                }
            }
        return rows

    created_values = [datetime.fromisoformat(row["createdAt"]) for row in rows if row.get("createdAt")]
    if created_values:
        start_dt = min(created_values) - timedelta(days=1)
    else:
        start_dt = now_dt - timedelta(days=35)
    end_dt = now_dt + timedelta(days=1)
    orders = rd.fetch_wpj_orders(
        rd.wpj_endpoint(),
        rd.SETTINGS.wpj_access_token,
        start_dt,
        end_dt,
        limit=1000,
        detailed=False,
    )
    orders_by_code = {str(order.get("code")): order for order in orders if order.get("code") is not None}
    for row in rows:
        code = str(row.get("orderCode") or "")
        order = orders_by_code.get(code)
        if not order:
            row["wpj"] = {
                "carrierCheck": {
                    "status": "missing_order",
                    "statusLabel": "objednávka ve WPJ nenalezena",
                }
            }
            continue
        row["wpj"] = {
            "status": order.get("status") or {},
            "dateCreated": order.get("dateCreated"),
            "carrierCheck": summarize_order_history(order),
        }
    return rows


def send_telegram_text(token: str, target: str, text: str) -> int | None:
    body = urllib.parse.urlencode(
        {
            "chat_id": target,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram send failed for {target}: {payload}")
    return payload.get("result", {}).get("message_id")


def notify(payload: dict[str, Any], text: str, dry_run: bool) -> list[dict[str, Any]]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    raw_targets = os.environ.get("STUCK_SHIPMENTS_TELEGRAM_TARGETS", "").strip() or os.environ.get("MORNING_REPORT_TARGET", "").strip()
    targets = [item.strip() for item in raw_targets.split(",") if item.strip()]
    if not token or not targets:
        return []
    results = []
    for target in targets:
        if dry_run:
            results.append({"target": target, "status": "dry_run", "messageId": None})
            continue
        message_id = send_telegram_text(token, target, text)
        results.append({"target": target, "status": "sent", "messageId": message_id})
    return results


def latest_csv_path(input_dir: Path) -> Path:
    candidates = sorted(input_dir.glob("*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No CSV files found in {input_dir}")
    return candidates[0]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        if str(value).endswith("Z"):
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(PRAGUE_TZ)
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=PRAGUE_TZ)
        return parsed.astimezone(PRAGUE_TZ)
    except Exception:
        return None


def normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def clean_html_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_pickup_delivery_name(value: str) -> bool:
    normalized = normalize_text(value)
    if not normalized:
        return False
    if any(keyword in normalized for keyword in PICKUP_POINT_KEYWORDS):
        return True
    if any(keyword in normalized for keyword in PICKUP_PROVIDER_KEYWORDS):
        return not any(hint in normalized for hint in NON_PICKUP_HINTS)
    return False


def build_csv_source_meta(path: Path) -> dict[str, Any]:
    return {
        "kind": "csv",
        "path": str(path),
        "fileName": path.name,
        "sha1": hashlib.sha1(path.read_bytes()).hexdigest(),
        "modifiedAt": datetime.fromtimestamp(path.stat().st_mtime, tz=PRAGUE_TZ).isoformat(),
    }


def carrier_status_rank(status: str) -> int:
    return {
        "unchecked": 0,
        "missing_order": 0,
        "no_signal": 1,
        "tracking_created": 2,
        "picked_up": 3,
        "ready_to_pickup": 4,
        "delivered": 5,
    }.get(status, 0)


def direct_carrier_status_rank(status: str) -> int:
    return {
        "unavailable": 0,
        "unsupported": 0,
        "fetch_error": 0,
        "anti_bot": 0,
        "no_result": 1,
        "info_received": 2,
        "moving": 3,
        "pickup_point": 4,
        "delivered": 5,
    }.get(status, 0)


def carrier_candidate_from_wpj_status(status: str) -> bool:
    return status in {"missing_order", "no_signal", "tracking_created", "unchecked"}


def build_packeta_tracking_url(carrier_check: dict[str, Any], country: str = "CZ") -> str | None:
    tracking_url = clean_string(carrier_check.get("trackingUrl"))
    if "tracking.packeta.com" in tracking_url:
        return tracking_url
    package_number = clean_string(carrier_check.get("packageNumberCarrier")).upper()
    if package_number.startswith("Z"):
        locale = "sk" if normalize_text(country) == "sk" else "cs"
        return f"{PACKETA_TRACKING_BASE}/{locale}/{urllib.parse.quote(package_number)}"
    return None


def build_gls_tracking_url(carrier_check: dict[str, Any]) -> str | None:
    tracking_url = clean_string(carrier_check.get("trackingUrl"))
    if "gls-group.eu" in tracking_url:
        return tracking_url
    package_number = clean_string(carrier_check.get("packageNumberCarrier"))
    if re.fullmatch(r"\d{11}", package_number):
        return f"https://gls-group.eu/SK/sk/sledovanie-zasielok.html?match={urllib.parse.quote(package_number)}"
    return None


def build_dpd_tracking_url(carrier_check: dict[str, Any], country: str = "CZ") -> str | None:
    tracking_url = clean_string(carrier_check.get("trackingUrl"))
    if "dpdgroup.com" in tracking_url:
        return tracking_url
    package_number = clean_string(carrier_check.get("packageNumberCarrier"))
    if re.fullmatch(r"\d{14}", package_number):
        locale = "sk" if normalize_text(country) == "sk" else "cz"
        lang = "sk" if normalize_text(country) == "sk" else "cz"
        return f"https://www.dpdgroup.com/{locale}/mydpd/my-parcels/track?lang={lang}&parcelNumber={urllib.parse.quote(package_number)}"
    return None


def build_ceska_posta_tracking_url(carrier_check: dict[str, Any]) -> str | None:
    tracking_url = clean_string(carrier_check.get("trackingUrl"))
    if "postaonline.cz/trackandtrace" in tracking_url:
        return tracking_url
    package_number = clean_string(carrier_check.get("packageNumberCarrier"))
    if package_number.startswith(("NB", "DR")):
        return f"{CESKA_POSTA_TRACKING_BASE}{urllib.parse.quote(package_number)}"
    return None


def detect_direct_carrier_provider(carrier_check: dict[str, Any]) -> str | None:
    tracking_url = clean_string(carrier_check.get("trackingUrl")).lower()
    package_number = clean_string(carrier_check.get("packageNumberCarrier")).upper()
    if "postaonline.cz/trackandtrace" in tracking_url or package_number.startswith(("NB", "DR")):
        return "ceska_posta"
    if "tracking.packeta.com" in tracking_url or package_number.startswith("Z"):
        return "packeta"
    if "gls-group.eu" in tracking_url:
        return "gls"
    if re.fullmatch(r"\d{11}", package_number):
        return "gls"
    if "dpdgroup.com" in tracking_url:
        return "dpd"
    if re.fullmatch(r"\d{14}", package_number):
        return "dpd"
    return None


def fetch_url_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": DIRECT_CARRIER_USER_AGENT})
    with urllib.request.urlopen(req, timeout=DIRECT_CARRIER_TIMEOUT) as response:
        return response.read().decode("utf-8", "ignore")


def fetch_rendered_dom(url: str, *, virtual_time_budget_ms: int = 10000) -> str:
    if not CHROME_BIN.exists():
        raise RuntimeError("Google Chrome headless není dostupný")
    completed = subprocess.run(
        [
            str(CHROME_BIN),
            "--headless=new",
            "--disable-gpu",
            f"--virtual-time-budget={virtual_time_budget_ms}",
            "--dump-dom",
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=RENDERED_DOM_TIMEOUT,
    )
    return completed.stdout


def parse_ceska_posta_date(value: str) -> datetime | None:
    raw = clean_string(value)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d.%m.%Y").replace(tzinfo=PRAGUE_TZ)
    except ValueError:
        return None


def parse_packeta_datetime(value: str) -> datetime | None:
    raw = clean_string(value)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=PRAGUE_TZ)
    except ValueError:
        return None


def parse_fourpx_datetime(value: Any) -> datetime | None:
    raw = clean_string(value)
    if not raw:
        return None
    candidates = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    for fmt in candidates:
        try:
            parsed = datetime.strptime(raw, fmt)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=PRAGUE_TZ)
            return parsed.astimezone(PRAGUE_TZ)
        except ValueError:
            continue
    return parse_iso_dt(raw)


def build_direct_carrier_result(
    *,
    provider: str,
    status: str,
    status_label: str,
    tracking_url: str | None,
    package_number: str | None,
    latest_movement_at: datetime | None = None,
    current_state: str | None = None,
    latest_event: dict[str, Any] | None = None,
    hours_without_movement: float | None = None,
) -> dict[str, Any]:
    serialized_latest_event = None
    if latest_event:
        serialized_latest_event = dict(latest_event)
        latest_event_dt = serialized_latest_event.get("dt")
        if isinstance(latest_event_dt, datetime):
            serialized_latest_event["dt"] = latest_event_dt.isoformat()
    return {
        "provider": provider,
        "status": status,
        "statusLabel": status_label,
        "trackingUrl": tracking_url,
        "packageNumber": package_number,
        "latestMovementAt": latest_movement_at.isoformat() if latest_movement_at else None,
        "hoursWithoutMovement": hours_without_movement,
        "currentState": current_state,
        "latestEvent": serialized_latest_event,
    }


def parse_fourpx_tracking_payload(payload: dict[str, Any], tracking_number: str, now_dt: datetime) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for raw_item in payload.get("trackingList") or []:
        item = raw_item if isinstance(raw_item, dict) else {}
        dt = parse_fourpx_datetime(
            item.get("occurDatetime")
            or item.get("occurTime")
            or item.get("scanTime")
            or item.get("eventTime")
        )
        event = clean_string(
            item.get("trackingContent")
            or item.get("tracking_content")
            or item.get("eventContent")
            or item.get("event")
            or item.get("desc")
        )
        code = clean_string(item.get("businessLinkCode") or item.get("business_link_code"))
        place = clean_string(
            item.get("trackingAddress")
            or item.get("tracking_address")
            or item.get("occurAddress")
            or item.get("address")
            or item.get("location")
        )
        if not (dt or event or code or place):
            continue
        events.append(
            {
                "dateText": clean_string(item.get("occurDatetime") or item.get("occurTime") or item.get("scanTime")),
                "event": event or code,
                "code": code or None,
                "place": place or None,
                "dt": dt,
            }
        )

    latest_event = None
    if events:
        events_with_dt = [item for item in events if item.get("dt")]
        if events_with_dt:
            latest_event = max(events_with_dt, key=lambda item: item["dt"])
        else:
            latest_event = events[0]

    latest_movement_at = latest_event.get("dt") if latest_event else None
    hours_without_movement = (
        round((now_dt - latest_movement_at).total_seconds() / 3600, 1)
        if latest_movement_at
        else None
    )
    current_state = clean_string((latest_event or {}).get("event"))
    normalized_signal = normalize_text(
        " ".join(
            part
            for part in [
                current_state,
                clean_string((latest_event or {}).get("code")),
                clean_string((latest_event or {}).get("place")),
            ]
            if part
        )
    )

    if any(token in normalized_signal for token in ("delivered", "signed", "doručen", "dorucen")):
        status = "delivered"
        status_label = "4PX potvrzuje doručení"
    elif any(
        token in normalized_signal
        for token in ("pickup", "pick up", "vyzved", "parcelshop", "locker", "z-box", "zbox", "balikovna")
    ):
        status = "pickup_point"
        status_label = "4PX potvrzuje výdejní místo"
    elif any(
        token in normalized_signal
        for token in ("packing", "information received", "shipment created", "manifest", "operating point")
    ):
        status = "info_received"
        status_label = "4PX má jen založení zásilky"
    elif latest_event:
        status = "moving"
        status_label = "4PX potvrzuje pohyb"
    else:
        status = "no_result"
        status_label = "4PX API bez výsledku"

    return build_direct_carrier_result(
        provider="fourpx",
        status=status,
        status_label=status_label,
        tracking_url=None,
        package_number=tracking_number,
        latest_movement_at=latest_movement_at,
        hours_without_movement=hours_without_movement,
        current_state=current_state or None,
        latest_event=latest_event,
    )


def parse_ceska_posta_tracking_page(page_html: str, tracking_url: str, package_number: str, now_dt: datetime) -> dict[str, Any]:
    normalized_html = re.sub(r"\s+", " ", page_html)
    if "k zásilce nebyly nalezeny žádné informace" in normalize_text(normalized_html):
        return build_direct_carrier_result(
            provider="ceska_posta",
            status="no_result",
            status_label="u České pošty bez výsledku",
            tracking_url=tracking_url,
            package_number=package_number,
        )

    active_tooltips = re.findall(
        r'<div[^>]+data-tooltip="[^"]+"[^>]*>(.*?)</div>\s*<img[^>]+progress-active[^>]*>',
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    current_state = clean_html_text(active_tooltips[-1]) if active_tooltips else None

    events: list[dict[str, Any]] = []
    for date_html, event_html, _zip_html, place_html in re.findall(
        r"<tr>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>",
        page_html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        date_text = clean_html_text(date_html)
        if not re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}$", date_text):
            continue
        event_text = clean_html_text(event_html).rstrip(".")
        place_text = clean_html_text(place_html)
        event_dt = parse_ceska_posta_date(date_text)
        if not event_dt or not event_text:
            continue
        events.append(
            {
                "dateText": date_text,
                "event": event_text,
                "place": place_text,
                "dt": event_dt,
            }
        )

    latest_event = events[0] if events else None
    latest_movement_at = latest_event["dt"] if latest_event else None
    hours_without_movement = (
        round((now_dt - latest_movement_at).total_seconds() / 3600, 1)
        if latest_movement_at
        else None
    )
    normalized_state = normalize_text(current_state)
    normalized_event = normalize_text((latest_event or {}).get("event"))

    if "dodána" in normalized_state or "dodání zásilky provedeno" in normalized_event:
        status = "delivered"
        status_label = "doručeno dopravcem"
    elif "uložena na poště" in normalized_state or "balíkovně" in normalized_state or "uložení zásilky" in normalized_event:
        status = "pickup_point"
        status_label = "na výdejním místě"
    elif not latest_event:
        status = "no_result"
        status_label = "u České pošty bez výsledku"
    elif "obdrženy údaje k zásilce" in normalized_event:
        status = "info_received"
        status_label = "u dopravce jen založeno"
    else:
        status = "moving"
        status_label = "u dopravce se hýbe"

    return build_direct_carrier_result(
        provider="ceska_posta",
        status=status,
        status_label=status_label,
        tracking_url=tracking_url,
        package_number=package_number,
        latest_movement_at=latest_movement_at,
        hours_without_movement=hours_without_movement,
        current_state=current_state,
        latest_event=latest_event,
    )


def parse_packeta_tracking_page(page_html: str, tracking_url: str, package_number: str, now_dt: datetime) -> dict[str, Any]:
    heading = clean_html_text(extract_first_match(r'<h2[^>]*data-testid="heading"[^>]*>(.*?)</h2>', page_html))
    latest_event = None
    for dt_raw, text_raw in re.findall(r'"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})","([^"]+)"', page_html):
        latest_event = {
            "dateText": dt_raw,
            "event": clean_html_text(text_raw),
            "dt": parse_packeta_datetime(dt_raw),
        }
        break
    latest_movement_at = latest_event.get("dt") if latest_event else None
    hours_without_movement = (
        round((now_dt - latest_movement_at).total_seconds() / 3600, 1)
        if latest_movement_at
        else None
    )
    normalized_heading = normalize_text(heading)
    normalized_event = normalize_text((latest_event or {}).get("event"))

    if "doručen" in normalized_heading or "vydán" in normalized_heading or "doručen" in normalized_event:
        status = "delivered"
        status_label = "doručeno dopravcem"
    elif (
        "vyzved" in normalized_heading
        or "výdej" in normalized_heading
        or "z-box" in normalized_heading
        or "vyzved" in normalized_event
    ):
        status = "pickup_point"
        status_label = "na výdejním místě"
    elif (
        "čekáme, až nám ji odesílatel předá" in normalized_event
        or "o vaší zásilce už víme" in normalized_event
    ):
        status = "info_received"
        status_label = "u dopravce jen založeno"
    elif latest_event or heading:
        status = "moving"
        status_label = "u dopravce se hýbe"
    else:
        status = "no_result"
        status_label = "u Zásilkovny bez výsledku"

    return build_direct_carrier_result(
        provider="packeta",
        status=status,
        status_label=status_label,
        tracking_url=tracking_url,
        package_number=package_number,
        latest_movement_at=latest_movement_at,
        hours_without_movement=hours_without_movement,
        current_state=heading or None,
        latest_event=latest_event,
    )


def parse_gls_tracking_page(page_html: str, tracking_url: str, package_number: str, now_dt: datetime) -> dict[str, Any]:
    current_state = clean_html_text(extract_first_match(r'id="witt002_details_status_value_current"[^>]*>(.*?)</div>', page_html))
    summary_text = clean_html_text(extract_first_match(r'id="witt002_details_eventtextdescription"[^>]*>(.*?)</strong>', page_html))
    normalized_state = normalize_text(current_state)
    normalized_summary = normalize_text(summary_text)

    if "doručen" in normalized_state or "doručen" in normalized_summary:
        status = "delivered"
        status_label = "doručeno dopravcem"
    elif "parcelshop" in normalized_state or "pickup" in normalized_state:
        status = "pickup_point"
        status_label = "na výdejním místě"
    elif "prijaté údaje" in normalized_state:
        status = "info_received"
        status_label = "u dopravce jen založeno"
    elif current_state or summary_text:
        status = "moving"
        status_label = "u dopravce se hýbe"
    else:
        status = "no_result"
        status_label = "u GLS bez výsledku"

    latest_event = {"event": summary_text} if summary_text else None
    return build_direct_carrier_result(
        provider="gls",
        status=status,
        status_label=status_label,
        tracking_url=tracking_url,
        package_number=package_number,
        latest_movement_at=None,
        hours_without_movement=None,
        current_state=current_state or None,
        latest_event=latest_event,
    )


def parse_dpd_geoapi_events(data: dict[str, Any], package_number: str, now_dt: datetime) -> dict[str, Any]:
    """Parse DPD GeoAPI v2 /parcels/{parcelIdent}/tracking response."""
    raw_events = data.get("parcelEvents") or []
    events: list[dict[str, Any]] = []
    for ev in raw_events:
        ev_status = ev.get("status") or {}
        code = clean_string(ev_status.get("statusCode"))
        desc = clean_string(ev_status.get("description"))
        additional = clean_string(ev.get("additionalInfo"))
        dt = parse_iso_dt(ev.get("createdAt"))
        events.append({"dt": dt, "code": code, "desc": desc, "additional": additional})

    tracking_url = build_dpd_tracking_url({"packageNumberCarrier": package_number})

    if not events:
        return build_direct_carrier_result(
            provider="dpd",
            status="no_result",
            status_label="DPD GeoAPI: zásilka bez eventů",
            tracking_url=tracking_url,
            package_number=package_number,
        )

    # Sort newest first (None dt last)
    events.sort(key=lambda e: e["dt"] or datetime.min.replace(tzinfo=PRAGUE_TZ), reverse=True)
    latest = events[0]
    latest_movement_at = latest["dt"]
    hours_without_movement = (
        round((now_dt - latest_movement_at).total_seconds() / 3600, 1)
        if latest_movement_at
        else None
    )

    # Status mapping — check all events for terminal states
    all_desc = " ".join(normalize_text(e["desc"] + " " + e["additional"]) for e in events)
    latest_desc = normalize_text(latest["desc"] + " " + latest["additional"])

    delivered_kw = ("doručeno", "doručena", "vydán", "delivered", "podpis příjemce", "signing")
    pickup_kw = ("výdejní", "výdejni", "parcelshop", "připravena k vyzvednutí", "připraveno k vyzvednutí", "z-box", "pickup point", "ready for pickup")
    info_kw = ("informace o zásilce", "zásilka byla vytvořena", "shipment created", "information received", "shipment info")

    if any(kw in all_desc for kw in delivered_kw):
        status = "delivered"
        status_label = "DPD GeoAPI: doručeno"
    elif any(kw in all_desc for kw in pickup_kw):
        status = "pickup_point"
        status_label = "DPD GeoAPI: na výdejním místě"
    elif any(kw in latest_desc for kw in info_kw) and len(events) == 1:
        status = "info_received"
        status_label = "DPD GeoAPI: jen info o zásilce"
    else:
        status = "moving"
        status_label = "DPD GeoAPI: pohyb"

    return build_direct_carrier_result(
        provider="dpd",
        status=status,
        status_label=status_label,
        tracking_url=tracking_url,
        package_number=package_number,
        latest_movement_at=latest_movement_at,
        hours_without_movement=hours_without_movement,
        current_state=latest["desc"] or None,
        latest_event={
            "dt": latest["dt"],
            "code": latest["code"],
            "event": latest["desc"],
            "additional": latest["additional"],
        },
    )


def fetch_dpd_geoapi_tracking(package_number: str, api_key: str, now_dt: datetime) -> dict[str, Any]:
    """Call DPD GeoAPI v2 tracking for a single 14-digit parcel number."""
    cache_key = f"dpd_geoapi:{package_number}"
    if cache_key in DPD_GEOAPI_CACHE:
        return dict(DPD_GEOAPI_CACHE[cache_key])

    url = f"{DPD_GEOAPI_BASE}/{urllib.parse.quote(package_number)}/tracking"
    req = urllib.request.Request(
        url,
        headers={"x-api-key": api_key, "Accept": "application/json", "User-Agent": DIRECT_CARRIER_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=DIRECT_CARRIER_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        result = parse_dpd_geoapi_events(data, package_number, now_dt)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "ignore")
        if exc.code == 404:
            result = build_direct_carrier_result(
                provider="dpd",
                status="no_result",
                status_label="DPD GeoAPI: zásilka nenalezena (404)",
                tracking_url=build_dpd_tracking_url({"packageNumberCarrier": package_number}),
                package_number=package_number,
            )
        elif exc.code == 401:
            raise RuntimeError(f"DPD GeoAPI: neplatný API klíč (401) – {body}") from exc
        else:
            raise RuntimeError(f"DPD GeoAPI HTTP {exc.code}: {body}") from exc

    DPD_GEOAPI_CACHE[cache_key] = dict(result)
    return result


def parse_dpd_tracking_page(page_html: str, tracking_url: str, package_number: str) -> dict[str, Any]:
    normalized = normalize_text(page_html)
    if "provádění bezpečnostního ověření" in normalized or "cloudflare" in normalized:
        return build_direct_carrier_result(
            provider="dpd",
            status="anti_bot",
            status_label="DPD blokuje automatický check",
            tracking_url=tracking_url,
            package_number=package_number,
        )

    status_labels = re.findall(r'<div[^>]*>\s*(Balík předán DPD|V přepravě|V doručovacím depu|Balík k doručení|Doručeno)\s*</div>', page_html)
    current_state = status_labels[-1] if status_labels else None
    normalized_state = normalize_text(current_state)
    if "doručeno" in normalized_state:
        status = "delivered"
        status_label = "doručeno dopravcem"
    elif current_state:
        status = "moving"
        status_label = "u dopravce se hýbe"
    else:
        status = "no_result"
        status_label = "u DPD bez výsledku"

    return build_direct_carrier_result(
        provider="dpd",
        status=status,
        status_label=status_label,
        tracking_url=tracking_url,
        package_number=package_number,
        current_state=current_state,
        latest_event={"event": current_state} if current_state else None,
    )


def resolve_fourpx_tracking_check(row: dict[str, Any], now_dt: datetime) -> dict[str, Any]:
    carrier_check = ((row.get("wpj") or {}).get("carrierCheck") or {})
    tracking_number = clean_string(carrier_check.get("trackingNumber4px"))
    if not tracking_number:
        return {
            **build_default_direct_carrier(),
            "provider": "fourpx",
            "status": "unavailable",
            "statusLabel": "chybí 4PX tracking číslo",
        }

    market = "SK" if clean_string(row.get("country")).upper() == "SK" else "CZ"
    cache_key = f"{market}:{tracking_number}"
    if cache_key in FOURPX_TRACKING_CACHE:
        return dict(FOURPX_TRACKING_CACHE[cache_key])

    try:
        rd = load_refresh_data_module()
        app_key, app_secret = rd.SETTINGS.fourpx_credentials(market)
        payload = rd.FOURPX_ADAPTER.call(
            "tr.order.tracking.get",
            {"deliveryOrderNo": tracking_number},
            app_key,
            app_secret,
        )
        direct = parse_fourpx_tracking_payload(payload or {}, tracking_number, now_dt)
    except Exception as exc:
        message = str(exc)
        if "未查询到相关物流轨迹" in message:
            direct = {
                **build_default_direct_carrier(),
                "provider": "fourpx",
                "status": "no_result",
                "statusLabel": "4PX API bez výsledku",
                "packageNumber": tracking_number,
            }
        else:
            direct = {
                **build_default_direct_carrier(),
                "provider": "fourpx",
                "status": "fetch_error",
                "statusLabel": "chyba 4PX tracking API",
                "packageNumber": tracking_number,
                "error": message,
            }
    FOURPX_TRACKING_CACHE[cache_key] = dict(direct)
    return direct


def resolve_direct_carrier_check(row: dict[str, Any], now_dt: datetime) -> dict[str, Any]:
    carrier_check = ((row.get("wpj") or {}).get("carrierCheck") or {})
    provider = detect_direct_carrier_provider(carrier_check)
    if not provider:
        direct = build_default_direct_carrier()
        direct["status"] = "unsupported"
        direct["statusLabel"] = "dopravce bez přímé podpory"
        return direct

    country = clean_string(row.get("country") or "CZ")
    if provider == "ceska_posta":
        tracking_url = build_ceska_posta_tracking_url(carrier_check)
    elif provider == "packeta":
        tracking_url = build_packeta_tracking_url(carrier_check, country=country)
    elif provider == "gls":
        tracking_url = build_gls_tracking_url(carrier_check)
    elif provider == "dpd":
        tracking_url = build_dpd_tracking_url(carrier_check, country=country)
    else:
        tracking_url = clean_string(carrier_check.get("trackingUrl")) or None
    package_number = clean_string(carrier_check.get("packageNumberCarrier"))
    if not tracking_url:
        direct = build_default_direct_carrier()
        direct["provider"] = provider
        direct["status"] = "unavailable"
        direct["statusLabel"] = "chybí tracking URL"
        direct["packageNumber"] = package_number or None
        return direct

    cache_key = f"{provider}:{tracking_url}"
    if cache_key in DIRECT_CARRIER_CACHE:
        return dict(DIRECT_CARRIER_CACHE[cache_key])

    try:
        if provider == "ceska_posta":
            page_html = fetch_url_text(tracking_url)
            direct = parse_ceska_posta_tracking_page(page_html, tracking_url, package_number, now_dt)
        elif provider == "packeta":
            page_html = fetch_url_text(tracking_url)
            direct = parse_packeta_tracking_page(page_html, tracking_url, package_number, now_dt)
        elif provider == "gls":
            page_html = fetch_rendered_dom(tracking_url, virtual_time_budget_ms=10000)
            direct = parse_gls_tracking_page(page_html, tracking_url, package_number, now_dt)
        elif provider == "dpd":
            # 1. Try DPD GeoAPI first (no Chrome dependency, no anti-bot)
            geoapi_key = ""
            try:
                rd = load_refresh_data_module()
                geoapi_key = getattr(rd.SETTINGS, "dpd_geoapi_key", "") or ""
            except Exception:
                pass

            direct = None
            if geoapi_key and re.fullmatch(r"\d{14}", package_number):
                try:
                    direct = fetch_dpd_geoapi_tracking(package_number, geoapi_key, now_dt)
                except Exception as geoapi_exc:
                    direct = {
                        **build_default_direct_carrier(),
                        "provider": "dpd",
                        "status": "fetch_error",
                        "statusLabel": f"DPD GeoAPI chyba: {geoapi_exc}",
                        "trackingUrl": tracking_url,
                        "packageNumber": package_number,
                    }

            # 2. Fall back to Chrome headless (original path)
            if direct is None:
                page_html = fetch_rendered_dom(tracking_url, virtual_time_budget_ms=15000)
                direct = parse_dpd_tracking_page(page_html, tracking_url, package_number)

            # 3. If Chrome headless returns anti_bot, try 4PX
            if direct.get("status") == "anti_bot":
                fourpx_direct = resolve_fourpx_tracking_check(row, now_dt)
                if (fourpx_direct.get("status") or "") not in {"unavailable", "fetch_error"}:
                    direct = fourpx_direct
        else:
            direct = {
                **build_default_direct_carrier(),
                "provider": provider,
                "status": "unsupported",
                "statusLabel": "dopravce bez přímé podpory",
                "trackingUrl": tracking_url,
                "packageNumber": package_number or None,
            }
    except Exception as exc:
        direct = {
            **build_default_direct_carrier(),
            "provider": provider,
            "status": "fetch_error",
            "statusLabel": "chyba přímého carrier checku",
            "trackingUrl": tracking_url,
            "packageNumber": package_number or None,
            "error": str(exc),
        }
    DIRECT_CARRIER_CACHE[cache_key] = dict(direct)
    return direct


def evaluate_carrier_escalation_candidate(row: dict[str, Any], thresholds: Thresholds) -> bool:
    direct = row.get("directCarrier") or {}
    direct_provider = clean_string(direct.get("provider")) or None
    direct_status = clean_string(direct.get("status")) or "unavailable"
    direct_hours = direct.get("hoursWithoutMovement")
    wpj_status = (((row.get("wpj") or {}).get("carrierCheck") or {}).get("status") or "unchecked")

    if direct_status in {"delivered", "pickup_point"}:
        return False
    if direct_status == "moving":
        return False
    if direct_status == "info_received":
        if isinstance(direct_hours, (int, float)):
            return float(direct_hours) >= thresholds.warn_hours
        return False
    if wpj_status in {"delivered", "ready_to_pickup"}:
        return False
    if wpj_status == "picked_up":
        return True
    return carrier_candidate_from_wpj_status(wpj_status)


def enrich_rows_with_direct_carrier(rows: list[dict[str, Any]], now_dt: datetime, thresholds: Thresholds) -> list[dict[str, Any]]:
    for row in rows:
        if row.get("wpj"):
            row["directCarrier"] = resolve_direct_carrier_check(row, now_dt)
        else:
            row["directCarrier"] = row.get("directCarrier") or build_default_direct_carrier()
        row["carrierEscalationCandidate"] = evaluate_carrier_escalation_candidate(row, thresholds)
    return rows


def build_row_from_wpj_order(order: dict[str, Any], now_dt: datetime, thresholds: Thresholds) -> dict[str, Any] | None:
    created_at = parse_iso_dt(order.get("dateCreated"))
    if not created_at:
        return None

    status_name = str(((order.get("status") or {}).get("name")) or "").strip()
    if not status_name or "storno" in normalize_text(status_name) or bool(order.get("cancelled")):
        return None
    normalized_status = normalize_text(status_name)
    if "exped" not in normalized_status and "vyzved" not in normalized_status and "nedoruc" not in normalized_status:
        return None

    delivery_name = (
        (((order.get("deliveryType") or {}).get("delivery") or {}).get("name"))
        or ""
    )
    pickup_delivery = is_pickup_delivery_name(delivery_name)
    carrier_check = summarize_order_history(order)
    last_movement_at = parse_iso_dt(carrier_check.get("latestHistoryAt")) or created_at
    hours_without_movement = round((now_dt - last_movement_at).total_seconds() / 3600, 1)
    if hours_without_movement < thresholds.warn_hours:
        return None
    email = clean_string(order.get("email"))
    delivery_address = order.get("deliveryAddress") or {}
    phone = clean_string(delivery_address.get("phone")) if isinstance(delivery_address, dict) else ""
    country = clean_string((((delivery_address or {}).get("country") or {}).get("code"))) or "unknown"
    total_price = (order.get("totalPrice") or {}).get("withVat") or 0
    currency = clean_string(((order.get("currency") or {}).get("code"))) or ("EUR" if country == "SK" else "CZK")
    severity = classify_severity(hours_without_movement, thresholds)
    row = {
        "id": clean_string(order.get("id")),
        "orderCode": clean_string(order.get("code")),
        "createdAt": created_at.isoformat(),
        "lastMovementAt": last_movement_at.isoformat(),
        "hoursWithoutMovement": hours_without_movement,
        "daysWithoutMovement": round(hours_without_movement / 24, 2),
        "severity": severity,
        "severityRank": SEVERITY_RANK[severity],
        "email": email,
        "phone": phone,
        "tiandeId": "",
        "status": status_name or "–",
        "price": {
            "amount": round(float(total_price or 0), 2),
            "currency": currency,
            "display": f"{round(float(total_price or 0), 2):.2f} {currency}".strip(),
        },
        "sponsorId": "",
        "country": country,
        "isZeroValue": round(float(total_price or 0), 2) == 0.0,
        "missingTiandeId": False,
        "missingSponsorId": False,
        "wpj": {
            "status": order.get("status") or {},
            "dateCreated": order.get("dateCreated"),
            "deliveryType": order.get("deliveryType") or {},
            "pickupDelivery": pickup_delivery,
            "carrierCheck": carrier_check,
        },
        "directCarrier": build_default_direct_carrier(),
        "carrierEscalationCandidate": carrier_candidate_from_wpj_status(carrier_check.get("status") or "unchecked"),
    }
    return row


def load_rows_from_wpj(now_dt: datetime, thresholds: Thresholds, lookback_days: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rd = load_refresh_data_module()
    if not rd.wpj_endpoint() or not rd.SETTINGS.wpj_access_token:
        raise SystemExit("WPJ endpoint nebo WPJ token chybí")

    start_dt = now_dt - timedelta(days=max(lookback_days, 3))
    end_dt = now_dt + timedelta(hours=1)
    orders: list[dict[str, Any]] = []
    window_start = start_dt
    while window_start < end_dt:
        window_end = min(window_start + timedelta(days=5), end_dt)
        page = rd.fetch_wpj_orders(
            rd.wpj_endpoint(),
            rd.SETTINGS.wpj_access_token,
            window_start,
            window_end,
            limit=1000,
            detailed=False,
        )
        orders.extend(page)
        window_start = window_end
    rows = []
    for order in orders:
        row = build_row_from_wpj_order(order, now_dt, thresholds)
        if not row:
            continue
        carrier_status = (((row.get("wpj") or {}).get("carrierCheck") or {}).get("status") or "unchecked")
        if carrier_status in {"delivered", "ready_to_pickup"}:
            continue
        row["directCarrier"] = resolve_direct_carrier_check(row, now_dt)
        row["carrierEscalationCandidate"] = evaluate_carrier_escalation_candidate(row, thresholds)
        if not row["carrierEscalationCandidate"]:
            continue
        rows.append(row)

    rows.sort(
        key=lambda item: (
            carrier_status_rank((((item.get("wpj") or {}).get("carrierCheck") or {}).get("status") or "unchecked")),
            -item["severityRank"],
            -item["hoursWithoutMovement"],
            item["orderCode"] or "",
        )
    )
    source_meta = {
        "kind": "wpj",
        "label": "WPJ live watchdog",
        "windowFrom": start_dt.isoformat(),
        "windowTo": end_dt.isoformat(),
        "lookbackDays": lookback_days,
        "scannedOrders": len(orders),
    }
    return rows, source_meta


def main() -> None:
    args = parse_args()
    thresholds = Thresholds(
        warn_hours=args.warn_hours,
        alert_hours=args.alert_hours,
        critical_hours=args.critical_hours,
    )
    now_dt = (
        datetime.fromisoformat(args.now).astimezone(PRAGUE_TZ)
        if args.now
        else datetime.now(PRAGUE_TZ)
    )
    if args.source == "wpj":
        rows, source_meta = load_rows_from_wpj(now_dt, thresholds, args.wpj_lookback_days)
    else:
        try:
            input_path = Path(args.input).expanduser() if args.input else latest_csv_path(Path(args.input_dir).expanduser())
        except FileNotFoundError as exc:
            raise SystemExit(str(exc))
        if not input_path.exists():
            raise SystemExit(f"Input CSV not found: {input_path}")
        rows = load_rows(input_path, now_dt, thresholds)
        if not args.skip_wpj:
            rows = enrich_rows_with_wpj(rows, now_dt)
            rows = enrich_rows_with_direct_carrier(rows, now_dt, thresholds)
            rows = filter_open_rows(rows)
        source_meta = build_csv_source_meta(input_path)

    payload = build_payload(source_meta, rows, now_dt, thresholds)
    previous_state = load_state(Path(args.state).expanduser())
    if (previous_state.get("sourceKind") or "") != source_meta.get("kind"):
        previous_state = {"orders": {}}
    delta = build_delta(rows, previous_state)
    text = format_telegram_text(payload, delta)
    notify_results = []
    if args.notify and should_notify(payload, delta, previous_state):
        notify_results = notify(payload, text, dry_run=args.dry_run)
    payload["notifications"] = notify_results
    payload["delta"] = delta

    write_json(Path(args.output_json).expanduser(), payload)
    write_text(Path(args.output_text).expanduser(), text)
    update_state(Path(args.state).expanduser(), payload, delta)

    if args.source == "wpj":
        print(f"Processed {payload['summary']['total']} live WPJ stuck shipments")
    else:
        print(f"Processed {payload['summary']['total']} stuck shipments from {source_meta['path']}")


if __name__ == "__main__":
    main()
