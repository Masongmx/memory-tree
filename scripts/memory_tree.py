#!/usr/bin/env python3
"""
Memory Tree 🌳 v2.3 — 简化的记忆管理

核心功能：
1. weekly - 周报生成（本周新记、本周遗忘、永久记忆清单）
2. search - 语义搜索（关键词模式，无外部依赖）
3. vector-search - 向量搜索（语义模式，使用本地 embedding）
4. mark - 永久标记（📌）
5. decay - 遗忘预测（基于 Ebbinghaus 曲线）
6. health - 健康度四维评分

v2.3 变更：
- 新增 vector-search 命令，使用本地 qwen3-embedding 模型
- 语义搜索替代关键词匹配，更准确
- 自动同步 MEMORY.md 到 SQLite 向量存储

v2.2 变更：
- 修复 Memory 解析（状态机实现，支持嵌套 ##）
- 添加 MEMORY.md 备份
- 添加遗忘曲线预测
- 添加健康评分
- 添加 --json 输出支持
- 修复飞书推送（使用 message tool 格式）
- 抽取公共模块
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


# ==================== Memory 解析（状态机实现）====================

def parse_memory_blocks(content):
    """解析 Memory 块 — 状态机实现，支持嵌套 ##"""
    blocks = []
    current_block = None
    
    for line in content.split('\n'):
        # 检测 ## 标题（但不匹配 ### 及更深层级）
        match = re.match(r'^## (.+)$', line)
        if match:
            # 保存上一个块
            if current_block is not None:
                blocks.append(current_block)
            # 开始新块
            current_block = {
                "title": match.group(1).strip(),
                "body": "",
                "is_permanent": False,
                "priority": "P2"
            }
            # 检测标题中的永久标记
            if '📌' in match.group(1) or '[P0]' in match.group(1):
                current_block["is_permanent"] = True
                current_block["priority"] = "P0"
            elif '[P1]' in match.group(1):
                current_block["priority"] = "P1"
        elif current_block is not None:
            current_block["body"] += line + '\n'
            # 检测永久标记
            if '📌' in line or '[P0]' in line:
                current_block["is_permanent"] = True
                current_block["priority"] = "P0"
    
    # 保存最后一个块
    if current_block is not None:
        blocks.append(current_block)
    
    # 计算每个块的 hash 和 full_text
    for block in blocks:
        block["hash"] = text_hash(block["title"] + block["body"])
        block["full_text"] = block["title"] + "\n" + block["body"]
    
    return blocks


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

def calculate_decay_weight(days_since_last_mention):
    """基于 Ebbinghaus 曲线计算衰减权重"""
    # R = e^(-t/S)，S=5 为衰减常数
    return math.exp(-days_since_last_mention / 5)


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


def cmd_decay(args):
    """遗忘预测 — 显示即将过期的记忆"""
    results = {"blocks": [], "to_cleanup": []}
    
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
            "status": "ok" if weight > 0.3 else "warning" if weight > 0.1 else "danger"
        }
        results["blocks"].append(entry)
        
        if weight < 0.1:
            results["to_cleanup"].append(block["title"])
    
    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results
    
    # 终端输出
    print(f"🌳 记忆树 — 遗忘预测 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")
    
    danger = [b for b in results["blocks"] if b["status"] == "danger"]
    warning = [b for b in results["blocks"] if b["status"] == "warning"]
    ok = [b for b in results["blocks"] if b["status"] == "ok"]
    permanent = [b for b in results["blocks"] if b["status"] == "permanent"]
    
    if danger:
        print(f"🔴 危险 ({len(danger)} 条，权重 < 0.1):")
        for b in danger[:10]:
            print(f"   {b['title']} (权重: {b['weight']}, {b['days']}天前)")
        print()
    
    if warning:
        print(f"🟡 警告 ({len(warning)} 条，权重 0.1-0.3):")
        for b in warning[:10]:
            print(f"   {b['title']} (权重: {b['weight']}, {b['days']}天前)")
        print()
    
    if ok:
        print(f"🟢 健康 ({len(ok)} 条，权重 > 0.3)")
    
    if permanent:
        print(f"📌 永久 ({len(permanent)} 条)")
    
    if results["to_cleanup"]:
        print(f"\n💡 建议清理的过期记忆:")
        for title in results["to_cleanup"][:10]:
            print(f"   • {title}")
    
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


# ==================== 自动提取 ====================

def cmd_auto_extract(args):
    """自动从会话历史提取记忆"""
    from memory_extractor import extract_from_all_sessions
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
    parser = argparse.ArgumentParser(description="🌳 Memory Tree v2.2 — 简化的记忆管理")
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
    
    # mark
    mark_parser = subparsers.add_parser("mark", help="标记为永久记忆")
    mark_parser.add_argument("keyword", help="标题关键词")
    mark_parser.add_argument("--json", action="store_true", help="JSON 输出")
    
    # visualize
    viz_parser = subparsers.add_parser("visualize", help="查看记忆概览")
    viz_parser.add_argument("--json", action="store_true", help="JSON 输出")
    
    # decay
    decay_parser = subparsers.add_parser("decay", help="遗忘预测")
    decay_parser.add_argument("--json", action="store_true", help="JSON 输出")
    
    # health
    health_parser = subparsers.add_parser("health", help="健康度评分")
    health_parser.add_argument("--json", action="store_true", help="JSON 输出")
    
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
    
    args = parser.parse_args()
    
    # 处理 search 的多个关键词
    if hasattr(args, 'query') and isinstance(args.query, list):
        args.query = " ".join(args.query)
    
    if args.command is None:
        print("🌳 Memory Tree v2.4 — 简化的记忆管理")
        print()
        print("用法:")
        print("  weekly              生成周报（本周新记、遗忘、永久清单）")
        print("  search \"查询\"       搜索记忆（关键词模式）")
        print("  vector-search \"查询\" 向量搜索记忆（语义模式）")
        print("  mark \"标题关键词\"   标记为永久记忆")
        print("  visualize           查看记忆概览")
        print("  decay               遗忘预测（Ebbinghaus 曲线）")
        print("  health              健康度四维评分")
        print("  auto-extract        自动从会话历史提取记忆")
        print("  stats               数据库统计")
        print("  sync                同步MEMORY.md到向量数据库")
        print()
        print("选项:")
        print("  --json              JSON 格式输出")
        print()
        print("v2.4 新增:")
        print("  - stats/sync 命令用于数据库管理")
        print("  - 新增 category/confidence/source/last_mention 字段")
        return
    
    if args.command == "weekly":
        cmd_weekly(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "vector-search":
        cmd_vector_search(args)
    elif args.command == "mark":
        cmd_mark(args)
    elif args.command == "visualize":
        cmd_visualize(args)
    elif args.command == "decay":
        cmd_decay(args)
    elif args.command == "health":
        cmd_health(args)
    elif args.command == "auto-extract":
        cmd_auto_extract(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "sync":
        cmd_sync(args)


if __name__ == "__main__":
    main()