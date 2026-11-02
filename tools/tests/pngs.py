"""Tiny valid and forged PNG and zip builders for the preview publisher's tests."""

from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib


def png(width: int = 8, height: int = 8, pad: int = 0) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\x80\x80\x80" * min(width, 8) for _ in range(min(height, 8)))
    body = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + body + b"\0" * pad


def make_zip(files: dict[str, bytes], symlinks: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            if name in symlinks:
                info.external_attr = (0o120777 << 16)
            zf.writestr(info, data)
    return buf.getvalue()


def index(models: dict[str, dict]) -> bytes:
    return json.dumps({"version": 1, "models": models, "examples": {}}).encode()
