---
name: memory-tree
description: 记忆管理技能。当用户提到"你记得"、"你忘了"、"记忆搜索"、"这个重要"时触发。支持向量搜索、标记永久、遗忘预测。每周定期执行 decay + health。
---

# 🌳 记忆树

> 搜得到、记得住、忘得掉。

OpenClaw 的记忆管理技能。语义搜索找到想要的记忆，自动提取记住重要的事情，过期记忆智能清理。

## 核心价值

| 价值 | 功能 | 说明 |
|------|------|------|
| **搜得到** | 向量搜索 | 语义搜索，不再只是关键词匹配 |
| **记得住** | 自动提取 + 标记 | 从对话中自动提取关键信息 |
| **忘得掉** | 遗忘曲线 | 基于 Ebbinghaus 曲线，智能清理 |

## 功能列表

| 功能 | 命令 | 触发方式 |
|------|------|----------|
| **向量搜索** | `vector-search "查询"` | 用户触发："你记不记得 xxx" |
| **重要性评分** | `importance "标题"` | 用户触发："这个记忆重要吗" |
| **标记永久** | `mark "标题"` | 用户触发："记住了" |
| **冲突检测** | `conflict` | Agent 主动：标记记忆时 |
| **遗忘预测** | `decay` | Agent 主动：每周定期 |
| **自动提取** | `auto-extract --limit 5` | Agent 主动：每周定期 |
| **健康评分** | `health` | Agent 主动：每周定期 |
| **同步** | `sync` | 用户触发："你怎么又忘了" |
| **统计** | `stats` | 用户触发："记忆统计" |
| **周报** | `weekly` | 用户触发："生成周报" |

## Agent 主动触发规则

| 时机 | 执行动作 |
|------|----------|
| 用户说"记住了" | 检查新记忆是否与旧记忆冲突 |
| 每周定期 | 遗忘预测 + 健康评分 + 自动提取 |

## 遗忘曲线

基于 Ebbinghaus 遗忘曲线，R = e^(-t/S)，S=10：

| 时间 | 保留率 | 说明 |
|------|--------|------|
| 1 天 | 90% | 几乎不衰减 |
| 7 天 | 50% | 开始衰减 |
| 14 天 | 25% | 明显衰减 |
| 30 天 | 5% | 几乎遗忘 |

**不会自动清理**，只会提醒用户确认。

## 命令行用法

```bash
# 向量搜索
python3 scripts/memory_tree.py vector-search "查询内容"

# 重要性评分
python3 scripts/memory_tree.py importance "记忆标题"

# 标记永久
python3 scripts/memory_tree.py mark "标题"

# 冲突检测
python3 scripts/memory_tree.py conflict

# 遗忘预测
python3 scripts/memory_tree.py decay

# 自动提取
python3 scripts/memory_tree.py auto-extract --limit 5

# 健康评分
python3 scripts/memory_tree.py health

# 同步
python3 scripts/memory_tree.py sync

# 统计
python3 scripts/memory_tree.py stats

# 周报
python3 scripts/memory_tree.py weekly
```

## 技术栈

- **Embedding**: qwen3-embedding（本地，4096维）
- **LLM**: qwen3:8b（本地）
- **存储**: SQLite + 向量
- **成本**: 零（全本地运行）

## 安装

```bash
clawhub install memory-tree
```

## License

MIT
