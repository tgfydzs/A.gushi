"""
node2 召集层
职能：召集所需的npc和上下文
"""


from enum import Enum, unique
from common import time_int, uuid, log
from B1_worldState_sch import B1WorldLayer
from B3_npc_sch import B3NPC

logger = log.SimpleLogger()



class N2ContextRetrieval():


    def __init__(self, world_state: B1WorldLayer, npc_list: list[B3NPC], event_context: list):
        self.context_retrieval_id = uuid.generate_session_id() # 事件id
        self.context_retrieval_timestamp = time_int.get_minute_ts() # 事件时间戳
        self.WorldState = world_state # 世界层
        self.npc_list = npc_list # npc列表
        self.event_context = event_context # 当前事件上下文


    def to_dict(self):
        return {
            '事件id': self.context_retrieval_id,
            '事件时间戳': time_int.ts_to_readable(self.context_retrieval_timestamp),
            '世界层': self.WorldState.to_dict(),
            'NPC列表': [npc.to_dict() for npc in self.npc_list],
            '当前事件上下文': self.event_context
        }







if __name__ == "__main__":
    dict1 = {
        'rule': ['杀人犯法', '有宵禁'],
        'state': ['南部有暴风雪', '将于公元102年3月颁布新的法令'],
        'world_knowledge': ['公元102年3月颁布的法令：农业减税... ...']
    }
    worldState = B1WorldLayer(dict1)


    memory = [
        "玩家今早主动上前搭话，询问镇上杂货铺的位置",
        "三天前王五曾帮玩家指路去往后山采药的小路",
        "上次玩家送给王五一枚野果，王五收下并道谢",
        "王五亲眼见过玩家和村口守卫发生过简短争执"
    ]
    belief = [
        "玩家是外来的旅人，对这片小镇环境并不熟悉",
        "玩家心地不坏，但性格有些急躁，容易与人起小摩擦",
        "后山区域有野兽出没，贸然进山会有危险",
        "镇上杂货铺老板为人抠门，买东西最好提前议价"
    ]
    personality = [
        "待人温和热心，愿意主动给外来路人提供帮助",
        "心思细腻，观察力强，会默默记住别人的言行举动",
        "做事谨慎保守，不会轻易怂恿他人去危险地带",
        "不爱多嘴议论他人是非，说话分寸感较强"
    ]
    goals = [
        "安稳守着自家小院落，平淡度日",
        "偶尔收集草药晾干售卖，补贴日常开销",
        "提醒往来旅人规避小镇周边的安全隐患",
        "和邻里维持和睦的相处关系"
    ]
    relationship = [
        "与玩家：初识友好，抱有善意，属于可信任的路人关系",
        "与杂货铺老板：普通邻里，来往不多，清楚对方经商风格",
        "与村口守卫：点头之交，日常碰面简单寒暄",
        "与隔壁老婆婆：邻里亲友，时常互相照看帮扶"
    ]
    current_state = [
        "正坐在自家院门口择刚采摘的草药",
        "天气晴朗，身心状态平稳，没有烦心事",
        "手边放着竹篮，里面装着晒干的艾草与薄荷"
    ]
    observation = [
        "玩家此刻脚步急促，看起来比较着急",
        "玩家身上背着布包，包里能看见露出的工具柄",
        "周围没有其他行人，环境安静无风"
    ]
    inventory = [
        "干艾草 一小捆",
        "薄荷干 半竹篮",
        "粗布手帕 1条",
        "铜币 12枚",
        "陶制水杯 1个"
    ]
    pending_reasoning = [
        "玩家着急赶路大概率是要寻找某个地点或者物品",
        "需要委婉提醒玩家后山危险，避免对方贸然前往",
        "可以告知杂货铺大致方位，顺带提一句老板议价的习惯"
    ]
    update_cost = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    npc_name = '王五'

    npc = B3NPC(memory, belief, personality, goals, relationship, current_state, observation, inventory, pending_reasoning, update_cost, npc_name)

    contextRetrieval = N2ContextRetrieval(world_state=worldState, npc_list=[npc], event_context=['123', 'aasg'])



    print(contextRetrieval.to_dict())





