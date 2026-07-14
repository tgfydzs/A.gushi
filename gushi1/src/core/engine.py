"""主引擎 — 世界模拟的调度核心.

Engine不负责NPC思考（那是LLM的职责）。
Engine负责调度、流程控制和状态协调。

数据流（固定不可改变）：
玩家输入 → EventLayer → PerceptionLayer → UpdateLayer → ReasoningLayer
→ UpdateLayer → ResponseLayer → DirectorLayer → PropagationLayer
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from src.core.config import Config, LLMConfig
from src.core.llm_client import LLMClient
from src.layers.world_layer import WorldLayer, WorldResult
from src.layers.world_state_layer import WorldStateLayer, WorldStateResult
from src.layers.event_layer import EventLayer, EventResult
from src.layers.perception_layer import PerceptionLayer, PerceptionResult
from src.layers.update_layer import UpdateLayer, UpdateResult
from src.layers.reasoning_layer import ReasoningLayer, ReasoningResult
from src.layers.response_layer import ResponseLayer, ResponseResult
from src.layers.director_layer import DirectorLayer, DirectorResult
from src.layers.propagation_layer import PropagationLayer, PropagationResult
from src.layers.base import LayerResult
from src.models.npc import NPC
from src.models.world import WorldDefinition
from src.models.world_state import WorldState
from src.models.event import Event


@dataclass
class TickResult:
    """单次tick的完整结果.

    Attributes:
        tick_number: 回合编号.
        player_input: 原始玩家输入.
        event_result: 事件层输出.
        perception_result: 感知层输出.
        update_perception_result: 感知更新结果.
        reasoning_result: 推理层输出.
        update_reasoning_result: 推理更新结果.
        response_result: 回应层输出.
        director_result: 导演层输出（最终叙事）.
        propagation_result: 传播层输出.
        success: 本轮是否成功.
        error: 错误信息.
    """

    tick_number: int = 0
    player_input: str = ""
    event_result: Optional[EventResult] = None
    perception_result: Optional[PerceptionResult] = None
    update_perception_result: Optional[UpdateResult] = None
    reasoning_result: Optional[ReasoningResult] = None
    update_reasoning_result: Optional[UpdateResult] = None
    response_result: Optional[ResponseResult] = None
    director_result: Optional[DirectorResult] = None
    propagation_result: Optional[PropagationResult] = None
    success: bool = True
    error: str = ""

    def get_narrative(self) -> str:
        """获取最终叙事文本（给玩家看）."""
        if self.director_result is not None:
            return self.director_result.narrative
        return ""

    def get_error_context(self) -> str:
        """获取错误上下文."""
        if self.success:
            return ""
        stages = [
            ("EventLayer", self.event_result),
            ("PerceptionLayer", self.perception_result),
            ("UpdateLayer(perception)", self.update_perception_result),
            ("ReasoningLayer", self.reasoning_result),
            ("UpdateLayer(reasoning)", self.update_reasoning_result),
            ("ResponseLayer", self.response_result),
            ("DirectorLayer", self.director_result),
        ]
        for name, result in stages:
            if result is not None and result.is_error():
                return f"{name} 失败: {result.error}"
        return self.error


@dataclass
class Engine:
    """主引擎 — 世界模拟调度核心.

    职责：
    - 管理所有Layer
    - 按固定顺序调度各层
    - 管理NPC列表和世界状态
    - 协调UpdateLayer更新维护表

    禁止：
    - Engine直接修改NPC维护表
    - Engine代替LLM做NPC思考
    - 改变层次执行顺序
    """

    config: Config = field(default_factory=Config)
    llm_client: LLMClient | None = None

    # 层次
    world_layer: WorldLayer = field(default_factory=WorldLayer)
    world_state_layer: WorldStateLayer = field(default_factory=WorldStateLayer)
    event_layer: EventLayer = field(default_factory=EventLayer)
    perception_layer: PerceptionLayer = field(default_factory=PerceptionLayer)
    update_layer: UpdateLayer = field(default_factory=UpdateLayer)
    reasoning_layer: ReasoningLayer = field(default_factory=ReasoningLayer)
    response_layer: ResponseLayer = field(default_factory=ResponseLayer)
    director_layer: DirectorLayer = field(default_factory=DirectorLayer)
    propagation_layer: PropagationLayer = field(
        default_factory=PropagationLayer
    )

    # 核心数据
    npcs: dict[str, NPC] = field(default_factory=dict)
    world_state: WorldState | None = None
    world_definition: WorldDefinition | None = None
    tick_count: int = 0

    # 导演层场景记忆 — {位置: 上一次的场景描述}
    scene_memory: dict[str, str] = field(default_factory=dict)
    last_event_location: str = ""

    # 对话历史 — 存储每轮NPC回应，按NPC过滤后发给ResponseLayer
    # 格式: [{"npc_id": "xxx", "npc_name": "xxx", "text": "xxx", "tick": 1}, ...]
    dialogue_history: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        """初始化后注入LLM客户端到各层."""
        if self.llm_client is not None:
            self._inject_llm_client()

    def _inject_llm_client(self) -> None:
        """将LLM客户端注入到需要LLM的层."""
        for layer in [
            self.event_layer,
            self.perception_layer,
            self.reasoning_layer,
            self.response_layer,
            self.director_layer,
        ]:
            if hasattr(layer, "llm_client"):
                layer.llm_client = self.llm_client

    def initialize_world(
        self,
        world_def: dict,
        world_state_data: dict | None = None,
    ) -> TickResult:
        """初始化世界.

        Args:
            world_def: 世界定义（规则、隐藏事件等）.
            world_state_data: 初始世界状态（时间、天气等）.

        Returns:
            TickResult.
        """
        result = TickResult(tick_number=0)

        # WorldLayer
        layer_result = self.world_layer.process(world_def, {})
        if not layer_result.success:
            result.success = False
            result.error = f"WorldLayer: {layer_result.error}"
            return result
        world_result = layer_result.data
        self.world_definition = world_result.world_definition

        # WorldStateLayer
        state_data = world_state_data or {
            "day": 1,
            "time_of_day": "morning",
            "weather": "sunny",
        }
        layer_result = self.world_state_layer.process(state_data, {})
        if not layer_result.success:
            result.success = False
            result.error = f"WorldStateLayer: {layer_result.error}"
            return result
        self.world_state = layer_result.data.world_state

        return result

    def add_npc(self, npc: NPC) -> None:
        """添加NPC到世界.

        Args:
            npc: NPC对象.
        """
        self.npcs[npc.npc_id] = npc

    def remove_npc(self, npc_id: str) -> None:
        """从世界移除NPC.

        Args:
            npc_id: NPC标识.
        """
        self.npcs.pop(npc_id, None)

    def get_npc(self, npc_id: str) -> NPC | None:
        """获取NPC.

        Args:
            npc_id: NPC标识.

        Returns:
            NPC或None.
        """
        return self.npcs.get(npc_id)

    def tick(self, player_input: str) -> TickResult:
        """执行一轮完整的世界模拟.

        固定执行顺序（不可改变）：
        1. EventLayer — 解析玩家输入
        2. PerceptionLayer — NPC感知
        3. UpdateLayer — 写入感知结果
        4. ReasoningLayer — NPC推理
        5. UpdateLayer — 写入推理结果
        6. ResponseLayer — NPC回应
        7. DirectorLayer — 整合叙事
        8. PropagationLayer — 传播

        Args:
            player_input: 玩家输入的原始文本.

        Returns:
            TickResult 包含本轮所有结果.
        """
        self.tick_count += 1
        result = TickResult(
            tick_number=self.tick_count,
            player_input=player_input,
        )

        # 推进世界时间
        if self.world_state is not None:
            for _ in range(self.config.world.time_advance_per_tick):
                self.world_state.advance_time()

        # 构建世界状态摘要
        world_state_summary = self._get_world_state_summary()

        # 构建NPC摘要（最小化：只发必要数据）
        npcs_summary = self._get_npcs_summary()

        # ====== 1. EventLayer ======
        # 传递NPC简要信息（含位置），帮助LLM正确判断事件地点和scope
        npc_info = {
            npc.npc_id: {"name": npc.name, "location": npc.current_state.location}
            for npc in self.npcs.values()
        }
        event_input = {
            "player_input": player_input,
            "world_state": world_state_summary,
            "available_npcs": list(self.npcs.keys()),
            "npc_info": npc_info,
            "player_location": self.world_state.player_location if self.world_state else "",
            "world_rules": [
                {"rule": r.description, "penalty": r.penalty}
                for r in (self.world_definition.rules if self.world_definition else [])
            ],
        }
        layer_result = self.event_layer.process(
            event_input, {"available_npcs": list(self.npcs.keys())}
        )
        if not layer_result.success:
            result.success = False
            result.error = f"EventLayer: {layer_result.error}"
            return result
        result.event_result = layer_result.data

        # 同步玩家位置到 WorldState
        if result.event_result.events and self.world_state:
            self.world_state.player_location = result.event_result.events[0].location

        if not result.event_result.events:
            # 无事件，直接返回
            return result

        # ====== 2. PerceptionLayer ======
        # 传递完整NPC对象，让LLM根据NPC维护表判断感知
        npcs_data = [
            {
                "npc_id": npc.npc_id,
                "name": npc.name,
                "role": npc.role.value,
                "location": npc.current_state.location,
                "emotion": npc.current_state.emotion.value,
                "personality": {
                    "openness": npc.personality.openness,
                    "conscientiousness": npc.personality.conscientiousness,
                    "extraversion": npc.personality.extraversion,
                    "agreeableness": npc.personality.agreeableness,
                    "neuroticism": npc.personality.neuroticism,
                    "intelligence": npc.personality.intelligence,
                    "traits": npc.personality.traits,
                },
                "knowledge": [k.fact for k in npc.knowledge[-5:]],
                "observations": [o.observation for o in npc.observation[-3:]],
                "goals": [g.goal for g in npc.goal if not g.is_completed],
            }
            for npc in self.npcs.values()
        ]
        perception_input = {
            "events": result.event_result.events,
            "npcs": npcs_data,
            "world_state": world_state_summary,
            "player_input": player_input,
        }
        layer_result = self.perception_layer.process(
            perception_input, {"npcs": npcs_data}
        )
        if not layer_result.success:
            result.success = False
            result.error = f"PerceptionLayer: {layer_result.error}"
            return result
        result.perception_result = layer_result.data

        # ====== 3. UpdateLayer (感知写入) ======
        update_input = self.update_layer.parser.parse_perception_to_updates(
            result.perception_result
        )
        layer_result = self.update_layer.process(
            update_input, {"npcs": self.npcs}
        )
        if not layer_result.success:
            result.success = False
            result.error = f"UpdateLayer(perception): {layer_result.error}"
            return result
        result.update_perception_result = layer_result.data

        # ====== 4. ReasoningLayer ======
        layer_result = self.reasoning_layer.process(
            {},
            {
                "npcs": self.npcs,
                "perception_result": result.perception_result,
            },
        )
        if not layer_result.success:
            result.success = False
            result.error = f"ReasoningLayer: {layer_result.error}"
            return result
        result.reasoning_result = layer_result.data

        # ====== 5. UpdateLayer (推理写入) ======
        update_input = self.update_layer.parser.parse_reasoning_to_updates(
            result.reasoning_result
        )
        layer_result = self.update_layer.process(
            update_input, {"npcs": self.npcs}
        )
        if not layer_result.success:
            result.success = False
            result.error = f"UpdateLayer(reasoning): {layer_result.error}"
            return result
        result.update_reasoning_result = layer_result.data

        # ====== 6. ResponseLayer ======
        # 传入对话目标+该NPC的对话历史+玩家原话兜底
        addressed_npc_id = ""
        if self.event_layer.current_event:
            addressed_npc_id = self.event_layer.current_event.addressed_npc_id
        layer_result = self.response_layer.process(
            {},
            {
                "npcs": self.npcs,
                "events": result.event_result.events,
                "perception_result": result.perception_result,
                "addressed_npc_id": addressed_npc_id,
                "dialogue_history": self.dialogue_history,
                "player_input": player_input,
            },
        )
        if not layer_result.success:
            result.success = False
            result.error = f"ResponseLayer: {layer_result.error}"
            return result
        result.response_result = layer_result.data

        # 追加到对话历史
        for r in result.response_result.npc_responses:
            self.dialogue_history.append({
                "npc_id": r.npc_id,
                "npc_name": r.npc_name,
                "text": r.response_text,
                "tick": self.tick_count,
            })

        # ====== 7. DirectorLayer ======
        # 提取事件位置和NPC位置，防止LLM把不同位置的NPC写进同一场景
        event_location = ""
        if result.event_result and result.event_result.events:
            event_location = result.event_result.events[0].location
        npc_locations = {
            npc_id: npc.current_state.location
            for npc_id, npc in self.npcs.items()
        }

        # 场景上下文：传入上一轮此位置的场景描述（如果位置相同）
        previous_scene = ""
        if event_location and event_location == self.last_event_location:
            previous_scene = self.scene_memory.get(event_location, "")
        # 位置变了 → 清除旧位置的场景记忆不会自动传，previous_scene为空

        # 过滤: 只传当前场景中NPC的对话历史给导演层
        scene_npc_ids = set()
        if self.event_layer.current_event:
            scene_npc_ids = set(self.event_layer.current_event.involved_npc_ids)
        scene_history = [
            h for h in self.dialogue_history
            if h["npc_id"] in scene_npc_ids
        ]

        layer_result = self.director_layer.process(
            result.response_result,
            {
                "world_state_summary": world_state_summary,
                "event_location": event_location,
                "npc_locations": npc_locations,
                "previous_scene": previous_scene,
                "dialogue_history": scene_history,
            },
        )
        if not layer_result.success:
            result.success = False
            result.error = f"DirectorLayer: {layer_result.error}"
            return result
        result.director_result = layer_result.data

        # 更新场景记忆
        self.last_event_location = event_location
        if result.director_result.scene_update:
            self.scene_memory[event_location] = result.director_result.scene_update

        # ====== 8. PropagationLayer ======
        prop_input = {
            "director_result": {
                "narrative": result.director_result.narrative,
            },
            "events": [
                {
                    "event_id": e.event_id,
                    "scope": e.scope.value,
                    "description": e.description,
                    "location": e.location,
                }
                for e in result.event_result.events
            ],
            "npcs_summary": npcs_data,
        }
        layer_result = self.propagation_layer.process(prop_input, {})
        if not layer_result.success:
            result.success = False
            result.error = f"PropagationLayer: {layer_result.error}"
            return result
        result.propagation_result = layer_result.data

        # 写入debug日志
        self._write_debug_log(result)

        return result

    # ================================================================
    # Debug日志
    # ================================================================

    def _write_debug_log(self, result: TickResult) -> None:
        """将本轮处理详情写入日志文件，用于事后debug."""
        log_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "logs",
        )
        os.makedirs(log_dir, exist_ok=True)

        ce = self.event_layer.current_event
        event_ctx = None
        if ce:
            event_ctx = {
                "location": ce.location,
                "topic": ce.topic,
                "addressed_npc_id": ce.addressed_npc_id,
                "involved_npc_ids": ce.involved_npc_ids,
            }

        # EventLayer LLM原始输出
        evt_raw = ""
        evt_parsed = None
        if result.event_result and hasattr(result.event_result, "_llm_raw"):
            evt_raw = result.event_result._llm_raw
            evt_parsed = getattr(result.event_result, "_llm_parsed", None)

        log = {
            "timestamp": datetime.now().isoformat(),
            "tick": self.tick_count,
            "player_input": result.player_input,
            "success": result.success,
            "error": result.error if not result.success else "",
            "current_event_before": event_ctx,
            "event_layer_llm_output": evt_raw,
            "event_layer_llm_parsed": evt_parsed,
            "events": [],
            "perception": [],
            "responses": [],
            "narrative": result.get_narrative() if result.success else "",
        }

        # 事件
        if result.event_result:
            for e in result.event_result.events:
                log["events"].append({
                    "event_id": e.event_id,
                    "type": e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
                    "location": e.location,
                    "scope": e.scope.value if hasattr(e.scope, "value") else str(e.scope),
                    "description": e.description,
                    "involved_npc_ids": e.involved_npc_ids,
                })

        # 感知
        if result.perception_result:
            for p in result.perception_result.npc_perceptions:
                log["perception"].append({
                    "npc_id": p.npc_id,
                    "perceived_ids": p.perceived_event_ids,
                    "missed_ids": p.missed_event_ids,
                    "summary": p.perceived_summary,
                })

        # 回应
        if result.response_result:
            for r in result.response_result.npc_responses:
                log["responses"].append({
                    "npc_id": r.npc_id,
                    "npc_name": r.npc_name,
                    "text": r.response_text,
                    "type": r.response_type,
                    "emotion": r.emotion,
                })

        log_path = os.path.join(log_dir, f"tick_{self.tick_count:04d}.json")
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=2)

    def run_loop(self) -> None:
        """运行交互式主循环（简单CLI版本）."""
        print(f"=== {self.config.world.world_name} === ")
        print(f"世界: {self.world_definition.name if self.world_definition else '未初始化'}")
        print(f"NPC数量: {len(self.npcs)}")
        print("输入 'quit' 退出, 'status' 查看状态")
        print("-" * 40)

        while True:
            if (
                self.config.engine.max_ticks > 0
                and self.tick_count >= self.config.engine.max_ticks
            ):
                print("达到最大回合数，模拟结束。")
                break

            try:
                player_input = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n模拟结束。")
                break

            if not player_input:
                continue

            if player_input.lower() == "quit":
                print("模拟结束。")
                break

            if player_input.lower() == "status":
                self._print_status()
                continue

            result = self.tick(player_input)

            if result.success:
                narrative = result.get_narrative()
                if narrative:
                    print(f"\n{narrative}")
                else:
                    print("（世界沉默...）")
            else:
                print(f"\n[错误] {result.get_error_context()}")

    def _get_world_state_summary(self) -> dict:
        """获取世界状态摘要（最小上下文）.

        Returns:
            世界状态摘要dict.
        """
        if self.world_state is None:
            return {}

        return {
            "day": self.world_state.day,
            "time_of_day": self.world_state.time_of_day.value,
            "weather": self.world_state.weather.value,
            "player_location": self.world_state.player_location,
            "active_events": [
                e.description
                for e in self.world_state.get_active_events()
            ],
        }

    def _get_npcs_summary(self) -> list[dict]:
        """获取NPC摘要列表（最小上下文）.

        Returns:
            NPC摘要列表.
        """
        summaries = []
        for npc in self.npcs.values():
            summaries.append({
                "npc_id": npc.npc_id,
                "name": npc.name,
                "role": npc.role.value,
                "location": npc.current_state.location,
                "emotion": npc.current_state.emotion.value,
                "pending_count": len(npc.pending_reason),
                "goal_count": len([g for g in npc.goal if not g.is_completed]),
            })
        return summaries

    def _print_status(self) -> None:
        """打印当前世界状态."""
        if self.world_state is not None:
            print(f"第{self.world_state.day}天 | {self.world_state.time_of_day.value} | {self.world_state.weather.value}")
        print(f"回合数: {self.tick_count}")
        print(f"NPC: {len(self.npcs)}")
        for npc in self.npcs.values():
            pending = len(npc.pending_reason)
            obs = len(npc.observation)
            mem = len(npc.memory)
            print(
                f"  {npc.name} [{npc.role.value}] "
                f"在{npc.current_state.location} "
                f"情绪:{npc.current_state.emotion.value} "
                f"Pending:{pending} Obs:{obs} Mem:{mem}"
            )
