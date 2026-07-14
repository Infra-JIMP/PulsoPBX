"""Combina perfis vindos do MikoPBX com excecoes cadastradas localmente."""

from email.utils import parseaddr

import mikopbx_api
from names import load_names


def _validated_email(value) -> str:
    email = str(value or "").strip().lower()
    _, parsed = parseaddr(email)
    local, separator, domain = parsed.partition("@")
    if parsed != email or not separator or not local or "." not in domain:
        return ""
    return email


def load_profiles() -> dict[str, dict]:
    profiles = {
        extension: {
            "nome": profile.get("nome", ""),
            "setor": "",
            "email": _validated_email(profile.get("email", "")),
            "notificar": True,
        }
        for extension, profile in mikopbx_api.get_cached_profiles().items()
    }
    for extension, override in load_names().items():
        profile = profiles.setdefault(
            extension,
            {"nome": "", "setor": "", "email": "", "notificar": True},
        )
        for field in ("nome", "setor"):
            if override.get(field):
                profile[field] = override[field]
        if override.get("email"):
            profile["email"] = _validated_email(override["email"])
        if "notificar" in override:
            profile["notificar"] = override["notificar"]
    return profiles


def notification_target(extension: str) -> tuple[str | None, dict]:
    profile = load_profiles().get(str(extension), {})
    email = str(profile.get("email") or "").strip().lower()
    if not email or profile.get("notificar") is False:
        return None, profile
    return f"email:{email}", profile
