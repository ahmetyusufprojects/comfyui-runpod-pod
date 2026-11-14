"""Private Cloudflare R2 storage for encrypted WebUI system backups."""
from __future__ import annotations

import os
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import PurePath


PREFIX = "system-backups/"
DEFAULT_BUCKET = "moody-webui-backups"
_OBJECT_ID = re.compile(
    r"\d{8}T\d{6}Z_[a-f0-9]{32}_[A-Za-z0-9_-]{1,48}\.wuis\.gpg\Z")
_CLIENT = None
_CLIENT_CONFIG = None


class R2Error(RuntimeError):
    pass


def _settings():
    bucket = os.environ.get("R2_BUCKET", DEFAULT_BUCKET).strip()
    access = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    endpoint = os.environ.get("R2_ENDPOINT", "").strip().rstrip("/")
    account = os.environ.get("R2_ACCOUNT_ID", "").strip()
    if not endpoint and account:
        endpoint = "https://%s.r2.cloudflarestorage.com" % account
    missing = []
    if not endpoint:
        missing.append("R2_ACCOUNT_ID veya R2_ENDPOINT")
    if not access:
        missing.append("R2_ACCESS_KEY_ID")
    if not secret:
        missing.append("R2_SECRET_ACCESS_KEY")
    if not bucket:
        missing.append("R2_BUCKET")
    if missing:
        raise R2Error("RunPod Secret ayarları eksik: " + ", ".join(missing))
    if not endpoint.startswith("https://") or "/" in endpoint[8:]:
        raise R2Error("R2_ENDPOINT geçerli bir HTTPS adresi olmalı.")
    return endpoint, bucket, access, secret


def _client():
    global _CLIENT, _CLIENT_CONFIG
    endpoint, bucket, access, secret = _settings()
    config = (endpoint, bucket, access, secret)
    if _CLIENT is not None and _CLIENT_CONFIG == config:
        return _CLIENT, bucket
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:
        raise R2Error("R2 aktarımı için boto3 çalışma paketi kurulu değil.") from exc
    _CLIENT = boto3.client(
        "s3", endpoint_url=endpoint, region_name="auto",
        aws_access_key_id=access, aws_secret_access_key=secret,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                      retries={"mode": "standard", "max_attempts": 4}))
    _CLIENT_CONFIG = config
    return _CLIENT, bucket


def status(check_connection=True):
    endpoint, bucket, _, _ = _settings()
    result = {"configured": True, "bucket": bucket,
              "endpoint_host": endpoint.split("/", 3)[2], "connected": False}
    if check_connection:
        client, _ = _client()
        try:
            client.head_bucket(Bucket=bucket)
        except Exception as exc:
            raise _safe_error(exc) from exc
        result["connected"] = True
    return result


def _safe_error(exc):
    try:
        response = getattr(exc, "response", {})
        code = str(response.get("Error", {}).get("Code", ""))
        if code:
            return R2Error("R2 isteği başarısız oldu (" + code[:40] + ").")
    except (AttributeError, TypeError):
        pass
    return R2Error("R2 isteği başarısız oldu (" + type(exc).__name__ + ").")


def _safe_name(value):
    raw = PurePath(str(value or "yedek").replace("\\", "/")).name
    stem = raw[:-9] if raw.lower().endswith(".wuis.gpg") else raw.rsplit(".", 1)[0]
    ascii_name = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_name).strip("-_")[:48]
    return slug or "yedek"


def _key_for(name):
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return PREFIX + now + "_" + uuid.uuid4().hex + "_" + _safe_name(name) + ".wuis.gpg"


def _key_from_id(object_id):
    if not isinstance(object_id, str) or not _OBJECT_ID.fullmatch(object_id):
        raise R2Error("Yedek kaydı kimliği geçersiz.")
    return PREFIX + object_id


def is_backup_id(object_id):
    return isinstance(object_id, str) and bool(_OBJECT_ID.fullmatch(object_id))


def list_backups():
    client, bucket = _client()
    items = []
    truncated = False
    token = None
    while True:
        request = {"Bucket": bucket, "Prefix": PREFIX, "MaxKeys": 1000}
        if token:
            request["ContinuationToken"] = token
        try:
            response = client.list_objects_v2(**request)
        except Exception as exc:
            raise _safe_error(exc) from exc
        for item in response.get("Contents", []):
            key = item.get("Key", "")
            if not key.startswith(PREFIX):
                continue
            object_id = key[len(PREFIX):]
            if not _OBJECT_ID.fullmatch(object_id):
                continue
            slug = object_id.split("_", 2)[-1].removesuffix(".wuis.gpg")
            items.append({
                "id": object_id,
                "name": slug.replace("_", " ").replace("-", " "),
                "size": int(item.get("Size", 0)),
                "modified": item.get("LastModified").isoformat()
                if item.get("LastModified") else "",
                "etag": str(item.get("ETag", "")).strip('"'),
            })
        truncated = bool(response.get("IsTruncated"))
        token = response.get("NextContinuationToken")
        if not truncated or not token:
            break
    items.sort(key=lambda item: item["modified"], reverse=True)
    return {"backups": items, "truncated": truncated}


def head_backup(object_id):
    client, bucket = _client()
    key = _key_from_id(object_id)
    try:
        response = client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        raise _safe_error(exc) from exc
    return {"id": object_id, "size": int(response.get("ContentLength", 0)),
            "modified": response.get("LastModified").isoformat()
            if response.get("LastModified") else ""}


def upload_file(path, name, callback=None):
    client, bucket = _client()
    key = _key_for(name)
    total = os.path.getsize(path)
    extra = {"ContentType": "application/octet-stream",
             "Metadata": {"original-name": _safe_name(name)}}
    try:
        client.upload_file(path, bucket, key, ExtraArgs=extra,
                           Callback=callback, Config=_transfer_config())
    except Exception as exc:
        raise _safe_error(exc) from exc
    return {"id": key[len(PREFIX):], "name": _safe_name(name),
            "size": total, "key": key}


def download_file(object_id, path, callback=None):
    client, bucket = _client()
    key = _key_from_id(object_id)
    try:
        client.download_file(bucket, key, path, Callback=callback,
                             Config=_transfer_config())
    except Exception as exc:
        raise _safe_error(exc) from exc
    return os.path.getsize(path)


def get_object(object_id):
    client, bucket = _client()
    key = _key_from_id(object_id)
    try:
        return client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:
        raise _safe_error(exc) from exc


def delete_backup(object_id):
    client, bucket = _client()
    key = _key_from_id(object_id)
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except Exception as exc:
        raise _safe_error(exc) from exc


def _transfer_config():
    try:
        from boto3.s3.transfer import TransferConfig
    except ImportError as exc:
        raise R2Error("R2 aktarımı için boto3 çalışma paketi kurulu değil.") from exc
    return TransferConfig(multipart_threshold=64 * 1024 * 1024,
                          multipart_chunksize=64 * 1024 * 1024,
                          max_concurrency=2, use_threads=True)
