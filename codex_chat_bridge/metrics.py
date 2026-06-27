from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Request counts by method, path, and status code
requests_total = Counter(
    "bridge_requests_total", "Total requests by method, path, status",
    ["method", "path", "status"],
)

# In-flight requests gauge
requests_in_flight = Gauge(
    "bridge_requests_in_flight", "Currently in-flight requests",
)

# Request duration histogram (milliseconds)
request_duration_ms = Histogram(
    "bridge_request_duration_ms", "Request duration in ms",
    ["method", "path"],
    buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000),
)

# Upstream errors by model and upstream status code
upstream_errors_total = Counter(
    "bridge_upstream_errors_total", "Upstream errors by model and upstream status code",
    ["model", "status_code"],
)

# Concurrency semaphore usage
concurrency_usage = Gauge(
    "bridge_concurrency_usage", "Current concurrent request count (from semaphore)",
)
