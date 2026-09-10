"""Deterministic Verification Test Suite for InMemoryCache.

Validates:
1. Basic operations (set, get, delete, size)
2. Passive TTL expiration
3. Active cleanup scavenger (cleanup_expired method purges memory leak)
4. Thread-safe concurrency under multithreaded contention
5. Isolation of mutable objects (deepcopy on store and retrieve)
"""

from __future__ import annotations

import threading
import time
from typing import Type


def run_tests_on_cache_class(cache_class: Type) -> dict[str, bool]:
    """Runs all 5 test cases on the provided cache class and returns per-test pass/fail status."""
    results = {}

    # Test 1: Basic Operations
    try:
        c = cache_class()
        c.set("k1", "v1")
        assert c.get("k1") == "v1"
        assert c.size() == 1
        assert c.delete("k1") is True
        assert c.get("k1") is None
        assert c.size() == 0
        results["test_basic_operations"] = True
    except Exception:
        results["test_basic_operations"] = False

    # Test 2: Passive TTL Expiration
    try:
        c = cache_class()
        c.set("temp", 100, ttl=0.1)
        assert c.get("temp") == 100
        time.sleep(0.15)
        assert c.get("temp") is None
        results["test_ttl_passive_expiration"] = True
    except Exception:
        results["test_ttl_passive_expiration"] = False

    # Test 3: Active Cleanup Scavenger (Memory Leak Prevention)
    try:
        c = cache_class()
        assert hasattr(c, "cleanup_expired"), "Cache must have cleanup_expired() method!"
        c.set("leak1", "val", ttl=0.05)
        c.set("leak2", "val", ttl=0.05)
        c.set("persist", "val", ttl=None)
        assert c.size() == 3
        time.sleep(0.1)
        # Without calling get(), active cleanup should remove leak1 and leak2
        c.cleanup_expired()
        assert c.size() == 1, f"Expected size 1 after cleanup_expired, got {c.size()}"
        assert c.get("persist") == "val"
        results["test_active_cleanup_scavenger"] = True
    except Exception:
        results["test_active_cleanup_scavenger"] = False

    # Test 4: Concurrency Multithreading Safety
    try:
        c = cache_class()
        errors = []

        def worker(w_id: int):
            try:
                for i in range(100):
                    # Concurrent read/write on shared keys with expiring TTL
                    c.set("shared_hot_key", i, ttl=0.001)
                    time.sleep(0.0005)
                    _ = c.get("shared_hot_key")
                    c.set(f"key_{w_id}_{i}", i, ttl=0.05)
                    _ = c.get(f"key_{w_id}_{i}")
                    if i % 10 == 0 and hasattr(c, "cleanup_expired"):
                        c.cleanup_expired()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Encountered concurrency errors: {errors}"
        results["test_concurrent_multithreading_safety"] = True
    except Exception:
        results["test_concurrent_multithreading_safety"] = False

    # Test 5: Mutable Object Isolation (Deep Copy Defense)
    try:
        c = cache_class()
        data = {"count": 1, "items": [10, 20]}
        c.set("shared", data)

        # External mutation should NOT affect cache
        data["count"] = 999
        data["items"].append(30)
        cached_val = c.get("shared")
        assert cached_val["count"] == 1, "Cached value was corrupted by external mutation!"
        assert len(cached_val["items"]) == 2, "Cached list was mutated externally!"

        # Internal return mutation should NOT corrupt cache
        cached_val["count"] = 555
        fresh_val = c.get("shared")
        assert fresh_val["count"] == 1, "Second get() returned mutated cached reference!"
        results["test_mutable_object_isolation"] = True
    except Exception:
        results["test_mutable_object_isolation"] = False

    return results


if __name__ == "__main__":
    from buggy_cache import InMemoryCache

    res = run_tests_on_cache_class(InMemoryCache)
    print("Test Results on Original Buggy Cache:")
    for name, passed in res.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {name}: {status}")
    total_passed = sum(1 for v in res.values() if v)
    print(f"\nTotal: {total_passed}/5 passed ({total_passed/5*100:.0f}%)")
