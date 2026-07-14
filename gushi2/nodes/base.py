"""BaseNode — 所有 LLM 节点的抽象基类.

每个 Node 的职责：
- 读取 WorldContext 中自己权限范围内的数据
- 构造 Prompt
- 通过 LLMManager 调用 LLM
- 解析 JSON 输出
- 返回对应 Schema 的暂态结果对象

禁止：
- 修改 WorldContext 中的任何 Entity
- 写入文件
- 跨节点读取数据
- 直接持有 LLMClient（必须通过 LLMManager）
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from engine.context import WorldContext
from engine.prompt_loader import PromptLoader

if TYPE_CHECKING:
    from engine.llm_manager import LLMManager

logger = logging.getLogger(__name__)


@dataclass
class NodeResult:
    """Node 执行结果包装.

    Attributes:
        success: 是否执行成功.
        data: 解析后的 Schema 对象（暂态）.
        raw_json: LLM 原始输出的 dict.
        error: 错误信息.
        node_name: 产生此结果的 Node 名称.
    """

    success: bool = True
    data: Any = None
    raw_json: Optional[dict] = None
    error: str = ""
    node_name: str = ""


class BaseNode(ABC):
    """LLM 节点抽象基类.

    所有 LLM 节点继承此类。

    用法:
        node = EventNode(llm_manager=mgr, prompt_loader=loader)
        result = node.execute(context)
        if result.success:
            current_event = result.data
    """

    # ── 子类必须定义 ──────────────────────────────────────

    @property
    @abstractmethod
    def node_name(self) -> str:
        """节点名称（对应流程图编号，如 "N7", "N11"）."""
        ...

    @property
    @abstractmethod
    def prompt_name(self) -> str:
        """Prompt 名称（对应 prompts/{name}.md，如 "event", "perception"）."""
        ...

    # ── 注入依赖 ──────────────────────────────────────────

    def __init__(
        self,
        llm_manager: Optional[LLMManager] = None,
        prompt_loader: Optional[PromptLoader] = None,
    ) -> None:
        """初始化节点.

        Args:
            llm_manager: LLM 调用管理器。为 None 时自动创建（含 MockLLMClient）。
            prompt_loader: Prompt 加载器。为 None 时自动创建。
        """
        # 延迟导入避免循环依赖
        if llm_manager is None:
            from engine.llm_manager import MockLLMManager
            llm_manager = MockLLMManager()
        self.llm_manager: LLMManager = llm_manager
        self.prompt_loader: PromptLoader = prompt_loader or PromptLoader()

    # ── 主流程 ────────────────────────────────────────────

    def execute(self, context: WorldContext) -> NodeResult:
        """执行节点主流程.

        1. 从 context 收集输入数据
        2. 加载对应 Prompt 并填充变量
        3. 调用 LLM
        4. 解析 JSON 输出
        5. 转换为 Schema 对象

        Args:
            context: 当前运行时上下文.

        Returns:
            NodeResult 包含暂态结果或错误信息.
        """
        try:
            # Step 1 + 2: 收集输入并构造 Prompt
            system_prompt, user_prompt = self._build_prompt(context)

            # Step 3: 调用 LLM
            response_text = self._call_llm(system_prompt, user_prompt)

            # Step 4 + 5: 解析 JSON → Schema 对象
            parsed = self._parse_json(response_text)
            data = self._to_schema(parsed, context)

            return NodeResult(
                success=True,
                data=data,
                raw_json=parsed,
                node_name=self.node_name,
            )

        except Exception as e:
            logger.error(f"[{self.node_name}] 执行失败: {e}", exc_info=True)
            return self._empty_result(str(e))

    # ── 子类覆盖 ──────────────────────────────────────────

    @abstractmethod
    def _build_prompt(self, context: WorldContext) -> tuple[str, str]:
        """收集输入数据并构造 Prompt.

        Args:
            context: 运行时上下文.

        Returns:
            (system_prompt, user_prompt) 元组.
        """
        ...

    @abstractmethod
    def _to_schema(self, parsed: dict, context: WorldContext) -> Any:
        """将 LLM 输出的 dict 转换为对应 Schema 对象.

        Args:
            parsed: LLM JSON 解析后的 dict.
            context: 运行时上下文.

        Returns:
            对应 Schema 的暂态对象.
        """
        ...

    # ── 子类可选覆盖 ──────────────────────────────────────

    def _empty_result(self, error_msg: str = "") -> NodeResult:
        """生成失败/默认结果.

        子类可以覆盖以返回更合适的空对象。
        """
        return NodeResult(
            success=False,
            error=error_msg,
            node_name=self.node_name,
        )

    # ── 公共工具方法 ──────────────────────────────────────

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """通过 LLMManager 调用 LLM 并返回原始文本."""
        return self.llm_manager.chat(
            node_name=self.prompt_name,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    @staticmethod
    def _parse_json(raw_text: str) -> dict:
        """解析 LLM 输出的 JSON.

        支持以下格式：
        - 纯 JSON
        - ```json ... ``` 包裹的 JSON
        - ``` ... ``` 包裹的 JSON

        Args:
            raw_text: LLM 原始输出文本.

        Returns:
            解析后的 dict。解析失败返回空 dict。
        """
        if not raw_text or not raw_text.strip():
            return {}

        text = raw_text.strip()

        # 尝试去除 markdown 代码块
        if text.startswith("```"):
            # 找到第一个换行后的内容
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1:]
            # 去掉结尾的 ```
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(
                f"JSON 解析失败，尝试修复: {raw_text[:200]}..."
            )
            # 简单修复：尝试截取第一个 { 到最后一个 }
            try:
                start = text.index("{")
                end = text.rindex("}") + 1
                return json.loads(text[start:end])
            except (ValueError, json.JSONDecodeError):
                return {}

    def _render_prompt(self, **variables: str) -> str:
        """加载当前节点的 Prompt 模板并填充变量.

        Args:
            **variables: 模板变量。

        Returns:
            渲染后的 Prompt 文本。
        """
        return self.prompt_loader.load_and_fill(
            self.prompt_name, **variables
        )
