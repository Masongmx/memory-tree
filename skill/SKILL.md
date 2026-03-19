---
name: memory-tree
description: >
  Confidence-based memory lifecycle management for OpenClaw agents.
  Uses local Ollama embeddings for semantic search and automatic confidence decay.
  Activate when user mentions: memory management, memory tree, 记忆树,
  memory lifecycle, confidence decay, memory cleanup, 记忆治理, 记忆衰减,
  记忆健康, memory health, memory search, 搜索记忆, 记忆状态, 整理记忆,
  衰减记忆, 记忆太多, MEMORY.md太大了.
---

# 🌳 Memory Tree — 记忆树

基于置信度的记忆生命周期管理。让龙虾拥有"遗忘"能力——常用知识常绿，不用的自动衰减归档。

## 自动行为（无需用户操作）

龙虾会根据用户意图自动调用相应功能。用户只需要**用自然语言描述需求**。

**首次安装后**，龙虾应自动运行初始化：
```bash
python3 skills/memory-tree/scripts/memory_tree.py setup
```
这会自动创建 cron 定时任务（每日衰减 + 每周归档），之后完全免维护。

**当用户要求搜索记忆时**：
```bash
python3 skills/memory-tree/scripts/memory_tree.py search "用户的搜索关键词"
```

**当用户要求查看记忆状态时**：
```bash
python3 skills/memory-tree/scripts/memory_tree.py visualize
```

**当用户要求整理/衰减记忆时**：
```bash
python3 skills/memory-tree/scripts/memory_tree.py decay
```

**当用户要求归档死亡记忆时**：
```bash
python3 skills/memory-tree/scripts/memory_tree.py cleanup --auto
```

## 用户意图识别

| 用户可能说的话 | 龙虾应该执行 |
|--------------|-------------|
| "搜索一下我之前的记忆" | `search` + 用户描述的关键词 |
| "帮我搜一下关于 xxx 的记忆" | `search` |
| "记忆树现在什么状态" | `visualize` |
| "记忆健康吗" | `visualize` |
| "帮我整理一下记忆" | `decay` |
| "记忆衰减一下" | `decay` |
| "归档死亡记忆" | `cleanup --auto` |
| "MEMORY.md 太大了" | `visualize` → 建议衰减 |
| "配置记忆树搜索引擎" | `setup`（自动检测） |

## 核心概念

每条记忆有一个**置信度**（0.0–1.0）：

| 阶段 | 分数 | Emoji | 含义 |
|------|------|-------|------|
| Sprout | 0.7 | 🌱 | 新知识 |
| Green | ≥0.8 | 🌿 | 常用，茁壮 |
| Yellow | 0.5–0.8 | 🍂 | 偶尔用，衰减中 |
| Dead | 0.3–0.5 | 🍁 | 很少用，即将归档 |
| Soil | <0.3 | 🪨 | 已归档，精华保留 |

## 置信度变化

| 事件 | 变化 |
|------|------|
| 新知识创建 | 设为 0.7 |
| 被搜索命中 | +0.03 |
| 被实际使用 | +0.08 |
| 手动确认重要 | 设为 0.95 |
| 每天未访问 (P2) | -0.008 |
| 每天未访问 (P1) | -0.004 |
| P0（核心原则） | 永不衰减 |

## 优先级标签

在 MEMORY.md 的章节标题中使用：

```markdown
## [P0] 核心原则        # 永不衰减
## [P1] 重要知识        # 慢衰减（约5个月归档）
## [P2] 日常笔记        # 快衰减（约3.5个月归档）
```

不标注 = P2（默认）。

## 搜索后端（自动检测）

| 优先级 | 后端 | 费用 | 要求 |
|--------|------|------|------|
| 1st | **Ollama**（本地） | 免费 | `ollama serve` + embedding 模型 |
| 2nd | **智谱 API**（云端） | 极低 | `ZHIPU_API_KEY` 环境变量 |
| 3rd | **OpenAI 兼容**（云端） | 按量 | `OPENAI_API_KEY` 环境变量 |
| 4th | **关键词 fallback** | 免费 | 零依赖 |

无需手动配置——setup 脚本自动检测并使用最佳可用后端。

## 数据文件

存储在 workspace 的 `memory-tree/data/` 下：
- `confidence.json` — 置信度分数和元数据
- `embeddings.json` — 缓存的嵌入向量
- `archive.json` — 归档的记忆记录

## 依赖

- Python 3.8+（纯标准库，不需要 pip 安装任何包）
- 至少一个搜索后端（关键词模式零依赖可用）
