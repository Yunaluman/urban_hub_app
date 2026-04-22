"""
Lecture MinIO optionnelle (exports, audit, debug) — hors chemin nominal API.

Ne pas utiliser dans les vues dashboard une fois Postgres Gold stabilisé.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from apps.utils.lake import download_from_lake, list_objects

logger = logging.getLogger(__name__)


def load_latest_parquet_under_prefix(
    bucket: str,
    prefix: str,
    *,
    suffix: str = '.parquet',
) -> tuple[pd.DataFrame, Optional[str]]:
    """
    Liste les clés sous ``prefix``, prend la dernière (tri lexicographique),
    télécharge le Parquet et renvoie ``(DataFrame, key)``.

    Le tri par nom convient aux clés contenant un timestamp ``%Y%m%d_%H%M%S``.
    """
    keys = [k for k in list_objects(bucket, prefix) if k.endswith(suffix)]
    if not keys:
        return pd.DataFrame(), None
    latest = sorted(keys)[-1]
    try:
        return download_from_lake(bucket, latest), latest
    except Exception as exc:
        logger.error('load_latest_parquet_under_prefix failed key=%s: %s', latest, exc)
        return pd.DataFrame(), latest
