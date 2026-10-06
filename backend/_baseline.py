"""Row-count baseline for the live database. Read-only; safe to rerun."""
import json
import sqlite3
import sys
from pathlib import Path

DB = Path(r"F:\info_1\backend\data\app.db")
TABLES = [
    "users", "roles", "projects", "project_members", "documents", "document_chunks",
    "risks", "blockers", "tasks", "project_insights", "ai_runs", "audit_logs",
    "system_settings", "generated_documents", "health_snapshots",
    "assistant_conversations", "assistant_messages",
]


def snapshot() -> dict:
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        have = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        out = {"__integrity__": con.execute("PRAGMA integrity_check").fetchone()[0]}
        for t in TABLES:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] if t in have else None
        out["__tables__"] = sorted(have)
        return out
    finally:
        con.close()


if __name__ == "__main__":
    snap = snapshot()
    mode = sys.argv[1] if len(sys.argv) > 1 else "print"
    if mode == "save":
        Path(sys.argv[2]).write_text(json.dumps(snap, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(snap, indent=2, sort_keys=True))