#!/usr/bin/env python3
"""
Memory Tree 🌳 v3.0 — 让 agent 记住该记的，忘记该忘的

核心功能：
1. weekly - 周报生成（本周新记、本周遗忘、永久记忆清单）
2. search - 语义搜索（关键词模式，无外部依赖）
3. vector-search - 向量搜索（语义模式，使用本地 embedding）
4. mark - 永久标记（📌）
5. decay - 遗忘预测（基于分类衰变率，用户确认模式）
6. health - 健康度四维评分
7. importance - 记忆重要性评分
8. conflict - 冲突检测
9. migrate - 三层架构迁移（STM → Staging → LTM）
10. multi-search - 多策略检索（semantic + keyword + graph + temporal）

v3.0 变更（P0+P1 整改）：
- 分类衰变率：strategy/fact/assumption/failure 不同衰变速度
- 三层记忆架构：STM(7天TTL) → Staging(LLM评分+48h冷却) → LTM(向量+图谱)
- 多策略检索：semantic(0.4) + keyword(0.25) + graph(0.2) + temporal(0.15) + Rerank
- 访问强化：每次访问提升记忆权重，延缓衰减
- vector_store.py 内嵌到 scripts/
- 错误处理统一：关键路径用户友好提示

v2.3 变更：
- 新增 vector-search 命令，使用本地 qwen3-embedding 模型
- 语义搜索替代关键词匹配，更准确
- 自动同步 MEMORY.md 到 SQLite 向量存储
"""

import json
import os
import hashlib
import re
import sys
import shutil
import argparse
import math
from datetime import datetime, timedelta
from pathlib import Path

# 导入公共模块
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))
from utils import (
    load_json, save_json, file_hash, file_age_days, text_hash,
    estimate_tokens, fmt_tokens, fmt_size,
    parse_memory_blocks,
    detect_enabled_channels, get_feishu_chat_id,
    WORKSPACE, OPENCLAW_CONFIG
)

# ==================== 路径配置 ====================
MEMORY_MD = WORKSPACE / "MEMORY.md"
MEMORY_DIR = WORKSPACE / "memory"
DATA_DIR = WORKSPACE / "memory-tree" / "data"
CONFIDENCE_DB = DATA_DIR / "confidence.json"
WEEKLY_REPORTS_DIR = MEMORY_DIR / "weekly-reports"
BACKUP_DIR = WORKSPACE / ".memory-backup"


# ==================== 依赖检查 ====================
def check_vector_dependencies():
    """检查向量搜索所需依赖"""
    missing = []
    
    try:
        import numpy
    except ImportError:
        missing.append("numpy")
    
    try:
        import requests
    except ImportError:
        missing.append("requests")
    
    # 检查 Ollama 服务
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        if resp.status_code != 200:
            missing.append("ollama (服务未运行)")
    except:
        missing.append("ollama (服务未启动，请运行 ollama serve)")
    
    return missing


def check_extractor_dependencies():
    """检查记忆提取所需依赖"""
    missing = []
    
    try:
        import requests
    except ImportError:
        missing.append("requests")
    
    # 检查 Ollama 服务
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        if resp.status_code != 200:
            missing.append("ollama (服务未运行)")
    except:
        missing.append("ollama (服务未启动，请运行 ollama serve)")
    
    # 检查 qwen3:8b 模型
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        models = [m["name"] for m in resp.json().get("models", [])]
        if not any("qwen3" in m for m in models):
            missing.append("qwen3:8b 模型 (请运行 ollama pull qwen3:8b)")
    except:
        pass  # 已在上面检查过服务
    
    return missing


# parse_memory_blocks 现已从 common/utils.py 导入


def get_permanent_memories():
    """提取 MEMORY.md 中的永久记忆"""
    if not MEMORY_MD.exists():
        return []
    
    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    
    permanent = []
    for block in blocks:
        if block["is_permanent"] or block["priority"] == "P0":
            # 提取摘要（第一行或前100字符）
            lines = block["body"].split('\n')
            summary = ""
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    summary = line[:100]
                    break
            if not summary:
                summary = block["title"][:100]
            
            permanent.append({
                "title": block["title"].replace("📌", "").replace("[P0]", "").strip(),
                "summary": summary,
                "priority": block["priority"]
            })
    
    return permanent


# ==================== 备份功能 ====================

def backup_memory():
    """标记前自动备份 MEMORY.md"""
    if not MEMORY_MD.exists():
        return None
    
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"MEMORY-{timestamp}.md"
    
    shutil.copy2(MEMORY_MD, backup_file)
    print(f"📦 已备份: {backup_file.name}")
    return backup_file


# ==================== 飞书报告输出 ====================

def send_feishu_report(content, title="记忆树报告"):
    """飞书推送 — 输出可被 message tool 使用的格式"""
    print(f"[FEISHU_REPORT]")
    print(f"title: {title}")
    print(f"content: {content}")
    print(f"[/FEISHU_REPORT]")
    return True


# ==================== 遗忘曲线 ====================

# 分类衰变率（参考 YourMemory）
# 衰变率越大，衰减越快
# 公式: R = e^(-t * decay_rate)
DECAY_RATES = {
    "strategy": 0.10,   # 策略类记忆衰变最慢（~38天衰减到50%）
    "fact": 0.16,       # 事实类记忆标准衰变（~24天衰减到50%）
    "assumption": 0.20, # 假设类记忆衰变较快（~19天衰减到50%）
    "failure": 0.35,    # 失败教训衰变最快（~11天衰减到50%）
    "default": 0.16,    # 默认使用 fact 类衰变率
}

# 遗忘阈值
FORGET_THRESHOLD = 0.05   # 保留率低于此值，建议遗忘
PROMOTE_THRESHOLD = 0.65  # 保留率高于此值，建议标记永久

# 访问强化系数
# 每次召回提升保留率，延缓衰减
RECALL_BOOST_FACTOR = 0.05  # 每次召回增加5%权重


def classify_memory_category(title, content=""):
    """
    分类记忆类型（关键词检测）
    
    分类规则（优先级从高到低）:
    1. failure - 包含失败、错误、教训、踩坑、bug、问题等关键词
    2. strategy - 包含策略、规则、方法、流程、原则、计划等关键词
    3. assumption - 包含假设、猜测、推测、可能、大概等关键词
    4. fact - 包含事实、数据、结果、配置、参数等关键词
    5. default - 无法分类时默认为 fact
    
    Args:
        title: 记忆标题
        content: 记忆内容（可选）
    
    Returns:
        str: 记忆类别（strategy/fact/assumption/failure）
    """
    full_text = (title + " " + content).lower()
    
    # 1. failure 关键词（最高优先级，失败教训最重要）
    failure_keywords = [
        "失败", "错误", "教训", "踩坑", "坑", "bug", "问题", "故障",
        "避免", "不要", "禁止", "杜绝", "惨痛", "惨了", "翻车",
        "错", "误", "fail", "error", "bug", "issue"
    ]
    for kw in failure_keywords:
        if kw in full_text:
            return "failure"
    
    # 2. strategy 关键词（策略、规则、方法）
    strategy_keywords = [
        "策略", "规则", "方法", "流程", "原则", "计划", "方案", "步骤",
        "习惯", "偏好", "风格", "最佳实践", "推荐", "建议",
        "strategy", "rule", "method", "process", "plan"
    ]
    for kw in strategy_keywords:
        if kw in full_text:
            return "strategy"
    
    # 3. assumption 关键词（假设、猜测）
    assumption_keywords = [
        "假设", "猜测", "推测", "可能", "大概", "也许", "似乎",
        "暂定", "待定", "暂且", "临时", "试",
        "assume", "guess", "maybe", "probably", "temporary"
    ]
    for kw in assumption_keywords:
        if kw in full_text:
            return "assumption"
    
    # 4. fact 关键词（事实、数据）
    fact_keywords = [
        "事实", "数据", "结果", "配置", "参数", "设置", "值",
        "是", "等于", "位于", "属于", "版本", "地址", "路径",
        "fact", "data", "result", "config", "value", "version"
    ]
    for kw in fact_keywords:
        if kw in full_text:
            return "fact"
    
    # 5. 默认为 default（使用 fact 衰变率）
    return "default"


def get_category_decay_rate(category):
    """
    根据记忆类型获取衰变率
    
    Args:
        category: 记忆类别（strategy/fact/assumption/failure）
    
    Returns:
        float: 衰变率 decay_rate
    """
    return DECAY_RATES.get(category, DECAY_RATES["default"])


def calculate_strength(days_since_last_recall, category="default", recall_count=0):
    """
    基于 Ebbinghaus 遗忘曲线计算记忆保留率（分类衰变 + recall_count 强化）
    
    公式: R = e^(-t * decay_rate) + (recall_count * RECALL_BOOST_FACTOR)
    - t: 距上次召回的天数
    - decay_rate: 衰变率，根据类别不同（0.10-0.35）
    - recall_count: 召回次数（强化因子）
    
    Args:
        days_since_last_recall: 距上次召回的天数
        category: 记忆类别（strategy/fact/assumption/failure）
        recall_count: 召回次数，用于强化
    
    Returns:
        float: 记忆保留率（0.0-1.0，上限1.0）
    """
    if days_since_last_recall <= 0:
        return 1.0
    
    # 获取分类衰变率
    decay_rate = get_category_decay_rate(category)
    
    # 基础衰变
    base_strength = math.exp(-days_since_last_recall * decay_rate)
    
    # recall_count 强化
    boosted_strength = base_strength + (recall_count * RECALL_BOOST_FACTOR)
    
    # 上限为1.0
    return min(1.0, boosted_strength)


def calculate_decay_weight(days_since_last_mention, decay_constant=None, category="default", access_count=0):
    """
    [兼容旧接口] 基于 Ebbinghaus 遗忘曲线计算记忆保留率
    
    已弃用，建议使用 calculate_strength()
    保留以兼容旧代码调用
    """
    return calculate_strength(days_since_last_mention, category, access_count)


# ==================== 三层记忆架构 ====================

# 三层记忆配置（参考 omem Weibull 三层衰变）
MEMORY_TIERS = {
    "STM": {
        "name": "短期记忆",
        "ttl_days": 7,           # STM TTL: 7天
        "description": "新记忆暂存，7天后自动进入Staging",
        "max_items": 100,
    },
    "Staging": {
        "name": "候选记忆",
        "cooldown_hours": 48,    # Staging 冷却期: 48小时
        "description": "LLM评分+冷却期，决定是否进入LTM",
        "min_score": 60,         # 进入LTM的最低分数
        "max_items": 50,
    },
    "LTM": {
        "name": "长期记忆",
        "description": "向量+图谱存储，永久保存（除非手动删除）",
        "max_items": 1000,
    }
}

# 数据库路径（三层架构）
STM_DB = DATA_DIR / "stm.db"
STAGING_DB = DATA_DIR / "staging.db"
LTM_DB = DATA_DIR / "ltm.db"


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
                status TEXT DEFAULT 'pending',  -- pending/approved/rejected
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
                graph_links TEXT  -- JSON: 关联记忆ID列表
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_permanent ON ltm_memories(is_permanent)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_decay ON ltm_memories(decay_weight)")
    
    conn.commit()
    return conn


def add_to_stm(title, content, category="default"):
    """添加记忆到 STM（短期记忆）"""
    conn = init_tier_db(STM_DB)
    
    # 设置过期时间（7天后）
    expires_at = datetime.now() + timedelta(days=MEMORY_TIERS["STM"]["ttl_days"])
    
    # 获取 embedding
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
    
    # 查找过期的 STM 记忆
    expired = stm_conn.execute(
        "SELECT id, title, content, category, embedding FROM stm_memories WHERE expires_at < ?",
        (now.strftime('%Y-%m-%d %H:%M:%S'),)
    ).fetchall()
    
    migrated = 0
    for stm_id, title, content, category, embedding in expired:
        # 设置评审时间（48小时后）
        review_at = now + timedelta(hours=MEMORY_TIERS["Staging"]["cooldown_hours"])
        
        staging_conn.execute(
            "INSERT INTO staging_memories (title, content, category, stm_id, review_at, embedding) VALUES (?, ?, ?, ?, ?, ?)",
            (title, content, category, stm_id, review_at.strftime('%Y-%m-%d %H:%M:%S'), embedding)
        )
        
        # 从 STM 删除
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
    
    # 查找待评审的 Staging 记忆（冷却期结束）
    pending = staging_conn.execute(
        "SELECT id, title, content, category, score, embedding FROM staging_memories WHERE status = 'pending' AND review_at < ?",
        (now.strftime('%Y-%m-%d %H:%M:%S'),)
    ).fetchall()
    
    approved = 0
    rejected = 0
    
    for staging_id, title, content, category, score, embedding in pending:
        # 简化评分：基于内容长度和是否有关键词
        if score == 0:
            score = calculate_importance_score({
                "title": title,
                "body": content,
                "is_permanent": False,
                "priority": "P2"
            })["score"]
        
        # 判断是否进入 LTM
        if score >= MEMORY_TIERS["Staging"]["min_score"]:
            # 进入 LTM
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
            # 拒绝（标记为待清理）
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
    
    # 1. STM → Staging
    stm_result = migrate_stm_to_staging()
    results["stm_to_staging"] = stm_result
    print(f"📤 STM → Staging: 迁移 {stm_result['migrated']} 条过期记忆")
    
    # 2. Staging 评审
    staging_result = review_staging()
    results["staging_review"] = staging_result
    print(f"📊 Staging 评审: 通过 {staging_result['approved']} 条, 拒绝 {staging_result['rejected']} 条")
    
    # 3. 统计
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


# ==================== 多策略检索 ====================

# 检索策略权重（参考 clawmind 4策略并行检索）
SEARCH_WEIGHTS = {
    "semantic": 0.40,    # 向量语义检索权重
    "keyword": 0.25,     # 关键词匹配权重
    "graph": 0.20,       # 图谱关联权重
    "temporal": 0.15,    # 时间相关性权重
}


def multi_strategy_search(query, top_k=10, weights=None):
    """
    多策略并行检索
    
    策略组合：
    1. semantic (40%) - 向量相似度检索
    2. keyword (25%) - 关键词匹配
    3. graph (20%) - 图谱关联（基于已关联记忆）
    4. temporal (15%) - 时间相关性
    
    Returns:
        list: 综合排序后的记忆列表
    """
    if weights is None:
        weights = SEARCH_WEIGHTS
    
    results = {}
    
    # 1. Semantic 检索
    semantic_results = []
    try:
        from vector_store import search_memories, init_db
        semantic_results = search_memories(query, top_k=top_k * 2)
        for r in semantic_results:
            results[r["id"]] = {
                "title": r["title"],
                "content": r["content"],
                "scores": {"semantic": r["score"]},
                "is_permanent": r.get("is_permanent", False)
            }
    except Exception as e:
        print(f"⚠️ 语义检索失败: {e}")
    
    # 2. Keyword 检索
    keyword_results = []
    if MEMORY_MD.exists():
        content = MEMORY_MD.read_text(encoding='utf-8')
        blocks = parse_memory_blocks(content)
        for block in blocks:
            kw_score = keyword_similarity(query, block.get("full_text", block.get("title", "")))
            if kw_score > 0.1:
                # 用 hash 作为临时 ID
                block_id = block.get("hash", text_hash(block["title"]))
                if block_id in results:
                    results[block_id]["scores"]["keyword"] = kw_score
                else:
                    results[block_id] = {
                        "title": block["title"],
                        "content": block.get("body", ""),
                        "scores": {"semantic": 0, "keyword": kw_score},
                        "is_permanent": block.get("is_permanent", False)
                    }
    
    # 3. Graph 检索（基于关联）
    graph_results = []
    try:
        ltm_conn = init_tier_db(LTM_DB)
        # 找到与查询相关的记忆，然后找它们的关联记忆
        for result_id in list(results.keys())[:5]:
            links = ltm_conn.execute(
                "SELECT graph_links FROM ltm_memories WHERE id = ?",
                (result_id,)
            ).fetchone()
            if links and links[0]:
                linked_ids = json.loads(links[0])
                for linked_id in linked_ids:
                    if linked_id not in results:
                        # 关联记忆获得 graph 分数
                        linked_mem = ltm_conn.execute(
                            "SELECT title, content, is_permanent FROM ltm_memories WHERE id = ?",
                            (linked_id,)
                        ).fetchone()
                        if linked_mem:
                            results[linked_id] = {
                                "title": linked_mem[0],
                                "content": linked_mem[1][:200],
                                "scores": {"semantic": 0, "keyword": 0, "graph": 0.5},
                                "is_permanent": linked_mem[2]
                            }
        ltm_conn.close()
    except Exception:
        pass
    
    # 4. Temporal 检索（时间相关性）
    try:
        ltm_conn = init_tier_db(LTM_DB)
        now = datetime.now()
        for mem_id in results:
            mem = ltm_conn.execute(
                "SELECT last_access, decay_weight FROM ltm_memories WHERE id = ?",
                (mem_id,)
            ).fetchone()
            if mem:
                last_access = datetime.fromisoformat(mem[0]) if mem[0] else now
                days_ago = (now - last_access).days
                temporal_score = max(0, 1 - days_ago / 30)  # 30天内衰减
                results[mem_id]["scores"]["temporal"] = temporal_score
            else:
                results[mem_id]["scores"]["temporal"] = 0.5  # 默认中等
        ltm_conn.close()
    except Exception:
        for mem_id in results:
            results[mem_id]["scores"]["temporal"] = 0.5
    
    # 计算综合分数
    final_results = []
    for mem_id, data in results.items():
        total_score = 0
        for strategy, weight in weights.items():
            strategy_score = data["scores"].get(strategy, 0)
            total_score += strategy_score * weight
        
        # Rerank: 永久记忆加分
        if data["is_permanent"]:
            total_score += 0.1
        
        final_results.append({
            "id": mem_id,
            "title": data["title"],
            "content": data["content"][:200],
            "total_score": round(total_score, 3),
            "scores": data["scores"],
            "is_permanent": data["is_permanent"]
        })
    
    # 排序
    final_results.sort(key=lambda x: x["total_score"], reverse=True)
    
    return final_results[:top_k]


def cmd_multi_search(args):
    """多策略检索命令"""
    query = getattr(args, 'query', '')
    top_k = getattr(args, 'top_k', 10)
    
    results = {"query": query, "matches": []}
    
    if not query or query == "空查询":
        # 显示统计
        stats = get_tier_stats()
        print(f"📊 三层记忆统计:")
        print(f"   STM: {stats['STM']['count']}/{stats['STM']['max']} 条")
        print(f"   Staging: {stats['Staging']['count']}/{stats['Staging']['max']} 条")
        print(f"   LTM: {stats['LTM']['count']}/{stats['LTM']['max']} 条")
        return results
    
    print(f"🔍 多策略检索: \"{query}\" (Top {top_k})\n")
    
    matches = multi_strategy_search(query, top_k=top_k)
    results["matches"] = matches
    
    # 输出
    print(f"策略权重: semantic({SEARCH_WEIGHTS['semantic']}) + keyword({SEARCH_WEIGHTS['keyword']}) + graph({SEARCH_WEIGHTS['graph']}) + temporal({SEARCH_WEIGHTS['temporal']})\n")
    
    for i, r in enumerate(matches, 1):
        marker = "📌" if r.get("is_permanent") else "  "
        scores_str = ", ".join(f"{k}:{v:.2f}" for k, v in r["scores"].items())
        print(f"  {i}. {marker}[{r['total_score']:.3f}] {r['title'][:40]}")
        print(f"     分解: {scores_str}")
        print()
    
    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
    
    return results


# ==================== 原有函数（保持兼容）====================

def estimate_days_since_mention(content):
    """估算内容最后提及时间"""
    # 尝试从内容中提取日期
    date_patterns = [
        r'(\d{4}-\d{2}-\d{2})',
        r'(\d{4}/\d{2}/\d{2})',
        r'(\d{2}-\d{2}-\d{4})',
    ]
    
    latest_date = None
    for pattern in date_patterns:
        matches = re.findall(pattern, content)
        for match in matches:
            try:
                if '-' in match and len(match.split('-')[0]) == 4:
                    date = datetime.strptime(match, '%Y-%m-%d')
                elif '/' in match:
                    date = datetime.strptime(match, '%Y/%m/%d')
                else:
                    continue
                if latest_date is None or date > latest_date:
                    latest_date = date
            except ValueError:
                pass
    
    if latest_date:
        return (datetime.now() - latest_date).days
    
    # 如果没有日期，假设7天前
    return 7


# ==================== 记忆重要性评分 ====================

# 重要性评分参数配置
IMPORTANCE_CONFIG = {
    # 访问频率权重（0-1）
    "access_frequency_weight": 0.25,
    # 时效性权重（0-1）
    "recency_weight": 0.30,
    # 内容价值权重（0-1）
    "content_value_weight": 0.25,
    # 用户标记权重（0-1）
    "user_mark_weight": 0.20,
    # 关键词加分列表（出现在标题或内容中时加分）
    "important_keywords": [
        "红线", "规则", "禁止", "必须", "核心", "关键", "重要",
        "偏好", "习惯", "身份", "记住", "不要", "避免",
        "记住这个", "这个很重要", "记住这条"
    ],
    # 临时信息关键词（减分）
    "temporary_keywords": [
        "今天", "明天", "本周", "临时", "待办", "待处理",
        "TODO", "FIXME", "临时记一下"
    ]
}


def calculate_importance_score(block, access_count=0, max_access_count=10):
    """
    计算记忆重要性评分（0-100分）
    
    评分维度：
    1. 访问频率 (25%) — 被搜索/提及的次数
    2. 时效性 (30%) — 最后提及时间，越近越重要
    3. 内容价值 (25%) — 是否包含关键词、内容长度
    4. 用户标记 (20%) — 是否被用户标记为重要/永久
    
    Args:
        block: 记忆块字典，包含 title, body, is_permanent, priority
        access_count: 访问次数
        max_access_count: 最大访问次数（用于归一化）
    
    Returns:
        dict: {"score": 0-100, "factors": {...}}
    """
    factors = {}
    
    # 1. 访问频率分数 (0-100)
    access_score = min(100, (access_count / max(max_access_count, 1)) * 100)
    factors["access_frequency"] = round(access_score, 1)
    
    # 2. 时效性分数 (0-100)
    days = estimate_days_since_mention(block.get("body", block.get("content", "")))
    recency_score = max(0, 100 - (days * 3))  # 每天扣3分
    factors["recency"] = round(recency_score, 1)
    factors["days_since_mention"] = days
    
    # 3. 内容价值分数 (0-100)
    content_value = 50  # 基础分
    full_text = block.get("full_text", block.get("title", "") + block.get("body", ""))
    
    # 关键词加分
    for kw in IMPORTANCE_CONFIG["important_keywords"]:
        if kw in full_text:
            content_value += 10
    
    # 临时信息减分
    for kw in IMPORTANCE_CONFIG["temporary_keywords"]:
        if kw in full_text:
            content_value -= 15
    
    # 内容长度奖励（有实质内容）
    content_len = len(block.get("body", "").strip())
    if content_len > 100:
        content_value += 5
    if content_len > 500:
        content_value += 5
    
    content_value = max(0, min(100, content_value))
    factors["content_value"] = round(content_value, 1)
    
    # 4. 用户标记分数 (0-100)
    if block.get("is_permanent"):
        user_mark_score = 100
    elif block.get("priority") == "P1":
        user_mark_score = 80
    elif block.get("priority") == "P0":
        user_mark_score = 100
    else:
        user_mark_score = 30
    factors["user_mark"] = user_mark_score
    
    # 加权总分
    total = (
        access_score * IMPORTANCE_CONFIG["access_frequency_weight"] +
        recency_score * IMPORTANCE_CONFIG["recency_weight"] +
        content_value * IMPORTANCE_CONFIG["content_value_weight"] +
        user_mark_score * IMPORTANCE_CONFIG["user_mark_weight"]
    )
    
    return {
        "score": round(total, 1),
        "factors": factors,
        "priority": "P0" if block.get("is_permanent") or block.get("priority") == "P0" else
                    "P1" if total >= 70 else
                    "P2" if total >= 40 else "P3"
    }


def cmd_importance(args):
    """显示记忆重要性评分"""
    results = {"memories": [], "stats": {}}
    
    if not MEMORY_MD.exists():
        if getattr(args, 'json', False):
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print("❌ MEMORY.md 不存在")
        return results
    
    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    
    # 尝试从数据库获取访问次数
    access_counts = {}
    try:
        from vector_store import init_db
        conn = init_db()
        for row in conn.execute("SELECT title, confidence FROM memories"):
            # 使用confidence字段暂存访问次数（简化实现）
            access_counts[row[0]] = int(row[1] * 10) if row[1] else 0
        conn.close()
    except Exception:
        pass
    
    # 计算每个记忆的重要性
    for block in blocks:
        access_count = access_counts.get(block["title"], 0)
        importance = calculate_importance_score(block, access_count)
        results["memories"].append({
            "title": block["title"],
            "score": importance["score"],
            "priority": importance["priority"],
            "factors": importance["factors"]
        })
    
    # 按分数排序
    results["memories"].sort(key=lambda x: -x["score"])
    
    # 统计
    p0_count = sum(1 for m in results["memories"] if m["priority"] == "P0")
    p1_count = sum(1 for m in results["memories"] if m["priority"] == "P1")
    p2_count = sum(1 for m in results["memories"] if m["priority"] == "P2")
    p3_count = sum(1 for m in results["memories"] if m["priority"] == "P3")
    
    results["stats"] = {
        "total": len(results["memories"]),
        "P0": p0_count,
        "P1": p1_count,
        "P2": p2_count,
        "P3": p3_count,
        "avg_score": round(sum(m["score"] for m in results["memories"]) / max(len(results["memories"]), 1), 1)
    }
    
    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results
    
    # 终端输出
    print(f"🌳 记忆树 — 重要性评分 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")
    
    print(f"📊 评分统计:")
    print(f"   总记忆: {results['stats']['total']} 条")
    print(f"   平均分: {results['stats']['avg_score']}/100")
    print(f"   P0 (核心): {p0_count} 条")
    print(f"   P1 (重要): {p1_count} 条")
    print(f"   P2 (一般): {p2_count} 条")
    print(f"   P3 (临时): {p3_count} 条")
    print()
    
    # 显示前10条高分记忆
    print("⭐ 高重要性记忆 (Top 10):")
    for i, m in enumerate(results["memories"][:10], 1):
        marker = "📌" if m["priority"] == "P0" else "  "
        print(f"   {marker} {m['score']:.0f}分 [{m['priority']}] {m['title'][:40]}")
    
    # 显示P3记忆（可能需要清理）
    p3_memories = [m for m in results["memories"] if m["priority"] == "P3"]
    if p3_memories:
        print(f"\n🗑️ 低重要性记忆 ({len(p3_memories)} 条，考虑归档):")
        for m in p3_memories[:5]:
            print(f"   {m['score']:.0f}分 {m['title'][:40]}")
    
    return results


def cmd_decay(args):
    """遗忘预测 — 显示可能不再有用的记忆，提醒用户确认"""
    results = {"blocks": [], "to_review": []}
    
    if not MEMORY_MD.exists():
        if getattr(args, 'json', False):
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print("❌ MEMORY.md 不存在")
        return results
    
    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    
    for block in blocks:
        if block["is_permanent"]:
            results["blocks"].append({
                "title": block["title"],
                "status": "permanent",
                "weight": 1.0
            })
            continue
        
        # 检查最后提及时间
        days = estimate_days_since_mention(block["content"] if "content" in block else block["body"])
        weight = calculate_decay_weight(days)
        
        entry = {
            "title": block["title"],
            "days": days,
            "weight": round(weight, 2),
            "status": "ok" if weight > 0.3 else "warning" if weight > 0.1 else "review"
        }
        results["blocks"].append(entry)
        
        # 权重低的记忆需要用户确认是否清理
        if weight < 0.3:
            results["to_review"].append({
                "title": block["title"],
                "days": days,
                "weight": round(weight, 2)
            })
    
    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results
    
    # 终端输出
    print(f"🌳 记忆树 — 遗忘预测 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")
    
    review = [b for b in results["blocks"] if b["status"] == "review"]
    warning = [b for b in results["blocks"] if b["status"] == "warning"]
    ok = [b for b in results["blocks"] if b["status"] == "ok"]
    permanent = [b for b in results["blocks"] if b["status"] == "permanent"]
    
    if permanent:
        print(f"📌 永久记忆 ({len(permanent)} 条，永不清理):")
        for b in permanent[:10]:
            print(f"   {b['title']}")
        print()
    
    if ok:
        print(f"🟢 健康 ({len(ok)} 条，权重 > 0.3)")
    
    if warning:
        print(f"🟡 关注 ({len(warning)} 条，权重 0.1-0.3):")
        for b in warning[:10]:
            print(f"   {b['title']} (权重: {b['weight']}, {b['days']}天前)")
        print()
    
    if review:
        print(f"🔴 需确认 ({len(review)} 条，权重 < 0.3):")
        print("   以下记忆可能不再有用，请确认是否要清理：")
        for b in review[:10]:
            print(f"   • {b['title']} (权重: {b['weight']}, {b['days']}天前)")
        print()
        print("⚠️  不会自动清理，需要你确认")
        print("   说「保留 xxx」或「删除 xxx」来操作")
    
    return results


# ==================== 健康评分 ====================

def calculate_freshness_score(blocks):
    """计算新鲜度分数"""
    if not blocks:
        return 100
    
    fresh_count = 0
    for block in blocks:
        days = estimate_days_since_mention(block.get("body", block.get("content", "")))
        if days <= 7:
            fresh_count += 1
    
    return round(fresh_count / len(blocks) * 100, 1)


def calculate_coverage_score(blocks):
    """计算覆盖度分数"""
    if not blocks:
        return 0
    
    # 简单的覆盖度计算：基于块数量
    if len(blocks) >= 20:
        return 100
    return round(len(blocks) / 20 * 100, 1)


def calculate_conflict_score(blocks):
    """计算冲突率"""
    # 检测标题重复
    titles = [b["title"].replace("📌", "").replace("[P0]", "").replace("[P1]", "").strip() 
              for b in blocks]
    
    duplicates = len(titles) - len(set(titles))
    
    if not blocks:
        return 0
    
    return round(duplicates / len(blocks) * 100, 1)


def calculate_redundancy_score(content):
    """计算冗余度"""
    lines = content.split('\n')
    non_empty_lines = [l.strip() for l in lines if l.strip()]
    
    if not non_empty_lines:
        return 0
    
    unique_lines = set(non_empty_lines)
    redundancy = (len(non_empty_lines) - len(unique_lines)) / len(non_empty_lines) * 100
    
    return round(redundancy, 1)


def cmd_health(args):
    """健康度四维评分"""
    results = {"scores": {}, "total": 0}
    
    if not MEMORY_MD.exists():
        if getattr(args, 'json', False):
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print("❌ MEMORY.md 不存在")
        return results
    
    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    
    scores = {
        "freshness": calculate_freshness_score(blocks),    # 新鲜度
        "coverage": calculate_coverage_score(blocks),      # 覆盖度
        "conflict": calculate_conflict_score(blocks),      # 冲突率
        "redundancy": calculate_redundancy_score(content)  # 冗余度
    }
    
    # 加权总分
    total = (
        scores["freshness"] * 0.30 +
        scores["coverage"] * 0.25 +
        (100 - scores["conflict"]) * 0.25 +
        (100 - scores["redundancy"]) * 0.20
    )
    
    results["scores"] = scores
    results["total"] = round(total, 1)
    
    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results
    
    # 终端输出
    print(f"🌳 记忆树 — 健康度评分 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")
    
    print(f"📊 四维评分:")
    print(f"   新鲜度 (30%): {scores['freshness']}/100")
    print(f"   覆盖度 (25%): {scores['coverage']}/100")
    print(f"   冲突率 (25%): {scores['conflict']}% (越低越好)")
    print(f"   冗余度 (20%): {scores['redundancy']}% (越低越好)")
    
    print(f"\n🏥 总分: {results['total']}/100")
    
    # 建议
    if scores["conflict"] > 20:
        print(f"\n💡 建议：存在标题重复的记忆，请合并或删除")
    if scores["redundancy"] > 30:
        print(f"💡 建议：存在大量重复内容，请精简 MEMORY.md")
    if scores["freshness"] < 50:
        print(f"💡 建议：大部分记忆较旧，请更新或归档")
    
    return results


# ==================== 周报生成 ====================

def scan_weekly_new_memories():
    """扫描本周新增的 memory 文件"""
    today = datetime.now()
    week_start = today - timedelta(days=today.weekday())  # 本周一
    week_end = week_start + timedelta(days=6)  # 本周日
    
    new_memories = []
    
    for f in MEMORY_DIR.glob("*.md"):
        if f.name == "README.md":
            continue
        
        # 检查文件修改时间
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        if week_start <= mtime <= week_end + timedelta(days=1):
            try:
                content = f.read_text(encoding='utf-8')
                # 提取标题和摘要
                lines = content.split('\n')
                title = ""
                summary = ""
                
                for line in lines:
                    line = line.strip()
                    if line.startswith('# ') and not title:
                        title = line[2:].strip()
                    elif line and not line.startswith('#') and not summary:
                        summary = line[:80]
                
                if not title:
                    title = f.stem
                
                # 检测是否永久记忆
                is_permanent = "📌" in content or "[P0]" in content or "记住这个" in content
                
                new_memories.append({
                    "title": title,
                    "summary": summary,
                    "file": f.name,
                    "date": mtime.strftime('%Y-%m-%d'),
                    "is_permanent": is_permanent
                })
            except (FileNotFoundError, PermissionError):
                pass
    
    # 按日期排序
    new_memories.sort(key=lambda x: x["date"], reverse=True)
    return new_memories


def detect_forgotten_memories():
    """检测本周遗忘的内容（对比 MEMORY.md 和历史记录）"""
    forgotten = []
    
    # 读取当前 MEMORY.md
    if not MEMORY_MD.exists():
        return forgotten
    
    current_content = MEMORY_MD.read_text(encoding='utf-8')
    current_blocks = parse_memory_blocks(current_content)
    current_titles = {b["title"] for b in current_blocks}
    
    # 检查 archive 目录
    archive_dir = MEMORY_DIR / "archive"
    if archive_dir.exists():
        for f in archive_dir.glob("MEMORY-*.md"):
            try:
                content = f.read_text(encoding='utf-8')
                blocks = parse_memory_blocks(content)
                for block in blocks:
                    if block["title"] not in current_titles:
                        # 提取摘要
                        lines = block["body"].split('\n')
                        summary = ""
                        for line in lines:
                            line = line.strip()
                            if line and not line.startswith('#'):
                                summary = line[:60]
                                break
                        
                        forgotten.append({
                            "title": block["title"],
                            "summary": summary,
                            "reason": "被新内容替代或归档"
                        })
            except (FileNotFoundError, PermissionError):
                pass
    
    return forgotten[:10]  # 最多显示10条


def cmd_weekly(args):
    """生成周报"""
    results = {}
    
    print(f"🌳 记忆树 — 周报生成 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")
    
    # 确保输出目录存在
    WEEKLY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    
    # 计算周数
    today = datetime.now()
    week_number = today.isocalendar()[1]
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    
    report_date = today.strftime('%Y-%m-%d')
    report_file = WEEKLY_REPORTS_DIR / f"memory-tree-{report_date}-W{week_number}.md"
    
    # 收集数据
    print("📊 收集记忆数据...")
    
    # 1. 本周新记
    new_memories = scan_weekly_new_memories()
    permanent_new = [m for m in new_memories if m["is_permanent"]]
    normal_new = [m for m in new_memories if not m["is_permanent"]]
    
    # 2. 本周遗忘
    forgotten = detect_forgotten_memories()
    
    # 3. 永久记忆清单
    permanent_memories = get_permanent_memories()
    
    # 4. MEMORY.md 统计
    memory_size = MEMORY_MD.stat().st_size if MEMORY_MD.exists() else 0
    memory_tokens = estimate_tokens(MEMORY_MD.read_text(encoding='utf-8')) if MEMORY_MD.exists() else 0
    
    results["new_memories"] = new_memories
    results["forgotten"] = forgotten
    results["permanent_memories"] = permanent_memories
    results["memory_size"] = memory_size
    results["memory_tokens"] = memory_tokens
    
    # 生成报告
    report_lines = [
        f"# 🌳 记忆树周报 | {today.year}-W{week_number}",
        f"",
        f"> 生成时间：{report_date} {today.strftime('%H:%M')}",
        f"> 统计周期：{week_start.strftime('%Y-%m-%d')} ~ {week_end.strftime('%Y-%m-%d')}",
        f"",
        f"---",
        f"",
    ]
    
    # 本周新记
    report_lines.append("## 📝 本周新记")
    report_lines.append("")
    
    if permanent_new:
        report_lines.append("📌 **永久记忆**：")
        report_lines.append("")
        for m in permanent_new[:10]:
            report_lines.append(f"  • {m['title']} ({m['date']})")
            if m['summary']:
                report_lines.append(f"    _{m['summary']}_")
        report_lines.append("")
    
    if normal_new:
        report_lines.append("🍃 **普通记忆**：")
        report_lines.append("")
        for m in normal_new[:15]:
            report_lines.append(f"  • {m['title']} ({m['date']})")
        report_lines.append("")
    
    if not new_memories:
        report_lines.append("_本周无新增记忆_")
        report_lines.append("")
    
    # 本周遗忘
    report_lines.append("## 🗑️ 本周遗忘")
    report_lines.append("")
    
    if forgotten:
        for f in forgotten:
            report_lines.append(f"  • {f['title']}")
            report_lines.append(f"    _{f['reason']}_")
        report_lines.append("")
    else:
        report_lines.append("_本周无遗忘内容_")
        report_lines.append("")
    
    # 永久记忆清单
    report_lines.append("## 📌 永久记忆清单")
    report_lines.append("")
    
    if permanent_memories:
        for i, p in enumerate(permanent_memories, 1):
            report_lines.append(f"  {i}. {p['title']}")
            if p['summary']:
                report_lines.append(f"     _{p['summary'][:60]}_")
        report_lines.append("")
    else:
        report_lines.append("_暂无永久记忆_")
        report_lines.append("")
    
    # 记忆健康
    report_lines.append("## 💡 记忆健康")
    report_lines.append("")
    report_lines.append(f"| 指标 | 数值 |")
    report_lines.append(f"|------|------|")
    report_lines.append(f"| MEMORY.md 大小 | {fmt_size(memory_size)} (~{fmt_tokens(memory_tokens)} tokens) |")
    report_lines.append(f"| 永久记忆 | {len(permanent_memories)} 条 |")
    report_lines.append(f"| 本周新增 | {len(new_memories)} 条 |")
    report_lines.append(f"| 本周遗忘 | {len(forgotten)} 条 |")
    report_lines.append("")
    report_lines.append("---")
    report_lines.append("")
    report_lines.append(f"*由记忆树自动生成*")
    
    report_content = "\n".join(report_lines)
    results["report_content"] = report_content
    
    # 写入本地文件
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report_content)
    
    print(f"✅ 周报已保存: {report_file}\n")
    
    # 检测推送渠道
    channels = detect_enabled_channels()
    
    if channels:
        print(f"📡 检测到已启用渠道: {', '.join(c['name'] for c in channels)}")
        
        for ch in channels:
            if ch['name'] == 'feishu':
                chat_id = get_feishu_chat_id(ch['config'])
                if chat_id:
                    print(f"\n📱 飞书推送配置:")
                    print(f"   chat_id: {chat_id}")
                    print(f"\n💡 推送命令:")
                    print(f"   message send --target {chat_id} --file {report_file}")
    else:
        print("📭 未检测到已启用的外部推送渠道")
        print("   周报已保存到本地")
    
    # 输出到终端
    print(f"\n{'='*60}")
    print(report_content)
    
    if getattr(args, 'json', False):
        print("\n\n--- JSON Output ---")
        print(json.dumps(results, indent=2, ensure_ascii=False))
    
    return results


# ==================== 搜索功能（关键词模式）====================

def keyword_similarity(query, text):
    """关键词相似度计算"""
    # 中文按字切分 + 英文按词切分
    query_words = set()
    for char in query:
        if '\u4e00' <= char <= '\u9fff':
            query_words.add(char)
    
    for word in re.findall(r'[a-zA-Z]{2,}', query.lower()):
        query_words.add(word)
    
    text_words = set()
    for char in text:
        if '\u4e00' <= char <= '\u9fff':
            text_words.add(char)
    
    for word in re.findall(r'[a-zA-Z]{2,}', text.lower()):
        text_words.add(word)
    
    if not query_words or not text_words:
        return 0
    
    common = query_words & text_words
    return len(common) / len(query_words)


def cmd_search(args):
    """关键词搜索"""
    query = args.query
    
    results = {"query": query, "matches": []}
    
    if not MEMORY_MD.exists():
        if getattr(args, 'json', False):
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print("❌ MEMORY.md 不存在")
        return results
    
    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    
    matches = []
    for block in blocks:
        sim = keyword_similarity(query, block["full_text"])
        if sim > 0.1:
            matches.append({
                "similarity": round(sim, 2),
                "title": block["title"],
                "body": block["body"][:200],
                "is_permanent": block["is_permanent"],
                "priority": block["priority"]
            })
    
    matches.sort(key=lambda x: x["similarity"], reverse=True)
    results["matches"] = matches
    
    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results
    
    if not matches:
        print("🔍 未找到相关记忆")
        return results
    
    print(f"🔍 找到 {len(matches)} 条相关记忆:\n")
    for r in matches[:10]:
        marker = "📌" if r["is_permanent"] else "  "
        print(f"  {marker} 相似:{r['similarity']:.0%} | {r['title']}")
        preview = r['body'][:100].replace('\n', ' ')
        if preview:
            print(f"     {preview}...")
        print()
    
    return results


# ==================== 永久标记 ====================

def cmd_mark(args):
    """标记为永久记忆"""
    title_keyword = args.keyword
    results = {"success": False, "title": None}
    
    if not MEMORY_MD.exists():
        print("❌ MEMORY.md 不存在")
        return results
    
    # 先备份
    backup_memory()
    
    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    
    # 查找匹配的知识块
    found = None
    for block in blocks:
        if title_keyword.lower() in block["title"].lower():
            found = block
            break
    
    if not found:
        print(f"❌ 未找到匹配的知识块: {title_keyword}")
        return results
    
    if found["is_permanent"]:
        print(f"✅ 已经是永久记忆: {found['title']}")
        results["success"] = True
        results["title"] = found["title"]
        return results
    
    # 在标题中添加 📌 标记
    old_title = found["title"]
    new_title = old_title + " 📌"
    
    # 替换内容
    new_content = content.replace(
        f"## {old_title}",
        f"## {new_title}"
    )
    
    if new_content != content:
        MEMORY_MD.write_text(new_content, encoding='utf-8')
        print(f"✅ 已标记为永久记忆: {new_title}")
        print(f"   该记忆永不衰减，永不参与清理")
        results["success"] = True
        results["title"] = new_title
    else:
        print(f"❌ 标记失败，请手动编辑 MEMORY.md")
    
    if getattr(args, 'json', False):
        print("\n\n--- JSON Output ---")
        print(json.dumps(results, indent=2, ensure_ascii=False))
    
    return results


# ==================== 向量搜索 ====================

def cmd_vector_search(args):
    """向量搜索（替代关键词搜索）"""
    # 依赖检查
    missing = check_vector_dependencies()
    if missing:
        print("❌ 向量搜索需要以下依赖：")
        for dep in missing:
            print(f"   - {dep}")
        print("\n💡 安装提示：")
        print("   pip install numpy requests")
        print("   ollama serve  # 启动 Ollama 服务")
        print("   ollama pull qwen3-embedding  # 安装 embedding 模型")
        return {"query": getattr(args, 'query', ''), "matches": [], "error": "missing dependencies"}
    
    from vector_store import search_memories, sync_from_markdown, DB_PATH, get_stats
    
    results = {"query": getattr(args, 'query', ''), "matches": []}
    
    # 强制同步或首次使用时自动同步
    if getattr(args, 'sync', False) or not DB_PATH.exists():
        count = sync_from_markdown()
        print(f"📥 同步完成: {count} 条记忆\n")
    
    query = getattr(args, 'query', '')
    if not query or query == "空查询":
        # 显示统计
        stats = get_stats()
        print(f"📊 向量存储统计:")
        print(f"   总记忆: {stats['total']} 条")
        print(f"   永久记忆: {stats['permanent']} 条")
        print(f"   数据库: {stats['db_path']}")
        return results
    
    matches = search_memories(query, top_k=getattr(args, 'top_k', 5))
    results["matches"] = matches
    
    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results
    
    print(f"🔍 向量搜索: \"{query}\"\n")
    for i, r in enumerate(matches, 1):
        marker = "📌" if r.get("is_permanent") else "  "
        print(f"  {i}. {marker}[{r['score']:.3f}] {r['title']}")
        preview = r['content'][:100].replace('\n', ' ').strip()
        if preview:
            print(f"     {preview}...")
        print()
    
    return results


# ==================== 可视化 ====================

def cmd_visualize(args):
    """显示 MEMORY.md 概览"""
    results = {"blocks": [], "stats": {}}
    
    if not MEMORY_MD.exists():
        print("❌ MEMORY.md 不存在")
        return results
    
    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    
    permanent = [b for b in blocks if b["is_permanent"]]
    normal = [b for b in blocks if not b["is_permanent"]]
    
    memory_size = MEMORY_MD.stat().st_size
    memory_tokens = estimate_tokens(content)
    
    results["stats"] = {
        "size": memory_size,
        "tokens": memory_tokens,
        "permanent_count": len(permanent),
        "normal_count": len(normal),
        "total_count": len(blocks)
    }
    results["blocks"] = [{"title": b["title"], "is_permanent": b["is_permanent"]} for b in blocks]
    
    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results
    
    print(f"🌳 记忆树概览 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")
    print(f"├── 📊 MEMORY.md: {fmt_size(memory_size)} (~{fmt_tokens(memory_tokens)} tokens)")
    print(f"├── 📌 永久记忆: {len(permanent)} 条")
    print(f"├── 🍃 普通记忆: {len(normal)} 条")
    print(f"└── 📝 总计: {len(blocks)} 条知识块\n")
    
    if permanent:
        print("📌 永久记忆清单:")
        for b in permanent[:10]:
            title = b["title"].replace("📌", "").replace("[P0]", "").strip()
            print(f"   • {title}")
        if len(permanent) > 10:
            print(f"   ... 还有 {len(permanent) - 10} 条")
        print()
    
    print("💡 使用 `search \"关键词\"` 搜索记忆")
    print("💡 使用 `mark \"标题关键词\"` 标记为永久记忆")
    
    return results


# ==================== 冲突检测 ====================

def cmd_conflict(args):
    """
    检测可能存在的记忆冲突，提醒用户确认
    
    检测方式：
    1. 关键词对检测 — 检查同一记忆中是否存在可能冲突的关键词
    2. 标题相似度检测 — 检查是否有主题相似但规则不同的记忆
    3. 语义相似度检测 — 使用向量相似度检查内容冲突（如果向量库可用）
    
    重要：Agent 不能自己判断冲突，必须提醒用户确认
    """
    results = {"conflicts": [], "suggestions": [], "by_type": {}}
    
    if not MEMORY_MD.exists():
        if getattr(args, 'json', False):
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print("❌ MEMORY.md 不存在")
        return results
    
    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    
    # ========== 1. 关键词对检测 ==========
    # 定义可能冲突的关键词对（可配置扩展）
    conflict_pairs = [
        # 格式：(关键词1, 关键词2, 说明)
        ("爹味", "深度分析", "深度分析可能像爹味说教"),
        ("简洁", "详细", "什么时候简洁，什么时候详细？"),
        ("飞书", "webchat", "什么情况下用飞书，什么用 webchat？"),
        ("重要", "一般", "什么算重要消息？"),
        ("立即", "延迟", "什么时候立即执行，什么时候延迟？"),
        ("自动", "手动", "什么时候自动，什么时候需要确认？"),
        ("公开", "私密", "什么信息可以公开，什么需要保密？"),
        ("记住", "忘记", "什么该记住，什么该忘记？"),
        ("优先", "延后", "优先级如何判断？"),
        ("自动推送", "手动确认", "什么时候自动，什么时候确认？"),
    ]
    
    keyword_conflicts = []
    for block in blocks:
        block_content = (block.get("content", "") or block.get("body", "")).lower()
        
        for keyword1, keyword2, description in conflict_pairs:
            if keyword1 in block_content and keyword2 in block_content:
                keyword_conflicts.append({
                    "type": "keyword_pair",
                    "title": block["title"],
                    "keywords": [keyword1, keyword2],
                    "description": description
                })
    
    results["by_type"]["keyword_pairs"] = keyword_conflicts
    results["conflicts"].extend(keyword_conflicts)
    
    # ========== 2. 标题相似度检测 ==========
    title_conflicts = []
    titles = [(i, b["title"].replace("📌", "").replace("[P0]", "").replace("[P1]", "").strip().lower())
              for i, b in enumerate(blocks)]
    
    for i, (idx1, title1) in enumerate(titles):
        for idx2, title2 in titles[i+1:]:
            # 如果标题相似度很高，可能是冲突
            if title1 and title2 and (title1 in title2 or title2 in title1):
                if title1 != title2:  # 不是完全相同
                    # 检查内容是否不同
                    body1 = blocks[idx1].get("body", "")[:100]
                    body2 = blocks[idx2].get("body", "")[:100]
                    if body1 != body2:  # 内容不同才是潜在冲突
                        title_conflicts.append({
                            "type": "similar_title",
                            "title": f"{blocks[idx1]['title']} vs {blocks[idx2]['title']}",
                            "keywords": ["相似主题"],
                            "description": "主题相似但规则可能不同"
                        })
    
    results["by_type"]["similar_titles"] = title_conflicts
    results["conflicts"].extend(title_conflicts)
    
    # ========== 3. 语义相似度检测（可选） ==========
    semantic_conflicts = []
    try:
        from vector_store import init_db, get_embedding
        import numpy as np
        
        conn = init_db()
        rows = conn.execute(
            "SELECT title, embedding FROM memories WHERE embedding IS NOT NULL"
        ).fetchall()
        conn.close()
        
        if len(rows) >= 2:
            # 计算所有embedding之间的相似度
            embeddings = []
            titles_list = []
            for title, emb_blob in rows:
                if emb_blob:
                    try:
                        emb = np.array(json.loads(emb_blob))
                        embeddings.append(emb)
                        titles_list.append(title)
                    except Exception:
                        continue
            
            # 检查高相似度但非完全相同的记忆
            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    sim = np.dot(embeddings[i], embeddings[j]) / (
                        np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[j])
                    )
                    # 相似度在0.85-0.98之间可能是冲突（太相似但不是重复）
                    if 0.85 < sim < 0.98:
                        semantic_conflicts.append({
                            "type": "semantic_similar",
                            "title": f"{titles_list[i]} vs {titles_list[j]}",
                            "keywords": ["语义相似"],
                            "description": f"语义相似度 {sim:.2%}，可能存在重复或冲突",
                            "similarity": round(sim, 3)
                        })
    except ImportError:
        pass  # 向量库不可用，跳过语义检测
    except Exception as e:
        pass  # 其他错误，静默跳过
    
    results["by_type"]["semantic"] = semantic_conflicts
    results["conflicts"].extend(semantic_conflicts)
    
    # 去重
    seen = set()
    unique_conflicts = []
    for c in results["conflicts"]:
        key = c["title"]
        if key not in seen:
            seen.add(key)
            unique_conflicts.append(c)
    results["conflicts"] = unique_conflicts
    
    # ========== 输出 ==========
    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results
    
    # 终端输出
    print(f"🌳 记忆树 — 冲突检测 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")
    
    if results["conflicts"]:
        print(f"⚠️  发现 {len(results['conflicts'])} 条可能冲突：\n")
        
        # 按类型分组显示
        if keyword_conflicts:
            print(f"📝 关键词冲突 ({len(keyword_conflicts)} 条):")
            for c in keyword_conflicts[:3]:
                print(f"   • {c['title']}")
                print(f"     关键词：{', '.join(c['keywords'])}")
                print(f"     说明：{c['description']}")
            print()
        
        if title_conflicts:
            print(f"📋 标题相似 ({len(title_conflicts)} 条):")
            for c in title_conflicts[:3]:
                print(f"   • {c['title']}")
                print(f"     说明：{c['description']}")
            print()
        
        if semantic_conflicts:
            print(f"🔍 语义相似 ({len(semantic_conflicts)} 条):")
            for c in semantic_conflicts[:3]:
                print(f"   • {c['title']}")
                print(f"     说明：{c['description']}")
            print()
        
        print("💬 请确认这些冲突应该如何处理：")
        print("   • 如果是正常情况，说「忽略 xxx」")
        print("   • 如果需要修改，说「修改 xxx」")
        print("   • 如果不确定，说「确认 xxx」")
        print()
        print("⚠️  Agent 不会自己判断，需要你确认")
    else:
        print("✅ 未发现明显冲突")
    
    return results


# ==================== 自动提取 ====================

def cmd_auto_extract(args):
    """自动从会话历史提取记忆"""
    # 依赖检查
    missing = check_extractor_dependencies()
    if missing:
        print("❌ 自动提取需要以下依赖：")
        for dep in missing:
            print(f"   - {dep}")
        print("\n💡 安装提示：")
        print("   pip install requests")
        print("   ollama serve  # 启动 Ollama 服务")
        print("   ollama pull qwen3:8b  # 安装提取模型")
        return {"extracted": 0, "stored": 0, "error": "missing dependencies"}
    
    # 检查 memory_extractor 模块
    try:
        from memory_extractor import extract_from_all_sessions
    except ImportError:
        print("❌ 缺少 memory_extractor 模块")
        print("   请确保 common/memory_extractor.py 存在")
        print("   或安装完整版技能：clawhub install memory-tree --full")
        return {"extracted": 0, "stored": 0, "error": "missing module"}
    
    from vector_store import store_extracted_memories
    
    limit = getattr(args, 'limit', 5)
    print(f"🧠 开始自动提取（最近 {limit} 个会话）...")
    
    memories = extract_from_all_sessions(limit=limit)
    print(f"📥 提取到 {len(memories)} 条记忆")
    
    if memories:
        result = store_extracted_memories(memories)
        stored = result["stored"]
        skipped = result["skipped"]
        print(f"✅ 入库 {stored} 条新记忆，跳过 {skipped} 条重复")
        
        if getattr(args, 'json', False):
            output = {
                "extracted": len(memories), 
                "stored": stored, 
                "skipped": skipped,
                "memories": memories[:10]
            }
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            print("\n📋 提取结果：")
            for i, mem in enumerate(memories[:10], 1):
                category = mem.get('category', '?')
                title = mem.get('title', '?')
                content = mem.get('content', '')[:60]
                permanent = "📌" if mem.get('is_permanent') else "  "
                print(f"  {i}. {permanent}[{category}] {title}")
                print(f"     {content}...")
    else:
        print("ℹ️ 没有提取到新记忆")


# ==================== 数据库统计 ====================

def cmd_stats(args):
    """数据库统计"""
    from vector_store import get_stats, DB_PATH, migrate_db
    
    # 先执行迁移
    migrate_db()
    
    if not DB_PATH.exists():
        print("❌ 数据库不存在，请先运行 sync 或 vector-search")
        return
    
    stats = get_stats()
    
    if getattr(args, 'json', False):
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    else:
        print(f"📊 记忆数据库统计")
        print(f"  总记忆: {stats['total']} 条")
        print(f"  永久记忆: {stats['permanent']} 条")
        print(f"  分类分布:")
        for cat, count in stats.get("categories", {}).items():
            print(f"    {cat}: {count} 条")


# ==================== MEMORY.md同步 ====================

def cmd_sync(args):
    """同步MEMORY.md到向量数据库"""
    from vector_store import sync_incremental, migrate_db
    
    # 先执行迁移
    migrate_db()
    
    print("🔄 同步MEMORY.md...")
    result = sync_incremental()
    print(f"  ✅ 新增: {result['added']} 条")
    print(f"  ✏️ 更新: {result['updated']} 条")
    print(f"  ⏭️ 未变: {result['unchanged']} 条")
    
    if getattr(args, 'json', False):
        print(json.dumps(result, indent=2, ensure_ascii=False))


# ==================== CLI ====================

def main():
    parser = argparse.ArgumentParser(description="🌳 Memory Tree v3.0 — 让 agent 记住该记的，忘记该忘的")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # weekly
    weekly_parser = subparsers.add_parser("weekly", help="生成周报")
    weekly_parser.add_argument("--json", action="store_true", help="JSON 输出")
    
    # search
    search_parser = subparsers.add_parser("search", help="搜索记忆")
    search_parser.add_argument("query", nargs="+", help="搜索关键词")
    search_parser.add_argument("--json", action="store_true", help="JSON 输出")
    
    # vector-search
    vsearch_parser = subparsers.add_parser("vector-search", help="向量搜索记忆")
    vsearch_parser.add_argument("query", nargs="?", default="", help="搜索查询")
    vsearch_parser.add_argument("--top-k", type=int, default=5, help="返回数量")
    vsearch_parser.add_argument("--json", action="store_true", help="JSON输出")
    vsearch_parser.add_argument("--sync", action="store_true", help="强制重新同步")
    
    # multi-search (新增 P1)
    msearch_parser = subparsers.add_parser("multi-search", help="多策略检索（semantic+keyword+graph+temporal）")
    msearch_parser.add_argument("query", nargs="?", default="", help="搜索查询")
    msearch_parser.add_argument("--top-k", type=int, default=10, help="返回数量")
    msearch_parser.add_argument("--json", action="store_true", help="JSON输出")
    
    # mark
    mark_parser = subparsers.add_parser("mark", help="标记为永久记忆")
    mark_parser.add_argument("keyword", help="标题关键词")
    mark_parser.add_argument("--json", action="store_true", help="JSON 输出")
    
    # visualize
    viz_parser = subparsers.add_parser("visualize", help="查看记忆概览")
    viz_parser.add_argument("--json", action="store_true", help="JSON 输出")
    
    # decay
    decay_parser = subparsers.add_parser("decay", help="遗忘预测（分类衰变率+访问强化）")
    decay_parser.add_argument("--json", action="store_true", help="JSON 输出")
    
    # health
    health_parser = subparsers.add_parser("health", help="健康度评分")
    health_parser.add_argument("--json", action="store_true", help="JSON 输出")
    
    # importance
    importance_parser = subparsers.add_parser("importance", help="记忆重要性评分")
    importance_parser.add_argument("--json", action="store_true", help="JSON输出")
    
    # migrate (新增 P1)
    migrate_parser = subparsers.add_parser("migrate", help="三层架构迁移（STM→Staging→LTM）")
    migrate_parser.add_argument("--json", action="store_true", help="JSON输出")
    
    # auto-extract
    extract_cmd = subparsers.add_parser("auto-extract", help="自动提取记忆")
    extract_cmd.add_argument("--limit", type=int, default=5, help="处理会话数量")
    extract_cmd.add_argument("--json", action="store_true", help="JSON输出")
    
    # stats
    stats_cmd = subparsers.add_parser("stats", help="数据库统计")
    stats_cmd.add_argument("--json", action="store_true", help="JSON输出")
    
    # sync
    sync_cmd = subparsers.add_parser("sync", help="同步MEMORY.md到向量数据库")
    sync_cmd.add_argument("--json", action="store_true", help="JSON输出")
    
    # conflict
    conflict_cmd = subparsers.add_parser("conflict", help="检测记忆冲突（关键词+语义）")
    conflict_cmd.add_argument("--json", action="store_true", help="JSON输出")
    
    args = parser.parse_args()
    
    # 处理 search 的多个关键词
    if hasattr(args, 'query') and isinstance(args.query, list):
        args.query = " ".join(args.query)
    
    if args.command is None:
        print("🌳 Memory Tree v3.0 — 让 agent 记住该记的，忘记该忘的")
        print()
        print("用法:")
        print("  weekly              生成周报（本周新记、遗忘、永久清单）")
        print("  search \"查询\"       搜索记忆（关键词模式）")
        print("  vector-search \"查询\" 向量搜索记忆（语义模式）")
        print("  multi-search \"查询\" 多策略检索（semantic+keyword+graph+temporal）")
        print("  mark \"标题关键词\"   标记为永久记忆")
        print("  visualize           查看记忆概览")
        print("  decay               遗忘预测（分类衰变率+访问强化）")
        print("  conflict            检测记忆冲突（关键词+语义）")
        print("  migrate             三层架构迁移（STM→Staging→LTM）")
        print("  health              健康度四维评分")
        print("  importance          记忆重要性评分")
        print("  auto-extract        自动从会话历史提取记忆")
        print("  stats               数据库统计")
        print("  sync                同步MEMORY.md到向量数据库")
        print()
        print("选项:")
        print("  --json              JSON 格式输出")
        print()
        print("v3.0 更新 (P0+P1 整改):")
        print("  - 分类衰变率：strategy/fact/assumption/failure 不同衰变速度")
        print("  - 三层架构：STM(7天TTL) → Staging(48h冷却) → LTM(向量+图谱)")
        print("  - 多策略检索：semantic(0.4) + keyword(0.25) + graph(0.2) + temporal(0.15)")
        print("  - vector_store.py 内嵌到 scripts/")
        return
    
    if args.command == "weekly":
        cmd_weekly(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "vector-search":
        cmd_vector_search(args)
    elif args.command == "multi-search":
        cmd_multi_search(args)
    elif args.command == "mark":
        cmd_mark(args)
    elif args.command == "visualize":
        cmd_visualize(args)
    elif args.command == "decay":
        cmd_decay(args)
    elif args.command == "health":
        cmd_health(args)
    elif args.command == "importance":
        cmd_importance(args)
    elif args.command == "migrate":
        cmd_migrate(args)
    elif args.command == "auto-extract":
        cmd_auto_extract(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "sync":
        cmd_sync(args)
    elif args.command == "conflict":
        cmd_conflict(args)


if __name__ == "__main__":
    main()