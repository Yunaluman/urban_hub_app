import io
import os
import logging

import boto3
import pandas as pd
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

MINIO_ENDPOINT = os.environ.get('MINIO_ENDPOINT', 'http://localhost:9000')
MINIO_ROOT_USER = os.environ.get('MINIO_ROOT_USER', 'minioadmin')
MINIO_ROOT_PASSWORD = os.environ.get('MINIO_ROOT_PASSWORD', 'minioadmin')

BUCKETS = [
    'urbanhub-raw',
    'urbanhub-processed',
    'urbanhub-analytics',
]

# Canonical object key layout (all buckets):
#   <domain>/<source>/<layer>/<...>
# where layer is one of: bronze | silver | gold | exports | test
# Examples:
#   meteo/openmeteo/bronze/openmeteo_<ts>.parquet
#   pollution/eea/bronze/Paris/1/202504_<ts>.zip
#   mobility/citybikes/silver/citybikes_<ts>.parquet
#   analysis/cross/gold/meteo_pollution_<ts>.parquet


def _get_client() -> boto3.client:
    """Return a boto3 S3 client configured for the MinIO endpoint."""
    return boto3.client(
        's3',
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ROOT_USER,
        aws_secret_access_key=MINIO_ROOT_PASSWORD,
    )


def _ensure_bucket(client: boto3.client, bucket: str) -> None:
    """Create the bucket if it does not already exist."""
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        error_code = int(exc.response['Error']['Code'])
        if error_code == 404:
            client.create_bucket(Bucket=bucket)
            logger.info("Bucket créé : %s", bucket)
        else:
            raise


def upload_to_lake(df: pd.DataFrame, bucket: str, path: str) -> None:
    """Upload a pandas DataFrame as a Parquet file to MinIO.

    Args:
        df:     The DataFrame to upload.
        bucket: Target bucket name (e.g. 'urbanhub-raw').
        path:   Object key inside the bucket (e.g. 'meteo/stations.parquet').
    """
    client = _get_client()
    _ensure_bucket(client, bucket)

    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False, engine='pyarrow')
    buffer.seek(0)

    client.put_object(
        Bucket=bucket,
        Key=path,
        Body=buffer,
        ContentType='application/octet-stream',
    )


def upload_bytes_to_lake(
    content: bytes,
    bucket: str,
    path: str,
    content_type: str = 'application/octet-stream',
) -> None:
    """Upload raw bytes to MinIO."""
    client = _get_client()
    _ensure_bucket(client, bucket)
    client.put_object(
        Bucket=bucket,
        Key=path,
        Body=io.BytesIO(content),
        ContentType=content_type,
    )


def download_from_lake(bucket: str, path: str) -> pd.DataFrame:
    """Download a Parquet file from MinIO and return it as a DataFrame.

    Args:
        bucket: Source bucket name (e.g. 'urbanhub-processed').
        path:   Object key inside the bucket (e.g. 'meteo/stations.parquet').

    Returns:
        A pandas DataFrame loaded from the Parquet file.
    """
    client = _get_client()

    response = client.get_object(Bucket=bucket, Key=path)
    buffer = io.BytesIO(response['Body'].read())

    return pd.read_parquet(buffer, engine='pyarrow')


def list_objects(bucket: str, prefix: str = '') -> list[str]:
    """List object keys in a bucket under a prefix."""
    client = _get_client()
    keys: list[str] = []
    token = None
    while True:
        kwargs = {'Bucket': bucket, 'Prefix': prefix}
        if token:
            kwargs['ContinuationToken'] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get('Contents', []) or []:
            k = obj.get('Key')
            if k:
                keys.append(k)
        if not resp.get('IsTruncated'):
            break
        token = resp.get('NextContinuationToken')
    return keys


def copy_object(bucket: str, src_key: str, dst_key: str) -> None:
    """Copy an object within the same bucket."""
    client = _get_client()
    client.copy_object(
        Bucket=bucket,
        Key=dst_key,
        CopySource={'Bucket': bucket, 'Key': src_key},
    )


def delete_object(bucket: str, key: str) -> None:
    """Delete an object from a bucket."""
    client = _get_client()
    client.delete_object(Bucket=bucket, Key=key)
