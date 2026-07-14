# Schema 系统设计计划

## 一、流程分析

基于 `流程图.mmd` 和现有 `gushi1` 代码，系统包含以下流程节点：

| 序号 | 节点 | 职责 | 调用LLM |
|------|------|------|---------|
| 1 | 玩家输入 | 用户自然语言输入，加入全局上下文 | ❌ |
| 2 | 事件层 | 判断事件延续/新建，维护跨tick事件上下文 | ✅ |
| 3 | 感知层 | LLM判断每个NPC对事件的感知情况，分发信息 | ✅ |
| 4 | 更新层(感知) | 将感知结果写入NPC的observation/pending_reason表 | ❌ |
| 5 | 推理层 | 有pending事件的NPC进行推理，产出新知识/信念 | ✅ |
| 6 | 更新层(推理) | 将推理结果写入knowledge/belief表，清除已处理pending | ❌ |
| 7 | 回应层 | NPC根据感知内容+维护表生成回应 | ✅ |
| 8 | 导演层 | 整合回应为连贯叙事，分NPC说话/旁白输出 | ✅ |
| 9 | 传播策略层 | 判断信息是否传播、谁能传播、传播给谁 | ❌ |
| 10 | 世界层 | 世界规则+世界状态，跨所有层的背景约束 | ❌ |

## 二、Schema 文件划分

共10个文件，按高内聚低耦合原则：

### 1. `world_schema.py` — 世界定义与状态
- WorldDefinition（世界定义）
- WorldRule（世界规则）
- HiddenEvent（隐藏事件）
- KnownEvent（已知事件）
- WorldState（世界状态）
- TimeOfDay（时段枚举）
- Weather（天气枚举）
- GlobalEvent（全局事件）
- EventPropagation（事件传播记录）
- WorldConfig（世界配置参数）

### 2. `npc_schema.py` — NPC核心标识与状态
- NPC（NPC主体，包含所有维护表的引用）
- NPCIdentity（NPC身份标识）
- NPCRole（角色类型枚举）
- PersonalityData（五大人格+认知）
- Emotion（情绪枚举）
- CurrentStateData（当前位置/情绪/动作/健康/存活）
- NPCSummary（NPC摘要—给LLM的最小上下文）

### 3. `event_schema.py` — 事件定义与上下文
- EventType（事件类型枚举）
- EventScope（公开程度枚举）
- Event（事件主体）
- EventDetail（事件细节）
- CurrentEvent（跨tick持久的事件上下文）
- EventTable（事件上下文表—多事件集合）

### 4. `perception_schema.py` — 感知系统
- PerceptionInput（感知层输入）
- NPCPerception（单个NPC感知结果）
- PerceptionResult（感知层输出）
- PerceptionJudgeRule（感知判断规则）

### 5. `memory_schema.py` — 记忆与知识
- MemoryEntry（记忆条目）
- KnowledgeEntry（知识条目）
- KnowledgeSource（知识来源枚举）
- ObservationEntry（观察条目）
- DetailLevel（细节程度枚举）

### 6. `belief_schema.py` — 信念与待解释事件
- BeliefEntry（信念条目）
- PendingEntry（待解释事件条目）
- ReasoningInput（推理层输入）
- NPCReasoning（单个NPC推理结果）
- ReasoningResult（推理层输出）

### 7. `relationship_schema.py` — 关系与好感度
- RelationshipType（关系类型枚举）
- RelationshipValue（对单个NPC的关系值）
- RelationshipGraph（全局关系图）
- FavorabilityChange（好感度变更记录）

### 8. `dialogue_schema.py` — 对话与回应
- NPCResponse（单个NPC回应）
- ResponseType（回应类型：speech/action/thought）
- ResponseResult（回应层输出）
- DialogueHistory（对话历史条目）
- DialogueContext（对话上下文—发给ResponseLayer）

### 9. `director_schema.py` — 导演与叙事
- DirectorStyle（导演样式/风格）
- NarrationType（旁白类型）
- SceneMemory（场景记忆）
- DirectorResult（导演层输出）
- NarrativeOutput（最终叙事—NPC说话/旁白）

### 10. `action_schema.py` — 行动与物品
- GoalEntry（目标条目）
- GoalPriority（优先级）
- Item（物品）
- ItemType（物品类型）
- Inventory（持有物集合）
- ActionType（动作类型枚举）
- NPCAction（NPC动作记录）
- UpdateRecord（NPC维护表更新记录）
- UpdateCost（表更新成本）

## 三、Schema 统一字段格式

每个Schema中的字段使用以下格式：

```python
字段名: type = default  # 中文名称 | AI可修改:bool | 程序可修改:bool | 说明
```

## 四、schema_mapping.md 结构

### 正向索引（按流程节点）

每个节点列出：
- 使用的Schema文件
- 读取的变量（不修改）
- 修改的变量
- 输出的变量（新产生）

### 反向索引（按变量）

每个变量列出：
- 定义于哪个Schema文件
- 被哪些节点读取
- 被哪些节点修改
- 被哪些节点输出/创建

## 五、设计原则

1. **单一数据源** — 同一变量只在一处定义，其他处通过引用使用
2. **AI/程序权限分离** — 明确标记每个字段是否允许AI修改、是否允许程序修改
3. **渐进扩展** — Schema设计考虑未来扩展（如多世界、联网NPC等）
4. **中文文档** — 说明/注释/文档使用中文；变量名/字段名/文件名使用英文
5. **不修改现有流程** — 如发现设计问题，在文档中提建议但不直接改动

## 六、发现的设计问题与建议

1. **table_update_cost（表更新成本）** — 流程图中提及form10但当前代码未实现。建议：在action_schema中添加UpdateCost定义。
2. **全局上下文(list1)** — 流程图提到但当前代码中未显式管理。建议：在dialogue_schema中明确定义GlobalContext。
3. **导演样式(list2)** — 流程图提到"导演样式"但当前代码未实现个性化样式。建议：在director_schema中预留DirectorStyle。
4. **传播策略层** — 当前PropagationLayer只做简单scope判断，流程图中"传播策略层→判断传播给谁"更复杂。建议：预留PropagationStrategy和PropagationRule。
5. **世界层权威性** — 流程图提到"世界状态优先级更高，传播层出现的变动会放在世界状态中，比如法律更新、规则更新"。当前代码中world_rules是静态的。建议：在world_schema中为规则添加可更新标记。
