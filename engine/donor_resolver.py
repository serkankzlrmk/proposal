"""
proposal/engine/donor_resolver.py — Shared donor-id resolution (call-aware).

Call-ingested donors keep underscored ids (unfpa_turkiye_cefm,
ech_sudan_2026). Legacy keys (OCHA_CBPF -> ocha_cbpf) need alias mapping.
This helper resolves BOTH correctly — exact manifest id first, then legacy
alias, then underscore-stripped normalization — so every endpoint scores
against the ACTUAL selected donor instead of silently falling back to
ocha_cbpf.
"""

from __future__ import annotations

from typing import Optional

from engine.yaml_rules import YamlDonorRuleLoader

LEGACY_ALIASES = {
    "ochacbpf": "ocha_cbpf",
    "usaidbha": "usaid_bha",
    "euprag": "eu_prag",
}

_loader: Optional[YamlDonorRuleLoader] = None


def _get_loader() -> YamlDonorRuleLoader:
    global _loader
    if _loader is None:
        _loader = YamlDonorRuleLoader()
    return _loader


def resolve_donor_id(donor: str, loader: Optional[YamlDonorRuleLoader] = None) -> str:
    """Resolve any donor label to a loadable manifest id (zero-crash).

    Order:
      1. exact id (call-ingested underscored ids, e.g. unfpa_turkiye_cefm)
      2. legacy alias (OCHA_CBPF -> ocha_cbpf)
      3. underscore-stripped exact (UNFPA_TURKIYE_CEFM -> unfpaturkiyecefm
         only if that file exists)
      4. loader default fallback (ocha_cbpf) — never raises
    """
    loader = loader or _get_loader()
    raw = (donor or "OCHA_CBPF").lower()
    available = set(loader.list_donors())

    if raw in available:
        return raw
    aliased = LEGACY_ALIASES.get(raw.replace("_", ""))
    if aliased and aliased in available:
        return aliased
    stripped = raw.replace("_", "")
    if stripped in available:
        return stripped
    return raw  # loader.load() falls back to DEFAULT_DONOR_ID with warning
