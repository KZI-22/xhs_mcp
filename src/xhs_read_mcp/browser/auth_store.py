"""Atomic persistence for Playwright browser storage state."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from xhs_read_mcp.errors import ErrorCode, XhsError


StorageState = dict[str, Any]


def _validate_state(value: Any) -> StorageState:
    if not isinstance(value, dict):
        raise ValueError("storage state must be a JSON object")
    cookies = value.get("cookies", [])
    origins = value.get("origins", [])
    if not isinstance(cookies, list) or not isinstance(origins, list):
        raise ValueError("storage state cookies and origins must be arrays")
    normalized = dict(value)
    normalized["cookies"] = cookies
    normalized["origins"] = origins
    return normalized


def _serialize(state: StorageState) -> bytes:
    normalized = _validate_state(state)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


class AuthStateStore:
    """Serialize access and use same-directory atomic replacement."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self._fingerprint: str | None = None

    async def exists(self) -> bool:
        """Return whether state is persisted without reading sensitive contents."""

        async with self._lock:
            try:
                await asyncio.to_thread(self.path.stat)
            except FileNotFoundError:
                return False
            except OSError as exc:
                raise XhsError(
                    ErrorCode.AUTH_STATE_ERROR,
                    "无法检查本地登录状态。",
                    details={"path": str(self.path), "reason": type(exc).__name__},
                ) from exc
            return True

    async def load(self) -> StorageState | None:
        async with self._lock:
            try:
                payload = await asyncio.to_thread(self.path.read_bytes)
            except FileNotFoundError:
                self._fingerprint = None
                return None
            except OSError as exc:
                raise XhsError(
                    ErrorCode.AUTH_STATE_ERROR,
                    "无法读取本地登录状态。",
                    details={"path": str(self.path), "reason": type(exc).__name__},
                ) from exc

            try:
                state = _validate_state(json.loads(payload.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise XhsError(
                    ErrorCode.AUTH_STATE_ERROR,
                    "本地登录状态文件已损坏，请调用 xhs_logout 清除后重新登录。",
                    details={"path": str(self.path), "reason": type(exc).__name__},
                ) from exc

            self._fingerprint = hashlib.sha256(_serialize(state)).hexdigest()
            return state

    async def save(self, state: StorageState) -> bool:
        """Save state and return whether the file content changed."""

        try:
            payload = _serialize(state)
        except ValueError as exc:
            raise XhsError(
                ErrorCode.AUTH_STATE_ERROR,
                "浏览器返回了无效的登录状态。",
                details={"reason": str(exc)},
            ) from exc

        fingerprint = hashlib.sha256(payload).hexdigest()
        async with self._lock:
            if fingerprint == self._fingerprint and self.path.exists():
                return False
            try:
                await asyncio.to_thread(self._write_atomic, payload)
            except OSError as exc:
                raise XhsError(
                    ErrorCode.AUTH_STATE_ERROR,
                    "无法保存本地登录状态。",
                    details={"path": str(self.path), "reason": type(exc).__name__},
                ) from exc
            self._fingerprint = fingerprint
            return True

    async def delete(self) -> bool:
        """Delete state idempotently and return whether a file existed."""

        async with self._lock:
            try:
                await asyncio.to_thread(self.path.unlink)
            except FileNotFoundError:
                self._fingerprint = None
                return False
            except OSError as exc:
                raise XhsError(
                    ErrorCode.AUTH_STATE_ERROR,
                    "无法删除本地登录状态。",
                    details={"path": str(self.path), "reason": type(exc).__name__},
                ) from exc
            self._fingerprint = None
            return True

    def _write_atomic(self, payload: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary_path, 0o600)
            except OSError:
                pass
            os.replace(temporary_path, self.path)
        finally:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
