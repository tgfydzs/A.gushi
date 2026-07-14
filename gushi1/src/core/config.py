"""全局配置管理.

使用Dataclass管理所有配置项，禁止魔法字符串。
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMConfig:
    """LLM API配置.

    Attributes:
        provider: LLM提供商 (openai/anthropic/local).
        model: 模型名称.
        api_key: API密钥.
        base_url: API基础URL.
        max_tokens: 最大生成token数.
        temperature: 生成温度 (0.0 - 1.0).
    """

    provider: str = "openai"
    model: str = "gpt-4"
    api_key: str = ""
    base_url: str = ""
    max_tokens: int = 1024
    temperature: float = 0.7


@dataclass
class WorldConfig:
    """世界配置.

    Attributes:
        world_name: 世界名称.
        max_npcs: 最大NPC数量.
        max_memory_per_npc: 每个NPC最大记忆条数.
        time_advance_per_tick: 每回合时间推进的时段数.
    """

    world_name: str = "DefaultWorld"
    max_npcs: int = 100
    max_memory_per_npc: int = 50
    time_advance_per_tick: int = 1


@dataclass
class EngineConfig:
    """引擎配置.

    Attributes:
        max_ticks: 最大回合数（0为无限制）.
        verbose: 是否输出详细日志.
        enable_llm_cache: 是否启用LLM响应缓存.
    """

    max_ticks: int = 0
    verbose: bool = False
    enable_llm_cache: bool = False


@dataclass
class Config:
    """全局配置.

    Attributes:
        llm: LLM配置.
        world: 世界配置.
        engine: 引擎配置.
    """

    llm: LLMConfig = field(default_factory=LLMConfig)
    world: WorldConfig = field(default_factory=WorldConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
