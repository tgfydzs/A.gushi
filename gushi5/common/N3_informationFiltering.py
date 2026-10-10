import json

from Schema.B4_player_sch import B4PlayerLayer
from common.log import get_logger
from common.small_tool import load_prompt, safe_json_loads
from common.LLMclient import LLMClient
from Schema.Context_sch import ContextHistory
from Schema.B3_npclist_sch import B3NPCListLayer










class N3_Info_Filtering:
    def __init__(self, logger=None):
        self.logger = logger if logger else get_logger('N3_IF')
        self.cli = LLMClient('N3')



    def judge_for_npc(self,
                      event_context: ContextHistory,
                      event_npcs: B3NPCListLayer,
                      related_npcs: B3NPCListLayer,
                      player: B4PlayerLayer = None,
                      director_context: 'ContextHistory' = None
                      ):
        """
        感知层，用于判断哪个npc知道哪些事情
        :param event_context: 事件上下文
        :param event_npcs: 事件涉及npc
        :param related_npcs: 事件涉及/潜在npc
        :param player: 玩家实例
        :param director_context: N9导演上下文（幕后信息，仅供AI参考，不向玩家暴露）
        :return: {
                    "事件涉及的npc":{
                        "NPC名字":"玩家：NPC收到的信息"
                    },
                    "事件涉及/潜在npc":{
                        "NPC名字":"玩家：NPC收到的信息"
                    },
                    "地段传播":{
                        "msg":"玩家：过滤后的地段广播信息",
                        "level":3,
                        "mask":0.6
                    }
                }
        """
        sys_prompt = load_prompt('N3_informationFiltering_sys.txt')
        all_context = []
        all_context.append({'role': 'system', 'content': sys_prompt})
        # 构建每个NPC的简要档案，供LLM做位置/感知范围判断
        def _npc_snapshot(npc_inst) -> dict:
            """提取NPC最简字段，只传位置判断必需的数据"""
            if npc_inst is None:
                return {}
            state = npc_inst.current_state
            return {
                '名字': npc_inst.name,
                '位置': state.get('当前所处位置', '未知'),
                '实力': state.get('实力等级', '未知'),
            }

        player_loc = player.player_dict['当前状态CurrentState']['当前所处位置'] if player else ''
        player_state = player.player_dict.get('当前状态CurrentState', {})

        dc = director_context.context if director_context else []
        dc_trimmed = dc[-3:] if len(dc) > 3 else dc

        user_input = {
            '该事件对话上下文': event_context.context,
            '玩家': {
                '名称': player.player_dict.get('玩家名称name', '未知'),
                '位置': player_loc,
                '实力': player_state.get('实力等级', '未知'),
            },
            '事件涉及的npc': [_npc_snapshot(n) for n in event_npcs],
            '事件涉及/潜在npc': [_npc_snapshot(n) for n in related_npcs],
            '幕后情报（仅供AI参考，不向玩家展示）': dc_trimmed,
        }
        all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})
        self.logger.debug(f'感知层LLM_messages：{all_context}')

        replay = self.cli.chat_by_context(all_context, label='N3')
        json_replay = safe_json_loads(replay, label='N3')
        return json_replay





























