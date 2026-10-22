"""
Lightweight in memory cache for DB queries and calculations
For use in kweb2.py for large or long execution queries
Returns the value from the cache if it exists, otherwise None
An entry is stale, and returns None, when:
  - a last_updated timestamp (the latest repo sync) is newer than the entry, or
  - the entry is older than max_age_seconds, because cached statuses and
    days_ago values are relative to the time they were computed
"""
from datetime import datetime, timedelta


class RequestCache:
    """In Memory cache class"""
    def __init__(self, max_age_seconds=3600):
        self.cache = {}
        self.max_age = timedelta(seconds=max_age_seconds)

    @staticmethod
    def key(name, **params):
        """Build a cache key that includes the query scope (repo_id, org_key, server).

        Unset params are dropped, so key("repos", org_key=None) == key("repos").
        """
        scope = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v)
        return f"{name}:{scope}"

    def get(self, key, last_updated):

        results = self.cache.get(key)
        if results is None:
            return None

        if self._is_stale(results["cache_time"], last_updated):
            self.cache.pop(key, None)
            return None

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
        self.cache[key] = entry

    def clear(self):
        self.cache.clear()
