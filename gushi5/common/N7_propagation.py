"""
N7 传播层（纯规则计算，不调用LLM，零API耗时）

将N3的地段传播数据在地点树中逐级扩散，
以"传闻"形式写入受影响地点NPC的Observation表。

流水线位置：N4-N6并行处理完成之后。
"""

import random

from common.log import get_logger
from Schema.B1_worldState_sch import B1WorldLayer
from Schema.B3_npclist_sch import B3NPCListLayer


class N7_Propagation:
    """
    N7 传播层

    输入：N3的"地段传播"数据（msg, level, mask）
    处理：在地点树中计算受影响的区域 → 区域内NPC按概率获知传闻
    输出：NPC Observation增量

    核心算法：
      1. 解析玩家位置为路径段列表
      2. level=N → 仅当前节点；level=N-1 → 向上扩散1级；level=1 → 全国
      3. 用 B1WorldLayer.find_npcs_at_location 查找受影响区域的NPC
      4. 排除已亲身经历事件的NPC（他们在N3已收到感知输入）
      5. 按 mask 概率随机判定每个NPC是否听到传闻
      6. 直接写入NPC的Observation表（去重）
    """

    def __init__(self, logger=None, seed=None):
        self.logger = logger if logger else get_logger('N7_Propagation')
        self.rng = random.Random(seed) if seed is not None else random.Random()

    def propagate(self,
                  ground_spread: dict,
                  world_layer: B1WorldLayer,
                  all_npc_list: B3NPCListLayer,
                  event_npc_names: set | list | None,
                  player_location: str) -> dict:
        """
        传播地段消息到受影响地点的NPC。

        :param ground_spread: N3输出的"地段传播"
            格式：{"msg": str, "level": int, "mask": float}
            msg为空或level=0表示本轮无传播
        :param world_layer: B1WorldLayer 世界地点树
        :param all_npc_list: 全部NPC列表
        :param event_npc_names: 本轮已参与事件的NPC（直接+潜在），不重复传播
        :param player_location: 事件发生位置（玩家当前位置）
        :return: {npc_name: [新增传闻列表]}
        """
        if not ground_spread:
            return {}

        msg = ground_spread.get('msg', '')
        level = ground_spread.get('level', 0)
        mask = ground_spread.get('mask', 0)

        # ---- 无传播内容 ----
        if not msg or level <= 0:
            return {}

        # ---- 1. 解析位置路径 ----
        path = world_layer.parse_path(player_location)
        if not path:
            self.logger.warn(f'N7 无法解析位置：{player_location}')
            return {}

        total_levels = len(path)

        # clamp: level 必须在 [1, total_levels]
        level = min(max(level, 1), total_levels)

        # affected_path = 前 level 段
        # level=total → 当前节点（全部段）
        # level=1 → 最广（仅国家名）
        affected_path = path[:level]

        # ---- 2. 查找受影响区域的NPC ----
        affected_npcs = world_layer.find_npcs_at_location(affected_path, all_npc_list)

        # ---- 3. 排除亲身经历事件的NPC ----
        exclude = set(event_npc_names) if event_npc_names else set()

        # ---- 4. 生成传闻文本 ----
        rumor = f'（传闻）{msg}'

        # ---- 5. 按概率判定 + 写入Observation ----
        results = {}
        affected_count = 0
        heard_count = 0

        for npc in affected_npcs.npc_list:
            if npc.name in exclude:
                continue
            affected_count += 1

            # 按mask概率判定
            if self.rng.random() >= mask:
                continue

            heard_count += 1

            # 写入Observation（去重）
            if rumor not in npc.observation:
                npc.observation.append(rumor)

            if npc.name not in results:
                results[npc.name] = []
            results[npc.name].append(rumor)

        # ---- 6. 截断 ----
        for npc in affected_npcs.npc_list:
            if npc.name not in exclude:
                npc.cap_fields()

        # ---- 7. 日志 ----
        affected_loc = world_layer.format_path(affected_path)
        self.logger.info(
            f'传播完成 | msg="{msg[:40]}{"..." if len(msg)>40 else ""}" '
            f'level={level}/{total_levels} area={affected_loc} '
            f'affects={affected_count} heard={heard_count} '
            f'npcs={list(results.keys())}'
        )

        return results
