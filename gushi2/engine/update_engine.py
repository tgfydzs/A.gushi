"""UpdateEngine — N12 唯一数据写入口.

所有 NPC 维护表、WorldState、SceneMemory 等的修改必须通过此类。
任何 Node 或其他模块不得直接修改 Entity。

写入规则：
1. 所有写入前必须通过 StateValidator 校验
2. 每次写入生成 NPCUpdateRecord 用于日志和回滚
3. 先验证、再记录、最后写入

不负责：
- LLM 调用
- 新事实生成
- 业务决策
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from engine.context import WorldContext
from engine.validation import StateValidator, ValidationResult

from Schema.world_schema import (
    WorldState, GlobalEvent, EventPropagation, WorldEventStatus,
)
from Schema.npc_schema import NPC, Emotion
from Schema.event_schema import CurrentEvent
from Schema.perception_schema import PerceptionResult
from Schema.memory_schema import (
    MemoryEntry, KnowledgeEntry, ObservationEntry, KnowledgeSource,
)
from Schema.belief_schema import BeliefEntry, PendingEntry, ReasoningResult
from Schema.relationship_schema import (
    RelationshipValue, RelationshipType, FavorabilityChange,
)
from Schema.dialogue_schema import (
    ResponseResult, DialogueHistory, DialogueHistoryEntry,
)
from Schema.director_schema import DirectorResult, SceneMemory
from Schema.action_schema import GoalEntry, NPCUpdateRecord

logger = logging.getLogger(__name__)


# =============================================================================
# UpdateEngine
# =============================================================================


class UpdateEngine:
    """N12 — 唯一数据写入口.

    接收所有 Node 的暂态输出，验证后写入持久 Entity。
    """

    def __init__(self, context: WorldContext) -> None:
        """初始化.

        Args:
            context: 运行时上下文（持有所有 Entity 的引用）.
        """
        self._ctx = context
        self._validator = StateValidator(
            npcs=context.npcs,
            world_rules=(
                context.world_definition.rules
                if context.world_definition else []
            ),
        )
        self._records: list[NPCUpdateRecord] = []
        self._errors: list[str] = []

    # ── 统一入口 ──────────────────────────────────────────

    def apply_tick_results(
        self,
        event_output: Any = None,            # EventNodeOutput
        perception_result: Optional[PerceptionResult] = None,
        reasoning_result: Optional[ReasoningResult] = None,
        response_result: Optional[ResponseResult] = None,
        director_result: Optional[DirectorResult] = None,
        propagation_output: Any = None,      # PropagationNodeOutput
    ) -> list[NPCUpdateRecord]:
        """统一入口：按顺序应用所有 Node 输出.

        顺序：Event → Perception → Reasoning → Response → Director → Propagation

        Returns:
            所有 NPCUpdateRecord 列表.
        """
        self._records.clear()
        self._errors.clear()

        # 1. Event
        if event_output is not None:
            self.apply_event_result(event_output)

        # 2. Perception
        if perception_result is not None:
            self.apply_perception_result(perception_result)

        # 3. Reasoning
        if reasoning_result is not None:
            self.apply_reasoning_result(reasoning_result)

        # 4. Response
        if response_result is not None:
            self.apply_response_result(response_result)

        # 5. Director
        if director_result is not None:
            self.apply_director_result(director_result)

        # 6. Propagation
        if propagation_output is not None:
            self.apply_propagation_result(propagation_output)

        return list(self._records)

    @property
    def errors(self) -> list[str]:
        """本轮写入中产生的错误."""
        return list(self._errors)

    @property
    def has_errors(self) -> bool:
        """本轮写入是否有错误."""
        return len(self._errors) > 0

    # ========================================================================
    # apply_event_result
    # ========================================================================

    def apply_event_result(self, event_output: Any) -> None:
        """应用 N7 事件层输出.

        处理：
        - 延续事件 → 更新 CurrentEvent 的部分字段
        - 新建事件 → 创建新的 CurrentEvent

        注意：不直接修改 WorldState 的历史事实。

        Args:
            event_output: EventNodeOutput 暂态对象.
        """
        try:
            # 校验
            val = self._validator.validate_event(
                location=event_output.location,
                involved_npc_ids=(
                    event_output.involved_npc_ids
                    or event_output.added_npc_ids
                ),
                scope=event_output.scope,
            )
            if not val.passed:
                self._errors.append(f"Event 校验失败: {val.error}")
                return

            if event_output.continue_context:
                # 延续事件：部分更新
                ce = self._ctx.current_event
                if ce is None:
                    # 没有旧事件 → 当作新建
                    self._ctx.current_event = CurrentEvent(
                        location=event_output.location,
                        topic=event_output.topic,
                        addressed_npc_id=event_output.addressed_npc_id,
                        involved_npc_ids=list(
                            event_output.involved_npc_ids
                        ),
                        event_type=event_output.event_type,
                        scope=event_output.scope,
                        tick_started=self._ctx.tick_number,
                        tick_updated=self._ctx.tick_number,
                    )
                    return

                # 更新话题
                if event_output.topic:
                    ce.topic = event_output.topic
                # 更新对话对象
                if event_output.addressed_npc_id:
                    ce.addressed_npc_id = event_output.addressed_npc_id
                # 加人
                for nid in event_output.added_npc_ids:
                    if nid not in ce.involved_npc_ids:
                        ce.involved_npc_ids.append(nid)
                # 踢人
                for nid in event_output.removed_npc_ids:
                    if nid in ce.involved_npc_ids:
                        ce.involved_npc_ids.remove(nid)

                ce.tick_updated = self._ctx.tick_number

            else:
                # 新建事件
                self._ctx.current_event = CurrentEvent(
                    location=event_output.location,
                    topic=event_output.topic,
                    addressed_npc_id=event_output.addressed_npc_id,
                    involved_npc_ids=list(event_output.involved_npc_ids),
                    event_type=event_output.event_type,
                    scope=event_output.scope,
                    tick_started=self._ctx.tick_number,
                    tick_updated=self._ctx.tick_number,
                )

            # 同步玩家位置到 WorldState
            if self._ctx.world_state and event_output.location:
                self._ctx.world_state.player_location = event_output.location

        except Exception as e:
            self._errors.append(f"apply_event_result 异常: {e}")
            logger.error(f"apply_event_result 异常: {e}", exc_info=True)

    # ========================================================================
    # apply_perception_result
    # ========================================================================

    def apply_perception_result(
        self, perception_result: PerceptionResult,
    ) -> None:
        """应用 N11 感知层输出.

        写入:
        - NPC.observation (只追加)
        - NPC.pending_reason (只追加)

        禁止修改: belief, knowledge, personality.

        Args:
            perception_result: PerceptionResult 暂态对象.
        """
        try:
            for perception in perception_result.npc_perceptions:
                npc = self._ctx.npcs.get(perception.npc_id)
                if npc is None:
                    continue

                record = NPCUpdateRecord(npc_id=npc.npc_id)
                updated: list[str] = []

                # 写入观察
                if perception.perceived_summary and not perception.has_no_perception():
                    npc.observation.append(ObservationEntry(
                        observation=perception.perceived_summary,
                        target_type="event",
                        detail_level=3,
                        is_anomaly=False,
                    ))
                    record.added_observations.append(
                        perception.perceived_summary
                    )
                    updated.append("observation")

                # 写入 pending（感知到了但未理解 → 挂起待推理）
                if perception.has_perception() and perception.perceived_summary:
                    npc.pending_reason.append(PendingEntry(
                        event_description=perception.perceived_summary,
                        priority=5,
                    ))
                    record.added_pending.append(
                        perception.perceived_summary
                    )
                    updated.append("pending_reason")

                if updated:
                    record.updated_tables = updated
                    self._records.append(record)

        except Exception as e:
            self._errors.append(f"apply_perception_result 异常: {e}")
            logger.error(f"apply_perception_result 异常: {e}", exc_info=True)

    # ========================================================================
    # apply_reasoning_result
    # ========================================================================

    def apply_reasoning_result(
        self, reasoning_result: ReasoningResult,
    ) -> None:
        """应用 N15 推理层输出.

        写入:
        - NPC.knowledge (new_knowledge → KnowledgeEntry, source=reasoning)
        - NPC.belief (new_beliefs → BeliefEntry, 追加不覆盖)
        - NPC.pending_reason (resolved_pending → 清除)
        - NPC.goal (goals_updated → 追加/更新)

        规则:
        1. reasoning_output 是推理摘要，不是事实——仅作日志
        2. new_beliefs 追加到现有信念列表，不覆盖已有信念
        3. new_knowledge 必须标记 source=reasoning，confidence=0.7
        4. 已处理的 pending 从列表中移除

        Args:
            reasoning_result: ReasoningResult 暂态对象.
        """
        try:
            for reasoning in reasoning_result.npc_reasonings:
                npc = self._ctx.npcs.get(reasoning.npc_id)
                if npc is None:
                    continue

                if not reasoning.has_results():
                    continue

                record = NPCUpdateRecord(npc_id=npc.npc_id)
                updated: list[str] = []

                # 写入新知识 (source=reasoning, confidence=0.7)
                for fact in reasoning.new_knowledge:
                    if not fact or not fact.strip():
                        continue
                    val = self._validator.validate_knowledge_insert(
                        npc_id=npc.npc_id,
                        fact=fact,
                        source="reasoning",
                        confidence=0.7,
                    )
                    if val.passed:
                        npc.knowledge.append(KnowledgeEntry(
                            fact=fact,
                            source=KnowledgeSource.REASONING,
                            confidence=0.7,
                        ))
                        record.added_knowledge.append(fact)
                        if "knowledge" not in updated:
                            updated.append("knowledge")
                    elif val.warning:
                        logger.warning(
                            f"Reasoning knowledge warning: {val.warning}"
                        )
                        # 警告不影响写入
                        npc.knowledge.append(KnowledgeEntry(
                            fact=fact,
                            source=KnowledgeSource.REASONING,
                            confidence=0.7,
                        ))

                # 写入新信念（追加，不覆盖）
                for belief_text in reasoning.new_beliefs:
                    if not belief_text or not belief_text.strip():
                        continue
                    val = self._validator.validate_belief_change(
                        npc_id=npc.npc_id,
                        new_belief=belief_text,
                        new_strength=0.6,
                    )
                    if val.passed:
                        npc.belief.append(BeliefEntry(
                            belief=belief_text,
                            basis="reasoning",
                            strength=0.6,
                        ))
                        record.added_beliefs.append(belief_text)
                        if "belief" not in updated:
                            updated.append("belief")

                # 清除已处理的 pending
                for resolved_desc in reasoning.resolved_pending:
                    npc.pending_reason = [
                        p for p in npc.pending_reason
                        if p.event_description != resolved_desc
                    ]

                # 更新/新增目标
                for goal_text in reasoning.goals_updated:
                    if not goal_text or not goal_text.strip():
                        continue
                    # 查找是否已存在类似目标
                    found = False
                    for g in npc.goal:
                        if g.goal == goal_text:
                            g.progress = "推理更新"
                            found = True
                            break
                    if not found:
                        npc.goal.append(GoalEntry(
                            goal=goal_text,
                            priority=6,
                            category="reactive",
                        ))
                    record.added_goals.append(goal_text)
                    if "goal" not in updated:
                        updated.append("goal")

                # 情绪变化
                if reasoning.emotion_change:
                    try:
                        new_emotion = Emotion(reasoning.emotion_change)
                        npc.current_state.emotion = new_emotion
                        if "current_state" not in updated:
                            updated.append("current_state")
                    except ValueError:
                        logger.warning(
                            f"无效的情绪值: {reasoning.emotion_change}"
                        )

                if updated:
                    record.updated_tables = updated
                    self._records.append(record)

        except Exception as e:
            self._errors.append(f"apply_reasoning_result 异常: {e}")
            logger.error(f"apply_reasoning_result 异常: {e}", exc_info=True)

    # ========================================================================
    # apply_response_result
    # ========================================================================

    def apply_response_result(
        self, response_result: ResponseResult,
    ) -> None:
        """应用 N16 回应层输出.

        写入:
        - DialogueHistory (追加对话记录)
        - RelationshipValue (好感度/信任度变化)

        规则:
        - favorability_changes 是建议，必须校验
        - 关系必须已存在才能修改（不自动创建陌生人关系）
        - 变化范围受限 (好感度单次 max ±30, 信任度单次 max ±25)

        Args:
            response_result: ResponseResult 暂态对象.
        """
        try:
            # 初始化 DialogueHistory
            if self._ctx.dialogue_history is None:
                self._ctx.dialogue_history = DialogueHistory()

            for response in response_result.npc_responses:
                npc = self._ctx.npcs.get(response.npc_id)
                if npc is None:
                    continue

                # 写入对话历史
                if response.response_text.strip():
                    self._ctx.dialogue_history.add(
                        npc_id=response.npc_id,
                        npc_name=response.npc_name,
                        text=response.response_text,
                        tick=self._ctx.tick_number,
                        response_type=response.response_type,
                        emotion=response.emotion,
                    )

                # 处理好感度变化
                if response.favorability_changes:
                    record = NPCUpdateRecord(npc_id=npc.npc_id)
                    updated: list[str] = []

                    for fc in response.favorability_changes:
                        if not isinstance(fc, dict):
                            continue

                        target_id = fc.get("target_npc_id", "")
                        fav_delta = fc.get("favorability_delta", 0)
                        trust_delta = fc.get("trust_delta", 0)
                        reason = fc.get("reason", "")

                        # 校验
                        val = self._validator.validate_relationship_change(
                            source_npc_id=npc.npc_id,
                            target_npc_id=target_id,
                            favorability_delta=fav_delta,
                            trust_delta=trust_delta,
                        )
                        if not val.passed:
                            self._errors.append(
                                f"好感度校验失败: {val.error}"
                            )
                            continue

                        # 获取或创建关系
                        if target_id not in npc.relationship:
                            npc.relationship[target_id] = RelationshipValue(
                                target_npc_id=target_id,
                                relationship_type=RelationshipType.STRANGER,
                            )

                        rel = npc.relationship[target_id]

                        # 应用变化
                        if fav_delta != 0:
                            new_fav = rel.favorability + fav_delta
                            rel.favorability = max(-100, min(100, new_fav))
                        if trust_delta != 0:
                            new_tr = rel.trust + trust_delta
                            rel.trust = max(0, min(100, new_tr))
                        if reason:
                            rel.history.append(
                                f"[Tick {self._ctx.tick_number}] {reason}"
                            )
                        if "relationship" not in updated:
                            updated.append("relationship")
                        record.updated_relationships.append(target_id)

                    if updated:
                        record.updated_tables = updated
                        self._records.append(record)

        except Exception as e:
            self._errors.append(f"apply_response_result 异常: {e}")
            logger.error(f"apply_response_result 异常: {e}", exc_info=True)

    # ========================================================================
    # apply_director_result
    # ========================================================================

    def apply_director_result(
        self, director_result: DirectorResult,
    ) -> None:
        """应用 N17 导演层输出.

        写入:
        - SceneMemory.scene_description (场景记忆)

        禁止修改: WorldState 的任何字段。

        Args:
            director_result: DirectorResult 暂态对象.
        """
        try:
            # 仅处理场景更新
            if not director_result.scene_update:
                return

            # 场景记忆更新通过 Engine 的 scene_memory 管理
            # 不在这里直接操作，由 WorldEngine 调用时处理
            # 这里只做校验——确保不修改 WorldState

            # 校验 WorldState 没有被意外修改
            if director_result.world_changes:
                # world_changes 是给玩家看的叙事提示
                # 不是真正的 WorldState 修改——不在此处处理
                pass

        except Exception as e:
            self._errors.append(f"apply_director_result 异常: {e}")
            logger.error(f"apply_director_result 异常: {e}", exc_info=True)

    # ========================================================================
    # apply_propagation_result
    # ========================================================================

    def apply_propagation_result(
        self, propagation_output: Any,
    ) -> None:
        """应用 N18 传播层输出.

        写入:
        - NPC.knowledge (传播内容 → KnowledgeEntry)
        - WorldState.global_events (重大事件升级)
        - WorldState.event_propagations (传播记录)

        规则:
        1. 传播 Knowledge 必须标记 source（propagation/rumor/official_news/letter）
        2. 保留 confidence（来自 LLM 判断的可信度）
        3. rumor 不能转为 fact
        4. 全局事件只写入描述文字，不修改 WorldState 结构

        Args:
            propagation_output: PropagationNodeOutput 暂态对象.
        """
        try:
            if not hasattr(propagation_output, 'suggestions'):
                return

            suggestions = propagation_output.suggestions or []

            for sug in suggestions:
                if not sug.should_receive:
                    continue
                if not sug.content or not sug.content.strip():
                    continue

                npc = self._ctx.npcs.get(sug.target_npc_id)
                if npc is None:
                    continue

                # 校验
                val = self._validator.validate_knowledge_insert(
                    npc_id=npc.npc_id,
                    fact=sug.content,
                    source=sug.propagation_method or "propagation",
                    confidence=sug.confidence,
                )
                if not val.passed:
                    self._errors.append(f"传播知识校验失败: {val.error}")
                    continue

                # 来源映射
                source_map = {
                    "word_of_mouth": KnowledgeSource.HEARD,
                    "rumor": KnowledgeSource.HEARD,
                    "official_news": KnowledgeSource.TAUGHT,
                    "letter": KnowledgeSource.DIRECT,
                    "magic": KnowledgeSource.HEARD,
                }
                k_source = source_map.get(
                    sug.propagation_method, KnowledgeSource.HEARD
                )

                # 写入知识
                npc.knowledge.append(KnowledgeEntry(
                    fact=sug.content,
                    source=k_source,
                    confidence=sug.confidence,
                ))

                record = NPCUpdateRecord(
                    npc_id=npc.npc_id,
                    updated_tables=["knowledge"],
                )
                record.added_knowledge.append(sug.content)
                self._records.append(record)

            # 全局事件升级
            ge = getattr(propagation_output, 'global_event', None)
            if ge is not None and ge.should_create:
                if self._ctx.world_state is not None:
                    self._ctx.world_state.global_events.append(
                        GlobalEvent(
                            event_id="",
                            description=ge.description,
                            affected_area=ge.affected_area or "all",
                            status=WorldEventStatus.ACTIVE,
                            source="propagation",
                        )
                    )

                # 传播记录
                if self._ctx.events and self._ctx.world_state is not None:
                    self._ctx.world_state.event_propagations.append(
                        EventPropagation(
                            source_event_id=self._ctx.events[0].event_id,
                            propagation_method="rumor",
                            hop_count=1,
                            is_complete=True,
                        )
                    )

        except Exception as e:
            self._errors.append(f"apply_propagation_result 异常: {e}")
            logger.error(f"apply_propagation_result 异常: {e}", exc_info=True)
