"""
base3 npc层
职能：每一个npc为一个对象，对象内部维护多个表
表：知识记忆Memory，信念Belief， 性格Personality， 生活目标Goals， 好感度Relationship， 当前状态CurrentState， 观察Observation， 持有物Inventory， 待解释事件PendingReasoning， 表更新成本UpdateCost
"""


from enum import Enum, unique

from Schema.B1_worldState_sch import B1WorldLayer
from common import time_int, uuid, log

logger = log.SimpleLogger()



class B3NPC:



    def __init__(self, memory: list, belief: list, personality: list, goals: list, relationship: list, current_state: list, observation: list, inventory: list, pending_reasoning: list, update_cost: list[int],
                 npc_name: str):
        if len(update_cost) != 10:
            logger.warn(f'更新成本表长度错误，应该长度：10--------实际长度：{update_cost}')

        self.npc_info = {npc_name: {
            '知识记忆Memory': memory,
            '信念Belief': belief,
            '性格Personality': personality,
            '生活目标Goals': goals,
            '好感度Relationship': relationship,
            '当前状态CurrentState': current_state,
            '观察Observation': observation,
            '持有物Inventory': inventory,
            '待解释事件PendingReasoning': pending_reasoning,
            '表更新成本UpdateCost': update_cost
        }}





if __name__ == '__main__':
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
