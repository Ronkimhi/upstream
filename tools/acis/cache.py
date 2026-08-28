"""JSON file-based cache with TTL for API responses."""
import json
import os
from datetime import datetime, timedelta


class FileCache:
    def __init__(self, cache_dir, validity_days=7):
        self.cache_dir = cache_dir
        self.validity_days = validity_days
        self.stats = {"hits": 0, "misses": 0}
        os.makedirs(cache_dir, exist_ok=True)

    def _cache_path(self, ticker, data_type):
        safe_ticker = ticker.replace("/", "_").replace(".", "_")
        return os.path.join(self.cache_dir, "{0}_{1}.json".format(safe_ticker, data_type))

    def get(self, ticker, data_type):
        data, _ = self.get_with_meta(ticker, data_type)
        return data

    def get_with_meta(self, ticker, data_type):
        """Return (data, cached_at_iso). F3 (2026-08-28): callers report the
        ORIGINAL fetch time on a cache hit, never the time of the lookup."""
        path = self._cache_path(ticker, data_type)
        if not os.path.exists(path):
            self.stats["misses"] += 1
            return None, None
        with open(path, "r") as f:
            cached = json.load(f)
        cached_at = datetime.fromisoformat(cached["cached_at"])
        if datetime.now() - cached_at > timedelta(days=self.validity_days):
            self.stats["misses"] += 1
            return None, None
        self.stats["hits"] += 1
        return cached["data"], cached["cached_at"]

    def set(self, ticker, data_type, data):
        path = self._cache_path(ticker, data_type)
        with open(path, "w") as f:
            json.dump({"cached_at": datetime.now().isoformat(), "data": data}, f)
