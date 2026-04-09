"""Tracker-specific tests with tmp_path for SQLite isolation."""
import pytest
from unillm.tracker import UsageTracker


def test_accumulates(tmp_path):
    t = UsageTracker(db_path=tmp_path / "test.db")
    t.record("qwen/qwen-turbo", {"prompt_tokens": 100, "completion_tokens": 50,
                                  "total_tokens": 150, "cost_usd": 0.000025})
    t.record("qwen/qwen-turbo", {"prompt_tokens": 200, "completion_tokens": 100,
                                  "total_tokens": 300, "cost_usd": 0.000050})
    assert t.total_calls == 2
    assert t.total_tokens == 450
    assert abs(t.total_cost_usd - 0.000075) < 1e-9


def test_persists_across_restarts(tmp_path):
    db = tmp_path / "test.db"
    t1 = UsageTracker(db_path=db)
    t1.record("openai/gpt-4o", {"prompt_tokens": 10, "completion_tokens": 5,
                                  "total_tokens": 15, "cost_usd": 0.001})
    del t1  # simulate process exit

    t2 = UsageTracker(db_path=db)  # new process — loads from DB
    assert t2.total_calls == 1
    assert t2.total_tokens == 15
    assert abs(t2.total_cost_usd - 0.001) < 1e-9


def test_reset_session_keeps_db(tmp_path):
    t = UsageTracker(db_path=tmp_path / "test.db")
    t.record("openai/gpt-4o", {"cost_usd": 0.01, "total_tokens": 500})
    t.reset_session()           # clears memory, NOT the DB
    assert t.total_calls == 0
    assert len(t.history()) == 1  # row still in DB


def test_wipe_clears_db(tmp_path):
    t = UsageTracker(db_path=tmp_path / "test.db")
    t.record("openai/gpt-4o", {"cost_usd": 0.01, "total_tokens": 500})
    t.wipe()
    assert t.total_calls == 0
    assert len(t.history()) == 0


def test_history_filter(tmp_path):
    t = UsageTracker(db_path=tmp_path / "test.db")
    t.record("qwen/qwen-turbo", {"total_tokens": 10, "cost_usd": 0.001})
    t.record("glm/glm-4",       {"total_tokens": 20, "cost_usd": 0.002})
    rows = t.history(model="glm/glm-4")
    assert len(rows) == 1
    assert rows[0]["model"] == "glm/glm-4"


def test_daily_cost(tmp_path):
    t = UsageTracker(db_path=tmp_path / "test.db")
    t.record("qwen/qwen-turbo", {"total_tokens": 100, "cost_usd": 0.005})
    days = t.daily_cost()
    assert len(days) == 1
    assert days[0]["calls"] == 1


def test_summary(tmp_path):
    t = UsageTracker(db_path=tmp_path / "test.db")
    t.record("glm/glm-4", {"prompt_tokens": 50, "completion_tokens": 30,
                             "total_tokens": 80, "cost_usd": 0.000008})
    s = t.summary(detailed=True)
    assert "glm/glm-4" in s
    assert "UniLLM" in s
    assert str(tmp_path / "test.db") in s
