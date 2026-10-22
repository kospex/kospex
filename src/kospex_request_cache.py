"""
Lightweight in memory cache for DB queries and calculations
For use in kweb2.py for large or long execution queries
Returns the value from the cache if it exists, otherwise None
An entry is stale, and returns None, when:
  - a last_updated timestamp (the latest repo sync) is newer than the entry, or
  - the entry is older than max_age_seconds, because cached statuses and
    days_ago values are relative to the time they were computed
"""
import threading
from collections import OrderedDict
from datetime import datetime, timedelta


class RequestCache:
    """In Memory cache class.

    Bounded to max_entries, least recently used evicted first. The scope in a
    key comes from the request URL, so without a bound each distinct value
    would hold an entry for the life of the process (CWE-770).

    kweb handlers run on worker threads, so reads and writes take a lock:
    an eviction between get()'s lookup and move_to_end() would raise KeyError.
    """
    def __init__(self, max_age_seconds=3600, max_entries=256):
        self.cache = OrderedDict()
        self.max_age = timedelta(seconds=max_age_seconds)
        self.max_entries = max_entries
        self._lock = threading.Lock()

    @staticmethod
    def key(name, **params):
        """Build a cache key that includes the query scope (repo_id, org_key, server).

        Unset params are dropped, so key("repos", org_key=None) == key("repos").
        """
        scope = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v)
        return f"{name}:{scope}"

    def get(self, key, last_updated):

        with self._lock:
            results = self.cache.get(key)
            if results is None:
                return None

            if self._is_stale(results["cache_time"], last_updated):
                self.cache.pop(key, None)
                return None

            self.cache.move_to_end(key)
            return results["data"]

    def _is_stale(self, cache_time, last_updated):
        if datetime.now().astimezone() - cache_time > self.max_age:
            return True

        if last_updated:
            try:
                updated = datetime.fromisoformat(last_updated)
            except (TypeError, ValueError):
                return True
            if updated.tzinfo is None:
                updated = updated.astimezone()
            if updated > cache_time:
                return True

        return False

    def set(self, key, data, cache_time=None):
        """Store data. Pass cache_time as when the query *started*, so a sync
        that lands while it runs still invalidates the result."""
        entry = {
            'data': data,
            'cache_time': cache_time or datetime.now().astimezone(),
        }
        with self._lock:
            self.cache[key] = entry
            self.cache.move_to_end(key)
            while len(self.cache) > self.max_entries:
                self.cache.popitem(last=False)

    def clear(self):
        with self._lock:
            self.cache.clear()
