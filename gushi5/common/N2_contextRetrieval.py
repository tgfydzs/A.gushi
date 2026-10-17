import json
import os

from Schema.N2_contextRetrieval_sch import N2ContextRetrievalLayer
from Schema.Context_sch import ContextHistory
from common.log import get_logger
from common.small_tool import load_prompt, safe_json_loads
from common.base_cnf import *
from common.LLMclient import LLMClient


class N2_CE_Process:

    def __init__(self, logger=None):
        self.cli = LLMClient('N2')
        self.logger = logger if logger else get_logger("N2_CE")



    def judge(self, context_event: ContextHistory,
              current_event_npcs: list = None,
              all_npc_names: list = None,
              director_context: ContextHistory = None,
              player_location: str = '',
              nearby_npcs: list = None,
              known_locations: list = None) -> dict:
        """
        接受ContextHistory对象，为当前事件上下文
        :param context_event: 接受ContextHistory对象，用于ai做新事件判断
        :param current_event_npcs: 当前事件中NPC名称列表
        :param all_npc_names: 全局所有NPC名称列表（用于防幻觉）
        :param director_context: N9导演上下文（幕后信息，仅供AI参考，不向玩家暴露）
        :param player_location: 玩家当前位置字符串
        :param nearby_npcs: 玩家附近区域的NPC列表 [{"名字":"...", "位置":"...", "距离":"..."}]
        :param known_locations: 已知地点路径列表（`move_to` 只能从中挑，防它自己编地名）
        :return:{
                    "is_new_event": str | bool,
                    "add_npc": list,
                    "del_npc": list,
                    "add_related_npc": list,
                    "del_related_npc": list,
                    "move_to": str,                    # 去向·地名，无移动为 ""
                    "行程": str,                        # 这一轮走到哪了（刚上路/半路/将到/到了），没动为 ""
                    "move_declared_minutes": int,      # 玩家自己明说的移动耗时（分钟），未声明为 0
                    "move_minutes": int,               # 玩家没明说时，你判的这段路耗时（分钟），判不出为 0
                    "时间判定": str,                    # 跳过 / 提及 / 无（"跳过"才推进时钟）
                    "时间跨度": dict                   # {"值":1,"单位":"月"}，单位 分钟/刻/时辰/日/周/月/年
                }
        """
        with open(os.path.join(BASE_PATH, r'prompt\N2_contextRetrieval_sys.txt'), 'r', encoding='utf-8') as f:
            sys_prompt = f.read()

        all_context = [{'role': 'system', 'content': sys_prompt}]
        # director_context 只保留最近3条摘要，防膨胀
        dc = director_context.context if director_context else []
        dc_trimmed = dc[-3:] if len(dc) > 3 else dc
        user_input = {
            '当前事件NPC': current_event_npcs or [],
            '世界观所有NPC（只能从中选取）': all_npc_names or [],
            '对话上下文': '\n'.join(context_event.context),
            '玩家当前位置': player_location,
            '玩家附近NPC': nearby_npcs or [],
            # 已知地点：`move_to` 必须从这里挑（裸模型爱编"天道宗上空"这种地名，
            # 编出来的地名解析不出坐标 → 位置静默不动，正是"移动没生效"的形状）
            '已知地点（move_to 只能从中选取）': list(known_locations or []),
            '幕后情报（仅供AI参考，不向玩家展示）': dc_trimmed,
        }
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.info(f'事件判断上下文：\n{all_context[1]}')

        replay = self.cli.chat_by_context(all_context, label='N2')
        json_replay = safe_json_loads(replay, label='N2')

        # 确保所有列表字段存在
        for key in ('add_npc', 'del_npc', 'add_related_npc', 'del_related_npc'):
            json_replay.setdefault(key, [])

        # 移动相关字段兜底（去向 + 行程交给 GameController 落坐标；耗时供 N8 用）
        json_replay.setdefault('move_to', '')
        json_replay.setdefault('行程', '')
        # 玩家自己明说的移动耗时（分钟）
        json_replay.setdefault('move_declared_minutes', 0)
        # 玩家没明说时，N2 读懂了输入后判的"这段路花了多久"（分钟）
        json_replay.setdefault('move_minutes', 0)

        # 时间跳过：只有判定为"跳过"才会推进时钟。
        # ⚠️ "提及"和"无"性质相同（都不动时钟），默认必须落在不动的那一侧——
        # 模型漏填时宁可时钟少走，也不能凭空跳一年。
        json_replay.setdefault('时间判定', '无')
        json_replay.setdefault('时间跨度', {'值': 0, '单位': '分钟'})

        return json_replay




























