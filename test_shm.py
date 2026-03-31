#!/usr/bin/env python3
"""
Tests for shm_redirect.so -- verifies that Python multiprocessing primitives
work when /dev/shm is unavailable and LD_PRELOAD redirects semaphores to /tmp/shm.

Run with:
    LD_PRELOAD=./shm_redirect.so python3 test_shm.py

Or via make:
    make test
"""

import multiprocessing
import os
import sys
import time

# Ensure LD_PRELOAD is set (unless on macOS where this won't apply)
if sys.platform == "linux" and "shm_redirect.so" not in os.environ.get("LD_PRELOAD", ""):
    # Re-exec with LD_PRELOAD set
    so_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shm_redirect.so")
    if os.path.exists(so_path):
        os.environ["LD_PRELOAD"] = so_path
        os.execvp(sys.executable, [sys.executable] + sys.argv)


def _square(x):
    """Worker function for Pool test."""
    return x * x


def _queue_producer(q, values):
    """Put values into a queue from a child process."""
    for v in values:
        q.put(v)


def test_lock():
    """Test that multiprocessing.Lock can be created and used."""
    lock = multiprocessing.Lock()
    lock.acquire()
    lock.release()
    print("  PASS: Lock")


def test_semaphore():
    """Test that multiprocessing.Semaphore works."""
    sem = multiprocessing.Semaphore(3)
    sem.acquire()
    sem.acquire()
    sem.release()
    sem.release()
    print("  PASS: Semaphore")


def test_queue():
    """Test that multiprocessing.Queue works across processes."""
    q = multiprocessing.Queue()
    values = [1, 2, 3, 4, 5]
    p = multiprocessing.Process(target=_queue_producer, args=(q, values))
    p.start()
    p.join(timeout=10)

    results = []
    while not q.empty():
        results.append(q.get_nowait())

    assert results == values, f"Expected {values}, got {results}"
    print("  PASS: Queue")


def test_pool():
    """Test that multiprocessing.Pool can map work to child processes."""
    with multiprocessing.Pool(2) as pool:
        results = pool.map(_square, [1, 2, 3, 4])

    assert results == [1, 4, 9, 16], f"Expected [1, 4, 9, 16], got {results}"
    print("  PASS: Pool")


def test_value_and_array():
    """Test shared Value and Array between processes."""
    val = multiprocessing.Value("i", 0)
    arr = multiprocessing.Array("i", [0, 0, 0])

    def _worker(v, a):
        v.value = 42
        for i in range(len(a)):
            a[i] = i + 1

    p = multiprocessing.Process(target=_worker, args=(val, arr))
    p.start()
    p.join(timeout=10)

    assert val.value == 42, f"Expected 42, got {val.value}"
    assert list(arr) == [1, 2, 3], f"Expected [1, 2, 3], got {list(arr)}"
    print("  PASS: Value and Array")


def test_shared_memory():
    """Test multiprocessing.shared_memory (Python 3.8+)."""
    try:
        from multiprocessing import shared_memory
    except ImportError:
        print("  SKIP: shared_memory (Python < 3.8)")
        return

    shm = shared_memory.SharedMemory(create=True, size=1024)
    try:
        shm.buf[0:5] = b"hello"
        assert bytes(shm.buf[0:5]) == b"hello"
        print("  PASS: shared_memory")
    finally:
        shm.close()
        shm.unlink()


def main():
    # Use 'fork' start method to match Lambda's behavior
    if sys.platform == "linux":
        multiprocessing.set_start_method("fork", force=True)

    print("Testing multiprocessing with shm_redirect shim...\n")

    tests = [
        test_lock,
        test_semaphore,
        test_queue,
        test_pool,
        test_value_and_array,
        test_shared_memory,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
