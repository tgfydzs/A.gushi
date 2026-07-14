"""StateValidator — 状态修改前的规则校验.

UpdateEngine 在执行写入前必须通过此层的校验。
所有校验规则独立于 UpdateEngine 逻辑——便于单独测试和修改。

校验分为：
- 存在性：引用的 ID 是否真实存在
- 合法性：字段值是否在允许范围内
- 一致性：修改是否与世界规则冲突
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from Schema.world_schema import WorldRule, WorldState
from Schema.npc_schema import NPC
from Schema.relationship_schema import RelationshipValue


# =============================================================================
# 校验结果
# =============================================================================


@dataclass
class ValidationResult:
    """单次校验结果.

    Attributes:
        passed: 是否通过.
        error: 不通过时的错误描述.
        warning: 通过但有风险的警告.
    """

    passed: bool = True
    error: str = ""
    warning: str = ""


# =============================================================================
# Validator
# =============================================================================


class StateValidator:
    """状态修改校验器.

    所有写入操作在 UpdateEngine 执行前必须通过此校验。
    """

    # 好感度变化范围
    FAVORABILITY_MIN: int = -100
    FAVORABILITY_MAX: int = 100
    FAVORABILITY_MAX_DELTA: int = 30  # 单次最大变化量

    # 信任度变化范围
    TRUST_MIN: int = 0
    TRUST_MAX: int = 100
    TRUST_MAX_DELTA: int = 25

    # 信念强度范围
    BELIEF_STRENGTH_MIN: float = 0.0
    BELIEF_STRENGTH_MAX: float = 1.0

    def __init__(
        self, npcs: dict[str, NPC] | None = None,
        world_rules: list[WorldRule] | None = None,
    ) -> None:
        """初始化校验器.

        Args:
            npcs: NPC 字典，用于 ID 存在性检查.
            world_rules: 世界规则列表，用于合规检查.
        """
        self._npcs: dict[str, NPC] = npcs or {}
        self._world_rules: list[WorldRule] = world_rules or []

    # ── Event 校验 ────────────────────────────────────────

    def validate_event(
        self,
        event_id: str = "",
        location: str = "",
        involved_npc_ids: list[str] | None = None,
        scope: str = "",
    ) -> ValidationResult:
        """校验事件输出的合法性.

        Args:
            event_id: 事件 ID.
            location: 事件位置.
            involved_npc_ids: 参与者 NPC ID 列表.
            scope: 公开程度 (private/area/group/public/hidden).

        Returns:
            ValidationResult.
        """
        # scope 合法性
        valid_scopes = {"private", "area", "group", "public", "hidden"}
        if scope and scope not in valid_scopes:
            return ValidationResult(
                passed=False,
                error=f"无效的 scope: '{scope}'，合法值: {valid_scopes}",
            )

        # NPC ID 存在性
        if involved_npc_ids:
            for nid in involved_npc_ids:
                if nid and nid not in self._npcs:
                    return ValidationResult(
                        passed=False,
                        error=f"NPC ID '{nid}' 不存在",
                    )

        return ValidationResult(passed=True)

    # ── Belief 变更校验 ────────────────────────────────────

    def validate_belief_change(
        self, npc_id: str, new_belief: str, new_strength: float = 0.5,
    ) -> ValidationResult:
        """校验信念变更.

        Args:
            npc_id: NPC ID.
            new_belief: 新信念内容.
            new_strength: 信念强度.

        Returns:
            ValidationResult.
        """
        if npc_id not in self._npcs:
            return ValidationResult(
                passed=False,
                error=f"NPC '{npc_id}' 不存在",
            )

        if not new_belief or not new_belief.strip():
            return ValidationResult(
                passed=False,
                error="信念内容不能为空",
            )

        if not (self.BELIEF_STRENGTH_MIN <= new_strength <= self.BELIEF_STRENGTH_MAX):
            return ValidationResult(
                passed=False,
                error=f"信念强度 {new_strength} 超出范围 "
                f"[{self.BELIEF_STRENGTH_MIN}, {self.BELIEF_STRENGTH_MAX}]",
            )

        return ValidationResult(passed=True)

    # ── Relationship 变更校验 ──────────────────────────────

    def validate_relationship_change(
        self,
        source_npc_id: str,
        target_npc_id: str,
        favorability_delta: int = 0,
        trust_delta: int = 0,
    ) -> ValidationResult:
        """校验好感度/信任度变更.

        Args:
            source_npc_id: 主体 NPC ID.
            target_npc_id: 目标 NPC ID.
            favorability_delta: 好感度变化量.
            trust_delta: 信任度变化量.

        Returns:
            ValidationResult.
        """
        # 存在性
        if source_npc_id not in self._npcs:
            return ValidationResult(
                passed=False,
                error=f"源 NPC '{source_npc_id}' 不存在",
            )
        if target_npc_id not in self._npcs:
            return ValidationResult(
                passed=False,
                error=f"目标 NPC '{target_npc_id}' 不存在",
            )
        if source_npc_id == target_npc_id:
            return ValidationResult(
                passed=False,
                error="不能修改自己对自己的好感度",
            )

        # 变化范围
        if abs(favorability_delta) > self.FAVORABILITY_MAX_DELTA:
            return ValidationResult(
                passed=False,
                error=f"好感度变化 {favorability_delta:+d} "
                f"超过最大单次变化 {self.FAVORABILITY_MAX_DELTA}",
            )
        if abs(trust_delta) > self.TRUST_MAX_DELTA:
            return ValidationResult(
                passed=False,
                error=f"信任度变化 {trust_delta:+d} "
                f"超过最大单次变化 {self.TRUST_MAX_DELTA}",
            )

        # 结果范围（产生警告而非拒绝——因为 apply 层会做 clamp）
        npc = self._npcs[source_npc_id]
        existing_rel = npc.relationship.get(target_npc_id)
        if existing_rel is not None:
            new_fav = existing_rel.favorability + favorability_delta
            new_tr = existing_rel.trust + trust_delta
            warning = ""
            if not (self.FAVORABILITY_MIN <= new_fav <= self.FAVORABILITY_MAX):
                warning = (
                    f"变更后好感度 {new_fav} 将超出 "
                    f"[{self.FAVORABILITY_MIN}, {self.FAVORABILITY_MAX}]，"
                    f"将被 clamp"
                )
            if not (self.TRUST_MIN <= new_tr <= self.TRUST_MAX):
                warning += (
                    f"变更后信任度 {new_tr} 将超出 "
                    f"[{self.TRUST_MIN}, {self.TRUST_MAX}]，"
                    f"将被 clamp"
                )
            if warning:
                return ValidationResult(passed=True, warning=warning)

        return ValidationResult(passed=True)

    # ── Knowledge 插入校验 ─────────────────────────────────

    def validate_knowledge_insert(
        self,
        npc_id: str,
        fact: str,
        source: str = "unknown",
        confidence: float = 1.0,
    ) -> ValidationResult:
        """校验知识插入.

        Args:
            npc_id: NPC ID.
            fact: 知识内容.
            source: 来源 (direct/heard/reasoning/propagation/rumor/unknown).
            confidence: 可信度 (0.0-1.0).

        Returns:
            ValidationResult.
        """
        if npc_id not in self._npcs:
            return ValidationResult(
                passed=False,
                error=f"NPC '{npc_id}' 不存在",
            )

        if not fact or not fact.strip():
            return ValidationResult(
                passed=False,
                error="知识内容不能为空",
            )

        valid_sources = {
            "direct", "heard", "reasoning",
            "propagation", "rumor", "official_news",
            "letter", "taught", "read", "unknown",
            "word_of_mouth",  # 传播层使用，映射到 KnowledgeSource.HEARD
        }
        if source and source not in valid_sources:
            return ValidationResult(
                passed=False,
                error=f"无效的知识来源: '{source}'",
            )

        if not (0.0 <= confidence <= 1.0):
            return ValidationResult(
                passed=False,
                error=f"可信度 {confidence} 超出 [0.0, 1.0]",
            )

        # 警告: propagation 和 rumor 的 confidence 不应太高
        if source in ("propagation", "rumor") and confidence > 0.8:
            return ValidationResult(
                passed=True,
                warning=(
                    f"传播/传言来源的知识 confidence={confidence} 偏高，"
                    f"建议 <= 0.7"
                ),
            )

        return ValidationResult(passed=True)

    # ── WorldState 修改校验 ────────────────────────────────

    def validate_world_state_change(
        self, world_state: WorldState, field_name: str, new_value: Any,
    ) -> ValidationResult:
        """校验 WorldState 修改.

        特定字段受世界规则保护，不可随意修改。
        - 时间 (day/time_of_day): 只能通过 advance_time() 修改
        - 天气 (weather): 只能通过特定机制修改
        - 规则 (rules): 只有 is_mutable=true 的规则可以被修改

        Args:
            world_state: 当前世界状态.
            field_name: 要修改的字段名.
            new_value: 新值.

        Returns:
            ValidationResult.
        """
        # 受保护的字段
        protected_fields = {"day", "time_of_day"}
        if field_name in protected_fields:
            return ValidationResult(
                passed=False,
                error=(
                    f"WorldState.{field_name} 受保护，"
                    f"只能通过 advance_time() 修改"
                ),
            )

        return ValidationResult(passed=True)

    # ── 批量 Pending 检查 ──────────────────────────────────

    def validate_pending_batch(
        self, npc_id: str, count: int,
    ) -> ValidationResult:
        """检查 NPC 的 pending 条目是否过多.

        Args:
            npc_id: NPC ID.
            count: 新增数量.

        Returns:
            ValidationResult.
        """
        if npc_id not in self._npcs:
            return ValidationResult(
                passed=False,
                error=f"NPC '{npc_id}' 不存在",
            )

        npc = self._npcs[npc_id]
        total_after = len(npc.pending_reason) + count

        if total_after > 50:
            return ValidationResult(
                passed=True,
                warning=f"NPC {npc.name} pending 条目数将达 {total_after}，建议清理",
            )

        return ValidationResult(passed=True)
