"""裁剪演示库，让部署包能传得动。

原始 miis.db 有 220MB：分割掩膜是未压缩的 uint8 体数据（单条 4.2MB），
再加上 25 条版本快照，光这两张表就 120MB 以上。演示只需要「点得开、看得见」，
因此按引用关系保留必要子集：

  - segment_versions（历史版本快照）整表清空：UI 的版本列表会空，但主流程不受影响
  - segments：保留 plan 引用的段 + 每例最近 3 个，其余删除（避免台账里出现打不开的行）
  - audit_events：保留最近 300 条

用法：python scripts/trim_demo_db.py deploy/miis.db
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

MAX_SEGMENTS_PER_CASE = 3
MAX_AUDIT_EVENTS = 300


def trim(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    before = db_path.stat().st_size

    cur.execute("DELETE FROM segment_versions")

    referenced = {r[0] for r in cur.execute("SELECT base_seg_uid FROM plans WHERE base_seg_uid IS NOT NULL")}

    keep: set[str] = set(referenced)
    for (case_id,) in cur.execute("SELECT DISTINCT case_id FROM segments").fetchall():
        rows = cur.execute(
            "SELECT seg_uid FROM segments WHERE case_id = ? ORDER BY updated_at DESC LIMIT ?",
            (case_id, MAX_SEGMENTS_PER_CASE),
        ).fetchall()
        keep.update(r[0] for r in rows)

    if keep:
        placeholders = ",".join("?" * len(keep))
        cur.execute(f"DELETE FROM segments WHERE seg_uid NOT IN ({placeholders})", tuple(keep))
        # 版本表已清空，但计划/测量仍按 seg_uid 关联，孤儿版本不会出现
    else:
        cur.execute("DELETE FROM segments")

    cur.execute(
        "DELETE FROM audit_events WHERE id NOT IN "
        "(SELECT id FROM audit_events ORDER BY created_at DESC LIMIT ?)",
        (MAX_AUDIT_EVENTS,),
    )

    con.commit()
    cur.execute("VACUUM")
    con.commit()

    segments = cur.execute("SELECT count(*) FROM segments").fetchone()[0]
    con.close()

    after = db_path.stat().st_size
    print(
        f"演示库裁剪完成：{before / 1048576:.1f} MB → {after / 1048576:.1f} MB，"
        f"保留 {segments} 个分割段"
    )


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "deploy/miis.db")
    if not target.is_file():
        raise SystemExit(f"找不到数据库：{target}")
    trim(target)
