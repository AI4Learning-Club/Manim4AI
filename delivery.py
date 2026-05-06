from __future__ import annotations

import hashlib
import posixpath
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse
from uuid import uuid4

from qcloud_cos import CosConfig, CosS3Client
from tencentcloud.cdn.v20180606 import cdn_client
from tencentcloud.cdn.v20180606 import models as cdn_models
from tencentcloud.common import credential
from tencentcloud.sts.v20180813 import models as sts_models
from tencentcloud.sts.v20180813 import sts_client

from config.settings import CloudSettings, ManimDeliverySettings

HLS_MANIFEST_CONTENT_TYPE = "application/vnd.apple.mpegurl"
HLS_SEGMENT_CONTENT_TYPE = "video/mp2t"


@dataclass(frozen=True)
class HlsPublishInput:
    hls_root: Path
    manifest_path: Path
    preview_version: int
    preview_sections: int
    is_final: bool
    run_key: str


@dataclass(frozen=True)
class HlsPublishedArtifact:
    delivery_type: str
    manifest_key: str
    manifest_url: str
    preview_version: int
    preview_sections: int
    is_final: bool
    object_keys: tuple[str, ...]
    preheated_urls: tuple[str, ...]


@dataclass(frozen=True)
class _TencentCredentialBundle:
    secret_id: str
    secret_key: str
    token: str | None = None
    expires_at: float | None = None


def _strip_slashes(value: str) -> str:
    return str(value or "").strip().strip("/")


def _content_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".m3u8":
        return HLS_MANIFEST_CONTENT_TYPE
    if suffix == ".ts":
        return HLS_SEGMENT_CONTENT_TYPE
    return "application/octet-stream"


def _cache_control_for_path(path: Path, delivery: ManimDeliverySettings) -> str:
    if path.suffix.lower() == ".m3u8":
        return delivery.cache_control_manifest
    return delivery.cache_control_segment


def _iter_hls_asset_paths(hls_root: Path, manifest_path: Path) -> list[Path]:
    root = hls_root.resolve()
    manifest = manifest_path.resolve()
    assets = {manifest}
    if not manifest.exists():
        raise FileNotFoundError(f"HLS manifest is missing: {manifest}")
    manifest_dir = manifest.parent
    for raw_line in manifest.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "://" in line:
            continue
        target = (manifest_dir / line).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"HLS manifest references a file outside hls_root: {line}") from exc
        if target.exists() and target.is_file():
            assets.add(target)
    return sorted(assets)


def _relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _object_key_for_relative(delivery: ManimDeliverySettings, run_key: str, relative_path: str) -> str:
    prefix = _strip_slashes(delivery.cos_object_prefix)
    normalized_run_key = _strip_slashes(run_key)
    parts = [part for part in (prefix, normalized_run_key, relative_path.lstrip("/")) if part]
    return "/".join(parts)


class TencentCdnTypeDSigner:
    def __init__(self, delivery: ManimDeliverySettings) -> None:
        base_url = str(delivery.cdn_base_url or "").strip()
        key = str(delivery.cdn_auth_key or "").strip()
        if not base_url:
            raise ValueError("manim.delivery.cdn_base_url is required for CDN delivery")
        if not key:
            raise ValueError("manim.delivery.cdn_auth_key is required for CDN TypeD signing")
        self._base_url = base_url.rstrip("/")
        self._key = key
        self._algorithm = str(delivery.cdn_auth_algorithm or "sha256").strip().lower()
        self._sign_param = str(delivery.cdn_sign_param or "sign").strip() or "sign"
        self._timestamp_param = str(delivery.cdn_timestamp_param or "t").strip() or "t"
        self._timestamp_format = str(delivery.cdn_timestamp_format or "decimal").strip().lower()
        if int(delivery.cdn_auth_ttl_seconds) <= 0:
            raise ValueError("manim.delivery.cdn_auth_ttl_seconds must be > 0 for Tencent CDN TypeD HLS URLs")

    def sign_url(self, object_key: str, *, timestamp: int | None = None) -> str:
        normalized_key = _strip_slashes(object_key)
        path = "/" + quote(normalized_key, safe="/._~-")
        ts = int(time.time() if timestamp is None else timestamp)
        ts_text = format(ts, "x") if self._timestamp_format in {"hex", "hexadecimal"} else str(ts)
        source = f"{self._key}{path}{ts_text}".encode()
        if self._algorithm == "md5":
            digest = hashlib.md5(source, usedforsecurity=False).hexdigest()
        elif self._algorithm == "sha256":
            digest = hashlib.sha256(source).hexdigest()
        else:
            raise ValueError(f"Unsupported CDN TypeD auth algorithm: {self._algorithm}")
        query = urlencode({self._sign_param: digest, self._timestamp_param: ts_text})
        return f"{self._base_url}{path}?{query}"


def _resolve_manifest_uri_to_key(manifest_key: str, uri: str) -> str:
    if "://" in uri:
        parsed = urlparse(uri)
        return parsed.path.lstrip("/")
    base_dir = posixpath.dirname(manifest_key)
    return posixpath.normpath(posixpath.join(base_dir, uri)).lstrip("/")


def render_signed_manifest(
    *,
    manifest_path: Path,
    manifest_key: str,
    signer: TencentCdnTypeDSigner,
    timestamp: int | None = None,
) -> str:
    lines: list[str] = []
    for raw_line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            lines.append(raw_line)
            continue
        segment_key = _resolve_manifest_uri_to_key(manifest_key, line)
        lines.append(signer.sign_url(segment_key, timestamp=timestamp))
    return "\n".join(lines) + "\n"


class TencentCosCdnPublisher:
    def __init__(
        self,
        *,
        delivery: ManimDeliverySettings,
        cloud: CloudSettings,
        cos_client: CosS3Client | None = None,
        cdn_client_override: cdn_client.CdnClient | None = None,
    ) -> None:
        self._delivery = delivery
        self._cloud = cloud
        self._signer = TencentCdnTypeDSigner(delivery)
        self._cos_client = cos_client
        self._cdn_client = cdn_client_override
        self._credential_bundle: _TencentCredentialBundle | None = None

    def _require_cloud_credentials(self) -> tuple[str, str]:
        secret_id = str(self._cloud.tencent_secret_id or "").strip()
        secret_key = str(self._cloud.tencent_secret_key or "").strip()
        if not secret_id or not secret_key:
            raise RuntimeError("Tencent Cloud SecretId/SecretKey are required for Manim COS delivery")
        return secret_id, secret_key

    def _cloud_credentials(self) -> _TencentCredentialBundle:
        role_arn = str(self._cloud.tencent_role_arn or "").strip()
        cached = self._credential_bundle
        if cached is not None and (cached.expires_at is None or cached.expires_at - time.time() > 300):
            return cached

        secret_id, secret_key = self._require_cloud_credentials()
        if not role_arn:
            self._credential_bundle = _TencentCredentialBundle(secret_id=secret_id, secret_key=secret_key)
            return self._credential_bundle

        duration_seconds = 3600
        cred = credential.Credential(secret_id, secret_key)
        client = sts_client.StsClient(cred, self._delivery.cos_region)
        req = sts_models.AssumeRoleRequest()
        req.RoleArn = role_arn
        req.RoleSessionName = f"manim-hls-{uuid4().hex[:8]}"
        req.DurationSeconds = duration_seconds
        resp = client.AssumeRole(req)
        creds = resp.Credentials
        self._cos_client = None
        self._cdn_client = None
        self._credential_bundle = _TencentCredentialBundle(
            secret_id=str(creds.TmpSecretId),
            secret_key=str(creds.TmpSecretKey),
            token=str(creds.Token),
            expires_at=time.time() + duration_seconds,
        )
        return self._credential_bundle

    def _cos(self) -> CosS3Client:
        if self._cos_client is None:
            cloud_credentials = self._cloud_credentials()
            config = CosConfig(
                Region=self._delivery.cos_region,
                SecretId=cloud_credentials.secret_id,
                SecretKey=cloud_credentials.secret_key,
                Token=cloud_credentials.token,
                Scheme="https",
            )
            self._cos_client = CosS3Client(config)
        return self._cos_client

    def _cdn(self) -> cdn_client.CdnClient:
        if self._cdn_client is None:
            cloud_credentials = self._cloud_credentials()
            cred = credential.Credential(
                cloud_credentials.secret_id,
                cloud_credentials.secret_key,
                cloud_credentials.token,
            )
            self._cdn_client = cdn_client.CdnClient(cred, "")
        return self._cdn_client

    def _upload_bytes(self, *, key: str, body: bytes, content_type: str, cache_control: str) -> None:
        kwargs: dict[str, object] = {
            "Bucket": self._delivery.cos_bucket,
            "Key": key,
            "Body": body,
            "ContentType": content_type,
            "CacheControl": cache_control,
        }
        if self._delivery.sse_cos_enabled:
            kwargs["ServerSideEncryption"] = "AES256"
        self._cos().put_object(**kwargs)

    def _upload_file(self, *, key: str, path: Path, content_type: str, cache_control: str) -> None:
        kwargs: dict[str, object] = {
            "Bucket": self._delivery.cos_bucket,
            "Key": key,
            "LocalFilePath": str(path),
            "MAXThread": self._delivery.cos_upload_threads,
            "ContentType": content_type,
            "CacheControl": cache_control,
        }
        if self._delivery.sse_cos_enabled:
            kwargs["ServerSideEncryption"] = "AES256"
        self._cos().upload_file(**kwargs)

    def _preheat(self, urls: list[str]) -> tuple[str, ...]:
        if not self._delivery.preheat_enabled or not urls:
            return ()
        req = cdn_models.PushUrlsCacheRequest()
        req.Urls = urls
        req.Area = self._delivery.preheat_area or "mainland"
        self._cdn().PushUrlsCache(req)
        return tuple(urls)

    def publish_hls(self, payload: HlsPublishInput) -> HlsPublishedArtifact:
        if not self._delivery.cos_bucket or not self._delivery.cos_region:
            raise RuntimeError("manim.delivery.cos_bucket and cos_region are required for Manim COS delivery")
        hls_root = payload.hls_root.resolve()
        manifest_path = payload.manifest_path.resolve()
        assets = _iter_hls_asset_paths(hls_root, manifest_path)
        object_keys = {
            path: _object_key_for_relative(
                self._delivery,
                payload.run_key,
                _relative_posix(path, hls_root),
            )
            for path in assets
        }
        manifest_key = object_keys[manifest_path]
        signed_at = int(time.time())

        uploaded_keys: list[str] = []
        for path in assets:
            key = object_keys[path]
            if path == manifest_path:
                body = render_signed_manifest(
                    manifest_path=manifest_path,
                    manifest_key=manifest_key,
                    signer=self._signer,
                    timestamp=signed_at,
                ).encode("utf-8")
                self._upload_bytes(
                    key=key,
                    body=body,
                    content_type=HLS_MANIFEST_CONTENT_TYPE,
                    cache_control=_cache_control_for_path(path, self._delivery),
                )
            else:
                self._upload_file(
                    key=key,
                    path=path,
                    content_type=_content_type_for_path(path),
                    cache_control=_cache_control_for_path(path, self._delivery),
                )
            uploaded_keys.append(key)

        manifest_url = self._signer.sign_url(manifest_key, timestamp=signed_at)
        preheated = self._preheat([manifest_url] + [self._signer.sign_url(key, timestamp=signed_at) for key in uploaded_keys if key != manifest_key])
        return HlsPublishedArtifact(
            delivery_type="hls",
            manifest_key=manifest_key,
            manifest_url=manifest_url,
            preview_version=payload.preview_version,
            preview_sections=payload.preview_sections,
            is_final=payload.is_final,
            object_keys=tuple(uploaded_keys),
            preheated_urls=preheated,
        )
