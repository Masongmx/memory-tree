#!/usr/bin/env python3
"""
Memory Tree - 公共工具模块
提供共享的 imports、路径配置和通用工具函数
"""

import json
import os
import hashlib
import re
import sys
import shutil
import argparse
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# ==================== 路径配置 ====================
WORKSPACE = Path.home() / ".openclaw" / "workspace"
OPENCLAW_CONFIG = WORKSPACE / "config.json"
MEMORY_MD = WORKSPACE / "MEMORY.md"
MEMORY_DIR = WORKSPACE / "memory"
DATA_DIR = WORKSPACE / "memory-tree" / "data"
CONFIDENCE_DB = DATA_DIR / "confidence.json"
WEEKLY_REPORTS_DIR = MEMORY_DIR / "weekly-reports"
BACKUP_DIR = WORKSPACE / ".memory-backup"

# 三层架构数据库路径
STM_DB = DATA_DIR / "stm.db"
STAGING_DB = DATA_DIR / "staging.db"
LTM_DB = DATA_DIR / "ltm.db"

# 导入公共模块
sys.path.insert(0, str(Path(__file__).parent))
try:
    sys.path.insert(1, str(Path(__file__).parent.parent.parent / "common"))
    from utils import (
        load_json, save_json, file_hash, file_age_days, text_hash,
        estimate_tokens, fmt_tokens, fmt_size,
        parse_memory_blocks,
        detect_enabled_channels, get_feishu_chat_id,
        WORKSPACE as COMMON_WORKSPACE, OPENCLAW_CONFIG as COMMON_OPENCLAW_CONFIG
    )
except ImportError:
    pass

# ==================== 内联工具函数 ====================

def load_json(path, default=None):
    """加载 JSON 文件"""
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}

def save_json(path, data):
    """保存 JSON 文件"""
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2))

def file_hash(path):
    """计算文件 MD5 哈希（取前8位）"""
    return hashlib.md5(Path(path).read_bytes()).hexdigest()[:8]

def file_age_days(path):
    """计算文件年龄（天）"""
    mtime = datetime.fromtimestamp(Path(path).stat().st_mtime)
    return (datetime.now() - mtime).days

def text_hash(text):
    """计算文本 MD5 哈希（取前8位）"""
    return hashlib.md5(text.encode()).hexdigest()[:8]

def estimate_tokens(text):
    """估算 token 数量（简单按4字符=1token）"""
    return len(text) // 4

def fmt_tokens(tokens):
    """格式化 token 数量"""
    return f"{tokens:,}"

def fmt_size(size):
    """格式化文件大小"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"

def parse_memory_blocks(content):
    """解析 MEMORY.md 格式的记忆块"""
    blocks = []
    if not content:
        return blocks

    current_block = None
    lines = content.split('\n')
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('## '):
            if current_block:
                blocks.append(current_block)
            title = line[3:].strip()
            is_permanent = '📌' in title or '[P0]' in title
            current_block = {
                'title': title.replace('📌', '').replace('[P0]', '').replace('[P1]', '').strip(),
                'body': '',
                'is_permanent': is_permanent,
                'priority': 'P0' if is_permanent else 'P2',
                'full_text': title
            }
        elif current_block is not None:
            current_block['body'] += line + '\n'
            current_block['full_text'] += '\n' + line
            if '[P0]' in line or '[P1]' in line:
                current_block['is_permanent'] = True
                current_block['priority'] = 'P0' if '[P0]' in line else 'P1'
        i += 1

    if current_block:
        blocks.append(current_block)

    for block in blocks:
        block['body'] = block['body'].strip()
        block['full_text'] = block['full_text'].strip()
    return blocks

def detect_enabled_channels():
    """检测已启用的推送渠道"""
    return []

def get_feishu_chat_id(config):
    """获取飞书 chat_id"""
    return None

def estimate_days_since_mention(content):
    """估算内容最后提及时间"""
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
    return 7

def keyword_similarity(query, text):
    """关键词相似度计算"""
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

def get_permanent_memories():
    """提取 MEMORY.md 中的永久记忆"""
    if not MEMORY_MD.exists():
        return []

    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)

    permanent = []
    for block in blocks:
        if block["is_permanent"] or block["priority"] == "P0":
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

def send_feishu_report(content, title="记忆树报告"):
    """飞书推送 — 输出可被 message tool 使用的格式"""
    print(f"[FEISHU_REPORT]")
    print(f"title: {title}")
    print(f"content: {content}")
    print(f"[/FEISHU_REPORT]")
    return True

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
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        if resp.status_code != 200:
            missing.append("ollama (服务未运行)")
    except:
        missing.append("ollama (服务未启动，请运行 ollama serve)")
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        models = [m["name"] for m in resp.json().get("models", [])]
        if not any("qwen3" in m for m in models):
            missing.append("qwen3:8b 模型 (请运行 ollama pull qwen3:8b)")
    except:
        pass
    return missing
