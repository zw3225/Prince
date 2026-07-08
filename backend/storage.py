from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from .models import SearchResult
from .pipeline import run_trend_search


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STORE_PATH = DATA_DIR / "trend_searches.json"
DATA_SCHEMA_VERSION = 29


class ResultStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._results: dict[str, dict] = {}
        self._latest_id: str | None = None
        self._load()

    def create(self, query: str, markets: list[str] | None = None, window_days: int = 30) -> dict:
        result = run_trend_search(query=query, markets=markets, window_days=window_days)
        payload = result.to_dict()
        with self._lock:
            self._results[result.search.id] = payload
            self._latest_id = result.search.id
            self._save()
        return payload

    def get(self, search_id: str | None = None) -> dict | None:
        with self._lock:
            resolved = search_id or self._latest_id
            if resolved is None:
                return None
            return self._results.get(resolved)

    def latest_or_seed(self) -> dict:
        existing = self.get()
        if existing:
            return existing
        return self.create("便携榨汁机 健身女生", ["US", "UK", "CA", "AU"], 30)

    def _load(self) -> None:
        if not STORE_PATH.exists():
            return
        try:
            payload = json.loads(STORE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if payload.get("schema_version") != DATA_SCHEMA_VERSION:
            return
        self._results = payload.get("results", {})
        self._latest_id = payload.get("latest_id")

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STORE_PATH.write_text(
            json.dumps(
                {"schema_version": DATA_SCHEMA_VERSION, "latest_id": self._latest_id, "results": self._results},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


store = ResultStore()
