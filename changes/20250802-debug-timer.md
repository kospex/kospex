# Debug Timer Options for Kospex

This document outlines various timer decorator options for debugging function performance in the Kospex codebase.

## 1. Simple Timer Decorator

Basic timing with print output:

```python
import time
import functools

def timer(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        end = time.perf_counter()
        print(f"{func.__name__} took {end - start:.6f} seconds")
        return result
    return wrapper

# Usage
@timer
def slow_function():
    time.sleep(1)
```

## 2. Configurable Timer with Logging ⭐ (Implemented)

Best for production debugging with proper logging integration:

```python
import time
import functools
import logging

def timer(logger=None, level=logging.INFO):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            result = func(*args, **kwargs)
            end = time.perf_counter()
            duration = end - start
            
            message = f"{func.__name__} executed in {duration:.6f}s"
            if logger:
                logger.log(level, message)
            else:
                print(message)
            return result
        return wrapper
    return decorator

# Usage
@timer(logger=logging.getLogger(__name__))
def my_function():
    pass
```

## 3. Context Manager Timer ⭐ (Implemented as KospexTimer)

For timing code blocks rather than functions. **This is the actual implementation in kospex_utils.py:**

```python
class KospexTimer:
    """Simple context manager for timing operations."""
    
    def __init__(self, description="operation"):
        self.description = description
        self.elapsed = None
        
    def __enter__(self):
        self.start = time.time()
        return self
        
    def __exit__(self, *args):
        self.elapsed = time.time() - self.start
        
    def __str__(self):
        return f"{self.description}: {self.elapsed:.3f}s" if self.elapsed else self.description

# Usage in Kospex
from kospex_utils import KospexTimer

with KospexTimer("database query") as timer:
    commits = kospex.kospex_query.commits(repo_id=repo_id)

print(timer)  # Output: "database query: 1.234s"
log.info(f"Loaded {len(commits)} commits in {timer.elapsed:.3f}s")
```

## 4. Advanced Timer with Statistics

Collects timing statistics across multiple calls:

```python
import time
import functools
from collections import defaultdict, deque

class TimerStats:
    def __init__(self):
        self.calls = defaultdict(list)
        self.recent_calls = defaultdict(lambda: deque(maxlen=10))
    
    def add_timing(self, func_name, duration):
        self.calls[func_name].append(duration)
        self.recent_calls[func_name].append(duration)
    
    def get_stats(self, func_name):
        times = self.calls[func_name]
        if not times:
            return None
        return {
            'count': len(times),
            'total': sum(times),
            'avg': sum(times) / len(times),
            'min': min(times),
            'max': max(times),
            'recent_avg': sum(self.recent_calls[func_name]) / len(self.recent_calls[func_name])
        }

timer_stats = TimerStats()

def timer_with_stats(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        end = time.perf_counter()
        duration = end - start
        
        timer_stats.add_timing(func.__name__, duration)
        print(f"{func.__name__} took {duration:.6f}s")
        return result
    return wrapper
```

## 5. Built-in cProfile Integration

For detailed function profiling:

```python
import cProfile
import functools

def profile(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        pr = cProfile.Profile()
        pr.enable()
        result = func(*args, **kwargs)
        pr.disable()
        pr.print_stats(sort='cumulative')
        return result
    return wrapper
```

## 6. Third-Party Libraries

### line_profiler
```bash
pip install line_profiler
```

```python
# Add @profile decorator and run with:
# kernprof -l -v script.py
@profile
def my_function():
    pass
```

### memory_profiler
```bash
pip install memory_profiler
```

```python
from memory_profiler import profile

@profile
def my_function():
    pass
```

## Usage Recommendations for Kospex

1. **Option 2** (Configurable Timer) - Best for general debugging, integrates with existing logging
2. **Option 3** (Context Manager) - Good for timing specific code blocks like database operations
3. **Option 4** (Statistics) - Useful for performance analysis of frequently called functions
4. **Option 5** (cProfile) - For deep performance analysis when needed

## Example Usage in Kospex

### Current Implementation (KospexTimer)
```python
import kospex_utils as KospexUtils

# Basic timing with context manager
with KospexUtils.KospexTimer("repository sync") as timer:
    kospex.sync_repo(repo_path)
log.info(timer)  # "repository sync: 2.456s"

# Multiple operations
with KospexUtils.KospexTimer("loading commits") as timer:
    commits = kospex.kospex_query.commits(repo_id=repo_id)
print(f"Loaded {len(commits)} commits in {timer.elapsed:.3f}s")

# File operations timing
with KospexUtils.KospexTimer("metadata files") as timer:
    metadata_files = kospex.file_metadata(file_path)
log.info(f"Found {len(metadata_files)} files: {timer}")
```

### Future Decorator Implementation
```python
from kospex_utils import timer
import logging

logger = logging.getLogger(__name__)

@timer(logger=logger, level=logging.DEBUG)
def sync_repository(repo_path):
    # Repository sync logic
    pass

@timer()  # Uses print for quick debugging
def analyze_commits():
    # Commit analysis logic
    pass
```