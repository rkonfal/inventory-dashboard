#!/usr/bin/env python3
"""Helpers for planned direct marketing-platform integrations.

This module is intentionally small and safe to import before real platform
clients are implemented. It centralizes environment-variable names, readiness
checks, and the normalized record shape that future fetchers should emit.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


@dataclass
class MarketingRecord:
    date: str
    platform: str
    accountId: str
    accountName: str
    currency: str
    spend: float
    impressions: int = 0
    clicks: int = 0
    conversions: float = 0.0
    conversionValue: float = 0.0
    campaignId: str = ''
    campaignName: str = ''
    rawSource: str = ''

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


@dataclass
class IntegrationStatus:
    name: str
    requiredEnv: List[str]
    optionalEnv: List[str]
    ready: bool
    missingEnv: List[str]
    note: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


INTEGRATIONS = {
    'meta': {
        'required': ['META_APP_ID', 'META_APP_SECRET', 'META_SYSTEM_USER_ID', 'META_ACCESS_TOKEN', 'META_BUSINESS_ID'],
        'optional': [],
        'note': 'Meta Marketing API přes Business Manager, System User a token.',
    },
    'google_ads': {
        'required': ['GOOGLE_ADS_DEVELOPER_TOKEN', 'GOOGLE_ADS_LOGIN_CUSTOMER_ID', 'GOOGLE_ADS_JSON_KEY_FILE_PATH'],
        'optional': [],
        'note': 'Google Ads API přes manager account a service account.',
    },
    'sklik': {
        'required': ['SKLIK_API_TOKEN'],
        'optional': ['SKLIK_AGENCY_ACCOUNT_ID'],
        'note': 'Sklik API přes centrální Seznam/Sklik token.',
    },
}


def env_value(key: str) -> str:
    return (os.environ.get(key) or '').strip()


def integration_status(name: str) -> IntegrationStatus:
    if name not in INTEGRATIONS:
        raise KeyError(f'Unknown integration: {name}')
    spec = INTEGRATIONS[name]
    required = list(spec['required'])
    optional = list(spec.get('optional') or [])
    missing = [key for key in required if not env_value(key)]
    return IntegrationStatus(
        name=name,
        requiredEnv=required,
        optionalEnv=optional,
        ready=not missing,
        missingEnv=missing,
        note=spec.get('note') or '',
    )


def all_integration_statuses() -> Dict[str, Dict[str, object]]:
    return {name: integration_status(name).to_dict() for name in INTEGRATIONS}


def normalized_marketing_schema() -> Dict[str, str]:
    return {
        'date': 'ISO datum dne metriky',
        'platform': 'meta | google_ads | sklik | ...',
        'accountId': 'ID reklamního účtu',
        'accountName': 'Název reklamního účtu',
        'currency': 'Měna zdroje',
        'spend': 'Útrata v měně zdroje',
        'impressions': 'Zobrazení',
        'clicks': 'Kliknutí',
        'conversions': 'Konverze',
        'conversionValue': 'Hodnota konverzí',
        'campaignId': 'ID kampaně, pokud je k dispozici',
        'campaignName': 'Název kampaně, pokud je k dispozici',
        'rawSource': 'Původní zdroj nebo endpoint',
    }


if __name__ == '__main__':
    import json

    print(json.dumps({
        'integrations': all_integration_statuses(),
        'schema': normalized_marketing_schema(),
    }, ensure_ascii=False, indent=2))
