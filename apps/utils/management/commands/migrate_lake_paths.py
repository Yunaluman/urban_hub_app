"""
Management command: migrate MinIO object keys to the canonical layout.

It performs copy+delete within the same bucket (safe for MinIO/S3).

Usage:
    python manage.py migrate_lake_paths --dry-run
    python manage.py migrate_lake_paths --apply
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.core.management.base import BaseCommand

from apps.utils.lake import copy_object, delete_object, list_objects


@dataclass(frozen=True)
class Rule:
    bucket: str
    pattern: re.Pattern[str]
    replace: str


RULES: list[Rule] = [
    # Raw bucket: old flat files -> domain/source/layer
    Rule(
        bucket='urbanhub-raw',
        pattern=re.compile(r'^meteo/openmeteo_(.+)\.parquet$'),
        replace=r'meteo/openmeteo/bronze/openmeteo_\1.parquet',
    ),
    Rule(
        bucket='urbanhub-processed',
        pattern=re.compile(r'^meteo/openmeteo_(.+)\.parquet$'),
        replace=r'meteo/openmeteo/silver/openmeteo_\1.parquet',
    ),
    Rule(
        bucket='urbanhub-raw',
        pattern=re.compile(r'^meteo/noaa_gsod_(.+)\.parquet$'),
        replace=r'meteo/noaa_gsod/bronze/noaa_gsod_\1.parquet',
    ),
    Rule(
        bucket='urbanhub-processed',
        pattern=re.compile(r'^meteo/noaa_gsod_(.+)\.parquet$'),
        replace=r'meteo/noaa_gsod/silver/noaa_gsod_\1.parquet',
    ),
    Rule(
        bucket='urbanhub-raw',
        pattern=re.compile(r'^mobility/citybikes_(.+)\.parquet$'),
        replace=r'mobility/citybikes/bronze/citybikes_\1.parquet',
    ),
    Rule(
        bucket='urbanhub-processed',
        pattern=re.compile(r'^mobility/citybikes_(.+)\.parquet$'),
        replace=r'mobility/citybikes/silver/citybikes_\1.parquet',
    ),
    Rule(
        bucket='urbanhub-raw',
        pattern=re.compile(r'^pollution/openaq_(.+)\.parquet$'),
        replace=r'pollution/openaq/bronze/openaq_\1.parquet',
    ),
    Rule(
        bucket='urbanhub-processed',
        pattern=re.compile(r'^pollution/openaq_(.+)\.parquet$'),
        replace=r'pollution/openaq/silver/openaq_\1.parquet',
    ),
    # Analytics: old flat analysis paths -> analysis/cross/gold
    Rule(
        bucket='urbanhub-analytics',
        pattern=re.compile(r'^analysis/meteo_pollution_(.+)\.parquet$'),
        replace=r'analysis/cross/gold/meteo_pollution_\1.parquet',
    ),
    Rule(
        bucket='urbanhub-analytics',
        pattern=re.compile(r'^analysis/meteo_mobility_corr_(.+)\.parquet$'),
        replace=r'analysis/cross/gold/meteo_mobility_corr_\1.parquet',
    ),
    Rule(
        bucket='urbanhub-analytics',
        pattern=re.compile(r'^analysis/meteo_mobility_effects_(.+)\.parquet$'),
        replace=r'analysis/cross/gold/meteo_mobility_effects_\1.parquet',
    ),
]


def _rewrite(bucket: str, key: str) -> str | None:
    for rule in RULES:
        if rule.bucket != bucket:
            continue
        if rule.pattern.match(key):
            return rule.pattern.sub(rule.replace, key)
    return None


class Command(BaseCommand):
    help = "Migrate MinIO object keys to canonical domain/source/layer layout"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', default=False)
        parser.add_argument('--apply', action='store_true', default=False)

    def handle(self, *args, **options):
        dry_run = bool(options['dry_run'])
        do_apply = bool(options['apply'])
        if dry_run == do_apply:
            self.stdout.write(self.style.ERROR('Choose exactly one: --dry-run or --apply'))
            return

        buckets = sorted({r.bucket for r in RULES})
        total = 0
        moved = 0

        for bucket in buckets:
            keys = list_objects(bucket=bucket, prefix='')
            for key in keys:
                new_key = _rewrite(bucket, key)
                if not new_key or new_key == key:
                    continue
                total += 1
                self.stdout.write(f'{bucket}: {key} -> {new_key}')
                if do_apply:
                    copy_object(bucket=bucket, src_key=key, dst_key=new_key)
                    delete_object(bucket=bucket, key=key)
                    moved += 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f'DRY RUN: {total} object(s) would be moved.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'APPLIED: {moved}/{total} object(s) moved.'))

