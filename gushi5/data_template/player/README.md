<!--
  player/README.md
  玩家初始数据配置说明。

  玩家数据是一个JSON文件，包含名称、状态、持有物、描述。
  base_cnf.py 中的 PLAYER_DATA_FILE 指定文件路径。
-->

# 玩家数据配置说明

## 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 玩家名称name | string | 是 | 玩家在游戏中的名字 |
| 当前状态CurrentState | dict | 是 | 含位置、实力等级、当前身份 |
| 持有物Inventory | dict | 是 | 格式 `物品名：描述`，可为空 |
| 描述Description | [string] | 是 | 玩家背景描述，每条≤50字 |

## 注意事项

- 位置必须与 B1_worldState 地点树中的路径一致
- 实力等级必须与世界观修炼体系一致
- 持有物可以是空对象 `{}`
- 描述至少2条，让LLM有足够背景参考
- 玩家文件路径在 `B1_worldState/global.json` 的 `世界配置.玩家初始数据` 中指定
