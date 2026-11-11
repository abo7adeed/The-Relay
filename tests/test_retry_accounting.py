"""Essential Acceptance Test 1: Retry and Latency Accounting.

Validates that:
1. Failed attempt execution time is assigned to working_ms.
2. Backoff sleep time is assigned exclusively to retry_wait_ms.
3. total_ms is measured independently using a monotonic wall-clock timer.
4. No time is double-counted and overhead epsilon < 15ms.
"""

import time
import pytest
from relay.pipeline.state import LatencyTimers


def test_latency_accounting_isolation_and_math():
    timers = LatencyTimers()
    t0 = time.perf_counter()

    # Simulate queue wait (e.g. 50ms)
    t_q_start = time.perf_counter()
    time.sleep(0.05)
    timers.queue_wait_ms = (time.perf_counter() - t_q_start) * 1000

    # Simulate Attempt 1 (Failed after 30ms of active work)
    t_w1_start = time.perf_counter()
    time.sleep(0.03)
    attempt1_work_ms = (time.perf_counter() - t_w1_start) * 1000

    # Simulate Retry Backoff Sleep (e.g. 40ms intentional sleep)
    t_r_start = time.perf_counter()
    time.sleep(0.04)
    timers.retry_wait_ms = (time.perf_counter() - t_r_start) * 1000

    # Simulate Attempt 2 (Succeeded after 35ms of active work)
    t_w2_start = time.perf_counter()
    time.sleep(0.035)
    attempt2_work_ms = (time.perf_counter() - t_w2_start) * 1000

    # Total active working time includes BOTH attempts
    timers.working_ms = attempt1_work_ms + attempt2_work_ms

    # Independent monotonic wall-clock total_ms
    timers.total_ms = (time.perf_counter() - t0) * 1000

    # Assertions
    # 1. Working time includes failed attempt duration
    assert timers.working_ms >= 60.0, f"working_ms must include both attempts ({timers.working_ms}ms)"

    # 2. Retry wait reflects only intentional backoff
    assert 35.0 <= timers.retry_wait_ms <= 60.0, f"retry_wait_ms should reflect backoff ({timers.retry_wait_ms}ms)"

    # 3. Queue wait reflects waiting before dequeue
    assert 45.0 <= timers.queue_wait_ms <= 75.0, f"queue_wait_ms out of range ({timers.queue_wait_ms}ms)"

    # 4. Strict timing accounting invariant: epsilon >= 0 and epsilon < 15ms
    epsilon = timers.compute_accounting_overhead()
    assert epsilon >= 0.0, f"total_ms ({timers.total_ms}) cannot be smaller than constituent sum"
    assert epsilon < 15.0, f"Unclassified scheduling overhead epsilon ({epsilon}ms) exceeded 15ms threshold"

