# NPC 全部表结构

"你是谁" 字段是 NPC 的完整内部状态。结构如下：

```json
{
    "名字Name": "NPC的完整名字",
    "知识记忆Memory": ["知道的明确事实，一条一元素"],
    "信念Belief": ["主观认知，需充分说服力才可改动"],
    "性格Personality": ["性格特质，几乎不可改"],
    "生活目标Goals": ["当前生活目标，可新增短期目标"],
    "好感度Relationship": ["与玩家关系记录，一行一条"],
    "当前状态CurrentState": {"当前所处位置": "完整路径", "实力等级": "...", "其他实时状态": "..."},
    "观察Observation": ["被动接收到的信息，追加一条"],
    "对玩家观察PlayerObservation": ["NPC对玩家的认知判断，去重"],
    "持有物Inventory": {"物品名": "数量或描述"},
    "待解释事件PendingReasoning": ["当前存疑的待解释事项"],
    "表更新成本UpdateCost": ["各类表更新的成本参考，不修改"]
}
```

各表语义与更新方向见「各表速查」。

- **List 型字段**：知识记忆Memory、信念Belief、性格Personality、生活目标Goals、好感度Relationship、观察Observation、对玩家观察PlayerObservation、待解释事件PendingReasoning
- **Dict 型字段**：当前状态CurrentState、持有物Inventory
- 未变化字段不输出
