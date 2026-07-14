"""PromptLoader — 统一加载 prompts/*.md.

职责：
- 根据 Prompt 名称加载对应的 .md 文件
- 路径自动定位（不依赖当前工作目录）
- 内置缓存（同一 prompt 不重复读取）
- 文件不存在时抛出明确异常
"""

from __future__ import annotations

from pathlib import Path

# prompts 目录的绝对路径（基于本文件位置推算）
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


class PromptNotFoundError(FileNotFoundError):
    """Prompt 文件不存在时的明确异常."""

    def __init__(self, prompt_name: str, searched_path: Path) -> None:
        super().__init__(
            f"Prompt 文件不存在: '{prompt_name}' "
            f"(搜索路径: {searched_path})"
        )
        self.prompt_name = prompt_name
        self.searched_path = searched_path


class PromptLoader:
    """Prompt 模板加载器.

    统一从 prompts/*.md 读取 Prompt 模板。
    内置缓存避免重复 I/O。

    用法:
        loader = PromptLoader()
        prompt_text = loader.load("event")          # → prompts/event.md
        rendered = loader.load_and_fill("event",    # → 加载并替换变量
            day="3", weather="sunny", ...)
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}

    # ── 路径 ──────────────────────────────────────────────

    @property
    def prompts_dir(self) -> Path:
        """prompts 目录的绝对路径."""
        return _PROMPTS_DIR

    def _resolve_path(self, prompt_name: str) -> Path:
        """将 Prompt 名称解析为文件路径.

        Args:
            prompt_name: Prompt 名称（如 "event"、"perception"）.

        Returns:
            对应的 .md 文件路径.
        """
        return self.prompts_dir / f"{prompt_name}.md"

    # ── 加载 ──────────────────────────────────────────────

    def load(self, prompt_name: str) -> str:
        """加载并返回 Prompt 模板原始文本.

        如果模板已被加载过，直接从缓存返回。

        Args:
            prompt_name: Prompt 名称。对应 prompts/{name}.md。

        Returns:
            Prompt 模板的完整文本。

        Raises:
            PromptNotFoundError: 文件不存在时抛出。
        """
        if prompt_name in self._cache:
            return self._cache[prompt_name]

        path = self._resolve_path(prompt_name)

        if not path.exists():
            raise PromptNotFoundError(prompt_name, path)

        text = path.read_text(encoding="utf-8")
        self._cache[prompt_name] = text
        return text

    def load_and_fill(self, prompt_name: str, **variables: str) -> str:
        """加载 Prompt 模板并替换其中的变量占位符.

        使用 {variable_name} 占位符语法。
        只替换显式传入的变量——模板中的其他花括号（如 JSON 示例）不受影响。

        Args:
            prompt_name: Prompt 名称。
            **variables: 要替换的变量名和值。
                例如: day="3", weather="sunny"

        Returns:
            渲染后的 Prompt 文本。

        Raises:
            PromptNotFoundError: 文件不存在时抛出。
        """
        template = self.load(prompt_name)
        result = template
        for key, value in variables.items():
            placeholder = "{" + key + "}"
            result = result.replace(placeholder, str(value))
        return result

    # ── 缓存管理 ──────────────────────────────────────────

    def clear_cache(self) -> None:
        """清空缓存。用于 Prompt 文件热更新场景."""
        self._cache.clear()

    def is_cached(self, prompt_name: str) -> bool:
        """检查指定 Prompt 是否已在缓存中."""
        return prompt_name in self._cache
