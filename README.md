# 🌳 Memory Tree — 记忆树

> 基于[OpenClaw](https://github.com/openclaw/openclaw) 代理的置信度记忆生命周期管理。

Memory Tree 赋予你的 AI 代理**遗忘**的能力——这是记忆中最具人性化的特征。频繁使用的知识保持新鲜翠绿。被忽视的知识逐渐褪色消散。当知识真正陈旧时，其精华会被归档，并从活跃记忆中移除。

**零云端 API 调用。零 Token 消耗。零手动维护。**

## 为什么需要？

OpenClaw 代理将长期记忆存储在 `MEMORY.md` 中。随着时间推移，这个文件不断增长——新规则、旧决策、技能列表、API 密钥、待办事项……所有内容堆积在一起。重要的知识被淹没在噪音之下。

结果如何？你的代理变得越来越慢、越来越昂贵、越来越健忘——因为它每次会话都要加载数千个无关的 Token 上下文。

Memory Tree 用一个简单的洞见解决了这个问题：**遗忘是特性，不是缺陷。**

## 亮点

- 🌍 **随处运行** — 本地机器、云虚拟机、WSL、Docker。无需 GPU。
- 🔍 **多后端搜索** — Ollama（免费）、智谱/OpenAI API 或关键词回退。自动检测。
- 💰 **零 Token 消耗** — 所有操作均为本地 Python 脚本，无需调用 LLM API
- 🔒 **隐私优先** — 使用 Ollama 后端时，你的记忆数据永远不会离开你的机器
- 📦 **一键安装** — `setup` 自动检测环境、索引记忆、创建定时任务
- 🔄 **安装后免维护** — 自动衰减和归档按计划运行

## 工作原理

`MEMORY.md` 中的每个知识块都会获得一个**置信度分数**（0.0–1.0）：

| 阶段 | 分数 | 含义 |
|-------|-------|---------|
| 🌱 新芽 | 0.7 | 新知识 |
| 🌿 翠绿 | ≥0.8 | 频繁使用，茁壮成长 |
| 🍂 枯黄 | 0.5–0.8 | 使用较少，正在衰减 |
| 🍁 凋零 | 0.3–0.5 | 极少使用，接近归档 |
| 🪨 落土 | <0.3 | 已归档，精华保留 |

### 置信度变化规则

| 事件 | 变化 |
|-------|--------|
| 新知识创建 | 设为 0.7 |
| 被搜索命中 | +0.03 |
| 被主动使用 | +0.08 |
| 手动确认 | 设为 0.95 |
| 每天未被访问（P2） | -0.008 |
| 每天未被访问（P1） | -0.004 |
| P0（核心原则） | 永不衰减 |

### 语义搜索

使用本地 [Ollama](https://ollama.ai) 嵌入模型（`qwen3-embedding`）进行语义搜索——理解语义，而不仅仅是关键词。命中搜索结果的知识块会自动提升置信度。频繁被召回的知识保持活跃。

## 环境要求

- [Python 3.8+](https://python.org)（无需安装 pip 包）
- 至少一个搜索后端：
  - **Ollama**（免费，推荐）：`ollama serve` + `ollama pull qwen3-embedding`
  - **智谱 API**：设置 `ZHIPU_API_KEY` 环境变量或通过 `config` 配置
  - **OpenAI API**：设置 `OPENAI_API_KEY` 环境变量或通过 `config` 配置
  - **关键词**（内置回退）：零依赖即可工作

## 作为 OpenClaw 技能安装

从 [Releases](https://github.com/Masongmx/memory-tree/releases) 下载 `memory-tree.skill` 并安装：

```bash
openclaw skill install memory-tree.skill
```

或克隆并复制到你的技能目录：

```bash
git clone https://github.com/Masongmx/memory-tree.git
cp -r memory-tree/skill/* ~/.openclaw/workspace/skills/memory-tree/
```

## 快速开始

```bash
# 一次性设置（自动创建定时任务 + 首次索引）
python3 skills/memory-tree/scripts/memory_tree.py setup

# 查看记忆树健康状态
python3 skills/memory-tree/scripts/memory_tree.py visualize

# 语义搜索
python3 skills/memory-tree/scripts/memory_tree.py search "how to fetch tweets"

# 自动衰减（通常通过定时任务运行）
python3 skills/memory-tree/scripts/memory_tree.py decay
```

## 优先级标签

在 `MEMORY.md` 的章节标题中使用，用于控制衰减速度：

```markdown
## [P0] 核心原则        # 永不衰减
## [P1] 重要知识        # 慢衰减（约5个月归档）
## [P2] 日常笔记        # 快衰减（约3.5个月归档）
```

无标签 = P2（默认）。

## 数据文件

所有数据存储在工作区的 `memory-tree/data/` 目录下：

| 文件 | 用途 |
|------|---------|
| `confidence.json` | 每个知识块的置信度分数和元数据 |
| `embeddings.json` | 语义搜索的缓存嵌入向量 |
| `archive.json` | 已归档的知识记录 |

## 项目结构

```
memory-tree/
├── skill/                  # OpenClaw 技能包
│   ├── SKILL.md
│   └── scripts/
│       └── memory_tree.py
├── core/                   # 独立脚本（同一文件）
│   └── memory_tree.py
├── ARTICLE.md              # 介绍文章（中文）
└── README.md               # 本文件
```

## 致谢

灵感来自 [@loryoncloud](https://x.com/loryoncloud) 的 [Memory-Like-A-Tree](https://github.com/loryoncloud/Memory-Like-A-Tree) 项目。

## 许可证

MIT
