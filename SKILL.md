---
name: memory-tree
description: 🌳 记忆树 — 让 agent 记住重要的，忘记过期的。周报+搜索+遗忘曲线+健康评分。说句话就能用。
---

# 🌳 记忆树

> 让 agent 记住重要的，忘记过期的。

OpenClaw 的记忆会膨胀、过期、冲突。记忆树帮你智能管理——周报自动生成，永久记忆标记，过期记忆自动清理。

## 核心功能

| 功能 | 一句话触发 | 说明 |
|------|-----------|------|
| 生成周报 | 「生成周报」 | 本周新记/遗忘/永久清单 |
| 搜索记忆 | 「搜索记忆 关键词」 | 本地关键词搜索 |
| 向量搜索 | 「向量搜索 查询」 | 语义搜索（更准确） |
| 标记永久 | 「记住这个」 | 📌 标记永不衰减 |
| 遗忘预测 | 「哪些记忆快过期了」 | 基于时间衰减预测 |
| 健康评分 | 「记忆健康怎么样」 | 四维度加权评分 |
| 自动提取 | 「自动提取记忆」 | 从会话历史提取 |
| 数据库统计 | 「记忆统计」 | 查看记忆条数和分类 |
| 自动备份 | — | 标记前自动备份 MEMORY.md |
| 周报推送 | — | 自动推送到飞书 |

## 技术架构

- **Embedding**: qwen3-embedding (本地, 4096维)
- **存储**: SQLite + 向量
- **提取**: qwen3:8b (本地LLM)
- **去重**: 相似度阈值 0.9

## 命令行用法

```bash
# 生成周报
python3 scripts/memory_tree.py weekly

# 搜索记忆（关键词模式）
python3 scripts/memory_tree.py search "关键词"

# 向量搜索（语义模式，更准确）
python3 scripts/memory_tree.py vector-search "查询内容"

# 标记永久记忆
python3 scripts/memory_tree.py mark "标题"

# 遗忘预测
python3 scripts/memory_tree.py decay

# 健康评分
python3 scripts/memory_tree.py health

# 自动提取记忆
python3 scripts/memory_tree.py auto-extract --limit 5

# 数据库统计
python3 scripts/memory_tree.py stats

# 同步MEMORY.md到向量数据库
python3 scripts/memory_tree.py sync

# JSON 输出
python3 scripts/memory_tree.py weekly --json
```

## 记忆架构

```
永久记忆（📌）  → 永不衰减，核心规则
重要记忆（P0）  → 缓慢衰减，定期复习
一般记忆        → 正常衰减，30天清理
临时记忆        → 快速衰减，7天清理
```

## 永久标记语法

在 MEMORY.md 中使用 `📌` 标记永久记忆：

```markdown
## 身份
- **小美**，老板的数字管家 📌
```

或使用 `[P0]` 标记：

```markdown
## 红线
- X/Twitter 链接必须用 x-tweet-fetcher [P0]
```

## 遗忘曲线

基于 Ebbinghaus 遗忘曲线，自动衰减过期记忆：

- **7天内**：权重 100%
- **14天**：权重 60%
- **30天**：权重 36%
- **60天**：权重 13%
- **90天+**：权重 <5%，自动清理

永久标记（📌 / [P0]）的记忆不受衰减影响。

## 健康评分

四维度加权评分：

| 维度 | 权重 | 说明 |
|------|------|------|
| 新鲜度 | 30% | 最近记忆的时间分布 |
| 覆盖度 | 25% | 主题覆盖是否全面 |
| 冲突率 | 25% | 记忆间是否存在矛盾 |
| 冗余度 | 20% | 重复内容的比例 |

- **90+**：优秀
- **70-89**：良好
- **50-69**：需关注
- **<50**：需整理

## 数据库字段

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| title | TEXT | 记忆标题 |
| content | TEXT | 记忆内容 |
| block_type | TEXT | 块类型（normal等） |
| category | TEXT | 分类（默认"其他"） |
| is_permanent | INTEGER | 是否永久记忆 |
| confidence | REAL | 置信度 |
| source | TEXT | 来源 |
| last_mention | TIMESTAMP | 最后提及时间 |
| created_at | TIMESTAMP | 创建时间 |
| updated_at | TIMESTAMP | 更新时间 |
| embedding | BLOB | 向量嵌入 |

## 安全机制

- **自动备份**：标记前自动备份 MEMORY.md
- **永久保护**：📌 标记的内容永不衰减
- **幂等操作**：重复标记不会产生冲突
- **零 API 调用**：纯本地运行

## 安装

```bash
clawhub install memory-tree
```

## 更新日志

- v2.4.0: 新增 stats/sync 命令，添加 category/confidence/source/last_mention 字段，数据库迁移支持
- v2.3.0: auto-extract 自动提取记忆，向量搜索优化
- v3.0.0: 遗忘曲线、健康评分、飞书推送修复、Memory 解析修复、`--json` 输出
- v2.0.0: 周报重构、推送渠道检测、永久标记语法
- v1.0.0: 初始版本

## License

MIT
