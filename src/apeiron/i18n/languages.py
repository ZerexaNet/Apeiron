"""Language resolution rules (pure functions, no Qt dependency).

Policy, in order:

1. a manual choice (``zh_CN`` / ``en_US``) always wins and is never overwritten
   by a later system-language change;
2. ``system`` re-detects the OS locale on every start;
3. a Chinese OS locale resolves to ``zh_CN``;
4. another supported OS locale resolves to that language;
5. anything else falls back to ``zh_CN`` (the product default).

The system locale is never inferred from IP address or region settings.
"""

from __future__ import annotations

import locale
import os
from typing import Final

DEFAULT_LANGUAGE: Final = "zh_CN"
FALLBACK_CHAIN: Final[dict[str, str]] = {"zh_CN": DEFAULT_LANGUAGE, "en_US": DEFAULT_LANGUAGE}

LANGUAGES: Final[dict[str, str]] = {
    "zh_CN": "简体中文",
    "en_US": "English",
}
SYSTEM_OPTION: Final = "system"


def normalise_locale(value: str) -> str:
    """Turn ``zh-Hans-CN``/``zh_CN.UTF-8``/``en-GB`` into a base language tag."""
    raw = (value or "").strip()
    if not raw:
        return ""
    raw = raw.split(".")[0].split("@")[0].replace("-", "_")
    parts = [p for p in raw.split("_") if p]
    if not parts:
        return ""
    language = parts[0].lower()
    region = parts[1].upper() if len(parts) > 1 and len(parts[1]) == 2 else ""
    # Chinese variants collapse to Simplified Chinese: Apeiron ships one
    # Chinese translation, and zh_TW/zh_HK users are better served by it than
    # by English.
    if language == "zh":
        return "zh_CN"
    if language == "en":
        return "en_US" if region in ("", "US") else "en_US"
    return ""


def system_language(raw_locale: str | None = None) -> str:
    """Detect the OS UI language and map it to a supported code."""
    candidates: list[str] = []
    if raw_locale:
        candidates.append(raw_locale)
    else:
        for env_key in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            value = os.environ.get(env_key)
            if value:
                candidates.append(value)
                break
        try:
            candidates.append(locale.getlocale()[0] or "")
        except (ValueError, TypeError):
            pass
    for candidate in candidates:
        mapped = normalise_locale(candidate)
        if mapped:
            return mapped
    return DEFAULT_LANGUAGE


def resolve_language(setting: str, raw_locale: str | None = None) -> str:
    """Resolve the effective language for a settings value."""
    if setting in LANGUAGES:
        return setting
    return system_language(raw_locale)


def is_manual(setting: str) -> bool:
    return setting in LANGUAGES


def available_choices() -> list[dict[str, str]]:
    choices = [{"code": SYSTEM_OPTION, "label": "settings.language.system"}]
    choices.extend({"code": code, "label": name} for code, name in LANGUAGES.items())
    return choices
