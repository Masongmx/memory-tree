#!/usr/bin/env python3
"""
Memory Tree - 搜索模块
提供关键词搜索、向量搜索、多策略搜索功能
"""

import json
from datetime import datetime

from memory_utils import (
    MEMORY_MD, parse_memory_blocks, keyword_similarity, text_hash,
    check_vector_dependencies
)
from memory_tier import get_tier_stats, init_tier_db, LTM_DB

# 检索策略权重
SEARCH_WEIGHTS = {
    "semantic": 0.40,
    "keyword": 0.25,
    "graph": 0.20,
    "temporal": 0.15,
}


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


def cmd_mark(args):
    """标记为永久记忆"""
    from memory_utils import backup_memory

    title_keyword = args.keyword
    results = {"success": False, "title": None}

    if not title_keyword or not title_keyword.strip():
        print("❌ 请提供要标记的记忆关键词")
        print("   用法: mark \"标题关键词\"")
        return results

    if not MEMORY_MD.exists():
        print("❌ MEMORY.md 不存在")
        return results

    backup_memory()

    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)

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

    old_title = found["title"]
    new_title = old_title + " 📌"

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


def score_fusion(results_dict, weights=None):
    """
    多策略分数融合

    Args:
        results_dict: {strategy_name: [results_list]}
        weights: {strategy_name: weight} 默认 semantic:0.5, keyword:0.3, temporal:0.2
    """
    if weights is None:
        weights = {"semantic": 0.50, "keyword": 0.30, "temporal": 0.20}

    aggregated = {}

    for strategy, results in results_dict.items():
        for r in results:
            id_key = r['id']
            if id_key not in aggregated:
                aggregated[id_key] = {
                    "id": r['id'],
                    "title": r['title'],
                    "content": r.get('content', ''),
                    "scores": {},
                    "is_permanent": r.get('is_permanent', False)
                }
            aggregated[id_key]["scores"][strategy] = r['score']

    final_results = []
    for id_key, data in aggregated.items():
        total_score = 0
        for strategy, weight in weights.items():
            strategy_score = data["scores"].get(strategy, 0)
            total_score += strategy_score * weight

        if data["is_permanent"]:
            total_score += 0.1

        data["total_score"] = round(total_score, 3)
        final_results.append(data)

    final_results.sort(key=lambda x: x["total_score"], reverse=True)

    return final_results


def cmd_vector_search(args):
    """多策略向量搜索（semantic + keyword + temporal）"""
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

    from vector_store import (
        search_memories, keyword_search, temporal_search,
        sync_incremental, rebuild_fts_index, DB_PATH, get_stats, init_db, migrate_db
    )

    results = {"query": getattr(args, 'query', ''), "matches": [], "strategies": {}}

    migrate_db()

    if getattr(args, 'sync', False) or not DB_PATH.exists():
        sync_result = sync_incremental()
        print(f"📥 同步完成: 新增 {sync_result['added']} + 更新 {sync_result['updated']} 条\n")
        rebuild_fts_index()

    query = getattr(args, 'query', '')
    top_k = getattr(args, 'top_k', 10)

    if not query or query == "空查询":
        stats = get_stats()
        conn = init_db()
        try:
            fts_count = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
        except Exception:
            fts_count = 0
        conn.close()

        print(f"📊 向量存储统计:")
        print(f"   总记忆: {stats['total']} 条")
        print(f"   永久记忆: {stats['permanent']} 条")
        print(f"   FTS索引: {fts_count} 条")
        print(f"   数据库: {stats['db_path']}")
        print(f"\n💡 多策略权重:")
        print(f"   semantic: 50% | keyword: 30% | temporal: 20%")
        return results

    results_by_strategy = {}

    # 1. Semantic 向量检索
    try:
        semantic_results = search_memories(query, top_k=top_k * 2)
        results_by_strategy["semantic"] = semantic_results
        results["strategies"]["semantic"] = len(semantic_results)
    except Exception as e:
        print(f"⚠️ 语义检索失败: {e}")
        results_by_strategy["semantic"] = []
        results["strategies"]["semantic"] = 0

    # 2. Keyword FTS5 检索
    try:
        keyword_results = keyword_search(query, top_k=top_k)
        results_by_strategy["keyword"] = keyword_results
        results["strategies"]["keyword"] = len(keyword_results)
    except Exception as e:
        print(f"⚠️ 关键词检索失败: {e}")
        results_by_strategy["keyword"] = []
        results["strategies"]["keyword"] = 0

    # 3. Temporal 时间检索
    try:
        temporal_results = temporal_search(top_k=top_k)
        results_by_strategy["temporal"] = temporal_results
        results["strategies"]["temporal"] = len(temporal_results)
    except Exception as e:
        print(f"⚠️ 时间检索失败: {e}")
        results_by_strategy["temporal"] = []
        results["strategies"]["temporal"] = 0

    weights = {"semantic": 0.50, "keyword": 0.30, "temporal": 0.20}
    fused_results = score_fusion(results_by_strategy, weights=weights)

    matches = fused_results[:top_k]
    results["matches"] = matches

    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results

    print(f"🔍 多策略检索: \"{query}\" (Top {top_k})\n")
    print(f"策略结果数: semantic({results['strategies']['semantic']}) + keyword({results['strategies']['keyword']}) + temporal({results['strategies']['temporal']})\n")

    for i, r in enumerate(matches, 1):
        marker = "📌" if r.get("is_permanent") else "  "
        scores_str = " | ".join(f"{k}:{v:.2f}" for k, v in r.get("scores", {}).items())
        print(f"  {i}. {marker}[{r['total_score']:.3f}] {r['title'][:50]}")
        print(f"     分解: {scores_str}")
        preview = r.get('content', '')[:100].replace('\n', ' ').strip()
        if preview:
            print(f"     {preview}...")
        print()

    return results


def multi_strategy_search(query, top_k=10, weights=None):
    """
    多策略并行检索

    策略组合：
    1. semantic (40%) - 向量相似度检索
    2. keyword (25%) - 关键词匹配
    3. graph (20%) - 图谱关联
    4. temporal (15%) - 时间相关性
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
    if MEMORY_MD.exists():
        content = MEMORY_MD.read_text(encoding='utf-8')
        blocks = parse_memory_blocks(content)
        for block in blocks:
            kw_score = keyword_similarity(query, block.get("full_text", block.get("title", "")))
            if kw_score > 0.1:
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

    # 3. Graph 检索
    try:
        ltm_conn = init_tier_db(LTM_DB)
        for result_id in list(results.keys())[:5]:
            links = ltm_conn.execute(
                "SELECT graph_links FROM ltm_memories WHERE id = ?",
                (result_id,)
            ).fetchone()
            if links and links[0]:
                linked_ids = json.loads(links[0])
                for linked_id in linked_ids:
                    if linked_id not in results:
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

    # 4. Temporal 检索
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
                temporal_score = max(0, 1 - days_ago / 30)
                results[mem_id]["scores"]["temporal"] = temporal_score
            else:
                results[mem_id]["scores"]["temporal"] = 0.5
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

    final_results.sort(key=lambda x: x["total_score"], reverse=True)

    return final_results[:top_k]


def cmd_multi_search(args):
    """多策略检索命令"""
    query = getattr(args, 'query', '')
    top_k = getattr(args, 'top_k', 10)

    results = {"query": query, "matches": []}

    if not query or query == "空查询":
        stats = get_tier_stats()
        print(f"📊 三层记忆统计:")
        print(f"   STM: {stats['STM']['count']}/{stats['STM']['max']} 条")
        print(f"   Staging: {stats['Staging']['count']}/{stats['Staging']['max']} 条")
        print(f"   LTM: {stats['LTM']['count']}/{stats['LTM']['max']} 条")
        return results

    print(f"🔍 多策略检索: \"{query}\" (Top {top_k})\n")

    matches = multi_strategy_search(query, top_k=top_k)
    results["matches"] = matches

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


def cmd_conflict(args):
    """检测可能存在的记忆冲突，提醒用户确认"""
    results = {"conflicts": [], "suggestions": [], "by_type": {}}

    if not MEMORY_MD.exists():
        if getattr(args, 'json', False):
            print(json.dumps(results, indent=2, ensure_ascii=False))
        else:
            print("❌ MEMORY.md 不存在")
        return results

    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)

    # 1. 关键词对检测
    conflict_pairs = [
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

    # 2. 标题相似度检测
    title_conflicts = []
    titles = [(i, b["title"].replace("📌", "").replace("[P0]", "").replace("[P1]", "").strip().lower())
              for i, b in enumerate(blocks)]

    for i, (idx1, title1) in enumerate(titles):
        for idx2, title2 in titles[i+1:]:
            if title1 and title2 and (title1 in title2 or title2 in title1):
                if title1 != title2:
                    body1 = blocks[idx1].get("body", "")[:100]
                    body2 = blocks[idx2].get("body", "")[:100]
                    if body1 != body2:
                        title_conflicts.append({
                            "type": "similar_title",
                            "title": f"{blocks[idx1]['title']} vs {blocks[idx2]['title']}",
                            "keywords": ["相似主题"],
                            "description": "主题相似但规则可能不同"
                        })

    results["by_type"]["similar_titles"] = title_conflicts
    results["conflicts"].extend(title_conflicts)

    # 3. 语义相似度检测
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

            for i in range(len(embeddings)):
                for j in range(i + 1, len(embeddings)):
                    sim = np.dot(embeddings[i], embeddings[j]) / (
                        np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[j])
                    )
                    if 0.85 < sim < 0.98:
                        semantic_conflicts.append({
                            "type": "semantic_similar",
                            "title": f"{titles_list[i]} vs {titles_list[j]}",
                            "keywords": ["语义相似"],
                            "description": f"语义相似度 {sim:.2%}，可能存在重复或冲突",
                            "similarity": round(sim, 3)
                        })
    except ImportError:
        pass
    except Exception as e:
        pass

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

    if getattr(args, 'json', False):
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return results

    print(f"🌳 记忆树 — 冲突检测 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n")

    if results["conflicts"]:
        print(f"⚠️  发现 {len(results['conflicts'])} 条可能冲突：\n")

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


def cmd_visualize(args):
    """显示 MEMORY.md 概览"""
    from memory_utils import fmt_size, fmt_tokens

    results = {"blocks": [], "stats": {}}

    if not MEMORY_MD.exists():
        print("❌ MEMORY.md 不存在")
        return results

    content = MEMORY_MD.read_text(encoding='utf-8')
    blocks = parse_memory_blocks(content)

    permanent = [b for b in blocks if b["is_permanent"]]
    normal = [b for b in blocks if not b["is_permanent"]]

    memory_size = MEMORY_MD.stat().st_size
    memory_tokens = estimate_days_since_mention(content)

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
