#!/usr/bin/env python3
"""
Memory Tree - 三层记忆架构模块
提供 STM(短期记忆) → Staging(候选) → LTM(长期记忆) 三层架构管理
"""

import json
import sqlite3
from datetime import datetime, timedelta

from memory_utils import (
    DATA_DIR, STM_DB, STAGING_DB, LTM_DB, MEMORY_MD,
    parse_memory_blocks, estimate_days_since_mention
)
from memory_decay import (
    calculate_importance_score, calculate_strength,
    get_category_decay_rate, PROMOTE_THRESHOLD, FORGET_THRESHOLD,
    MEMORY_TIERS
)

# 三层记忆配置（已在 memory_decay 中定义，这里引用）
# MEMORY_TIERS = {
#     "STM": {"name": "短期记忆", "ttl_days": 7, "max_items": 100},
#     "Staging": {"name": "候选记忆", "cooldown_hours": 48, "min_score": 60, "max_items": 50},
#     "LTM": {"name": "长期记忆", "max_items": 1000}
# }


def init_tier_db(db_path):
    """初始化层级数据库"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))

    if db_path == STM_DB:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stm_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'default',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                access_count INTEGER DEFAULT 0,
                embedding BLOB
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON stm_memories(expires_at)")

    elif db_path == STAGING_DB:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS staging_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'default',
                stm_id INTEGER,
                score REAL DEFAULT 0,
                entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                review_at TIMESTAMP,
                status TEXT DEFAULT 'pending',
                access_count INTEGER DEFAULT 0,
                embedding BLOB
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_review ON staging_memories(review_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON staging_memories(status)")

    elif db_path == LTM_DB:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ltm_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'default',
                staging_id INTEGER,
                is_permanent INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_access TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                access_count INTEGER DEFAULT 0,
                decay_weight REAL DEFAULT 1.0,
                embedding BLOB,
                graph_links TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_permanent ON ltm_memories(is_permanent)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decay ON ltm_memories(decay_weight)")

    conn.commit()
    return conn


def add_to_stm(title, content, category="default"):
    """添加记忆到 STM（短期记忆）"""
    conn = init_tier_db(STM_DB)

    expires_at = datetime.now() + timedelta(days=MEMORY_TIERS["STM"]["ttl_days"])

    try:
        from vector_store import get_embedding
        embedding = get_embedding(f"{title}\n{content}")
        embedding_blob = json.dumps(embedding)
    except Exception as e:
        embedding_blob = None

    conn.execute(
        "INSERT INTO stm_memories (title, content, category, expires_at, embedding) VALUES (?, ?, ?, ?, ?)",
        (title, content, category, expires_at.strftime('%Y-%m-%d %H:%M:%S'), embedding_blob)
    )
    conn.commit()
    conn.close()

    return {"success": True, "tier": "STM", "expires": expires_at.strftime('%Y-%m-%d %H:%M:%S')}


def migrate_stm_to_staging():
    """将过期 STM 记忆迁移到 Staging"""
    stm_conn = init_tier_db(STM_DB)
    staging_conn = init_tier_db(STAGING_DB)

    now = datetime.now()

    expired = stm_conn.execute(
        "SELECT id, title, content, category, embedding FROM stm_memories WHERE expires_at < ?",
        (now.strftime('%Y-%m-%d %H:%M:%S'),)
    ).fetchall()

    migrated = 0
    for stm_id, title, content, category, embedding in expired:
        review_at = now + timedelta(hours=MEMORY_TIERS["Staging"]["cooldown_hours"])

        staging_conn.execute(
            "INSERT INTO staging_memories (title, content, category, stm_id, review_at, embedding) VALUES (?, ?, ?, ?, ?, ?)",
            (title, content, category, stm_id, review_at.strftime('%Y-%m-%d %H:%M:%S'), embedding)
        )

        stm_conn.execute("DELETE FROM stm_memories WHERE id = ?", (stm_id,))
        migrated += 1

    stm_conn.commit()
    staging_conn.commit()
    stm_conn.close()
    staging_conn.close()

    return {"migrated": migrated}


def review_staging():
    """评审 Staging 记忆，决定是否进入 LTM"""
    staging_conn = init_tier_db(STAGING_DB)
    ltm_conn = init_tier_db(LTM_DB)

    now = datetime.now()

    pending = staging_conn.execute(
        "SELECT id, title, content, category, score, embedding FROM staging_memories WHERE status = 'pending' AND review_at < ?",
        (now.strftime('%Y-%m-%d %H:%M:%S'),)
    ).fetchall()

    approved = 0
    rejected = 0

    for staging_id, title, content, category, score, embedding in pending:
        if score == 0:
            score = calculate_importance_score({
                "title": title,
                "body": content,
                "is_permanent": False,
                "priority": "P2"
            })["score"]

        if score >= MEMORY_TIERS["Staging"]["min_score"]:
            ltm_conn.execute(
                "INSERT INTO ltm_memories (title, content, category, staging_id, embedding) VALUES (?, ?, ?, ?, ?)",
                (title, content, category, staging_id, embedding)
            )
            staging_conn.execute(
                "UPDATE staging_memories SET status = 'approved' WHERE id = ?",
                (staging_id,)
            )
            approved += 1
        else:
            staging_conn.execute(
                "UPDATE staging_memories SET status = 'rejected' WHERE id = ?",
                (staging_id,)
            )
            rejected += 1

    staging_conn.commit()
    ltm_conn.commit()
    staging_conn.close()
    ltm_conn.close()

    return {"approved": approved, "rejected": rejected}


def get_tier_stats():
    """获取三层记忆统计"""
    stm_conn = init_tier_db(STM_DB)
    staging_conn = init_tier_db(STAGING_DB)
    ltm_conn = init_tier_db(LTM_DB)

    stm_count = stm_conn.execute("SELECT COUNT(*) FROM stm_memories").fetchone()[0]
    staging_count = staging_conn.execute("SELECT COUNT(*) FROM staging_memories WHERE status = 'pending'").fetchone()[0]
    ltm_count = ltm_conn.execute("SELECT COUNT(*) FROM ltm_memories").fetchone()[0]
    ltm_permanent = ltm_conn.execute("SELECT COUNT(*) FROM ltm_memories WHERE is_permanent = 1").fetchone()[0]

    stm_conn.close()
    staging_conn.close()
    ltm_conn.close()

    return {
        "STM": {"count": stm_count, "max": MEMORY_TIERS["STM"]["max_items"]},
        "Staging": {"count": staging_count, "max": MEMORY_TIERS["Staging"]["max_items"]},
        "LTM": {"count": ltm_count, "permanent": ltm_permanent, "max": MEMORY_TIERS["LTM"]["max_items"]},
    }


def cmd_migrate(args):
    """三层架构迁移命令"""
    results = {"stm_to_staging": {}, "staging_review": {}, "tier_stats": {}}

    print(f"🌳 记忆树 — 三层架构迁移 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")

    stm_result = migrate_stm_to_staging()
    results["stm_to_staging"] = stm_result
    print(f"📤 STM → Staging: 迁移 {stm_result['migrated']} 条过期记忆")

    staging_result = review_staging()
    results["staging_review"] = staging_result
    print(f"📊 Staging 评审: 通过 {staging_result['approved']} 条, 拒绝 {staging_result['rejected']} 条")

    stats = get_tier_stats()
    results["tier_stats"] = stats
    print(f"\n📈 三层架构状态:")
    print(f"   STM (短期): {stats['STM']['count']}/{stats['STM']['max']} 条")
    print(f"   Staging (候选): {stats['Staging']['count']}/{stats['Staging']['max']} 条")
    print(f"   LTM (长期): {stats['LTM']['count']}/{stats['LTM']['max']} 条 (永久: {stats['LTM']['permanent']})")

    if getattr(args, 'json', False):
        print("\n\n--- JSON Output ---")
        print(json.dumps(results, indent=2, ensure_ascii=False))

    return results
