# 🌳 记忆树

让龙虾记住重要的事，忘记不重要的。

**装上就自动运行**：每天衰减、每周归档，无需任何操作。

---

## 它解决什么问题？

龙虾的 `MEMORY.md` 会越来越臃肿。过期信息堆积，token 白烧，重要知识被噪音淹没。

记忆树用**置信度机制**自动治理：
- 常用的知识 → 保持活跃 🌿
- 不用的知识 → 自动衰减 → 归档 🪨
- 标记 [P0] 的核心知识 → 永不衰减

---

## 你只需要知道两件事

### 1. 想回忆某事？
说：「帮我回忆一下 xxx」

语义搜索，能理解"辣的"和"火锅"相关。

### 2. 某条记忆特别重要？
说：「把这条标记为 P0」

永久保护，永不衰减。

---

## 其他的全自动

装好初始化后：
- 每天凌晨 3 点自动衰减
- 每周自动归档死亡记忆
- 搜索引擎自动检测最佳后端

**你不用管它，它自己会跑。**

---

## 搜索后端

自动检测优先级：

1. **Ollama**（本地，免费，隐私）— 默认
2. **智谱 / OpenAI API**（云端）
3. **关键词搜索**（零依赖 fallback）

用 Ollama 时，记忆数据永不离开你的机器。

---

## 安装

```bash
git clone https://github.com/Masongmx/memory-tree.git
cp -r memory-tree/skill ~/.openclaw/workspace/skills/memory-tree
```

初始化：「帮我初始化记忆树」——之后全自动。

---

## 技术规格

- Python 3.8+，零外部依赖
- 本地运行，隐私安全
- macOS / Linux / WSL

---

## 相关项目

- [Lobster Doctor](https://github.com/Masongmx/lobster-doctor) — workspace 健康管理

---

## License

MIT
