"""Prometheus metrics endpoint."""

from __future__ import annotations


def test_metrics_endpoint(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "dg_domains_total" in body
    assert "dg_active_alerts_total" in body
    assert 'dg_circuit_breaker_open{service="rdap"}' in body
    assert 'dg_circuit_breaker_failures{service="vt"}' in body
