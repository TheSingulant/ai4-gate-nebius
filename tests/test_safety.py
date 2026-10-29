import json

import pytest

from gate_demo.safety import BudgetExceeded, DailyBudget, SlidingRateLimiter, redact_bytes, redact_text


def test_ip_and_global_rate_limits():
    limiter = SlidingRateLimiter(2, 3, window=60)
    limiter.check("10.0.0.1", now=0)
    limiter.check("10.0.0.1", now=1)
    with pytest.raises(Exception) as ip_limited:
        limiter.check("10.0.0.1", now=2)
    assert ip_limited.value.code == "RATE_LIMIT" and ip_limited.value.scope == "ip"
    limiter.check("10.0.0.2", now=3)
    with pytest.raises(Exception) as global_limited:
        limiter.check("10.0.0.3", now=4)
    assert global_limited.value.scope == "global"
    limiter.check("10.0.0.1", now=61)


def test_daily_request_and_token_budget():
    budget = DailyBudget(1, 10, ":memory:", agent_run_cap=1)
    budget.before_request()
    with pytest.raises(BudgetExceeded):
        budget.before_request()
    budget.charge_tokens(10)
    fresh = DailyBudget(5, 10, ":memory:")
    fresh.before_request()
    fresh.charge_tokens(10)
    with pytest.raises(BudgetExceeded):
        fresh.before_request()
    runs = DailyBudget(5, 100, ":memory:", agent_run_cap=1)
    runs.before_agent_run()
    with pytest.raises(BudgetExceeded):
        runs.before_agent_run()


def test_redaction_removes_the_key():
    secret = "nf-test-sentinel-9f3a7c"
    assert secret not in redact_text(f"bearer {secret}", [secret])
    body = redact_bytes(json.dumps({"note": secret}).encode(), [secret])
    assert secret.encode() not in body
    assert b"[REDACTED]" in body
