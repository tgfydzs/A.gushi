# Schema 变量映射索引

Phase 2 核心产物。建立流程节点与 Entity 之间的完整读写映射。

---

## 一、流程节点回顾（来自Phase 1分析）

流程图共19个节点。以下编号与流程图一一对应。

| 编号 | 节点名 | 类型 | 核心职责 | LLM? |
|------|--------|------|----------|------|
| N1 | a1 玩家输入 | 入口 | 接收用户自然语言，写入全局上下文 | 否 |
| N2 | list1 全局上下文 | 存储 | 累积玩家输入和NPC回应 | 否 |
| N3 | world1 世界规则 | 存储 | 不变的世界法律/物理/社会规则 | 否 |
| N4 | world2 世界状态 | 存储 | 可变全局状态（时间/天气/全局事件） | 否 |
| N5 | call1 召集事物 | 聚合 | 汇总NPC列表+环境规则+事件上下文+世界层 | 否 |
| N6 | a2 事件层 | 处理 | 接收玩家输入+上下文 | 否 |
| N7 | a3 AI判断 | 决策 | 判断事件延续还是新建 | 是 |
| N8 | event_old 老事件 | 数据 | 延续已有事件上下文 | 否 |
| N9 | event1 新事件 | 数据 | 创建全新事件上下文 | 否 |
| N10 | a4 汇总 | 聚合 | 无论新旧事件，统一汇总后传递 | 否 |
| N11 | ai1 AI感知判断 | 处理 | 判断每个NPC对事件感知到了什么 | 是 |
| N12 | a5 更新层 | 写入 | 唯一写入口——将感知结果写入NPC维护表 | 否 |
| N13 | form1~10 维护表 | 存储 | NPC全部持久数据 | 否 |
| N14 | p1 推理检查 | 决策 | 检查form9是否有需要推理的pending | 否 |
| N15 | a6 推理层 | 处理 | NPC对pending事件进行推理 | 是 |
| N16 | a7 回应层 | 处理 | NPC根据维护表+感知生成回应 | 是 |
| N17 | a8 导演层 | 处理 | 整合回应为叙事，分NPC说话/旁白 | 是 |
| N18 | a9-leve1-p3 传播层 | 处理 | 判断信息是否传播、谁能传播、传播给谁 | 否 |
| N19 | a10 轮结束 | 终点 | 本轮流程终止 | 否 |

---

## 二、正向索引：每个节点读/写/产出的Entity

下面为每个流程节点列出精确的数据边界。

图例：
- R = 只读（Read）
- W = 修改（Write/Modify）
- O = 产出（Output，新建或产生后传递给下游）
- RW = 读且修改

---
### N1 — 玩家输入 (a1)

**职责**：接收用户自然语言输入，加入全局上下文。

这个节点不做任何处理，只是接收和存储。

| 角色 | Entity | Schema文件 |
|------|--------|-----------|
| 输入 | player_input (原始文本) | — |
| W | GlobalContext.player_inputs | dialogue_schema |

不产出任何Entity（原始文本直接传递给N5）。

---

### N2 — 全局上下文 (list1)

**职责**：跨tick累积的全局叙事信息。

| 角色 | Entity | 字段 |
|------|--------|------|
| 实体 | GlobalContext | dialogue_schema |
| 创建者 | N1(写入player_inputs) + N16(追加NPC回应) | — |
| 消费者 | N5(事件层获取上下文) | — |

---

### N3 + N4 — 世界层 (world1 + world2)

**职责**：世界规则（不变）+ 世界状态（可变）。

| 实体 | Entity | Schema文件 |
|------|--------|-----------|
| 世界规则 | WorldDefinition | world_schema |
| 世界状态 | WorldState | world_schema |

N3 (world1) — 世界规则：

| 角色 | Entity | 字段 |
|------|--------|------|
| 实体 | WorldDefinition.rules[] | WorldRule |
| 实体 | WorldDefinition.hidden_events[] | HiddenEvent |
| 实体 | WorldDefinition.known_events[] | KnownEvent |
| 初始化来源 | 世界定义JSON | — |
| 消费者 | N5(事件层)、N17(导演层)、N18(传播层) | — |

N4 (world2) — 世界状态：

| 角色 | Entity | 字段 |
|------|--------|------|
| 实体 | WorldState.day | day |
| 实体 | WorldState.time_of_day | time_of_day |
| 实体 | WorldState.weather | weather |
| 实体 | WorldState.player_location | player_location |
| 实体 | WorldState.global_events[] | GlobalEvent |
| 实体 | WorldState.event_propagations[] | EventPropagation |
| W | 时间推进（定时更新） | 程序自动 |
| W | N18写入（传播层变动） | 传播层 |

---

### N5 — 召集事物 (call1)

**职责**：汇总事件层需要的所有上下文信息。

**这不是一个数据实体**——它是一个信息聚合点。

从以下来源收集数据：
- 所需NPC的基本信息（N3/N4提供位置）
- 当前环境规则（WorldRule）
- 当前事件上下文（CurrentEvent / GlobalContext）
- 世界层信息（WorldState）

不修改任何Entity。不产出新的Entity。

---

### N6 + N7 + N8 + N9 — 事件层 (a2 + a3 + event_old + event1)

**职责**：接收玩家输入和上下文，由AI（N7）判断是延续老事件还是新建事件。

**调用LLM。** AI需要决策：
1. 事件是延续还是新建
2. 如果延续：更新话题/参与者/对话对象
3. 如果新建：创建新的事件上下文

| 角色 | Entity | 字段 | 来源/去向 |
|------|--------|------|-----------|
| R | WorldState (摘要) | day/time_of_day/weather/player_location | N4 |
| R | WorldDefinition.rules | 适用规则 | N3 |
| R | NPCIdentity + CurrentStateData | NPC的位置/名称 | N5 |
| R | CurrentEvent (上一轮) | 全部字段 | 跨tick持久 |
| R | GlobalContext.player_inputs | 历史输入 | N2 |
| W | CurrentEvent.location | AI判断更新 | 跨tick持久 |
| W | CurrentEvent.topic | AI判断更新 | 跨tick持久 |
| W | CurrentEvent.addressed_npc_id | AI判断更新 | 跨tick持久 |
| W | CurrentEvent.involved_npc_ids | AI判断更新 | 跨tick持久 |
| W | CurrentEvent.event_type | AI判断更新 | 跨tick持久 |
| W | CurrentEvent.scope | AI判断更新 | 跨tick持久 |
| O | EventTable.events[] | Event(从CurrentEvent转换) | 传给N10 |

---
### N10 + N11 — 感知层 (a4 + ai1)

**职责**：汇总事件 → AI判断每个NPC感知到什么 → 分发信息。

N10 (a4) 是纯聚合节点，N11 (ai1) 调用LLM。

**这是系统的信息闸门**。后续所有层只能使用感知层分发的信息，绝不能看到完整的事件表。

| 角色 | Entity | 字段 | 说明 |
|------|--------|------|------|
| R | EventTable.events[] | Event全部字段 | 来自N6-N9 |
| R | NPC.identity | npc_id/name/role | NPC是谁 |
| R | NPC.current_state | location/emotion | NPC在哪、什么情绪 |
| R | NPC.personality | intelligence/traits | 认知能力影响感知质量 |
| R | NPC.knowledge (最近N条) | KnowledgeEntry.fact | 已有知识影响感知角度 |
| R | NPC.observation (最近N条) | ObservationEntry.observation | 上次观察影响感知连续性 |
| R | NPC.goal (未完成) | GoalEntry.goal | 目标影响感知关注点 |
| O | NPCPerception.npc_id | npc_id | 哪个NPC的感知 |
| O | NPCPerception.perceived_event_ids | 事件ID列表 | 感知到了哪些事件 |
| O | NPCPerception.perceived_summary | 自然语言 | LLM生成的感知摘要 |
| O | NPCPerception.missed_event_ids | 事件ID列表 | 未感知到的事件 |
| O | PerceptionResult | 聚合所有NPCPerception | 传给N12 |

**感知硬约束（程序保证，LLM不可违反）**：
1. scope=HIDDEN 的事件绝不会出现在任何NPC的Perception中
2. NPC在event.involved_npc_ids中的事件必须被感知（N11的代码级保护）
3. NPC不在事件发生地 → 不可能直接感知

---

### N12 — 更新层(感知写入) (a5)

**职责**：将感知结果写入NPC维护表。**唯一写入口**。

**不调用LLM**。纯程序逻辑。

| 角色 | Entity | 字段 | 说明 |
|------|--------|------|------|
| R | NPCPerception (来自N11) | 全部字段 | 知道谁感知到了什么 |
| W | NPC.observation | ObservationEntry | 追加新观察条目 |
| W | ObservationEntry.observation | 文本 | perceived_summary的内容 |
| W | ObservationEntry.detail_level | 1-5 | 取决于NPC认知能力 |
| W | NPC.pending_reason | PendingEntry | 每次感知都可能产生新的待解释事件 |
| W | PendingEntry.event_description | 文本 | perceived_summary的内容 |
| W | PendingEntry.priority | 1-10 | 默认5，后续可被AI调整 |
| O | NPCUpdateRecord | 更新日志 | 记录本轮修改了什么 |

---
### N13 — NPC维护表 (form1~10)

**职责**：NPC的全部持久数据。这是Entity的聚合，而非处理节点。

| 表编号 | Entity | Schema文件 | 说明 |
|--------|--------|-----------|------|
| form1 | MemoryEntry + KnowledgeEntry | memory_schema | 记忆/知识表 |
| form2 | BeliefEntry | belief_schema | 信念表 |
| form3 | PersonalityData | npc_schema | 性格表 |
| form4 | GoalEntry | action_schema | 生活目标表 |
| form5 | RelationshipValue | relationship_schema | 好感度表 |
| form6 | CurrentStateData | npc_schema | 当前状态表 |
| form7 | ObservationEntry | memory_schema | 观察表 |
| form8 | Item | action_schema | 持有物表 |
| form9 | PendingEntry | belief_schema | 待解释事件表 |
| form10 | UpdateCost | action_schema | 表更新成本 |

---

### N14 + N15 — 推理层 (p1 + a6)

**职责**：检查form9(Pending)——如果有待解释事件 → NPC进行推理。

N14 (p1) 是纯程序判断：NPC.has_pending()。
N15 (a6) 调用LLM进行推理。

| 角色 | Entity | 字段 | 说明 |
|------|--------|------|------|
| R | NPC.pending_reason | PendingEntry[] | 待解释事件列表 |
| R | NPC.observation (最近N条) | ObservationEntry | 观察用于推理线索 |
| R | NPC.memory (最近N条) | MemoryEntry | 记忆用于关联 |
| R | NPC.knowledge (最近N条) | KnowledgeEntry | 已有知识用于推理 |
| R | NPC.goal (未完成) | GoalEntry | 目标影响推理方向 |
| R | NPC.personality | intelligence | 认知能力限制推理深度 |
| R | NPC.belief (最近N条) | BeliefEntry | 已有信念影响推理角度 |
| O | NPCReasoning.npc_id | — | 哪个NPC的推理 |
| O | NPCReasoning.new_knowledge | 字符串列表 | 推理出的新知识 |
| O | NPCReasoning.new_beliefs | 字符串列表 | 推理形成的新信念 |
| O | NPCReasoning.resolved_pending | 字符串列表 | 哪些pending已被解释 |
| O | NPCReasoning.goals_updated | 字符串列表 | 推理导致的目标更新 |

---

### 推理层写入 (N12第二次调用)

经过推理层后，更新层再次执行：

| 角色 | Entity | 字段 | 说明 |
|------|--------|------|------|
| R | NPCReasoning | 全部字段 | 来自N15 |
| W | NPC.knowledge | KnowledgeEntry | 追加推理产出的新知识 |
| W | KnowledgeEntry.fact | 文本 | 推理出的知识 |
| W | KnowledgeEntry.source | reasoning | 来源标记 |
| W | KnowledgeEntry.confidence | 0.7 (默认) | 推理出的知识可信度 |
| W | NPC.belief | BeliefEntry | 追加推理产出的新信念 |
| W | BeliefEntry.belief | 文本 | 信念内容 |
| W | BeliefEntry.strength | 0.6 (默认) | 推理形成，初始不深 |
| W | NPC.pending_reason | PendingEntry[] | 清除已解释的pending |
| W | NPC.goal (可选) | GoalEntry | 更新或新增目标 |
| O | NPCUpdateRecord | 更新日志 | 记录本轮修改 |

---
### N16 — 回应层 (a7)

**职责**：NPC根据自己感知到的内容+维护表，决定是否回应及如何回应。

**调用LLM。** 每个NPC只能拿到自己的perceived_summary，绝不能看到完整事件表。

**输出双写**：回应同时写入 GlobalContext(list1) 和 NPC知识(form1)。

| 角色 | Entity | 字段 | 说明 |
|------|--------|------|------|
| R | NPCPerception.perceived_summary | 文本 | NPC本轮感知到的内容（来自N11） |
| R | NPC.current_state | location/emotion/action | 当前位置/情绪/动作 |
| R | NPC.personality | intelligence/traits | 认知能力+性格特质 |
| R | NPC.knowledge (最近N条) | KnowledgeEntry | 已有知识 |
| R | NPC.belief (最近N条) | BeliefEntry | 已有信念 |
| R | NPC.observation (最近N条) | ObservationEntry | 最近观察 |
| R | NPC.memory (最近N条) | MemoryEntry | 最近记忆 |
| R | NPC.pending_reason (最近N条) | PendingEntry | 待解释事件 |
| R | NPC.goal (未完成) | GoalEntry | 当前目标 |
| R | NPC.relationship | RelationshipValue | 对其他人物的关系 |
| R | DialogueHistory (该NPC的) | DialogueHistoryEntry | 防重复+保持对话连续性 |
| R | CurrentEvent.addressed_npc_id | — | 判断玩家是否直接对该NPC说话 |
| O | NPCResponse.npc_id | — | 哪个NPC的回应 |
| O | NPCResponse.npc_name | — | NPC名称 |
| O | NPCResponse.response_text | 文本 | 回应内容 |
| O | NPCResponse.response_type | speech/action/thought | 回应形式 |
| O | NPCResponse.target_id | player/NPC ID | 对谁说/对谁做 |
| O | NPCResponse.emotion | 文本 | 回应时的情绪 |
| W | GlobalContext | (追加) | 回应内容写入全局上下文 |
| W | NPC.knowledge (可选) | KnowledgeEntry | 回应中产生的新知识 |
| W | FavorabilityChange (可选) | — | AI建议的好感度变化→传给N12执行 |

---
### N17 — 导演层 (a8)

**职责**：整合所有NPC回应为连贯叙事。分NPC说话(a11)和旁白(a12)输出。

**调用LLM。** 核心约束：禁止新增世界事实，只做润色和衔接。

| 角色 | Entity | 字段 | 说明 |
|------|--------|------|------|
| R | NPCResponse (来自N16) | 全部字段 | 所有NPC的回应 |
| R | WorldState (摘要) | day/time_of_day/weather | 世界时间天气 |
| R | WorldDefinition.rules | WorldRule | 世界规则约束叙事 |
| R | CurrentEvent (来自N7) | location/topic | 当前事件上下文 |
| R | DirectorStyleConfig | style/format等 | 导演样式(list2) |
| R | SceneMemory (上轮同位置) | scene_description | 场景衔接 |
| R | DialogueHistory (场景NPC) | DialogueHistoryEntry | 防止重复叙事 |
| R | NPC.current_state (场景NPC) | location | 确认位置一致性 |
| O | DirectorResult.narrative | 文本 | 完整叙事文本 |
| O | DirectorResult.scene_update | 文本 | 场景更新描述 |
| W | SceneMemory.scene_description | 文本 | 更新场景记忆 |
| O | NarrativeOutput.npc_speeches | 列表 | NPC说话部分→a11 |
| O | NarrativeOutput.narrations | 列表 | 旁白部分→a12 |

---

### N18 — 传播策略层 (a9 + leve1 + p3)

**职责**：判断信息是否传播、谁能传播、传播给谁。

**不调用LLM**。纯程序逻辑。

流程：
1. a9: 信息传播层入口，接收导演层输出
2. leve1: 传播策略层——检查call1信息+NPC维护表，判断谁有权传播
3. p3: 判断传播给谁——按事件scope+内容敏感性决定目标

| 角色 | Entity | 字段 | 说明 |
|------|--------|------|------|
| R | Event.scope | public/area/group/private/hidden | 公开程度决定传播范围 |
| R | Event.location | 文本 | 事件位置决定传播区域 |
| R | Event.description | 文本 | 事件内容直接影响传播 |
| R | DirectorResult.narrative | 文本 | 导演层输出的叙事 |
| R | N5(call1) 上下文 | — | 当前事件信息 |
| R | NPC维护表 | — | NPC各自的知识/权限 |
| R | WorldState | — | 已有的全局事件 |
| W | NPC.knowledge (目标NPC) | KnowledgeEntry | 传播结果写入NPC知识 |
| W | KnowledgeEntry.source | propagation | 来源标记 |
| W | KnowledgeEntry.confidence | 视hop数递减 | 传播跳数越多，可信度越低 |
| W | WorldState.global_events | GlobalEvent | 重大事件写为全局事件 |
| W | WorldState.event_propagations | EventPropagation | 记录传播日志 |
| O | PropagationEntry | — | 单条传播记录 |

**传播策略的核心决策逻辑（程序实现）**：

- scope=PRIVATE 或 HIDDEN → 不传播
- scope=PUBLIC → 告知所有同区域NPC
- scope=AREA → 告知同location的NPC
- scope=GROUP → 告知同faction的NPC
- 内容为密谋/秘密的信息 → 排除目标对象（如密谋对象不能被传播到）
- 每次传播信息失真度递增(0.15/hop)，超过3 hop不再继续

**流程图明确的问题**：p3有两条输出边：
- p3→form1：给NPC知识（单个NPC知道）
- p3→world2：给世界状态（变成全局事件）

这对应两套不同的传播逻辑：
- 口口相传 → form1
- 官方公告/重大事件 → world2

---

### N19 — 该轮对话结束 (a10)

**职责**：标记本轮流程结束。

不操作任何Entity。引擎在此节点后执行全局清扫：
- 时间推进
- 记忆衰减
- 更新成本重置
- 日志写入

---
---

## 三、反向索引：每个Entity被哪些节点使用

以下为每个Entity列出精确的节点访问权限。

### world_schema 下的 Entity

| Entity | 读 (R) | 修改 (W) | 产出 (O) |
|--------|-------|---------|---------|
| WorldDefinition | N5,N6,N7,N17 | — | — (初始化时创建) |
| WorldDefinition.rules[] | N6,N7,N17 | N18(规则更新) | — |
| WorldRule | N6,N7,N17 | N18 | — |
| HiddenEvent | N6,N7 | N6(触发标记) | — |
| HiddenEvent.is_triggered | N6,N7 | N6 | — |
| KnownEvent | N6,N7 | — | — |
| WorldState | N5,N6,N7,N11,N17,N18 | N4(定时更新),N17(player位置),N18(传播写入) | — |
| WorldState.day | N5,N6,N7,N11,N17 | N4 | — |
| WorldState.time_of_day | N5,N6,N7,N11,N17 | N4 | — |
| WorldState.weather | N5,N6,N7,N11,N17 | N4 | — |
| WorldState.player_location | N6,N7 | N17 | — |
| WorldState.global_events[] | N11,N17,N18 | N18 | — |
| WorldState.event_propagations[] | — | N18 | — |
| GlobalEvent | N11,N17,N18 | N18 | — |
| EventPropagation | — | N18 | N18 |
| WorldConfig | N4(初始化) | — | — |

### npc_schema 下的 Entity

| Entity | 读 (R) | 修改 (W) | 产出 (O) |
|--------|-------|---------|---------|
| NPC | N6,N7,N11,N12,N14,N15,N16,N17,N18 | N12,N16 | — (初始化时创建) |
| NPCIdentity | N6,N7,N11,N16 | — | — |
| NPCIdentity.npc_id | N6,N7,N11,N12,N14,N15,N16,N17,N18 | — | — |
| NPCIdentity.name | N6,N7,N11,N16,N17 | — | — |
| NPCIdentity.role | N6,N7,N11,N16 | — | — |
| NPCIdentity.faction | N11,N18 | N12 | — |
| NPCIdentity.title | N16 | N12 | — |
| NPCIdentity.background | N6,N11,N16 | — | — |
| PersonalityData | N11,N15,N16 | — (初始化后几乎不变) | — |
| PersonalityData.intelligence | N11,N15,N16 | — | — |
| PersonalityData.traits | N11,N16 | — | — |
| PersonalityData.openness | N11 | — | — |
| PersonalityData.extraversion | N11 | — | — |
| PersonalityData.agreeableness | N11,N16 | — | — |
| PersonalityData.neuroticism | N11 | — | — |
| PersonalityData.conscientiousness | N11 | — | — |
| CurrentStateData | N6,N7,N11,N16,N17 | N12 | — |
| CurrentStateData.location | N6,N7,N11,N16,N17,N18 | N12 | — |
| CurrentStateData.emotion | N11,N16 | N12,N16 | — |
| CurrentStateData.action | N11,N16 | N12 | — |
| CurrentStateData.health_value | — | N12 | — |
| CurrentStateData.is_alive | N6(过滤) | N12 | — |
| CurrentStateData.is_conscious | N11(过滤) | N12 | — |
| CurrentStateData.busy_level | N11 | N12 | — |

### event_schema 下的 Entity

| Entity | 读 (R) | 修改 (W) | 产出 (O) |
|--------|-------|---------|---------|
| Event | N11,N12,N16,N17,N18 | N7 | N7 |
| Event.event_id | N11,N12,N18 | N7 | N7 |
| Event.event_type | N11,N18 | N7 | N7 |
| Event.description | N11,N12,N18 | N7 | N7 |
| Event.scope | N11,N18 | N7 | N7 |
| Event.location | N11,N17,N18 | N7 | N7 |
| Event.involved_npc_ids | N11,N12 | N7 | N7 |
| EventDetail | — | N7 | N7 |
| CurrentEvent | N7,N16 | N7 | — |
| CurrentEvent.location | N6,N7,N16 | N7 | — |
| CurrentEvent.topic | N6,N7,N16,N17 | N7 | — |
| CurrentEvent.addressed_npc_id | N7,N16 | N7 | — |
| CurrentEvent.involved_npc_ids | N7,N12 | N7 | — |
| CurrentEvent.scope | N7,N18 | N7 | — |
| EventTable | N10,N11 | — | N7 |

---
### perception_schema 下的 Entity

| Entity | 读 (R) | 修改 (W) | 产出 (O) |
|--------|-------|---------|---------|
| NPCPerception | N12,N14,N15,N16 | — | N11 |
| NPCPerception.npc_id | N12,N14,N15,N16 | — | N11 |
| NPCPerception.perceived_event_ids | N12 | — | N11 |
| NPCPerception.perceived_summary | N12,N16 | — | N11 |
| NPCPerception.missed_event_ids | — | — | N11 |

### memory_schema 下的 Entity

| Entity | 读 (R) | 修改 (W) | 产出 (O) |
|--------|-------|---------|---------|
| MemoryEntry | N15,N16 | N12 | — |
| MemoryEntry.content | N15,N16 | N12 | — |
| MemoryEntry.importance | N15 | N12 | — |
| MemoryEntry.emotion_tags | N15 | N12 | — |
| KnowledgeEntry | N11,N14,N15,N16,N18 | N12,N16,N18 | — |
| KnowledgeEntry.fact | N11,N14,N15,N16,N18 | N12,N16,N18 | — |
| KnowledgeEntry.source | N14,N15,N16 | N12,N16,N18 | — |
| KnowledgeEntry.confidence | N14,N15,N16 | N12,N18 | — |
| KnowledgeEntry.is_secret | N16,N18 | N12,N16 | — |
| ObservationEntry | N11,N14,N15,N16 | N12 | — |
| ObservationEntry.observation | N11,N14,N15,N16 | N12 | — |
| ObservationEntry.detail_level | N11 | N12 | — |
| ObservationEntry.is_anomaly | N14 | N12 | — |

### belief_schema 下的 Entity

| Entity | 读 (R) | 修改 (W) | 产出 (O) |
|--------|-------|---------|---------|
| BeliefEntry | N15,N16 | N12 | — |
| BeliefEntry.belief | N15,N16 | N12 | — |
| BeliefEntry.strength | N15 | N12 | — |
| BeliefEntry.basis | N15,N16 | N12 | — |
| BeliefEntry.counter_evidence | N15 | N12 | — |
| PendingEntry | N14,N15,N16 | N12 | — |
| PendingEntry.event_description | N14,N15 | N12 | — |
| PendingEntry.priority | N14 | N12 | — |
| PendingEntry.attempts | N14 | N12 | — |

### relationship_schema 下的 Entity

| Entity | 读 (R) | 修改 (W) | 产出 (O) |
|--------|-------|---------|---------|
| RelationshipValue | N16,N18 | N12 | — |
| RelationshipValue.favorability | N16,N18 | N12 | — |
| RelationshipValue.trust | N16,N18 | N12 | — |
| RelationshipValue.relationship_type | N16,N18 | N12 | — |
| RelationshipValue.history | — | N12 | — |
| FavorabilityChange | — | N12 | N16(建议) |
| RelationshipGraph | N16,N18 | N12 | — |

### dialogue_schema 下的 Entity

| Entity | 读 (R) | 修改 (W) | 产出 (O) |
|--------|-------|---------|---------|
| NPCResponse | N17 | — | N16 |
| NPCResponse.response_text | N17 | — | N16 |
| NPCResponse.response_type | N17 | — | N16 |
| NPCResponse.emotion | N17 | — | N16 |
| NPCResponse.target_id | N17,N18 | — | N16 |
| DialogueHistoryEntry | N16,N17 | — | N16(追加) |
| DialogueHistory | N16,N17 | N16 | — |
| GlobalContext | N5,N7,N16 | N1,N16 | — |
| GlobalContext.player_inputs | N5,N7 | N1 | — |
| GlobalContext.significant_events | N5,N7,N18 | N12 | — |

### director_schema 下的 Entity

| Entity | 读 (R) | 修改 (W) | 产出 (O) |
|--------|-------|---------|---------|
| DirectorStyleConfig | N17 | — | — |
| SceneMemory | N17 | N17 | — |
| SceneMemory.scene_description | N17 | N17 | N17 |
| SceneMemory.last_tick | N17 | N17 | — |
| SceneMemory.notable_features | N17 | N17 | N17 |

### action_schema 下的 Entity

| Entity | 读 (R) | 修改 (W) | 产出 (O) |
|--------|-------|---------|---------|
| GoalEntry | N11,N14,N15,N16 | N12,N15 | — |
| GoalEntry.goal | N11,N14,N15,N16 | N12,N15 | — |
| GoalEntry.priority | N14,N15 | N12 | — |
| GoalEntry.is_completed | N11(过滤),N14(过滤) | N12 | — |
| GoalEntry.progress | N14,N15 | N12 | — |
| Item | N16 | N12 | — |
| NPCAction | N11,N18 | N12 | N16(建议) |

### location_schema 下的 Entity

| Entity | 读 (R) | 修改 (W) | 产出 (O) |
|--------|-------|---------|---------|
| Location | N6,N7,N11,N16,N17,N18 | N12 | — |
| Location.name | N6,N7,N11,N16,N17 | — | — |
| Location.location_type | N11,N18 | — | — |
| Location.access | N11,N18 | — | — |
| Location.npcs_present | N6,N7,N11,N17 | N12 | — |
| Location.connected_locations | N18 | — | — |
| Location.notable_features | N17 | N17 | N17 |

---
## 四、核心问题分析

### 4.1 事件涉及哪些NPC？

根据流程图的分析，一个事件涉及NPC分为几个层次：

**第一层：直接参与者 (involved_npc_ids)**
- 玩家直接对话的对象（addressed_npc_id）
- 玩家点名提到的人
- 与事件动作直接相关的NPC

这些NPC由 N7(事件层AI) 在 CurrentEvent 中标记。
**硬约束**：他们在N11(感知层)中强制感知到事件。

**第二层：同场景观察者**
- 同一location的其他NPC
- 同faction/group的NPC（scope=group时）

这些NPC由 N11(感知层AI) 判断是否感知到事件。

**第三层：间接受影响者**
- 通过N18(传播层)间接得知的NPC
- 通过关系网络被波及的NPC

**需要考虑的边界情况**：
1. NPC主动加入事件：存在NPC但未在involved_npc_ids中，该NPC主动插话→N7需要支持added_npc_ids
2. NPC离开事件：involved NPC可能因故离开→N7需要支持removed_npc_ids
3. NPC因位置变化失去感知：上轮在同一位置，本轮移动到其他位置→N11需要检查位置
4. NPC因状态变化无法参与：昏迷/死亡的NPC→N11需要检查is_conscious/is_alive
5. 新NPC进入场景→N6/N7需要更新involved_npc_ids

### 4.2 事件应该传播到哪里？

传播是一个多阶段决策：

**阶段1：能不能传播？(N18 leve1)**
- scope=PRIVATE/HIDDEN → 不能传播
- 被标记为秘密/密谋的内容 → 不能传播到目标对象
- NPC没有传播权限（如被威胁/被控制）→ 不能传播

**阶段2：传播给谁？(N18 p3)**
- scope=PUBLIC → 同区域所有NPC（通过form1）
- scope=AREA → 同location的NPC
- scope=GROUP → 同faction的NPC
- 定向传播（如写信）→ 指定NPC的知识表
- 成为全局事件 → world2（如战争、灾难、法律变更）

**阶段3：怎么传播？**
- 口口相传 → 逐hop传播到NPC知识，每hop有失真度
- 官方公告 → 直接写入world2，成为GlobalEvent
- 书信/密报 → 直接写入目标NPC知识，不经过hop

**阶段4：传播优先级冲突**
- 世界状态优先级高于传播层
- 如果传播的内容与世界规则冲突→世界规则优先（流程图注释已明确）

---
## 五、设计观察与建议

以下基于流程分析发现的问题，不修改现有流程，仅提出建议供后续讨论。

### 5.1 Entity归属问题

**当前状态**：部分暂态数据与Entity混在同一文件中。

| 文件 | 混入的暂态数据 | 建议 |
|------|-------------|------|
| belief_schema.py | ReasoningInput, NPCReasoning, ReasoningResult | 移至独立的 layer_io 或标记为暂态 |
| perception_schema.py | PerceptionInput, PerceptionResult | 同上 |
| dialogue_schema.py | DialogueContext, ResponseResult | 同上 |
| director_schema.py | DirectorInput, DirectorResult, NarrativeOutput | 同上 |

这些暂态结构是流程节点传递数据的必要容器，它们不属于任何Entity。
建议在第三阶段设计Prompt时，将它们作为Prompt的输入/输出结构使用，而不是作为Schema Entity。

### 5.2 变量缺失

**form10(更新成本)** 已在 action_schema 中定义 UpdateCost 和 UpdateCostTracker。
但当前代码中未在N12(更新层)中实际调用。建议在N12每次写入维护表时，调用 record_update()。

**GlobalContext(list1)** 当前只有 player_inputs 和 significant_events。
流程图中N16(回应层)的输出目的地包含 list1，但当前的 GlobalContext 缺少
"NPC回应记录"字段。建议增加 dialogue_records 或复用 DialogueHistory。

**Location** 当前只是NPC和Event上的字符串字段，但流程图中多处涉及位置判断。
已新增 location_schema.py 作为一等实体，但需要在代码中实际使用。

### 5.3 流程边界问题

**N5(call1)的职责范围**：流程图中标注"世界状态优先级更高"——说明call1不仅是
数据聚合，还承担冲突裁决职责。当前实现中Engine直接构建输入dict传给EventLayer，
没有显式的call1裁决步骤。建议在N5处增加裁决逻辑：当世界状态与传播层变动冲突时，
世界状态优先。

**N14(p1)的触发条件**：当前代码中"NPC有pending且有observation"才触发推理。
但实际情况中，NPC可能仅凭 记忆+知识 就能推理，不需要新观察。
建议放宽触发条件：pending_count > 0 即可触发推理。

### 5.4 数据冗余风险

**记忆 vs 知识 vs 观察**：三个表之间存在内容重复风险：
- 观察 → 经推理后 → 变为知识
- 知识 → 经反复确认后 → 变为记忆
- 同一事实可能同时存在于三个表中

建议在N12(更新层)中增加去重逻辑：写入新条目前检查是否与已有条目高度重复。

### 5.5 权限层

流程图第91行："call1 & NPC维护表 --传播当前事件--> leve1"
这标注了传播前需要通过权限检查。当前 PropagationLayer 中
没有显式的权限检查逻辑。建议：
1. 检查NPC是否有传播权限（基于role/faction/关系）
2. 检查内容是否属于秘密（is_secret标记）
3. 检查传播目标是否被排除（如密谋对象）

---
