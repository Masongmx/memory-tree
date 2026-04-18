#!/usr/bin/env python3
"""
Memory Tree - 遗忘曲线 + 健康评分模块
提供基于分类衰变率的记忆衰减计算和健康度评分
"""

import json
import math
from datetime import datetime, timedelta

from memory_utils import (
    MEMORY_MD, parse_memory_blocks, estimate_days_since_mention
)

# ==================== 遗忘曲线 ====================

# 分类衰变率
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
    """
    full_text = (title + " " + content).lower()

    failure_keywords = [
        "失败", "错误", "教训", "踩坑", "坑", "bug", "问题", "故障",
        "避免", "不要", "禁止", "杜绝", "惨痛", "惨了", "翻车",
        "错", "误", "fail", "error", "bug", "issue"
    ]
    for kw in failure_keywords:
        if kw in full_text:
            return "failure"

    strategy_keywords = [
        "策略", "规则", "方法", "流程", "原则", "计划", "方案", "步骤",
        "习惯", "偏好", "风格", "最佳实践", "推荐", "建议",
        "strategy", "rule", "method", "process", "plan"
    ]
    for kw in strategy_keywords:
        if kw in full_text:
            return "strategy"

    assumption_keywords = [
        "假设", "猜测", "推测", "可能", "大概", "也许", "似乎",
        "暂定", "待定", "暂且", "临时", "试",
        "assume", "guess", "maybe", "probably", "temporary"
    ]
    for kw in assumption_keywords:
        if kw in full_text:
            return "assumption"

    fact_keywords = [
        "事实", "数据", "结果", "配置", "参数", "设置", "值",
        "是", "等于", "位于", "属于", "版本", "地址", "路径",
        "fact", "data", "result", "config", "value", "version"
    ]
    for kw in fact_keywords:
        if kw in full_text:
            return "fact"

    return "default"


def get_category_decay_rate(category):
    """根据记忆类型获取衰变率"""
    return DECAY_RATES.get(category, DECAY_RATES["default"])


def calculate_strength(days_since_last_recall, category="default", recall_count=0):
    """
    基于 Ebbinghaus 遗忘曲线计算记忆保留率（分类衰变 + recall_count 强化）

    公式: R = e^(-t * decay_rate) + (recall_count * RECALL_BOOST_FACTOR)
    """
    if days_since_last_recall <= 0:
        return 1.0

    decay_rate = get_category_decay_rate(category)
    base_strength = math.exp(-days_since_last_recall * decay_rate)
    boosted_strength = base_strength + (recall_count * RECALL_BOOST_FACTOR)
    return min(1.0, boosted_strength)


def calculate_decay_weight(days_since_last_mention, decay_constant=None, category="default", access_count=0):
    """[兼容旧接口] 基于 Ebbinghaus 遗忘曲线计算记忆保留率"""
    return calculate_strength(days_since_last_mention, category, access_count)


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
    if len(blocks) >= 20:
        return 100
    return round(len(blocks) / 20 * 100, 1)


def calculate_conflict_score(blocks):
    """计算冲突率"""
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
        "freshness": calculate_freshness_score(blocks),
        "coverage": calculate_coverage_score(blocks),
        "conflict": calculate_conflict_score(blocks),
        "redundancy": calculate_redundancy_score(content)
    }

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

    print(f"🌳 记忆树 — 健康度评分 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")
    print(f"📊 四维评分:")
    print(f"   新鲜度 (30%): {scores['freshness']}/100")
    print(f"   覆盖度 (25%): {scores['coverage']}/100")
    print(f"   冲突率 (25%): {scores['conflict']}% (越低越好)")
    print(f"   冗余度 (20%): {scores['redundancy']}% (越低越好)")
    print(f"\n🏥 总分: {results['total']}/100")

    if scores["conflict"] > 20:
        print(f"\n💡 建议：存在标题重复的记忆，请合并或删除")
    if scores["redundancy"] > 30:
        print(f"💡 建议：存在大量重复内容，请精简 MEMORY.md")
    if scores["freshness"] < 50:
        print(f"💡 建议：大部分记忆较旧，请更新或归档")

    return results


# ==================== 记忆重要性评分 ====================

IMPORTANCE_CONFIG = {
    "access_frequency_weight": 0.25,
    "recency_weight": 0.30,
    "content_value_weight": 0.25,
    "user_mark_weight": 0.20,
    "important_keywords": [
        "红线", "规则", "禁止", "必须", "核心", "关键", "重要",
        "偏好", "习惯", "身份", "记住", "不要", "避免",
        "记住这个", "这个很重要", "记住这条"
    ],
    "temporary_keywords": [
        "今天", "明天", "本周", "临时", "待办", "待处理",
        "TODO", "FIXME", "临时记一下"
    ]
}


def calculate_importance_score(block, access_count=0, max_access_count=10):
    """
    计算记忆重要性评分（0-100分）

    评分维度：
    1. 访问频率 (25%)
    2. 时效性 (30%)
    3. 内容价值 (25%)
    4. 用户标记 (20%)
    """
    factors = {}

    # 1. 访问频率分数 (0-100)
    access_score = min(100, (access_count / max(max_access_count, 1)) * 100)
    factors["access_frequency"] = round(access_score, 1)

    # 2. 时效性分数 (0-100)
    days = estimate_days_since_mention(block.get("body", block.get("content", "")))
    recency_score = max(0, 100 - (days * 3))
    factors["recency"] = round(recency_score, 1)
    factors["days_since_mention"] = days

    # 3. 内容价值分数 (0-100)
    content_value = 50
    full_text = block.get("full_text", block.get("title", "") + block.get("body", ""))

    for kw in IMPORTANCE_CONFIG["important_keywords"]:
        if kw in full_text:
            content_value += 10

    for kw in IMPORTANCE_CONFIG["temporary_keywords"]:
        if kw in full_text:
            content_value -= 15

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

    access_counts = {}
    try:
        from vector_store import init_db
        conn = init_db()
        for row in conn.execute("SELECT title, confidence FROM memories"):
            access_counts[row[0]] = int(row[1] * 10) if row[1] else 0
        conn.close()
    except Exception:
        pass

    for block in blocks:
        access_count = access_counts.get(block["title"], 0)
        importance = calculate_importance_score(block, access_count)
        results["memories"].append({
            "title": block["title"],
            "score": importance["score"],
            "priority": importance["priority"],
            "factors": importance["factors"]
        })

    results["memories"].sort(key=lambda x: -x["score"])

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

    print(f"🌳 记忆树 — 重要性评分 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")
    print(f"📊 评分统计:")
    print(f"   总记忆: {results['stats']['total']} 条")
    print(f"   平均分: {results['stats']['avg_score']}/100")
    print(f"   P0 (核心): {p0_count} 条")
    print(f"   P1 (重要): {p1_count} 条")
    print(f"   P2 (一般): {p2_count} 条")
    print(f"   P3 (临时): {p3_count} 条")
    print()
    print("⭐ 高重要性记忆 (Top 10):")
    for i, m in enumerate(results["memories"][:10], 1):
        marker = "📌" if m["priority"] == "P0" else "  "
        print(f"   {marker} {m['score']:.0f}分 [{m['priority']}] {m['title'][:40]}")

    p3_memories = [m for m in results["memories"] if m["priority"] == "P3"]
    if p3_memories:
        print(f"\n🗑️ 低重要性记忆 ({len(p3_memories)} 条，考虑归档):")
        for m in p3_memories[:5]:
            print(f"   {m['score']:.0f}分 {m['title'][:40]}")

    return results


# ==================== 遗忘预测 ====================

def cmd_decay(args):
    """遗忘预测 — 显示可能不再有用的记忆，提醒用户确认（分类衰变预测）"""
    results = {"blocks": [], "to_review": [], "category_stats": {}, "decay_predictions": {}}

    if not MEMORY_MD.exists():
        if getattr(args, 'json', False):
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print("❌ MEMORY.md 不存在")
        return results

    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)

    category_counts = {"strategy": 0, "fact": 0, "assumption": 0, "failure": 0, "default": 0}

    for block in blocks:
        if block["is_permanent"]:
            results["blocks"].append({
                "title": block["title"],
                "status": "permanent",
                "strength": 1.0,
                "category": "permanent"
            })
            continue

        category = classify_memory_category(block["title"], block.get("body", ""))
        category_counts[category] += 1

        days = estimate_days_since_mention(block["content"] if "content" in block else block["body"])
        strength = calculate_strength(days, category, recall_count=0)

        entry = {
            "title": block["title"],
            "days": days,
            "strength": round(strength, 2),
            "category": category,
            "decay_rate": get_category_decay_rate(category),
            "status": "ok" if strength > PROMOTE_THRESHOLD else "warning" if strength > FORGET_THRESHOLD else "forget"
        }
        results["blocks"].append(entry)

        if strength < PROMOTE_THRESHOLD:
            results["to_review"].append({
                "title": block["title"],
                "days": days,
                "strength": round(strength, 2),
                "category": category
            })

    results["category_stats"] = category_counts

    # 衰变预测
    decay_predictions = {}
    for category, decay_rate in DECAY_RATES.items():
        if category == "default":
            continue
        predictions = {}
        for days in [1, 7, 14, 30, 60, 90]:
            strength = calculate_strength(days, category, recall_count=0)
            predictions[f"{days}天"] = round(strength, 2)
        forget_days = round(-math.log(FORGET_THRESHOLD) / decay_rate, 1)
        promote_days = round(-math.log(PROMOTE_THRESHOLD) / decay_rate, 1)
        decay_predictions[category] = {
            "decay_rate": decay_rate,
            "predictions": predictions,
            "forget_days": forget_days,
            "promote_days": promote_days
        }
    results["decay_predictions"] = decay_predictions

    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results

    print(f"🌳 记忆树 — 遗忘预测（分类衰变） ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")

    print("📊 分类衰变预测表:")
    print("┌────────────┬──────────┬──────────┬──────────┬──────────┬──────────┬──────────┬──────────┐")
    print("│ 类别       │ 衰变率   │ 1天      │ 7天      │ 14天     │ 30天     │ 遗忘天数 │ 推荐天数 │")
    print("├────────────┼──────────┼──────────┼──────────┼──────────┼──────────┼──────────┼──────────┤")
    for category, pred in decay_predictions.items():
        cat_name = {"strategy": "策略", "fact": "事实", "assumption": "假设", "failure": "失败"}.get(category, category)
        print(f"│ {cat_name:10} │ {pred['decay_rate']:8.2f} │ {pred['predictions']['1天']:8.2f} │ {pred['predictions']['7天']:8.2f} │ {pred['predictions']['14天']:8.2f} │ {pred['predictions']['30天']:8.2f} │ {pred['forget_days']:8.1f} │ {pred['promote_days']:8.1f} │")
    print("└────────────┴──────────┴──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘")
    print()
    print(f"阈值说明: FORGET_THRESHOLD={FORGET_THRESHOLD}, PROMOTE_THRESHOLD={PROMOTE_THRESHOLD}")
    print()

    print("📋 记忆分类统计:")
    for category, count in category_counts.items():
        if count > 0:
            cat_name = {"strategy": "策略", "fact": "事实", "assumption": "假设", "failure": "失败", "default": "未分类"}.get(category, category)
            decay_rate = get_category_decay_rate(category)
            print(f"   {cat_name}: {count} 条 (衰变率: {decay_rate})")
    print()

    forget = [b for b in results["blocks"] if b["status"] == "forget"]
    warning = [b for b in results["blocks"] if b["status"] == "warning"]
    ok = [b for b in results["blocks"] if b["status"] == "ok"]
    permanent = [b for b in results["blocks"] if b.get("category") == "permanent"]

    if permanent:
        print(f"📌 永久记忆 ({len(permanent)} 条，永不遗忘):")
        for b in permanent[:10]:
            print(f"   {b['title']}")
        print()

    if ok:
        print(f"🟢 健康 ({len(ok)} 条，strength > {PROMOTE_THRESHOLD}):")
        for b in ok[:5]:
            cat_name = {"strategy": "策略", "fact": "事实", "assumption": "假设", "failure": "失败"}.get(b.get("category", "default"), b.get("category", "default"))
            print(f"   [{cat_name}] {b['title'][:40]} (强度: {b['strength']})")
        if len(ok) > 5:
            print(f"   ... 还有 {len(ok) - 5} 条")
        print()

    if warning:
        print(f"🟡 关注 ({len(warning)} 条，strength {FORGET_THRESHOLD}-{PROMOTE_THRESHOLD}):")
        for b in warning[:10]:
            cat_name = {"strategy": "策略", "fact": "事实", "assumption": "假设", "failure": "失败"}.get(b.get("category", "default"), b.get("category", "default"))
            print(f"   [{cat_name}] {b['title'][:40]} ({b['days']}天, 强度: {b['strength']})")
        print()

    if forget:
        print(f"🔴 建议遗忘 ({len(forget)} 条，strength < {FORGET_THRESHOLD}):")
        for b in forget[:10]:
            cat_name = {"strategy": "策略", "fact": "事实", "assumption": "假设", "failure": "失败"}.get(b.get("category", "default"), b.get("category", "default"))
            print(f"   [{cat_name}] {b['title'][:40]} ({b['days']}天, 强度: {b['strength']})")
        print()
        print("⚠️  不会自动清理，需要你确认")
        print("   说「保留 xxx」或「删除 xxx」来操作")

    return results
