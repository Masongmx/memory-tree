# 🏆 记忆系统对标分析

## 优秀项目概览

| 项目 | Stars | 定位 | 特色 |
|------|-------|------|------|
| **mem0** | 51k | Universal memory layer | 商业级，+26%准确率，91%更快 |
| **Letta** | 21.7k | Stateful agents | 原MemGPT，学术背景，持续学习 |
| **cognee** | 14.6k | Knowledge Engine | 6行代码集成 |
| **memvid** | 13.6k | Memory layer | 无服务器，单文件，即时检索 |

## 深度对比：memory-tree vs mem0

| 维度 | mem0 | memory-tree | 差距分析 |
|------|------|-------------|----------|
| **部署** | 云服务+自托管 | 纯本地 | mem0更灵活，我们更隐私 |
| **向量存储** | 多种(Qdrant/Chroma/Pinecone) | SQLite+向量 | mem0支持更多，我们更轻量 |
| **Embedding** | 外部服务(OpenAI等) | 本地qwen3 | 我们零成本 |
| **LLM** | 外部(GPT-4.1-nano) | 本地qwen3:8b | 我们零成本 |
| **API** | ✅ REST API | ❌ | 我们需补 |
| **SDK** | Python/TypeScript | ❌ | 我们需补 |
| **成本** | 需付费API | 零成本 | 我们优势 |
| **隐私** | 数据上云 | 完全本地 | 我们优势 |
| **特色** | 商业级稳定性 | 开箱即用 | 各有侧重 |

## mem0 核心能力

1. **多级记忆**：User → Session → Agent 三层状态
2. **自适应个性化**：记住用户偏好，持续学习
3. **低延迟高准确**：+26%准确率，91%更快响应
4. **Token优化**：比全上下文少90% token
5. **生产就绪**：Y Combinator S24 背书

## Letta 核心能力

1. **有状态Agent**：高级记忆，持续学习
2. **CLI + API**：本地运行+云端服务
3. **Skills/Subagents**：支持技能和子代理
4. **模型无关**：支持各种LLM
5. **学术背景**：原MemGPT，研究驱动

## memory-tree 的优势

1. **零成本**：全本地运行，无需付费API
2. **完全隐私**：数据不上云，本地存储
3. **开箱即用**：无需配置API key
4. **OpenClaw原生**：深度集成，无缝使用
5. **轻量级**：SQLite + 本地Embedding

## memory-tree 的不足

1. **无API接口**：只能CLI调用，无法被其他应用调用
2. **无SDK**：没有Python/TypeScript封装
3. **功能简单**：缺少多级记忆、知识图谱
4. **规模限制**：SQLite不适合大规模数据

## 改进路线图

### 核心原则
- 专注 OpenClaw 用户需求，不做面向开发者的 API/SDK
- 降低使用门槛，一句话触发
- 强化 CLI 功能，提高搜索/提取/周报质量

### Phase 1：搜索优化（短期）
- [ ] 提高向量搜索准确度（调整 embedding 模型参数）
- [ ] 支持混合搜索（向量+关键词加权）
- [ ] 搜索结果去重和排序优化

### Phase 2：提取优化（中期）
- [ ] 优化自动提取的 prompt 质量
- [ ] 支持更多记忆类别
- [ ] 提取结果的人工确认机制

### Phase 3：周报增强（中期）
- [ ] 周报增加洞察分析
- [ ] 记忆趋势可视化
- [ ] 智能提醒（快过期的重要记忆）

## 参考资源

- mem0: https://github.com/mem0ai/mem0
- Letta: https://github.com/letta-ai/letta
- cognee: https://github.com/topoteretes/cognee
- memvid: https://github.com/memvid/memvid
