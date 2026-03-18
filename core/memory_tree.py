#!/usr/bin/env python3
"""
memory-tree 核心模块
基于置信度的记忆生命周期管理 + 本地 Ollama embedding 语义搜索

架构：
- MEMORY.md → 索引（每条知识段都有置信度）
- memory/*.md → 日志源（只读，提供养分）
- data/confidence.json → 置信度数据库
- data/embeddings.json → embedding 向量缓存

生命周期：🌱萌芽(0.7) → 🌿绿叶(≥0.8) → 🍂黄叶(0.5-0.8) → 🍁枯叶(0.3-0.5) → 🪨土壤(<0.3)
"""

import json
import os
import hashlib
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 路径配置
WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_MD = WORKSPACE / "MEMORY.md"
MEMORY_DIR = WORKSPACE / "memory"
DATA_DIR = WORKSPACE / "memory-tree" / "data"
CONFIDENCE_DB = DATA_DIR / "confidence.json"
EMBEDDINGS_DB = DATA_DIR / "embeddings.json"

OLLAMA_URL = "http://localhost:11434/api/embeddings"
EMBEDDING_MODEL = "qwen3-embedding:latest"

# 置信度参数
DEFAULT_CONFIDENCE = 0.7      # 新知识萌芽
GREEN_THRESHOLD = 0.8          # 绿叶
YELLOW_THRESHOLD = 0.5         # 黄叶
DEAD_THRESHOLD = 0.3           # 枯叶/土壤
DECAY_P2 = 0.008               # P2 每天衰减
DECAY_P1 = 0.004               # P1 每天衰减
DECAY_P0 = 0.0                 # P0 永不衰减
HIT_BOOST = 0.03               # 被搜索命中
USE_BOOST = 0.08               # 被引用使用


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path, default=None):
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default or {}


def save_json(path, data):
    ensure_data_dir()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def text_hash(text):
    """生成文本指纹，用于标识同一条知识"""
    return hashlib.md5(text.strip().encode()).hexdigest()[:12]


def parse_memory_blocks(content):
    """
    解析 MEMORY.md 为知识块列表
    每个块 = 标题 + 正文
    """
    blocks = []
    # 按 ## 标题分割
    sections = re.split(r'(?=^## )', content, flags=re.MULTILINE)
    for section in sections:
        section = section.strip()
        if not section:
            continue
        # 提取标题
        title_match = re.match(r'^##\s+(.+)', section)
        title = title_match.group(1).strip() if title_match else "无标题"
        # 提取优先级
        priority = "P2"
        if "[P0]" in title:
            priority = "P0"
        elif "[P1]" in title:
            priority = "P1"
        # 提取正文（去掉标题行）
        lines = section.split('\n')
        body = '\n'.join(lines[1:]).strip()
        blocks.append({
            "title": title,
            "body": body,
            "priority": priority,
            "hash": text_hash(title + body),
            "full_text": title + "\n" + body
        })
    return blocks


def get_confidence(db, block_hash, priority):
    """获取某条知识的当前置信度"""
    entry = db.get(block_hash, {})
    if entry:
        # 根据上次访问时间计算衰减
        conf = entry.get("confidence", DEFAULT_CONFIDENCE)
        last_access = entry.get("last_access")
        if last_access and priority != "P0":
            last = datetime.fromisoformat(last_access)
            days_ago = (datetime.now() - last).days
            decay_rate = DECAY_P1 if priority == "P1" else DECAY_P2
            conf = max(0, conf - (days_ago * decay_rate))
        return round(conf, 3)
    return DEFAULT_CONFIDENCE


def get_status(confidence):
    """返回知识状态"""
    if confidence >= GREEN_THRESHOLD:
        return "🌿", "绿叶"
    elif confidence >= YELLOW_THRESHOLD:
        return "🍂", "黄叶"
    elif confidence >= DEAD_THRESHOLD:
        return "🍁", "枯叶"
    else:
        return "🪨", "土壤"


def get_embed(text):
    """调用 Ollama 获取 embedding 向量"""
    import urllib.request
    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=json.dumps({"model": EMBEDDING_MODEL, "prompt": text}).encode(),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result["embedding"]
    except Exception as e:
        print(f"⚠️ Embedding 获取失败: {e}", file=sys.stderr)
        return None


def cosine_sim(a, b):
    """余弦相似度"""
    import math
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0
    return dot / (norm_a * norm_b)


# ==================== 核心命令 ====================

def cmd_setup():
    """一键设置：创建自动索引、衰减、清理的 cron 任务"""
    import subprocess
    workspace = str(WORKSPACE)
    script = f"{workspace}/memory-tree/core/memory_tree.py"
    python = sys.executable

    # 检查 openclaw cron 是否可用
    try:
        result = subprocess.run(
            ["openclaw", "cron", "list", "--json"],
            capture_output=True, text=True, timeout=10
        )
        has_cron = result.returncode == 0
    except Exception:
        has_cron = False

    if has_cron:
        print("🔧 通过 OpenClaw cron 设置自动任务...")
        # 每天凌晨3点衰减
        # 每周日凌晨4点清理归档
        # 注意：cron job 需要通过 openclaw CLI 或 gateway API 添加
        # 这里提供手动命令
        print()
        print("请运行以下命令完成设置（或由 agent 自动执行）：")
        print()
        print("  openclaw cron add \\")
        print('    --name "memory-tree-daily-decay" \\')
        print('    --schedule "0 3 * * *" \\')
        print(f'    --cmd "cd {workspace} && {python} {script} decay"')
        print()
        print("  openclaw cron add \\")
        print('    --name "memory-tree-weekly-cleanup" \\')
        print('    --schedule "0 4 * * 0" \\')
        print(f'    --cmd "cd {workspace} && {python} {script} cleanup --auto"')
        print()
    else:
        print("🔧 请设置 crontab 定时任务：")
        print("  crontab -e")
        print(f"  # 每天凌晨3点衰减")
        print(f"  0 3 * * * cd {workspace} && {python} {script} decay >> /tmp/memory-tree.log 2>&1")
        print(f"  # 每周日凌晨4点自动归档")
        print(f"  0 4 * * 0 cd {workspace} && {python} {script} cleanup --auto >> /tmp/memory-tree.log 2>&1")
        print()

    # 立即执行一次索引
    print("🌱 执行首次索引...")
    cmd_index()
    print()
    print("✅ 设置完成！记忆树将自动生长。")


def cmd_index():
    """索引 MEMORY.md 中的所有知识块"""
    if not MEMORY_MD.exists():
        print("❌ MEMORY.md 不存在")
        return

    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    db = load_json(CONFIDENCE_DB, {})

    new_count = 0
    for block in blocks:
        h = block["hash"]
        if h not in db:
            db[h] = {
                "confidence": DEFAULT_CONFIDENCE,
                "priority": block["priority"],
                "created": datetime.now().isoformat(),
                "last_access": datetime.now().isoformat(),
                "hit_count": 0,
                "use_count": 0,
                "title": block["title"]
            }
            new_count += 1
        else:
            # 更新最后访问时间
            db[h]["last_access"] = datetime.now().isoformat()

    save_json(CONFIDENCE_DB, db)
    print(f"✅ 索引完成: {len(blocks)} 条知识, {new_count} 条新增")


def cmd_visualize():
    """可视化记忆树状态"""
    if not MEMORY_MD.exists():
        print("❌ MEMORY.md 不存在")
        return

    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    db = load_json(CONFIDENCE_DB, {})

    statuses = {"🌿": 0, "🍂": 0, "🍁": 0, "🪨": 0}
    details = []

    for block in blocks:
        h = block["hash"]
        conf = get_confidence(db, h, block["priority"])
        icon, label = get_status(conf)
        statuses[icon] += 1
        details.append((icon, conf, block["title"]))

    total = len(blocks)
    healthy = statuses["🌿"] / total * 100 if total else 0

    print(f"🌳 记忆树状态 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    print(f"├── 📊 健康度: {healthy:.0f}%")
    print(f"├── 🍃 总计: {total}")
    print(f"│   ├── 🌿 绿叶: {statuses['🌿']}")
    print(f"│   ├── 🍂 黄叶: {statuses['🍂']}")
    print(f"│   ├── 🍁 枯叶: {statuses['🍁']}")
    print(f"│   └── 🪨 土壤: {statuses['🪨']}")
    print()

    for icon, conf, title in sorted(details, key=lambda x: x[1]):
        print(f"  {icon} {conf:.2f} | {title}")


def cmd_decay():
    """衰减不活跃的知识"""
    db = load_json(CONFIDENCE_DB, {})
    if not db:
        print("📭 暂无记忆数据")
        return

    now = datetime.now()
    decayed = 0
    for h, entry in db.items():
        priority = entry.get("priority", "P2")
        if priority == "P0":
            continue
        last = datetime.fromisoformat(entry.get("last_access", now.isoformat()))
        days_ago = (now - last).days
        if days_ago > 0:
            rate = DECAY_P1 if priority == "P1" else DECAY_P2
            entry["confidence"] = round(max(0, entry["confidence"] - (days_ago * rate)), 3)
            entry["last_access"] = now.isoformat()  # 重置衰减周期
            decayed += 1

    save_json(CONFIDENCE_DB, db)
    print(f"🍂 衰减完成: {decayed} 条知识已更新")


def cmd_search(query):
    """语义搜索记忆"""
    if not MEMORY_MD.exists():
        print("❌ MEMORY.md 不存在")
        return

    query_vec = get_embed(query)
    if not query_vec:
        print("❌ Embedding 获取失败，请确认 Ollama 正在运行")
        return

    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    db = load_json(CONFIDENCE_DB, {})
    emb_cache = load_json(EMBEDDINGS_DB, {})

    results = []
    for block in blocks:
        h = block["hash"]
        # 获取或计算 embedding
        if h not in emb_cache:
            vec = get_embed(block["full_text"])
            if vec:
                emb_cache[h] = vec
            else:
                continue
        vec = emb_cache[h]

        sim = cosine_sim(query_vec, vec)
        conf = get_confidence(db, h, block["priority"])

        if sim > 0.3:  # 相似度阈值
            results.append({
                "similarity": round(sim, 3),
                "confidence": conf,
                "icon": get_status(conf)[0],
                "title": block["title"],
                "body": block["body"][:200],
                "hash": h,
                "priority": block["priority"]
            })

    save_json(EMBEDDINGS_DB, emb_cache)

    # 更新命中计数（提升置信度）
    for r in sorted(results, key=lambda x: x["similarity"], reverse=True):
        h = r["hash"]
        if h in db:
            db[h]["confidence"] = round(min(1.0, db[h]["confidence"] + HIT_BOOST), 3)
            db[h]["last_access"] = datetime.now().isoformat()
            db[h]["hit_count"] = db[h].get("hit_count", 0) + 1

    save_json(CONFIDENCE_DB, db)

    # 输出结果
    results.sort(key=lambda x: x["similarity"], reverse=True)
    if not results:
        print("🔍 未找到相关记忆")
        return

    print(f"🔍 找到 {len(results)} 条相关记忆:")
    for r in results:
        print(f"\n  {r['icon']} 相似度:{r['similarity']:.2f} 置信度:{r['confidence']:.2f} | {r['title']}")
        print(f"     {r['body'][:150]}...")


def cmd_use(block_hash_prefix):
    """标记某条知识被使用（大幅提升置信度）"""
    db = load_json(CONFIDENCE_DB, {})
    matched = False
    for h, entry in db.items():
        if h.startswith(block_hash_prefix):
            entry["confidence"] = round(min(1.0, entry.get("confidence", 0.5) + USE_BOOST), 3)
            entry["last_access"] = datetime.now().isoformat()
            entry["use_count"] = entry.get("use_count", 0) + 1
            matched = True
            print(f"🌿 已使用: {entry.get('title', h)} (置信度: {entry['confidence']:.2f})")

    if matched:
        save_json(CONFIDENCE_DB, db)
    else:
        print(f"❌ 未找到匹配的知识 (前缀: {block_hash_prefix})")


def cmd_cleanup():
    """清理枯叶（低置信度知识，提示用户决定）"""
    db = load_json(CONFIDENCE_DB, {})
    if not db:
        print("📭 暂无记忆数据")
        return

    dead = []
    for h, entry in db.items():
        conf = get_confidence(db, h, entry.get("priority", "P2"))
        if conf < DEAD_THRESHOLD:
            dead.append((h, conf, entry.get("title", "未知")))

    if not dead:
        print("✅ 没有需要清理的枯叶")
        return

    print(f"🪨 发现 {len(dead)} 条枯叶/土壤知识:")
    for h, conf, title in sorted(dead, key=lambda x: x[1]):
        print(f"  🪨 {conf:.2f} | {title} (hash: {h})")
    print(f"\n💡 使用 --auto 自动归档，或手动在 MEMORY.md 中清理")


def cmd_cleanup_auto():
    """自动归档枯叶"""
    db = load_json(CONFIDENCE_DB, {})
    archive = load_json(DATA_DIR / "archive.json", [])

    dead_hashes = set()
    for h, entry in db.items():
        conf = get_confidence(db, h, entry.get("priority", "P2"))
        if conf < DEAD_THRESHOLD and entry.get("priority") != "P0":
            archive.append({
                "hash": h,
                "title": entry.get("title", ""),
                "confidence": conf,
                "archived_at": datetime.now().isoformat(),
                "body_preview": ""  # 不存储正文，只记录存在过
            })
            dead_hashes.add(h)
            del db[h]

    save_json(CONFIDENCE_DB, db)
    save_json(DATA_DIR / "archive.json", archive)

    if dead_hashes:
        print(f"🪨 已归档 {len(dead_hashes)} 条枯叶知识")
    else:
        print("✅ 没有需要归档的枯叶")


# ==================== CLI ====================

def main():
    if len(sys.argv) < 2:
        print("🌳 Memory Tree - 记忆生命周期管理")
        print()
        print("用法:")
        print("  python3 memory_tree.py index          索引 MEMORY.md")
        print("  python3 memory_tree.py visualize       查看记忆树状态")
        print("  python3 memory_tree.py decay           执行衰减")
        print("  python3 memory_tree.py search \"查询\"   语义搜索")
        print("  python3 memory_tree.py use <hash>      标记使用")
        print("  python3 memory_tree.py cleanup         查看枯叶")
        print("  python3 memory_tree.py cleanup --auto  自动归档枯叶")
        return

    cmd = sys.argv[1]
    if cmd == "setup":
        cmd_setup()
    elif cmd == "index":
        cmd_index()
    elif cmd == "visualize":
        cmd_visualize()
    elif cmd == "decay":
        cmd_decay()
    elif cmd == "search":
        query = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
        if not query:
            print("❌ 请提供搜索内容")
            return
        cmd_search(query)
    elif cmd == "use":
        if len(sys.argv) < 3:
            print("❌ 请提供知识 hash")
            return
        cmd_use(sys.argv[2])
    elif cmd == "cleanup":
        if "--auto" in sys.argv:
            cmd_cleanup_auto()
        else:
            cmd_cleanup()
    else:
        print(f"❌ 未知命令: {cmd}")


if __name__ == "__main__":
    main()
