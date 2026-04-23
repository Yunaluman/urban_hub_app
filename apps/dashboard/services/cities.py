from __future__ import annotations

import re

ALL_CITIES = '__all__'

_CITY_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ' -]{2,60}$")


def is_reasonable_city_name(value: str) -> bool:
    """
    Heuristique: un nom de ville "humain" (pas de codes, pas d'adresses).

    Contrat historique:
    - pas de virgule (évite "rue, code postal, pays")
    - pas de chiffres
    - charset limité (lettres/espaces/'/-)
    """
    if not value:
        return False
    text = str(value).strip()
    if not text or ',' in text:
        return False
    if any(ch.isdigit() for ch in text):
        return False
    return bool(_CITY_RE.match(text))


def canonicalize_city(raw: str, canonical: list[str]) -> str | None:
    """
    Normalise un libellé ville hétérogène vers une ville canonique.

    Ex: "80 Boulevard ... Paris 75015 France" -> "Paris"
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    s_l = s.lower()
    matches = [c for c in canonical if c and c.lower() in s_l]
    if not matches:
        return None
    return max(matches, key=len)


def city_filter_value(selected_city: str) -> str:
    """`__all__` côté UI devient filtre vide côté requêtes."""
    return '' if str(selected_city).strip() == ALL_CITIES else str(selected_city).strip()

