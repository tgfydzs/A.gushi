<!--
  B3_npc/README.md
  NPC数据配置说明。

  按势力分组，每个文件夹对应一个势力/组织。
  每个文件夹内放一个或多个 .json 文件，每个文件是一个NPC数组。
  运行时 walk_category_files() 会递归读取所有json文件。
-->

# NPC数据 (B3_npc) 配置说明

## 目录结构

```
B3_npc/
├── README.md
├── 势力A/          ← 文件夹名为势力名
│   └── 势力A.json  ← 文件名不限，内容是 [] 包裹的NPC数组
├── 势力B/
│   └── 势力B.json
└── ...
```

## NPC字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 名字Name | string | 是 | 格式：`称号（身份说明）、真名（标签）`，括号前用于匹配 |
| 知识记忆Memory | [string] | 是 | NPC知道的信息，每条≤50字 |
| 信念Belief | [string] | 是 | 核心价值观，3~5条 |
| 性格Personality | dict | 是 | `{"特质名": 数值}`，数值1~10。旧写法 `["特质名：数值"]` 仍能载入（就地转换） |
| 生活目标Goals | [string] | 是 | NPC的短期和长期目标，3~5条 |
| 好感度Relationship | dict | 是 | `{"对象名": 数值}`，-10~10。旧写法 `["对象名：数值"]` 仍能载入 |
| 当前状态CurrentState | dict | 是 | 含位置、实力等级、身份、心情、体力状态 |
| 观察Observation | [string] | 否 | 初始观察到的信息，运行时动态增长 |
| 对玩家观察PlayerObservation | [string] | 否 | 若NPC认识玩家，填写初始印象 |
| 持有物Inventory | dict | 否 | 格式 `物品名：描述` |
| 待解释事件PendingReasoning | [string] | 否 | 初始疑虑，运行时动态变化 |
| 表更新成本UpdateCost | [number] | 是 | 11个数字，**按 `COST_TABLE_ORDER` 顺序**对应各表的更新代价（正数=越难改） |

## 名字命名规范

- 格式：`常用名（身份说明）、本名（标签）`
- 括号前的内容用于 NPC 名字匹配（`_strip_brackets`）
- 示例：`玄清（天道宗宗主，道号）、苏玄清（本名）`
- LLM输出任意名字时，代码会用括号前内容做 `in` 匹配

## 实力等级

必须与 B1_worldState 中定义的修炼体系一致。

## 位置

必须与 B1_worldState 中世界地点列表中的路径一致。
