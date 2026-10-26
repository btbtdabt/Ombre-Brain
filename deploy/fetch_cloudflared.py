#!/usr/bin/env python3
"""构建期下载 cloudflared 二进制（镜像自带 python，无需 curl / apt-get update）。

背景（用户反馈 #3）：旧 Dockerfile 为了拿 curl 去下 cloudflared，先跑
`apt-get update && apt-get install curl ca-certificates`，这一步会打到 Debian
镜像源，间歇性 502 导致**整个镜像构建失败**，每次升级都要手动注释掉那几行。

cloudflared 本来就是从 GitHub Releases 直接下载的静态二进制，跟 apt 无关。这里改用
python:slim 自带的 python（系统已含 ca-certificates）直接下载，并做指数退避重试，
从根上避开 apt。不需要 Tunnel 的用户可在构建时 `--build-arg INSTALL_CLOUDFLARED=0`
完全跳过本步骤。

用法：python fetch_cloudflared.py <目标路径>
"""
from __future__ import annotations

import platform
import hashlib
import os
import sys
import time
import urllib.request

# platform.machine() → cloudflared release 资产的架构后缀
_ARCH_MAP = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "arm",
    "armv6l": "arm",
    "i686": "386",
    "i386": "386",
}

# Pin both the release and the per-architecture artifact digest.  Using the
# GitHub ``latest`` redirect made otherwise identical Docker builds fetch
# different, unauthenticated bytes over time.
_CLOUDFLARED_VERSION = "2026.10.3"
_SHA256 = {
    "386": "7c533230928eae6443d6d87dce16e0af67fa1f60fb56860dca4e09883a8ee394",
    "amd64": "053b80b0a23031a038fdbf3c073f59f82b8f1f152aa67971d7383ffafc0dbab0",
    "arm": "76f35faae119881ddf766b61baa9e27803aea6bea7111ed4170ed1b7a5c8637e",
    "arm64": "89297ebfb9e8c99c0dc013a1140601453cf3798d8306c5591651f4a3f5210be8",
}
_MAX_DOWNLOAD_BYTES = 128 * 1024 * 1024


def cloudflared_arch(machine: str | None = None) -> str:
    m = (machine or platform.machine()).lower()
    if m not in _ARCH_MAP:
        raise SystemExit(f"不支持的架构：{m!r}（cloudflared 无对应发行资产）")
    return _ARCH_MAP[m]


def release_url(arch: str) -> str:
    return (
        "https://github.com/cloudflare/cloudflared/releases/download/"
        f"{_CLOUDFLARED_VERSION}/cloudflared-linux-{arch}"
    )


def expected_sha256(arch: str) -> str:
    try:
        return _SHA256[arch]
    except KeyError as exc:
        raise SystemExit(f"不支持的 cloudflared 发行架构：{arch!r}") from exc


def download(
    url: str,
    dest: str,
    *,
    expected_digest: str,
    attempts: int = 5,
) -> None:
    last: Exception | None = None
    part = f"{dest}.part"
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OmbreBrain-Build"})
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:  # nosec B310
                response_headers = getattr(r, "headers", {}) or {}
                content_length = response_headers.get("Content-Length")
                if content_length:
                    try:
                        declared_bytes = int(content_length)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("无效的 Content-Length") from exc
                    if declared_bytes < 0 or declared_bytes > _MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            f"下载声明体积超过 {_MAX_DOWNLOAD_BYTES} 字节上限"
                        )
                digest = hashlib.sha256()
                downloaded = 0
                while chunk := r.read(1024 * 1024):
                    downloaded += len(chunk)
                    if downloaded > _MAX_DOWNLOAD_BYTES:
                        raise ValueError(
                            f"下载实际体积超过 {_MAX_DOWNLOAD_BYTES} 字节上限"
                        )
                    digest.update(chunk)
                    f.write(chunk)
            actual_digest = digest.hexdigest()
            if actual_digest != expected_digest:
                raise ValueError(
                    "SHA-256 校验失败："
                    f"expected={expected_digest}, actual={actual_digest}"
                )
            os.replace(part, dest)
            print(
                f"[fetch_cloudflared] 下载并校验成功（第 {attempt} 次尝试）：{url}"
            )
            return
        except Exception as e:  # noqa: BLE001 - 构建期尽量重试，最后一击才失败
            last = e
            try:
                os.remove(part)
            except FileNotFoundError:
                pass
            print(f"[fetch_cloudflared] 第 {attempt}/{attempts} 次失败：{e}")
            if attempt < attempts:
                time.sleep(attempt * 3)
    raise SystemExit(f"[fetch_cloudflared] 重试 {attempts} 次仍失败：{last}")


def main(argv: list[str]) -> int:
    dest = argv[1] if len(argv) > 1 else "/usr/local/bin/cloudflared"
    arch = cloudflared_arch()
    download(
        release_url(arch),
        dest,
        expected_digest=expected_sha256(arch),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
