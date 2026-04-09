"""
Cost & usage tracker — with SQLite persistence.

Data survives process restarts. Every call is written to a local SQLite DB.
On startup, historical totals are loaded back into memory automatically.

Default DB path: ~/.unillm/usage.db
Override:  export UNILLM_DB=./my_project.db
           or UsageTracker(db_path="./my_project.db")

Usage:
    import unillm

    resp = await unillm.completion("qwen/qwen-turbo", messages)

    print(unillm.tracker.summary(detailed=True))
    print(unillm.tracker.total_cost_usd)          # lifetime total, across restarts

    # History queries
    rows = unillm.tracker.history(limit=20)
    rows = unillm.tracker.history(model="qwen/qwen-turbo")
    rows = unillm.tracker.history(since="2026-04-01")

    unillm.tracker.reset_session()   # clear in-memory only (DB untouched)
    unillm.tracker.wipe()            # !! delete ALL history from DB
"""
from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Per-model aggregate (in-memory view, rebuilt from DB on startup)
# ---------------------------------------------------------------------------
@dataclass
class ModelStats:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    errors: int = 0
    total_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.calls if self.calls else 0.0


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------
class UsageTracker:
    """
    Thread-safe usage + cost tracker backed by SQLite.

    Two tables:
      calls  — one row per API call (full detail, queryable)
      totals — running per-model aggregates (fast reads)
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = os.getenv("UNILLM_DB") or Path.home() / ".unillm" / "usage.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._session_start = datetime.now(timezone.utc)
        self._models: dict[str, ModelStats] = {}

        self._init_db()
        self._load_totals()   # restore aggregates from previous runs

    # ── DB setup ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")   # safe for concurrent writes
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS calls (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts                TEXT    NOT NULL,
                    model             TEXT    NOT NULL,
                    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens      INTEGER NOT NULL DEFAULT 0,
                    cost_usd          REAL    NOT NULL DEFAULT 0,
                    latency_ms        REAL    NOT NULL DEFAULT 0,
                    error             INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS totals (
                    model             TEXT PRIMARY KEY,
                    calls             INTEGER NOT NULL DEFAULT 0,
                    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens      INTEGER NOT NULL DEFAULT 0,
                    cost_usd          REAL    NOT NULL DEFAULT 0,
                    errors            INTEGER NOT NULL DEFAULT 0,
                    total_latency_ms  REAL    NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_calls_ts    ON calls(ts);
                CREATE INDEX IF NOT EXISTS idx_calls_model ON calls(model);
            """)

    def _load_totals(self) -> None:
        """Rebuild in-memory _models dict from the totals table."""
        with self._connect() as con:
            for row in con.execute("SELECT * FROM totals"):
                self._models[row["model"]] = ModelStats(
                    calls=row["calls"],
                    prompt_tokens=row["prompt_tokens"],
                    completion_tokens=row["completion_tokens"],
                    total_tokens=row["total_tokens"],
                    cost_usd=row["cost_usd"],
                    errors=row["errors"],
                    total_latency_ms=row["total_latency_ms"],
                )

    # ── Record a call ────────────────────────────────────────────────────────

    def record(
        self,
        model: str,
        usage: dict,
        latency_ms: float = 0.0,
        error: bool = False,
    ) -> None:
        pt   = usage.get("prompt_tokens", 0)
        ct   = usage.get("completion_tokens", 0)
        tt   = usage.get("total_tokens", 0) or (pt + ct)
        cost = usage.get("cost_usd", 0.0)
        ts   = datetime.now(timezone.utc).isoformat()

        with self._lock:
            # Update in-memory aggregate
            if model not in self._models:
                self._models[model] = ModelStats()
            s = self._models[model]
            s.calls              += 1
            s.prompt_tokens      += pt
            s.completion_tokens  += ct
            s.total_tokens       += tt
            s.cost_usd           += cost
            s.total_latency_ms   += latency_ms
            if error:
                s.errors += 1

            # Persist both tables atomically
            with self._connect() as con:
                con.execute(
                    """INSERT INTO calls
                       (ts, model, prompt_tokens, completion_tokens,
                        total_tokens, cost_usd, latency_ms, error)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (ts, model, pt, ct, tt, cost, latency_ms, int(error)),
                )
                con.execute(
                    """INSERT INTO totals
                       (model, calls, prompt_tokens, completion_tokens,
                        total_tokens, cost_usd, errors, total_latency_ms)
                       VALUES (?,1,?,?,?,?,?,?)
                       ON CONFLICT(model) DO UPDATE SET
                           calls              = calls              + 1,
                           prompt_tokens      = prompt_tokens      + excluded.prompt_tokens,
                           completion_tokens  = completion_tokens  + excluded.completion_tokens,
                           total_tokens       = total_tokens       + excluded.total_tokens,
                           cost_usd           = cost_usd           + excluded.cost_usd,
                           errors             = errors             + excluded.errors,
                           total_latency_ms   = total_latency_ms   + excluded.total_latency_ms
                    """,
                    (model, pt, ct, tt, cost, int(error), latency_ms),
                )

    # ── Aggregates (lifetime, from in-memory cache) ──────────────────────────

    @property
    def total_cost_usd(self) -> float:
        with self._lock:
            return sum(s.cost_usd for s in self._models.values())

    @property
    def total_tokens(self) -> int:
        with self._lock:
            return sum(s.total_tokens for s in self._models.values())

    @property
    def total_calls(self) -> int:
        with self._lock:
            return sum(s.calls for s in self._models.values())

    def per_model(self) -> dict[str, ModelStats]:
        with self._lock:
            return dict(self._models)

    # ── History queries (directly from DB) ──────────────────────────────────

    def history(
        self,
        *,
        limit: int = 50,
        model: Optional[str] = None,
        since: Optional[str] = None,   # e.g. "2026-04-01"
        until: Optional[str] = None,
        errors_only: bool = False,
    ) -> list[dict]:
        """
        Query the raw calls table.

        Examples:
            tracker.history(limit=10)
            tracker.history(model="qwen/qwen-turbo", since="2026-04-01")
            tracker.history(errors_only=True)
        """
        clauses, params = [], []
        if model:
            clauses.append("model = ?");    params.append(model)
        if since:
            clauses.append("ts >= ?");      params.append(since)
        if until:
            clauses.append("ts <= ?");      params.append(until)
        if errors_only:
            clauses.append("error = 1")

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM calls {where} ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        with self._connect() as con:
            return [dict(row) for row in con.execute(sql, params)]

    def daily_cost(self) -> list[dict]:
        """Return cost grouped by day, newest first."""
        with self._connect() as con:
            rows = con.execute("""
                SELECT substr(ts,1,10) AS day,
                       COUNT(*)          AS calls,
                       SUM(total_tokens) AS tokens,
                       SUM(cost_usd)     AS cost_usd
                FROM calls
                GROUP BY day
                ORDER BY day DESC
            """).fetchall()
        return [dict(r) for r in rows]

    # ── Resets ───────────────────────────────────────────────────────────────

    def reset_session(self) -> None:
        """Clear in-memory counters only. DB history is preserved."""
        with self._lock:
            self._models = {}
            self._session_start = datetime.now(timezone.utc)

    def wipe(self) -> None:
        """Delete ALL history from the database and reset memory. Irreversible."""
        with self._lock:
            self._models = {}
            self._session_start = datetime.now(timezone.utc)
            with self._connect() as con:
                con.executescript("DELETE FROM calls; DELETE FROM totals;")

    # ── Summary ──────────────────────────────────────────────────────────────

    def summary(self, *, detailed: bool = False) -> str:
        lines = [
            "╔══════════════════════════════════════════════════╗",
            "║              UniLLM Usage Summary                 ║",
            "╚══════════════════════════════════════════════════╝",
            f"  DB            : {self._db_path}",
            f"  Session start : {self._session_start.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"  Total calls   : {self.total_calls}  (lifetime)",
            f"  Total tokens  : {self.total_tokens:,}",
            f"  Total cost    : ${self.total_cost_usd:.6f}",
        ]
        if detailed:
            lines.append("")
            lines.append("  Per-model breakdown (lifetime):")
            lines.append(
                f"  {'Model':<35} {'Calls':>6} {'Tokens':>10} "
                f"{'Cost ($)':>12} {'Avg ms':>8} {'Errors':>7}"
            )
            lines.append("  " + "─" * 83)
            with self._lock:
                for name, s in sorted(self._models.items()):
                    lines.append(
                        f"  {name:<35} {s.calls:>6} {s.total_tokens:>10,}"
                        f"  {s.cost_usd:>10.6f}   {s.avg_latency_ms:>6.0f}"
                        f"  {s.errors:>6}"
                    )

            days = self.daily_cost()
            if days:
                lines.append("")
                lines.append("  Daily cost (last 7 days):")
                lines.append(
                    f"  {'Date':<12} {'Calls':>6} {'Tokens':>10} {'Cost ($)':>12}"
                )
                lines.append("  " + "─" * 44)
                for d in days[:7]:
                    lines.append(
                        f"  {d['day']:<12} {d['calls']:>6} "
                        f"{d['tokens']:>10,}  {d['cost_usd']:>10.6f}"
                    )
        return "\n".join(lines)

    @property
    def db_path(self) -> Path:
        return self._db_path


# ---------------------------------------------------------------------------
# Module-level singleton — uses ~/.unillm/usage.db by default
# ---------------------------------------------------------------------------
tracker = UsageTracker()
