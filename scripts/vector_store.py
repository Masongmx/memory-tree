#!/usr/bin/env python3
"""
本地向量存储 — SQLite + Ollama embedding

使用本地 qwen3-embedding 模型实现语义检索。
"""

import sqlite3
import json
import numpy as np
import requests
from pathlib import Path

WORKSPACE = Path.home() / ".openclaw" / "workspace"
DB_PATH = WORKSPACE / "memory" / "memory_vectors.db"
OLLAMA_URL = "http://localhost:11434/api/embeddings"
DEFAULT_MODEL = "qwen3-embedding"


def get_embedding(text, model=DEFAULT_MODEL):
    """调用Ollama获取embedding向量"""
    resp = requests.post(
        OLLAMA_URL,
        json={"model": model, "prompt": text},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def init_db():
    """初始化SQLite数据库（含FTS5全文搜索索引）"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    
    # 主表
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            block_type TEXT DEFAULT 'normal',
            category TEXT DEFAULT '其他',
            is_permanent INTEGER DEFAULT 0,
            confidence REAL DEFAULT 0.5,
            source TEXT DEFAULT '',
            last_mention TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            embedding BLOB
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON memories(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_permanent ON memories(is_permanent)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_last_mention ON memories(last_mention)")
    
    # FTS5 全文搜索虚表
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            title,
            content,
            content='memories',
            content_rowid='id'
        )
    """)
    
    conn.commit()
    return conn


def rebuild_fts_index():
    """重建FTS5索引"""
    conn = init_db()
    try:
        # 清空FTS索引
        conn.execute("DELETE FROM memories_fts")
        # 从主表重建
        conn.execute("""
            INSERT INTO memories_fts(rowid, title, content)
            SELECT id, title, content FROM memories
        """)
        conn.commit()
        return True
    except Exception as e:
        print(f"⚠️ FTS索引重建失败: {e}")
        return False
    finally:
        conn.close()


def keyword_search(query, top_k=10):
    """
    FTS5全文搜索
    
    Args:
        query: 搜索查询（支持中文分词和英文单词）
        top_k: 返回数量
    
    Returns:
        list: [{id, title, score, is_permanent}, ...]
    """
    conn = init_db()
    
    # FTS5 查询（使用 bm25 排序）
    # 注意：中文需要特殊处理，SQLite FTS5 默认不支持中文分词
    # 使用简单的模糊匹配作为fallback
    results = []
    
    try:
        # 尝试FTS5查询
        rows = conn.execute("""
            SELECT m.id, m.title, m.content, m.is_permanent, 
                   bm25(memories_fts) as fts_score
            FROM memories_fts f
            JOIN memories m ON f.rowid = m.id
            WHERE memories_fts MATCH ?
            ORDER BY bm25(memories_fts)
            LIMIT ?
        """, (query, top_k * 2)).fetchall()
        
        # bm25 返回负数，越小越好，需要转换为正数分数
        max_score = 0
        for row in rows:
            raw_score = row[4] if row[4] else 0
            if raw_score < max_score:
                max_score = raw_score
        
        for row in rows:
            raw_score = row[4] if row[4] else 0
            # 将bm25负数转换为0-1的正数分数
            # bm25越小越好，所以用 max_score - raw_score
            normalized_score = max(0, (max_score - raw_score) / (abs(max_score) + 1))
            results.append({
                "id": row[0],
                "title": row[1],
                "content": row[2][:200],
                "score": round(normalized_score, 3),
                "is_permanent": bool(row[3])
            })
    
    except sqlite3.OperationalError:
        # FTS5不支持中文或查询失败，fallback到简单的LIKE匹配
        keywords = query.split()
        for kw in keywords[:3]:  # 最多3个关键词
            rows = conn.execute("""
                SELECT id, title, content, is_permanent
                FROM memories
                WHERE title LIKE ? OR content LIKE ?
                LIMIT ?
            """, (f'%{kw}%', f'%{kw}%', top_k)).fetchall()
            
            for row in rows:
                # 简单的匹配分数
                title_matches = row[1].lower().count(kw.lower())
                content_matches = row[2].lower().count(kw.lower())
                score = min(1.0, (title_matches * 0.3 + content_matches * 0.1))
                
                # 避免重复
                if not any(r['id'] == row[0] for r in results):
                    results.append({
                        "id": row[0],
                        "title": row[1],
                        "content": row[2][:200],
                        "score": round(score, 3),
                        "is_permanent": bool(row[3])
                    })
    
    conn.close()
    
    # 按分数排序
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_k]


def temporal_search(top_k=10, decay_days=30):
    """
    时间加权检索（最近访问优先）
    
    Args:
        top_k: 返回数量
        decay_days: 衰减周期（天），超过此天数的记忆分数接近0
    
    Returns:
        list: [{id, title, score, days_ago, is_permanent}, ...]
    """
    import datetime
    
    conn = init_db()
    now = datetime.datetime.now()
    results = []
    
    rows = conn.execute("""
        SELECT id, title, content, is_permanent, last_mention, created_at
        FROM memories
        ORDER BY last_mention DESC
        LIMIT ?
    """, (top_k * 3,)).fetchall()
    
    for row in rows:
        last_mention = row[4] if row[4] else row[5]  # fallback to created_at
        
        if last_mention:
            try:
                # 解析时间戳
                if isinstance(last_mention, str):
                    mention_time = datetime.datetime.fromisoformat(last_mention.replace('Z', '+00:00').split('+')[0])
                else:
                    mention_time = datetime.datetime.fromtimestamp(last_mention)
                days_ago = (now - mention_time).days
            except Exception:
                days_ago = 30  # 解析失败，使用默认值
        else:
            days_ago = 30
        
        # 时间衰减分数（指数衰减）
        # 公式: score = e^(-days / decay_days)
        import math
        score = math.exp(-days_ago / decay_days)
        
        results.append({
            "id": row[0],
            "title": row[1],
            "content": row[2][:200],
            "score": round(score, 3),
            "days_ago": days_ago,
            "is_permanent": bool(row[3])
        })
    
    conn.close()
    
    # 永久记忆不衰减
    for r in results:
        if r['is_permanent']:
            r['score'] = 1.0
    
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_k]


def migrate_db():
    """迁移数据库 - 添加新字段"""
    conn = sqlite3.connect(str(DB_PATH))
    
    # 获取现有列
    cursor = conn.execute("PRAGMA table_info(memories)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    
    # 添加缺失的列（使用NULL默认值避免SQLite限制）
    migrations = [
        ("category", "TEXT DEFAULT '其他'"),
        ("confidence", "REAL DEFAULT 0.5"),
        ("source", "TEXT DEFAULT ''"),
        ("last_mention", "TEXT"),  # 使用TEXT而非TIMESTAMP，无默认值
    ]
    
    for col_name, col_def in migrations:
        if col_name not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE memories ADD COLUMN {col_name} {col_def}")
                print(f"✅ 添加字段: {col_name}")
            except Exception as e:
                if "duplicate column" not in str(e).lower():
                    print(f"⚠️ 字段 {col_name} 添加失败: {e}")
    
    conn.commit()
    
    # 创建索引（列添加后）
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON memories(category)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_last_mention ON memories(last_mention)")
        conn.commit()
    except Exception:
        pass
    
    conn.close()


def store_memory(title, content, is_permanent=False, block_type="normal"):
    """存储一条记忆（含向量）"""
    conn = init_db()
    embedding = get_embedding(f"{title}\n{content}")
    embedding_blob = json.dumps(embedding)
    conn.execute(
        "INSERT INTO memories (title, content, block_type, is_permanent, embedding) VALUES (?, ?, ?, ?, ?)",
        (title, content, block_type, int(is_permanent), embedding_blob)
    )
    conn.commit()
    conn.close()


def search_memories(query, top_k=5):
    """向量检索 — 返回最相关的记忆"""
    conn = init_db()
    query_embedding = np.array(get_embedding(query))
    
    results = []
    for row in conn.execute("SELECT id, title, content, embedding, is_permanent FROM memories"):
        mid, title, content, emb_blob, is_permanent = row
        emb = np.array(json.loads(emb_blob))
        # 余弦相似度
        similarity = np.dot(query_embedding, emb) / (np.linalg.norm(query_embedding) * np.linalg.norm(emb))
        results.append({
            "id": mid, 
            "title": title, 
            "content": content[:200], 
            "score": float(similarity),
            "is_permanent": bool(is_permanent)
        })
    
    conn.close()
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def sync_from_markdown():
    """从MEMORY.md同步到SQLite"""
    memory_file = WORKSPACE / "MEMORY.md"
    if not memory_file.exists():
        return 0
    
    content = memory_file.read_text(encoding='utf-8')
    
    # 按##分割
    import re
    blocks = []
    current = None
    for line in content.split('\n'):
        match = re.match(r'^## (.+)$', line)
        if match:
            if current:
                blocks.append(current)
            title = match.group(1).strip()
            # 检测标题中的永久标记
            is_permanent = '📌' in title or '[P0]' in title
            current = {"title": title, "content": "", "is_permanent": is_permanent}
        elif current:
            current["content"] += line + '\n'
            # 检测内容中的永久标记
            if '📌' in line or '[P0]' in line:
                current["is_permanent"] = True
    if current:
        blocks.append(current)
    
    # 先获取所有 embedding（可能耗时）
    embeddings = []
    for block in blocks:
        emb = get_embedding(f"{block['title']}\n{block['content']}")
        embeddings.append((block, emb))
    
    # 在一个事务中完成清空和插入
    conn = init_db()
    conn.execute("DELETE FROM memories")
    for block, emb in embeddings:
        emb_blob = json.dumps(emb)
        conn.execute(
            "INSERT INTO memories (title, content, is_permanent, embedding) VALUES (?, ?, ?, ?)",
            (block["title"], block["content"], int(block["is_permanent"]), emb_blob)
        )
    conn.commit()
    conn.close()
    
    return len(blocks)


def get_stats():
    """获取向量存储统计"""
    conn = init_db()
    
    total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    permanent = conn.execute("SELECT COUNT(*) FROM memories WHERE is_permanent = 1").fetchone()[0]
    
    # 分类统计
    categories = conn.execute(
        "SELECT category, COUNT(*) FROM memories GROUP BY category"
    ).fetchall()
    categories_dict = {c[0]: c[1] for c in categories}
    
    conn.close()
    return {
        "total": total,
        "permanent": permanent,
        "categories": categories_dict,
        "db_path": str(DB_PATH)
    }


def memory_exists_similar(title, content, threshold=0.9):
    """检查是否存在相似记忆（避免重复）"""
    conn = init_db()
    
    # 先检查标题完全匹配
    existing = conn.execute(
        "SELECT id FROM memories WHERE title = ?",
        (title,)
    ).fetchone()
    if existing:
        conn.close()
        return True
    
    # 再检查向量相似度
    rows = conn.execute("SELECT embedding FROM memories").fetchall()
    if not rows:
        conn.close()
        return False
    
    new_emb = np.array(get_embedding(f"{title}\n{content}"))
    
    for row in rows:
        existing_emb = np.array(json.loads(row[0]))
        sim = np.dot(new_emb, existing_emb) / (np.linalg.norm(new_emb) * np.linalg.norm(existing_emb))
        if sim > threshold:
            conn.close()
            return True
    
    conn.close()
    return False


def store_extracted_memories(memories):
    """将提取的记忆存入向量数据库"""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    stored = 0
    skipped = 0
    
    for mem in memories:
        title = mem.get("title", "")
        content = mem.get("content", "")
        is_permanent = mem.get("is_permanent", False)
        category = mem.get("category", "其他")
        
        if not title or not content:
            continue
        
        # 检查标题是否已存在
        existing = conn.execute(
            "SELECT id FROM memories WHERE title = ?",
            (title,)
        ).fetchone()
        if existing:
            skipped += 1
            continue
        
        # 检查向量相似度
        try:
            new_emb = np.array(get_embedding(f"{title}\n{content}"))
        except Exception as e:
            print(f"⚠️ Embedding失败 [{title}]: {e}")
            continue
        
        rows = conn.execute("SELECT embedding FROM memories").fetchall()
        is_duplicate = False
        
        for row in rows:
            existing_emb = np.array(json.loads(row[0]))
            sim = np.dot(new_emb, existing_emb) / (np.linalg.norm(new_emb) * np.linalg.norm(existing_emb))
            if sim > 0.9:
                is_duplicate = True
                break
        
        if is_duplicate:
            skipped += 1
            continue
        
        # 存储
        try:
            embedding_blob = json.dumps(new_emb.tolist())
            conn.execute(
                "INSERT INTO memories (title, content, block_type, is_permanent, embedding) VALUES (?, ?, ?, ?, ?)",
                (title, content, category, int(is_permanent), embedding_blob)
            )
            stored += 1
        except Exception as e:
            print(f"⚠️ 存储失败 [{title}]: {e}")
    
    conn.commit()
    conn.close()
    return {"stored": stored, "skipped": skipped}


def sync_incremental():
    """增量同步MEMORY.md到SQLite"""
    memory_file = WORKSPACE / "MEMORY.md"
    if not memory_file.exists():
        return {"added": 0, "updated": 0, "unchanged": 0}
    
    content = memory_file.read_text(encoding='utf-8')
    
    # 解析MEMORY.md块
    import re
    blocks = []
    current = None
    for line in content.split('\n'):
        match = re.match(r'^## (.+)$', line)
        if match:
            if current:
                blocks.append(current)
            current = {"title": match.group(1).strip(), "content": "", "is_permanent": False}
        elif current:
            current["content"] += line + '\n'
            if '📌' in line or '[P0]' in line:
                current["is_permanent"] = True
    if current:
        blocks.append(current)
    
    # 对比现有记录，增量更新
    conn = init_db()
    added = 0
    updated = 0
    unchanged = 0
    
    for block in blocks:
        existing = conn.execute(
            "SELECT id, content, is_permanent FROM memories WHERE title=?",
            (block["title"],)
        ).fetchone()
        
        if existing is None:
            # 新记忆
            embedding = get_embedding(f"{block['title']}\n{block['content']}")
            conn.execute(
                "INSERT INTO memories (title, content, is_permanent, embedding, last_mention) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (block["title"], block["content"], int(block["is_permanent"]), json.dumps(embedding))
            )
            added += 1
        elif existing[1] != block["content"] or existing[2] != int(block["is_permanent"]):
            # 内容有变化
            embedding = get_embedding(f"{block['title']}\n{block['content']}")
            conn.execute(
                "UPDATE memories SET content=?, is_permanent=?, embedding=?, updated_at=CURRENT_TIMESTAMP, last_mention=CURRENT_TIMESTAMP WHERE id=?",
                (block["content"], int(block["is_permanent"]), json.dumps(embedding), existing[0])
            )
            updated += 1
        else:
            # 无变化，更新last_mention
            conn.execute(
                "UPDATE memories SET last_mention=CURRENT_TIMESTAMP WHERE id=?",
                (existing[0],)
            )
            unchanged += 1
    
    conn.commit()
    conn.close()
    return {"added": added, "updated": updated, "unchanged": unchanged}


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python vector_store.py [sync|search|stats]")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "sync":
        count = sync_from_markdown()
        print(f"📥 同步完成: {count} 条记忆")
    
    elif cmd == "search":
        if len(sys.argv) < 3:
            print("用法: python vector_store.py search \"查询\"")
            sys.exit(1)
        query = sys.argv[2]
        results = search_memories(query)
        for r in results:
            print(f"[{r['score']:.3f}] {r['title']}")
    
    elif cmd == "stats":
        stats = get_stats()
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    
    else:
        print(f"未知命令: {cmd}")