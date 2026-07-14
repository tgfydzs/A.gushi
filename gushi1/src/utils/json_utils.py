"""JSON 解析工具 — 处理LLM输出的JSON清理与解析."""

import json
import re
from typing import Any, Optional


def sanitize_text(text: str) -> str:
    """清理文本中的非法Unicode代理对字符.

    DeepSeek等LLM有时返回包含unpaired surrogate的文本
    （如 \\udcaf），这些字符无法被UTF-8编码，会导致程序崩溃。

    Args:
        text: 原始文本.

    Returns:
        清理后的安全文本.
    """
    if not text:
        return text
    # 移除非法代理对字符 (U+D800-U+DFFF)
    # surrogates不允许出现在有效的UTF-8中
    cleaned: list[str] = []
    for ch in text:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            continue  # 跳过非法代理字符
        cleaned.append(ch)
    return "".join(cleaned)


def clean_json(text: str) -> str:
    """清理LLM输出，提取JSON部分.

    处理LLM输出中可能混入的Markdown代码块标记
    和自然语言包围的JSON。

    Args:
        text: LLM原始输出文本.

    Returns:
        清理后的纯JSON字符串.
    """
    # 尝试提取 ```json ... ``` 代码块
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if json_match:
        return json_match.group(1).strip()

    # 尝试提取 { ... } 最外层JSON对象
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        return text[brace_start:brace_end + 1].strip()

    # 尝试提取 [ ... ] 最外层JSON数组
    bracket_start = text.find("[")
    bracket_end = text.rfind("]")
    if bracket_start != -1 and bracket_end != -1 and bracket_end > bracket_start:
        return text[bracket_start:bracket_end + 1].strip()

    return text.strip()


def parse_json(text: str) -> Optional[dict[str, Any] | list[Any]]:
    """安全解析JSON字符串.

    首先清理输出，然后解析为Python对象。

    Args:
        text: LLM原始输出文本.

    Returns:
        解析后的dict或list，解析失败返回None.
    """
    cleaned = clean_json(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def safe_get(obj: dict[str, Any], key: str, default: Any = None) -> Any:
    """从dict安全获取值.

    Args:
        obj: 源字典.
        key: 键名.
        default: 默认值.

    Returns:
        键对应的值或默认值.
    """
    if not isinstance(obj, dict):
        return default
    return obj.get(key, default)
