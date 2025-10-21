"""
Lightweight in memory cache for DB queries and calculations
For use in kweb2.py for large or long execution queries
Returns the value from the cache if it exists, otherwise None
if a last_updated timestamp is provided, it will be compared to the cache_time
if the cache_time is older than the last_updated timestamp, the result will return None
"""
from datetime import datetime

class RequestCache:
    """In Memory cache class"""
    def __init__(self):
        self.cache = {}

    def get(self, key, last_updated):

        if results := self.cache.get(key):
            if last_updated and last_updated > results['cache_time']:
                return None
            return results['data']

        return None

    def set(self, key, data):
        entry = {
            'data': data,
            'cache_time': datetime.now().astimezone().strftime('%Y-%m-%dT%H:%M:%S%z')
        }
        self.cache[key] = entry

    def clear(self):
        self.cache.clear()
