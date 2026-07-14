"""配置系统 — 统一配置加载.

读取 config/ 目录下的 YAML 配置文件，
提供类型化的配置访问接口。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_CONFIG_DIR = Path(__file__).resolve().parent


# =============================================================================
# 配置数据结构
# =============================================================================


@dataclass
class NodeLLMConfigData:
    """节点级 LLM 配置."""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 1024


@dataclass
class LLMConfigData:
    """LLM 全局配置."""
    provider: str = "openai"
    model: str = "gpt-4"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 1024
    nodes: dict[str, NodeLLMConfigData] = field(default_factory=dict)


@dataclass
class EngineConfigData:
    """运行时引擎配置."""
    max_ticks: int = 0
    verbose: bool = False
    auto_advance_time: bool = True
    time_advance_per_tick: int = 1


@dataclass
class PropagationConfig:
    """传播配置."""
    enable: bool = True
    max_hops: int = 3
    base_distortion_rate: float = 0.15


@dataclass
class WorldConfigData:
    """世界配置."""
    world_name: str = "Default World"


# =============================================================================
# 加载器
# =============================================================================


def _load_yaml(path: Path) -> dict:
    """加载 YAML 文件为 dict.

    优先使用 pyyaml，不可用时回退到简单行解析。
    """
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # 回退：基于缩进的简单 YAML 解析
        result: dict = {}
        scopes: list[dict] = [result]  # 缩进栈
        prev_indent: int = -1

        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                # 去掉行内注释
                if "#" in raw_line:
                    raw_line = raw_line[:raw_line.index("#")]
                line = raw_line.rstrip()
                if not line.strip():
                    continue

                indent = len(raw_line) - len(raw_line.lstrip())
                stripped = line.strip()

                # 缩进层级管理
                if indent == 0:
                    scopes = [result]
                elif indent > prev_indent:
                    pass  # 进入子级，保持当前栈
                elif indent < prev_indent:
                    # 退出子级
                    while len(scopes) > 1 and indent < prev_indent:
                        scopes.pop()
                        prev_indent = max(
                            (len(raw) - len(raw.lstrip()))
                            for raw in [" " * (len(scopes) - 1) * 2]
                        ) if len(scopes) > 1 else 0
                prev_indent = indent

                # 当前作用域
                current = scopes[-1]

                if stripped.endswith(":"):
                    key = stripped[:-1].strip()
                    new_scope: dict = {}
                    current[key] = new_scope
                    scopes.append(new_scope)
                elif ":" in stripped:
                    key, _, val = stripped.partition(":")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    val = _parse_value(val)
                    current[key] = val

        return result


def _parse_value(val: str):
    """将字符串值转为合适的 Python 类型."""
    if val in ("true", "True"):
        return True
    if val in ("false", "False"):
        return False
    if val == "":
        return ""
    try:
        return int(val)
    except ValueError:
        try:
            return float(val)
        except ValueError:
            return val


# =============================================================================
# 公共 API
# =============================================================================


def load_llm_config() -> LLMConfigData:
    """加载 LLM 配置."""
    path = _CONFIG_DIR / "llm.yaml"
    if not path.exists():
        return LLMConfigData()

    data = _load_yaml(path)

    # 读取 provider 和对应配置段
    provider = data.get("provider", "deepseek")
    provider_data = data.get(provider, data)

    # 读取 node 覆盖
    nodes_config: dict[str, NodeLLMConfigData] = {}
    overrides = data.get("node_overrides", data.get("nodes", {}))
    if isinstance(overrides, dict):
        for name, ndata in overrides.items():
            if isinstance(ndata, dict):
                nodes_config[name] = NodeLLMConfigData(
                    model=ndata.get("model", ""),
                    temperature=float(ndata.get("temperature", 0.7)),
                    max_tokens=int(ndata.get("max_tokens", 1024)),
                )

    return LLMConfigData(
        provider=provider,
        model=provider_data.get("model", "deepseek-chat"),
        api_key="",  # 从环境变量获取，不写入配置
        base_url=provider_data.get("base_url", ""),
        temperature=float(provider_data.get("temperature", 0.7)),
        max_tokens=int(provider_data.get("max_tokens", 2048)),
        nodes=nodes_config,
    )


def load_runtime_config() -> EngineConfigData:
    """加载运行时配置."""
    path = _CONFIG_DIR / "runtime.yaml"
    if not path.exists():
        return EngineConfigData()

    data = _load_yaml(path)
    engine_data = data.get("engine", {})
    if isinstance(engine_data, dict):
        return EngineConfigData(
            max_ticks=int(engine_data.get("max_ticks", 0)),
            verbose=bool(engine_data.get("verbose", False)),
            auto_advance_time=bool(engine_data.get("auto_advance_time", True)),
            time_advance_per_tick=int(engine_data.get("time_advance_per_tick", 1)),
        )
    return EngineConfigData()


def get_llm_mode() -> str:
    """获取 LLM 运行模式: mock / real.

    Returns:
        "mock" 或 "real".
    """
    path = _CONFIG_DIR / "runtime.yaml"
    if not path.exists():
        return "mock"
    data = _load_yaml(path)
    return data.get("llm_mode", "mock")


def load_world_config() -> WorldConfigData:
    """加载世界配置."""
    path = _CONFIG_DIR / "world.yaml"
    if not path.exists():
        return WorldConfigData()

    data = _load_yaml(path)
    world_data = data.get("world", {})
    if isinstance(world_data, dict):
        return WorldConfigData(
            world_name=world_data.get("world_name", "Default World"),
        )
    return WorldConfigData()
