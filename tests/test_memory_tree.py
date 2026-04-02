#!/usr/bin/env python3
"""
Memory Tree 单元测试

测试内容：
1. 遗忘曲线算法（Ebbinghaus，S=10）
2. 记忆重要性评分算法
3. 冲突检测
4. 边界条件
5. 异常处理
"""

import sys
import math
import json
import pytest
from pathlib import Path
from datetime import datetime, timedelta

# 添加 scripts 目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

# 导入被测试的模块
import memory_tree as mt


class TestEbbinghausDecay:
    """测试 Ebbinghaus 遗忘曲线算法"""
    
    def test_decay_constant_is_10(self):
        """验证衰减常数为10"""
        assert mt.DECAY_CONSTANT_S == 10, "衰减常数应为10"
    
    def test_decay_weight_day_0(self):
        """测试第0天的保留率（应为1.0）"""
        weight = mt.calculate_decay_weight(0)
        assert weight == 1.0, "第0天保留率应为1.0"
    
    def test_decay_weight_day_7(self):
        """测试第7天的保留率（应约50%）"""
        weight = mt.calculate_decay_weight(7)
        expected = math.exp(-7/10)  # 约0.497
        assert abs(weight - expected) < 0.01, f"第7天保留率应约50%，实际{weight:.3f}"
    
    def test_decay_weight_day_14(self):
        """测试第14天的保留率（应约25%）"""
        weight = mt.calculate_decay_weight(14)
        expected = math.exp(-14/10)  # 约0.247
        assert abs(weight - expected) < 0.01, f"第14天保留率应约25%，实际{weight:.3f}"
    
    def test_decay_weight_day_30(self):
        """测试第30天的保留率（应约5%）"""
        weight = mt.calculate_decay_weight(30)
        expected = math.exp(-30/10)  # 约0.0498
        assert abs(weight - expected) < 0.01, f"第30天保留率应约5%，实际{weight:.3f}"
    
    def test_decay_weight_negative_days(self):
        """测试负天数（应返回1.0）"""
        weight = mt.calculate_decay_weight(-5)
        assert weight == 1.0, "负天数保留率应为1.0"
    
    def test_decay_weight_custom_constant(self):
        """测试自定义衰减常数"""
        weight_default = mt.calculate_decay_weight(7)
        weight_custom = mt.calculate_decay_weight(7, decay_constant=5)
        # S=5 时衰减更快
        assert weight_custom < weight_default, "S=5 应该比 S=10 衰减更快"


class TestImportanceScore:
    """测试记忆重要性评分算法"""
    
    def test_permanent_memory_score(self):
        """测试永久记忆的评分（应接近满分）"""
        block = {
            "title": "核心规则",
            "body": "这是核心规则",
            "is_permanent": True,
            "priority": "P0"
        }
        result = mt.calculate_importance_score(block)
        assert result["score"] >= 70, "永久记忆评分应>=70"
        assert result["priority"] == "P0", "永久记忆应为P0"
    
    def test_temporary_memory_score(self):
        """测试临时记忆的评分（应较低）"""
        block = {
            "title": "今天的任务",
            "body": "今天要完成临时任务",
            "is_permanent": False,
            "priority": "P2"
        }
        result = mt.calculate_importance_score(block)
        assert result["score"] < 70, "临时记忆评分应<70"
    
    def test_keywords_boost(self):
        """测试关键词加分"""
        block_no_kw = {
            "title": "普通记忆",
            "body": "普通内容",
            "is_permanent": False
        }
        block_with_kw = {
            "title": "重要规则",
            "body": "这是核心红线，必须遵守",
            "is_permanent": False
        }
        
        result_no_kw = mt.calculate_importance_score(block_no_kw)
        result_with_kw = mt.calculate_importance_score(block_with_kw)
        
        assert result_with_kw["score"] > result_no_kw["score"], "包含关键词的记忆评分应更高"
    
    def test_temporal_decay(self):
        """测试时效性衰减"""
        # 模拟旧记忆（通过body中包含旧日期）
        block_old = {
            "title": "旧任务",
            "body": "2025-01-01: 这个任务已完成",
            "is_permanent": False
        }
        # 模拟新记忆
        block_new = {
            "title": "新任务",
            "body": f"{datetime.now().strftime('%Y-%m-%d')}: 这个任务正在进行",
            "is_permanent": False
        }
        
        result_old = mt.calculate_importance_score(block_old)
        result_new = mt.calculate_importance_score(block_new)
        
        # 新记忆的时效性分数应该更高
        assert result_new["factors"]["recency"] >= result_old["factors"]["recency"], \
            "新记忆的时效性分数应更高"
    
    def test_factors_sum_to_weight(self):
        """测试权重配置总和为1.0"""
        weights = mt.IMPORTANCE_CONFIG
        total = (
            weights["access_frequency_weight"] +
            weights["recency_weight"] +
            weights["content_value_weight"] +
            weights["user_mark_weight"]
        )
        assert abs(total - 1.0) < 0.01, f"权重总和应为1.0，实际{total}"


class TestConflictDetection:
    """测试冲突检测"""
    
    def test_keyword_pair_conflict(self):
        """测试关键词对冲突检测"""
        # 创建包含冲突关键词的测试内容
        conflict_pairs = [
            ("爹味", "深度分析"),
            ("简洁", "详细"),
            ("自动", "手动"),
        ]
        
        for kw1, kw2 in conflict_pairs:
            # 这些关键词对应该被检测到
            assert (kw1, kw2) in [(p[0], p[1]) for p in mt.conflict_pairs if len(p) >= 2] or \
                   any(kw1 in str(p) and kw2 in str(p) for p in mt.conflict_pairs), \
                   f"关键词对 ({kw1}, {kw2}) 应该在冲突检测列表中"
    
    def test_permanent_memory_no_conflict(self):
        """测试永久记忆不会被标记为冲突"""
        # 永久记忆之间不应该产生冲突警告
        # 这个测试验证冲突检测不会误报永久记忆
        pass  # 实际测试需要模拟 MEMORY.md


class TestParseMemoryBlocks:
    """测试 Memory 块解析"""
    
    def test_parse_simple_block(self):
        """测试简单块解析"""
        content = """## 标题一
内容一

## 标题二
内容二
"""
        blocks = mt.parse_memory_blocks(content)
        assert len(blocks) == 2, "应解析出2个块"
        assert blocks[0]["title"] == "标题一"
        assert blocks[1]["title"] == "标题二"
    
    def test_parse_permanent_marker_in_title(self):
        """测试标题中的永久标记"""
        content = """## 核心规则 📌
这是永久记忆
"""
        blocks = mt.parse_memory_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["is_permanent"] == True, "应识别永久标记"
    
    def test_parse_permanent_marker_in_body(self):
        """测试内容中的永久标记"""
        content = """## 规则
这是重要规则 [P0]
"""
        blocks = mt.parse_memory_blocks(content)
        assert len(blocks) == 1
        assert blocks[0]["is_permanent"] == True, "应识别内容中的永久标记"
    
    def test_parse_nested_headers(self):
        """测试嵌套标题（### 不应被解析为块）"""
        content = """## 主标题
内容

### 子标题
子内容

## 另一个主标题
"""
        blocks = mt.parse_memory_blocks(content)
        assert len(blocks) == 2, "只应解析 ## 级别的标题"
    
    def test_parse_empty_content(self):
        """测试空内容"""
        blocks = mt.parse_memory_blocks("")
        assert len(blocks) == 0, "空内容应返回空列表"


class TestEstimateDaysSinceMention:
    """测试最后提及时间估算"""
    
    def test_date_in_content(self):
        """测试内容中包含日期"""
        content = "2025-03-20: 这是一条记录"
        days = mt.estimate_days_since_mention(content)
        # 如果今天接近2025-03-20，days应该很小
        assert days >= 0, "天数应为非负数"
    
    def test_no_date_in_content(self):
        """测试内容中不包含日期"""
        content = "这是一条没有日期的记录"
        days = mt.estimate_days_since_mention(content)
        assert days == 7, "无日期时应返回默认值7天"
    
    def test_multiple_dates(self):
        """测试内容中包含多个日期"""
        content = "2025-03-01 开始，2025-03-20 完成"
        days = mt.estimate_days_since_mention(content)
        # 应该返回最近日期的天数
        assert days >= 0


class TestEdgeCases:
    """边界条件测试"""
    
    def test_decay_very_large_days(self):
        """测试非常大的天数"""
        weight = mt.calculate_decay_weight(365)
        assert weight > 0, "即使一年后保留率也应>0"
        assert weight < 0.001, "一年后保留率应非常小"
    
    def test_importance_empty_block(self):
        """测试空块的重要性评分"""
        block = {"title": "", "body": "", "is_permanent": False}
        result = mt.calculate_importance_score(block)
        assert "score" in result, "应返回评分"
        assert result["score"] >= 0, "评分应为非负数"
    
    def test_importance_very_long_content(self):
        """测试非常长的内容"""
        block = {
            "title": "长内容",
            "body": "x" * 10000,
            "is_permanent": False
        }
        result = mt.calculate_importance_score(block)
        assert result["score"] <= 100, "评分不应超过100"


class TestConfiguration:
    """配置参数测试"""
    
    def test_decay_constant_positive(self):
        """测试衰减常数为正数"""
        assert mt.DECAY_CONSTANT_S > 0, "衰减常数应为正数"
    
    def test_importance_weights_valid(self):
        """测试重要性评分权重有效"""
        config = mt.IMPORTANCE_CONFIG
        for key in ["access_frequency_weight", "recency_weight", "content_value_weight", "user_mark_weight"]:
            assert key in config, f"缺少权重配置: {key}"
            assert 0 <= config[key] <= 1, f"权重应在0-1之间: {key}"


if __name__ == "__main__":
    # 运行所有测试
    pytest.main([__file__, "-v"])