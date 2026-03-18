#!/usr/bin/env python3
"""
Memory Tree 🌳 — 置信度驱动的记忆生命周期管理

支持多级语义搜索后端：
1. 本地 Ollama（零成本，推荐）
2. 云端 API（智谱/OpenAI/等，按需配置）
3. 关键词 fallback（无需任何依赖，功能完整但精度较低）

生命周期：🌱萌芽(0.7) → 🌿绿叶(≥0.8) → 🍂黄叶(0.5-0.8) → 🍁枯叶(0.3-0.5) → 🪨土壤(<0.3)
"""

import json
import os
import hashlib
import re
import sys
import math
import urllib.request
from datetime import datetime
from pathlib import Path

# ==================== 路径配置 ====================
WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_MD = WORKSPACE / "MEMORY.md"
DATA_DIR = WORKSPACE / "memory-tree" / "data"
CONFIDENCE_DB = DATA_DIR / "confidence.json"
EMBEDDINGS_DB = DATA_DIR / "embeddings.json"
CONFIG_FILE = DATA_DIR / "config.json"

# ==================== 置信度参数 ====================
DEFAULT_CONFIDENCE = 0.7
GREEN_THRESHOLD = 0.8
YELLOW_THRESHOLD = 0.5
DEAD_THRESHOLD = 0.3
DECAY_P2 = 0.008
DECAY_P1 = 0.004
HIT_BOOST = 0.03
USE_BOOST = 0.08


# ==================== Embedding 后端 ====================

class EmbeddingBackend:
    """基类"""
    name = "unknown"
    def embed(self, text):
        raise NotImplementedError


class OllamaBackend(EmbeddingBackend):
    """本地 Ollama — 零成本，需要 Ollama 运行"""
    name = "ollama"
    def __init__(self, url="http://localhost:11434", model="qwen3-embedding"):
        self.url = url.rstrip("/")
        self.model = model

    def embed(self, text):
        try:
            req = urllib.request.Request(
                f"{self.url}/api/embeddings",
                data=json.dumps({"model": self.model, "prompt": text}).encode(),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())["embedding"]
        except Exception:
            return None

    @staticmethod
    def check_available(url="http://localhost:11434"):
        try:
            req = urllib.request.Request(f"{url.rstrip('/')}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                models = json.loads(resp.read()).get("models", [])
                names = [m["name"] for m in models]
                # 检查有没有 embedding 模型
                embedding_models = [n for n in names if "embed" in n.lower()]
                return bool(embedding_models), embedding_models
        except Exception:
            return False, []


class ZhipuBackend(EmbeddingBackend):
    """智谱 Embedding — 云端 API，需要 API key"""
    name = "zhipu"
    def __init__(self, api_key, model="embedding-3"):
        self.api_key = api_key
        self.model = model
        self.url = "https://open.bigmodel.cn/api/paas/v4/embeddings"

    def embed(self, text):
        try:
            req = urllib.request.Request(
                self.url,
                data=json.dumps({"model": self.model, "input": text}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}"
                }
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())["data"][0]["embedding"]
        except Exception:
            return None


class OpenAIBackend(EmbeddingBackend):
    """OpenAI Embedding — 云端 API，需要 API key"""
    name = "openai"
    def __init__(self, api_key, model="text-embedding-3-small", base_url=None):
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.url = f"{self.base_url}/embeddings"

    def embed(self, text):
        try:
            req = urllib.request.Request(
                self.url,
                data=json.dumps({"model": self.model, "input": text}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}"
                }
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())["data"][0]["embedding"]
        except Exception:
            return None


class KeywordBackend(EmbeddingBackend):
    """关键词 fallback — 无需任何依赖，基于词频的简单相似度"""
    name = "keyword"
    def __init__(self):
        self._cache = {}

    def embed(self, text):
        """生成词袋向量（归一化词频）"""
        import string
        # 中文按字切分 + 英文按词切分
        words = list(text)
        for word in re.findall(r'[a-zA-Z]{2,}', text.lower()):
            words.append(word)
        # 去除标点和空白
        table = str.maketrans('', '', string.punctuation + string.whitespace)
        words = [w for w in words if w.translate(table)]
        if not words:
            return None
        # 词频
        freq = {}
        for w in words:
            freq[w] = freq.get(w, 0) + 1
        # 归一化
        total = len(words)
        return {k: v / total for k, v in freq.items()}


def cosine_sim_vec(a, b):
    """向量余弦相似度（list）"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0


def cosine_sim_dict(a, b):
    """词袋余弦相似度（dict）"""
    common = set(a.keys()) & set(b.keys())
    if not common:
        return 0
    dot = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na > 0 and nb > 0 else 0


def cosine_sim(a, b):
    """自动选择相似度计算方式"""
    if isinstance(a, list) and isinstance(b, list):
        return cosine_sim_vec(a, b)
    elif isinstance(a, dict) and isinstance(b, dict):
        return cosine_sim_dict(a, b)
    return 0


# ==================== 后端自动检测 ====================

def auto_detect_backend():
    """按优先级自动检测可用的 embedding 后端"""
    config = load_json(CONFIG_FILE, {})
    manual = config.get("backend")

    # 1. 用户手动指定的后端
    if manual == "keyword":
        return KeywordBackend()
    elif manual == "ollama":
        url = config.get("ollama_url", "http://localhost:11434")
        model = config.get("ollama_model", "qwen3-embedding")
        return OllamaBackend(url, model)
    elif manual == "zhipu":
        key = config.get("zhipu_api_key", "")
        if key:
            return ZhipuBackend(key)
    elif manual == "openai":
        key = config.get("openai_api_key", "")
        base = config.get("openai_base_url", "")
        if key:
            return OpenAIBackend(key, base_url=base if base else None)

    # 2. 自动检测 Ollama（优先，零成本）
    available, models = OllamaBackend.check_available()
    if available:
        model = config.get("ollama_model")
        if model and model in models:
            return OllamaBackend(model=model)
        # 使用第一个 embedding 模型
        return OllamaBackend(model=models[0])

    # 3. 检测智谱配置
    zhipu_key = config.get("zhipu_api_key") or os.environ.get("ZHIPU_API_KEY", "")
    if zhipu_key:
        return ZhipuBackend(zhipu_key)

    # 4. 检测 OpenAI 配置
    openai_key = config.get("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        base = config.get("openai_base_url") or os.environ.get("OPENAI_BASE_URL", "")
        return OpenAIBackend(openai_key, base_url=base if base else None)

    # 5. 关键词 fallback
    return KeywordBackend()


# ==================== 数据工具 ====================

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
    return hashlib.md5(text.strip().encode()).hexdigest()[:12]


def parse_memory_blocks(content):
    blocks = []
    sections = re.split(r'(?=^## )', content, flags=re.MULTILINE)
    for section in sections:
        section = section.strip()
        if not section:
            continue
        title_match = re.match(r'^##\s+(.+)', section)
        title = title_match.group(1).strip() if title_match else "无标题"
        priority = "P2"
        if "[P0]" in title:
            priority = "P0"
        elif "[P1]" in title:
            priority = "P1"
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
    entry = db.get(block_hash, {})
    if entry:
        conf = entry.get("confidence", DEFAULT_CONFIDENCE)
        last_access = entry.get("last_access")
        if last_access and priority != "P0":
            last = datetime.fromisoformat(last_access)
            days_ago = (datetime.now() - last).days
            rate = DECAY_P1 if priority == "P1" else DECAY_P2
            conf = max(0, conf - (days_ago * rate))
        return round(conf, 3)
    return DEFAULT_CONFIDENCE


def get_status(confidence):
    if confidence >= GREEN_THRESHOLD:
        return "🌿", "绿叶"
    elif confidence >= YELLOW_THRESHOLD:
        return "🍂", "黄叶"
    elif confidence >= DEAD_THRESHOLD:
        return "🍁", "枯叶"
    else:
        return "🪨", "土壤"


# ==================== 核心命令 ====================

def cmd_index():
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
            db[h]["last_access"] = datetime.now().isoformat()
    save_json(CONFIDENCE_DB, db)
    print(f"✅ 索引完成: {len(blocks)} 条知识, {new_count} 条新增")


def cmd_visualize():
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
    backend = auto_detect_backend()
    print(f"🌳 记忆树 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    print(f"├── 📊 健康度: {healthy:.0f}%")
    print(f"├── 🔍 搜索后端: {backend.name}")
    print(f"├── 🍃 总计: {total}")
    print(f"│   ├── 🌿 绿叶: {statuses['🌿']}")
    print(f"│   ├── 🍂 黄叶: {statuses['🍂']}")
    print(f"│   ├── 🍁 枯叶: {statuses['🍁']}")
    print(f"│   └── 🪨 土壤: {statuses['🪨']}")
    print()
    for icon, conf, title in sorted(details, key=lambda x: x[1]):
        bar = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))
        print(f"  {icon} {bar} {conf:.2f} | {title}")


def cmd_decay():
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
            entry["last_access"] = now.isoformat()
            decayed += 1
    save_json(CONFIDENCE_DB, db)
    print(f"🍂 衰减完成: {decayed} 条知识已更新")


def cmd_search(query):
    if not MEMORY_MD.exists():
        print("❌ MEMORY.md 不存在")
        return
    backend = auto_detect_backend()
    query_vec = backend.embed(query)
    if query_vec is None:
        print(f"❌ {backend.name} embedding 获取失败")
        return
    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)
    db = load_json(CONFIDENCE_DB, {})
    emb_cache = load_json(EMBEDDINGS_DB, {})
    results = []
    for block in blocks:
        h = block["hash"]
        # 获取或计算 embedding（关键词模式不缓存）
        if isinstance(query_vec, dict):
            # 关键词模式：实时计算
            vec = backend.embed(block["full_text"])
        else:
            # 向量模式：使用缓存
            if h not in emb_cache:
                vec = backend.embed(block["full_text"])
                if vec:
                    emb_cache[h] = vec
            else:
                vec = emb_cache[h]
        if vec is None:
            continue
        sim = cosine_sim(query_vec, vec)
        conf = get_confidence(db, h, block["priority"])
        if sim > 0.2:
            results.append({
                "similarity": round(sim, 3),
                "confidence": conf,
                "icon": get_status(conf)[0],
                "title": block["title"],
                "body": block["body"][:200],
                "hash": h,
                "priority": block["priority"]
            })
    # 关键词模式不缓存向量
    if not isinstance(query_vec, dict):
        save_json(EMBEDDINGS_DB, emb_cache)
    # 更新命中计数
    for r in sorted(results, key=lambda x: x["similarity"], reverse=True):
        h = r["hash"]
        if h in db:
            db[h]["confidence"] = round(min(1.0, db[h]["confidence"] + HIT_BOOST), 3)
            db[h]["last_access"] = datetime.now().isoformat()
            db[h]["hit_count"] = db[h].get("hit_count", 0) + 1
    save_json(CONFIDENCE_DB, db)
    results.sort(key=lambda x: x["similarity"], reverse=True)
    if not results:
        print("🔍 未找到相关记忆")
        return
    print(f"🔍 找到 {len(results)} 条相关记忆 (后端: {backend.name}):")
    for r in results:
        print(f"\n  {r['icon']} 相似:{r['similarity']:.2f} 置信:{r['confidence']:.2f} | {r['title']}")
        preview = r['body'][:120].replace('\n', ' ')
        print(f"     {preview}...")


def cmd_use(block_hash_prefix):
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
    print(f"\n💡 使用 --auto 自动归档")


def cmd_cleanup_auto():
    db = load_json(CONFIDENCE_DB, {})
    archive = load_json(DATA_DIR / "archive.json", [])
    dead_hashes = set()
    for h, entry in list(db.items()):
        conf = get_confidence(db, h, entry.get("priority", "P2"))
        if conf < DEAD_THRESHOLD and entry.get("priority") != "P0":
            archive.append({
                "hash": h,
                "title": entry.get("title", ""),
                "confidence": conf,
                "archived_at": datetime.now().isoformat()
            })
            dead_hashes.add(h)
            del db[h]
    save_json(CONFIDENCE_DB, db)
    save_json(DATA_DIR / "archive.json", archive)
    if dead_hashes:
        print(f"🪨 已归档 {len(dead_hashes)} 条枯叶知识")
    else:
        print("✅ 没有需要归档的枯叶")


def cmd_setup():
    """一键设置"""
    import subprocess
    workspace = str(WORKSPACE)
    script = str(WORKSPACE / "memory-tree" / "core" / "memory_tree.py")
    python = sys.executable

    # 检测后端
    backend = auto_detect_backend()
    print(f"🔍 自动检测到搜索后端: {backend.name}")

    if backend.name == "keyword":
        print("  ⚠️ 未检测到 Ollama 或云端 API，使用关键词模式")
        print("  💡 安装 Ollama 可解锁语义搜索：")
        print("     curl -fsSL https://ollama.ai/install.sh | sh")
        print("     ollama pull qwen3-embedding")
        print()

    # 保存配置
    config = load_json(CONFIG_FILE, {})
    config["backend"] = backend.name
    if isinstance(backend, OllamaBackend):
        config["ollama_model"] = backend.model
    save_json(CONFIG_FILE, config)

    # 首次索引
    print("🌱 执行首次索引...")
    cmd_index()
    print()

    # 创建 cron 任务提示
    print("⏰ 建议设置自动定时任务：")
    print("  每天凌晨3点: 自动衰减")
    print("  每周日凌晨4点: 自动归档枯叶")
    print()
    print("  crontab -e")
    print(f'  0 3 * * * cd {workspace} && {python} {script} decay')
    print(f'  0 4 * * 0 cd {workspace} && {python} {script} cleanup --auto')
    print()
    print("  或使用 OpenClaw cron（由 agent 自动配置）")
    print()
    print("✅ 设置完成！记忆树将自动生长。")


def cmd_config(key=None, value=None):
    """查看/修改配置"""
    config = load_json(CONFIG_FILE, {})
    if key and value:
        config[key] = value
        save_json(CONFIG_FILE, config)
        print(f"✅ 已设置 {key} = {value}")
    elif key:
        val = config.get(key, "(未设置)")
        print(f"  {key} = {val}")
    else:
        print("📋 当前配置:")
        for k, v in config.items():
            if "key" in k.lower() or "secret" in k.lower():
                v = v[:8] + "..." if isinstance(v, str) and len(v) > 8 else v
            print(f"  {k} = {v}")
        print()
        print("📌 可用配置项:")
        print("  backend       搜索后端: ollama / zhipu / openai / keyword")
        print("  ollama_url    Ollama 地址 (默认: http://localhost:11434)")
        print("  ollama_model  Ollama embedding 模型")
        print("  zhipu_api_key 智谱 API key")
        print("  openai_api_key OpenAI API key")
        print("  openai_base_url OpenAI 兼容 API 地址")
        print()
        print("用法: python3 memory_tree.py config <key> <value>")


# ==================== CLI ====================

def main():
    if len(sys.argv) < 2:
        print("🌳 Memory Tree — 记忆生命周期管理")
        print()
        print("用法:")
        print("  setup              一键设置（首次使用）")
        print("  index              索引 MEMORY.md")
        print("  visualize          查看记忆树状态")
        print("  search \"查询\"       语义搜索记忆")
        print("  use <hash>         标记知识被使用")
        print("  decay              执行置信度衰减")
        print("  cleanup            查看枯叶")
        print("  cleanup --auto     自动归档枯叶")
        print("  config [key] [val] 查看/修改配置")
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
    elif cmd == "config":
        cmd_config(
            sys.argv[2] if len(sys.argv) > 2 else None,
            sys.argv[3] if len(sys.argv) > 3 else None
        )
    else:
        print(f"❌ 未知命令: {cmd}")


if __name__ == "__main__":
    main()
