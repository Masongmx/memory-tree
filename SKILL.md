---
name: memory-tree
description: |
  记忆树 v2.0 — 让龙虾记住重要的事。周报自动生成，永久记忆标记。Activate when user mentions: memory tree, 记忆树, 搜索记忆, 回忆一下, 记住这个, 这个很重要, 别忘了, MEMORY.md太大, 周报.
---

# 🌳 记忆树 v2.0

让龙虾拥有人类般的记忆——记住重要的，忘记过期的。

**v2.0 核心功能**：
- **周报生成**：每周自动统计新记、遗忘、永久记忆
- **永久标记**：说"记住这个"自动标记 📌
- **关键词搜索**：无需外部依赖，本地运行

---

## v2.0 改进

| v1.0 的问题 | v2.0 的解法 |
|-------------|-------------|
| 搜索命中次数无意义 | **周报展示具体内容**：本周新记、遗忘清单 |
| 衰减机制无效 | **删除 decay**，简化为永久/普通两类 |
| Ollama embedding 失败率高 | **关键词搜索**，无外部依赖 |
| 枯叶/土壤概念复杂 | **删除**，只保留永久记忆标记 |

---

## 快速开始

### 1. 生成周报
```
python3 skills/memory-tree/scripts/memory_tree.py weekly
```

输出：
- 本周新记（永久记忆 + 普通记忆）
- 本周遗忘（被归档的内容）
- 永久记忆清单
- 记忆健康统计

### 2. 搜索记忆
```
python3 skills/memory-tree/scripts/memory_tree.py search "关键词"
```

### 3. 标记永久记忆
```
python3 skills/memory-tree/scripts/memory_tree.py mark "标题关键词"
```

或直接对龙虾说："记住这个"、"这个很重要"

### 4. 查看概览
```
python3 skills/memory-tree/scripts/memory_tree.py visualize
```

---

## 推送渠道

周报生成时自动检测 `openclaw.json` 中已启用的渠道：

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "groupAllowFrom": ["oc_xxx"]
    }
  }
}
```

飞书 chatId 从 `groupAllowFrom[0]` 获取。

---

## 永久记忆

### 标记方式

1. **命令行**：`mark "标题关键词"`
2. **语音**：说"记住这个"、"这个很重要"
3. **手动**：在 MEMORY.md 标题后加 `📌`

### 永久记忆特点

- 永不衰减
- 不参与清理
- 在周报中单独展示

---

## 文件结构

```
~/.openclaw/workspace/
├── MEMORY.md              # 主记忆文件
├── memory/
│   ├── weekly-reports/    # 周报输出
│   ├── archive/           # 归档记忆
│   └── *.md               # 日常记忆
└── memory-tree/
    └── data/              # 数据存储
```

---

## 安装

```bash
git clone https://github.com/Masongmx/memory-tree.git
cp -r memory-tree/skill ~/.openclaw/workspace/skills/memory-tree
```

---

## 致谢

灵感来自 [@loryoncloud](https://x.com/loryoncloud) 的 [Memory-Like-A-Tree](https://github.com/loryoncloud/Memory-Like-A-Tree) 项目。

---

## License

MIT