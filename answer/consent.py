"""Consent rules for contacting a senior (FR-10).

- Contact details are shown only if consent_public_contact is true.
- Only professional fields are shown: GitHub, LinkedIn, website, and email.
  Phone numbers are never stored or shown.
- The relay (message through the bot) needs consent_relay AND the senior
  must have started the bot (telegram_user_hash is set).
- Contact details never go into Qdrant, the LLM prompt, or logs.
"""

from __future__ import annotations

from store.db import parse_bool

CONSENT_DENIED = (
    "This senior has not opted in to public contact. "
    "Ask your department secretariat to forward a message."
)

CONTACT_FIELDS = ("github", "linkedin", "website", "contact_email")


def contact_card(senior) -> dict | None:
    """The contact fields the senior agreed to share, or None without consent."""
    if not senior or not parse_bool(senior.get("consent_public_contact")):
        return None
    return {field: senior[field] for field in CONTACT_FIELDS if senior.get(field)}


def can_relay(senior) -> bool:
    if not senior:
        return False
    return parse_bool(senior.get("consent_relay")) and bool(senior.get("telegram_user_hash"))
