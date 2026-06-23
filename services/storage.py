from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

try:  # pragma: no cover
    from ..models.records import SCHEMA_VERSION
except ImportError:  # pragma: no cover
    from models.records import SCHEMA_VERSION


class AtomicJSONStorage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def load(self, default: dict[str, Any] | None = None) -> dict[str, Any]:
        default = default or {"schema_version": SCHEMA_VERSION}
        async with self._get_lock():
            if not self.path.exists() or self.path.stat().st_size == 0:
                return dict(default)
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    return dict(default)
                data.setdefault("schema_version", SCHEMA_VERSION)
                return data
            except (OSError, json.JSONDecodeError):
                await self._backup_corrupt_locked()
                return dict(default)

    async def save(self, data: dict[str, Any]) -> None:
        async with self._get_lock():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = dict(data)
            payload.setdefault("schema_version", SCHEMA_VERSION)
            tmp_path = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.{int(time.time() * 1000)}.tmp"
            )
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_path, self.path)

    async def _backup_corrupt_locked(self) -> None:
        if not self.path.exists():
            return
        backup = self.path.with_suffix(
            self.path.suffix + f".corrupt.{int(time.time())}"
        )
        try:
            os.replace(self.path, backup)
        except OSError:
            pass


class PluginDataStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.processed_requests = AtomicJSONStorage(
            self.data_dir / "processed_requests.json"
        )
        self.pending_requests = AtomicJSONStorage(self.data_dir / "pending_requests.json")
        self.associations = AtomicJSONStorage(
            self.data_dir / "user_group_associations.json"
        )
        self.context_cache = AtomicJSONStorage(self.data_dir / "context_cache.json")
        self.rate_limits = AtomicJSONStorage(self.data_dir / "rate_limits.json")
