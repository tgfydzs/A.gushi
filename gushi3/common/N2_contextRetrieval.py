import os

from Schema.N2_contextRetrieval_sch import N2ContextRetrievalLayer
from Schema.Context_sch import ContextHistory
from common.log import get_logger
from common.small_tool import *
from common.base_cnf import *
from common.LLMclient import LLMClient


class N2_CE_Process:

    def __init__(self, logger=None):
        self.cli = LLMClient()
        self.logger = logger if logger else get_logger("N2_CE")



    def judge(self, context_event: ContextHistory,
              current_event_npcs: list = None,
              all_npc_names: list = None,
              director_context: ContextHistory = None,
              player_location: str = '',
              nearby_npcs: list = None) -> dict:
        """
        接受ContextHistory对象，为当前事件上下文
        :param context_event: 接受ContextHistory对象，用于ai做新事件判断
        :param current_event_npcs: 当前事件中NPC名称列表
        :param all_npc_names: 全局所有NPC名称列表（用于防幻觉）
        :param director_context: N9导演上下文（幕后信息，仅供AI参考，不向玩家暴露）
        :param player_location: 玩家当前位置字符串
        :param nearby_npcs: 玩家附近区域的NPC列表 [{"名字":"...", "位置":"...", "距离":"..."}]
        :return:{
                    "is_new_event": str | bool,
                    "add_npc": list,
                    "del_npc": list,
                    "add_related_npc": list,
                    "del_related_npc": list
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
            '幕后情报（仅供AI参考，不向玩家展示）': dc_trimmed,
        }
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.info(f'事件判断上下文：\n{all_context[1]}')

        replay = self.cli.chat_by_context(all_context, label='N2')
        json_replay = safe_json_loads(replay, label='N2')

        # 代码层防幻觉：子串匹配过滤不存在的NPC名
        for key in ('add_npc', 'del_npc', 'add_related_npc', 'del_related_npc'):
            raw_list = json_replay.get(key, []) or []
            filtered = []
            for n in raw_list:
                resolved = resolve_npc_name(n, all_npc_names or [])
                if resolved:
                    filtered.append(resolved)
                else:
                    self.logger.warn(f'N2返回了不存在的NPC名，已过滤：{n}')
            json_replay[key] = filtered

        return json_replay




























