# Gushi2 AI 多NPC世界模拟系统 — Node 架构设计

> **设计依据**: 流程图.mmd（唯一可信业务设计来源）
> **设计原则**: 信息隔离优先、数据不可变性传递、Node 不直接修改 Entity
> **日期**: 2026-07-14

---

## 一、系统总体 Tick 流程

```
                            +------------------------------+
                            |   WorldMaintenanceNode       |
                            |   (定时异步，独立于 Tick)      |
                            +----------+-------------------+
                                       | 定时更新世界状态
                                       v
+-------------------------------------------------------------------------+
|                          单 轮 Tick 流 程                                |
|                                                                         |
|  [1] InputNode                                                          |
|       |                                                                 |
|       | 规范化输入                                                        |
|       v                                                                 |
|  [2] ContextAssemblyNode                                                |
|       |                                                                 |
|       | 世界规则 + 世界状态 + 环境规则 + 所需NPC + 当前事件上下文           |
|       v                                                                 |
|  [3] EventOrchestrationNode                                             |
|       |                                                                 |
|       | 新建 RealityEvent 或 延续已有 RealityEvent                        |
|       v                                                                 |
|  [4] InformationAccessNode                                              |
|       |                                                                 |
|       | 规则引擎：为每个NPC计算可见信息边界                                  |
|       v                                                                 |
|  [5] PerceptionNode                                                     |
|       |                                                                 |
|       | AI判定每个NPC从其可见信息中实际感知到什么                            |
|       v                                                                 |
|  [6] UpdateNode                                                         |
|       |                                                                 |
|       | 更新 NPC维护表(记忆/信念/性格/目标/好感/状态/观察/持有物/待解释)    |
|       v                                                                 |
|  [7] ReasoningNode                                                      |
|       |                                                                 |
|       | 检查待解释事件表 → AI推理                                          |
|       v                                                                 |
|  [8] ResponseNode                                                       |
|       |                                                                 |
|       | NPC回应生成(加入全局上下文 & 记忆表)                                |
|       v                                                                 |
|  [9] DirectorNode                                                       |
|       |                                                                 |
|       | 导演样式控制 → 分别输出 NPC说话 / 旁白                             |
|       | 规则: 当前环境规则 + 当前事件上下文 + 世界规则                       |
|       v                                                                 |
| [10] InformationPropagationNode                                         |
|       |                                                                 |
|       | 信息传播策略: 判断是否有权传播 → 判断传播给谁                        |
|       v                                                                 |
|       +--→ 给NPC → 写入 form1(记忆/知识表)
|       |                                                       |
|       +--→ 给世界 → 写入 world2(世界状态)                       |
|       |                                                       |
|       v                                                       |
|  该轮对话结束 <------------------------------------------------+
+-------------------------------------------------------------------------+
```

### 补充：WorldMaintenanceNode（独立于Tick的定时任务）

```
WorldMaintenanceNode
    │
    │ 根据时间戳标记世界事件
    │ → 定时更新/扩大/关闭世界状态中的事件
    │ → 世界状态优先级高于事件层局部状态
    │ → 传播层产生的变动（法律更新、规则更新）写入世界状态
    v
```

---

## 二、流程图模块分析

### 2.1 模块拆解

| 模块 | 名称 | 职责 | 解决的问题 | 输入 | 输出 | 数据生命周期 | 需LLM | 修改数据 |
|------|------|------|------------|------|------|-------------|--------|----------|
| a1 | 玩家输入 | 接收玩家原始指令 | 系统入口 | 玩家文本/动作 | 结构化玩家指令 | 单Tick | NO | NO |
| list1 | 全局上下文 | 聚合所有对话历史、事件记录 | 统一上下文 | 玩家输入+事件+回应 | 全局上下文表 | 持续累积 | NO | 追加 |
| world1 | 世界规则 | 定义世界运行法则 | 世界一致性 | 设计者定义 | 规则集 | 持久不变(设计时) | NO | 设计者可改 |
| world2 | 世界状态 | 记录世界全局状态 | 跨事件状态追踪 | 事件传播+定时更新 | 世界状态快照 | 持久可变 | NO | 定时+传播写 |
| a2 | 事件层 | 事件生命周期管理 | 事件新建/延续 | 上下文+规则+NPC | 当前RealityEvent | 事件持续期间 | YES(a3) | YES(新建) |
| event1/old | 当前事件 | 正在进行的RealityEvent实例 | 事件驱动 | 事件编排 | 事件对象 | 到事件结束 | NO | YES |
| a4 | 感知层 | NPC信息接收判断 | NPC只能感知其可见信息 | 事件+规则+NPC身份 | 每个NPC的PerceivedEvent | 单Tick | YES | NO |
| a5 | 更新层 | 更新NPC维护表 | 状态同步 | 感知结果 | 更新后的NPC表 | Tick间持久 | NO | YES |
| NPC维护表 | NPC状态存储 | 10张表维护NPC完整状态 | NPC一致性 | 更新层+传播层 | 记忆/信念/性格/目标/好感/状态/观察/持有物/待解释/成本 | 持久可变 | NO | YES |
| a6 | 推理层 | NPC自主推理 | 待解释事件需要NPC思考 | 待解释事件表 | 推理结果 | 单Tick | YES | NO |
| a7 | 回应层 | NPC生成回应 | NPC行动/对话输出 | NPC状态+推理 | NPC回应文本 | 单Tick | YES | 追加上下文 |
| a8 | 导演层 | 控制输出样式 | 叙事风格统一 | 回应+导演样式 | NPC说话 / 旁白 | 单Tick | YES(p2) | NO |
| a9 | 信息传播层 | 跨NPC信息传递 | 信息流动 | 当前对话 | 传播决策 | 单Tick | NO | YES |
| level1 | 传播策略层 | 判断传播权限 | 信息安全 | 事件+维护表 | 有权/无权+目标 | 单Tick | NO | NO |
| p3 | 权限层 | 判断传播目标 | 信息隔离 | 策略决策 | NPC知识写入/世界状态写入 | 单Tick | NO | YES |

---

## 三、Node 详细定义

---

### Node 1: InputNode

**职责**: 接收玩家原始输入，转化为结构化数据。不做推理，只做格式标准化。

**输入模板**:
```
{
  raw_text:       string,          // 玩家原始输入
  timestamp:      number,          // 输入时间戳
  session_id:     string           // 会话标识
}
```

**输出模板**:
```
{
  input_id:        string,         // 输入唯一ID
  input_type:      enum,           // ACTION | DIALOGUE | QUERY | COMMAND
  content:         string,         // 规范化文本
  mentions:        string[],       // 提及的NPC/地点/物品
  timestamp:       number
}
```

**是否调用LLM**: NO

**是否修改数据**: NO

---

### Node 2: ContextAssemblyNode

**职责**: 召集本轮所需全部上下文。从世界状态、世界规则中拉取相关数据。不做判断，只做聚合。

**输入模板**:
```
{
  player_input:    InputNodeOutput, // 结构化玩家输入
  world_state:     WorldState,      // 当前世界状态
  world_rules:     WorldRule[],     // 适用的世界规则
  environment:     EnvironmentRule, // 当前环境规则
  active_events:   RealityEvent[],  // 当前活跃事件列表
  global_context:  ContextEntry[]   // 全局上下文历史
}
```

**输出模板**:
```
{
  tick_context_id:  string,
  player_input:     InputNodeOutput,
  relevant_rules:   WorldRule[],
  current_environment: EnvironmentRule,
  active_events:    RealityEvent[],
  required_npcs:    NPCIdentity[],   // 当前事件所需的NPC列表
  event_context:    EventSnapshot,   // 当前事件上下文快照
  global_context:   ContextEntry[]   // 截断/过滤后的上下文
}
```

**是否调用LLM**: NO

**是否修改数据**: NO

---

### Node 3: EventOrchestrationNode

**职责**: 判断本轮是继续已有事件还是新建事件。调用LLM做事件级别的决策。输出RealityEvent对象。

**输入模板**:
```
{
  tick_context_id:  string,
  player_input:     InputNodeOutput,
  relevant_rules:   WorldRule[],
  active_events:    RealityEvent[],
  required_npcs:    NPCIdentity[],
  event_context:    EventSnapshot
}
```

**输出模板**:
```
{
  action:            enum,          // CONTINUE_EVENT | CREATE_EVENT
  reality_event: {                  // 如果是CONTINUE则返回已有事件ID
    event_id:         string,
    event_type:       string,       // MURDER | DIPLOMACY | EXPLORATION | DIALOGUE | ...
    event_phase:      enum,         // OPENING | ESCALATION | CLIMAX | RESOLUTION
    reality_data:     object,       // 世界真实事件：包含所有秘密、隐藏信息
    participants:     NPCIdentity[],
    location:         string,
    rules_override:   object|null   // 本事件特有的规则
  }
}
```

**是否调用LLM**: YES — 判断新建/延续事件，初始化事件世界观

**是否修改数据**: YES — 新建RealityEvent时写入事件表

**写入入口**: 通过 EventStore 写入

---

### Node 4: InformationAccessNode

**职责**: 核心权限系统。对每一个NPC，基于其身份、位置、关系、已有知识，计算出该NPC能够接触到的信息边界。这是一层**纯规则引擎**，绝对不调用LLM。

**输入模板**:
```
{
  reality_event:     RealityEvent,
  world_rules:       WorldRule[],
  npc_states: {                       // 每个NPC的当前状态
    npc_id: {
      identity:        NPCIdentity,   // 身份(种族/职业/阵营)
      location:        string,        // 当前位置
      relationships:   Relationship[], // 与事件中其他实体的关系
      knowledge_state: KnowledgeState, // 已有知识
      status:          NPCStatus      // 当前状态(健康/情绪/职位)
    }
  }
}
```

**输出模板**:
```
{
  event_id:           string,
  reality_event:      RealityEvent,   // 真实事件(保留，供后续引用)
  npc_information_view: {             // 每个NPC的独立信息视图
    npc_id: {
      visible_facts:    Fact[],        // 该NPC能看到的客观事实
      visible_relations: Relation[],   // 该NPC能感知的关系
      blocked_facts:    FactID[],      // 被屏蔽的事实ID(只记录ID，不透露内容)
    }
  },
  access_decision_log: {              // 决策日志(调试用)
    npc_id: {
      rule_applied:   string,         // 应用了哪条规则
      reason:         string          // 为什么允许/拒绝
    }
  }
}
```

**是否调用LLM**: NO — 纯规则引擎

**是否修改数据**: NO — 只计算，不写入

**关键设计说明**:
- 这是一个确定性函数：相同输入永远产生相同输出
- 不允许出现 `hidden_events` 字段告诉NPC"这里有秘密"
- 被屏蔽的信息对NPC**不存在**，不进入其任何上下文
- 规则定义优先级：世界规则 > 事件特殊规则 > 默认策略

---

### Node 5: PerceptionNode

**职责**: 对每个NPC，从其**独立信息视图**出发，调用LLM判断该NPC实际感知到了什么（注意、忽略、误解）。NPC不会接触到自己无权看到的信息。

**输入模板**:
```
{
  event_id:            string,
  npc_information_view: {            // 来自InformationAccessNode
    npc_id: {
      visible_facts:    Fact[],
      visible_relations: Relation[]
    }
  },
  npc_personalities: {               // 每个NPC的性格/状态
    npc_id: {
      personality:      PersonalityTable,
      current_status:   NPCStatus,
      attention_focus:  string|null   // 当前关注点
    }
  }
}
```

**输出模板**:
```
{
  event_id:             string,
  npc_perceptions: {
    npc_id: {
      perceived_events:   PerceivedEvent[],  // NPC感知到的事件片段
      missed_signals:     FactID[],          // 存在但NPC未注意的信息
      misinterpretations: Misinterpretation[], // 误解的信息(可选)
      emotional_reaction: EmotionResult,
      attention_shift:    AttentionShift|null // 注意力转移
    }
  }
}
```

**是否调用LLM**: YES — 对每个NPC独立调用(可并行)

**是否修改数据**: NO

**关键设计说明**:
- LLM prompt 只包含该NPC可见的事实，**不含不可见事实**
- 不需要告诉LLM"忽略秘密"，因为秘密根本不在prompt中
- 每个NPC的LLM调用是独立的、可并行的

---

### Node 6: UpdateNode

**职责**: 将PerceptionNode的结果写入NPC维护表。这是整个架构中唯一可以修改NPC维护表的Node（传播层也可以追加到form1）。

**输入模板**:
```
{
  player_input:           InputNodeOutput,
  event_id:               string,
  reality_event:          RealityEvent,
  npc_perceptions: {
    npc_id: {
      perceived_events:     PerceivedEvent[],
      emotional_reaction:   EmotionResult
    }
  }
}
```

**输出模板**:
```
{
  update_results: {
    npc_id: {
      updated_tables:     string[],           // 更新了哪些表
      memory_count:       number,             // 新增记忆条数
      belief_changes:     BeliefChange[],     // 信念变化
      relationship_changes: RelationshipDelta[],
      status_changes:     StatusChange[],
      pending_inferences: PendingInference[]  // 待解释事件
    }
  }
}
```

**是否调用LLM**: NO — 确定性更新操作

**是否修改数据**: YES — 写入NPC维护表

**写入入口**: 通过 UpdateEngine

**对应NPC维护表（共10张）**:

| 表 | 说明 | 写入时机 |
|----|------|----------|
| form1 记忆/知识表 | NPC已知的所有信息 | UpdateNode + 传播层 |
| form2 信念表 | NPC相信什么 | UpdateNode |
| form3 性格表 | NPC性格特征 | 创建时定义，极少更新 |
| form4 生活目标表 | NPC的目标 | UpdateNode + 推理层 |
| form5 好感度表 | NPC对其他实体的好感 | UpdateNode |
| form6 当前状态表 | 健康/情绪/位置 | UpdateNode |
| form7 观察表 | NPC当前观察到的事物 | UpdateNode(本Tick) |
| form8 持有物表 | NPC持有的物品 | UpdateNode |
| form9 待解释事件表 | 需要推理的事件 | UpdateNode |
| form10 表更新成本 | 每次更新的计算成本 | UpdateNode(自维护) |

---

### Node 7: ReasoningNode

**职责**: 检查每个NPC的待解释事件表(form9)。如果有未解释事件，调用LLM进行推理。

**输入模板**:
```
{
  npc_pending_inferences: {
    npc_id: {
      pending_events:    PendingInference[],  // form9中的待解释事件
      npc_state: {                           // form2+form3+form4+form6
        beliefs:           Belief[],
        personality:       PersonalityTable,
        goals:             Goal[],
        current_status:    NPCStatus
      },
      knowledge:          KnowledgeEntry[],   // form1中的已知信息
      memories:           Memory[]            // form1中的相关记忆
    }
  }
}
```

**输出模板**:
```
{
  inference_results: {
    npc_id: {
      resolved_events:    ResolvedInference[],  // 已推理完成的事件
      new_beliefs:        Belief[],             // 新产生的信念
      new_goals:          Goal[],               // 新产生的目标
      emotional_changes:  EmotionDelta[],       // 情绪变化
      action_intents:     ActionIntent[]        // 产生的行动意图
    }
  }
}
```

**是否调用LLM**: YES — 对有待解释事件的NPC独立调用(可并行)

**是否修改数据**: NO — 产出推理结果，由UpdateEngine统一写入

**写入入口**: 推理结果返回后，由UpdateEngine统一写入form1/form2/form4/form6/form9

---

### Node 8: ResponseNode

**职责**: 为每个NPC生成本轮回应（对话、行动）。使用NPC维护表中的完整信息。回应产物写入全局上下文和记忆表。

**输入模板**:
```
{
  event_id:             string,
  player_input:         InputNodeOutput,
  reality_event:        RealityEvent,
  director_style:       DirectorStyle,      // 来自导演样式表
  npc_current_states: {
    npc_id: {
      personality:        PersonalityTable,
      current_status:     NPCStatus,
      goals:              Goal[],
      relationships:      Relationship[],
      memories:           Memory[],
      beliefs:            Belief[],
      action_intents:     ActionIntent[]     // 来自推理层(如有)
    }
  }
}
```

**输出模板**:
```
{
  npc_responses: {
    npc_id: {
      response_type:     enum,             // SPEAK | ACTION | SILENCE | EMOTE
      content:           string,           // 回应内容
      target:            string|null,      // 回应目标(NPC_ID 或 PLAYER)
      tone:              string,           // 语气
      action_data:       object|null,      // 如果是行动，附带行动数据
      time_cost:         number            // 回应耗时
    }
  }
}
```

**是否调用LLM**: YES — 对每个NPC独立调用(可并行)

**是否修改数据**: NO — 产出文本回应

**写入入口**: 回应结果返回后，通过 UpdateEngine 追加到：
- list1 全局上下文
- form1 NPC记忆/知识表

---

### Node 9: DirectorNode

**职责**: 控制叙事输出样式。汇总所有NPC回应，根据导演样式规则分拆为"NPC说话"和"旁白"两种输出。同时为下一轮信息传播提供输入。

**输入模板**:
```
{
  npc_responses: {
    npc_id: {
      response_type:     enum,
      content:           string,
      target:            string|null,
      tone:              string,
      action_data:       object|null
    }
  },
  director_style:       DirectorStyle,
  environment_rules:    EnvironmentRule,
  event_context:        EventSnapshot,
  world_rules:          WorldRule[]
}
```

**输出模板**:
```
{
  narrative_output: {
    npc_dialogues: {
      npc_id:           string,
      text:             string,           // NPC说的原话
      emotion:          string
    }[],
    narration: {
      text:             string,           // 旁白(除NPC说的一切)
      scene_description: string,
      action_description: string
    },
    current_dialogue_snapshot: object     // 本轮对话快照，供传播层使用
  }
}
```

**是否调用LLM**: YES — 判断输出样式、编排叙事

**是否修改数据**: NO — 只做输出转换

---

### Node 10: InformationPropagationNode

**职责**: 将本轮对话中的信息传播到应该收到的NPC和世界状态中。这是NPC间信息流动的入口。

**输入模板**:
```
{
  current_dialogue_snapshot: object,      // 当前对话快照
  event_context:              EventSnapshot,
  npc_maintenance: {                     // NPC维护表
    npc_id: {
      knowledge:    KnowledgeEntry[],
      relationships: Relationship[],
      status:       NPCStatus
    }
  },
  world_state:            WorldState
}
```

**输出模板**:
```
{
  propagation_decisions: {
    to_npc: {
      npc_id:   string,
      knowledge: KnowledgeEntry[],        // 写入该NPC的知识
      reason:   string                    // 为什么传播给这个NPC
    }[],
    to_world: {
      state_update: WorldStateDelta,      // 世界状态变更
      reason:       string
    }[]
  }
}
```

**是否调用LLM**: NO — 确定性策略判断

**是否修改数据**: YES

**写入入口**: 通过 UpdateEngine
- → NPC → form1(记忆/知识表)
- → 世界 → world2(世界状态)

---

### 传播策略层 嵌入 InformationPropagationNode

**职责**: 判断信息传播权限和目标。

**核心问题**: "NPC A 说了要谋害 NPC B，这个消息应该被谁听到？"

**传播策略规则**:

| 规则 | 说明 |
|------|------|
| 在场规则 | 对话发生时在场的NPC可以听到 |
| 通信规则 | NPC明确说"写信给X"，X应该收到信息 |
| 密谋规则 | 密谋对话不应传播到密谋目标 |
| 窃听规则 | 特殊NPC可能有窃听能力 |
| 世界规则 | 法律/法规变更通过世界状态层级传播 |

**输入模板**:
```
{
  dialogue_content:  string,              // 当前对话内容
  current_participants: NPCIdentity[],    // 当前对话参与者
  all_active_npcs:    NPCIdentity[],      // 所有活跃NPC
  npc_knowledge: {                        // 参与者的知识状态
    npc_id: KnowledgeEntry[]
  },
  world_rules:        WorldRule[]
}
```

**输出模板**:
```
{
  propagation_targets: {
    npc_id: {
      should_receive:   boolean,
      receive_content:  KnowledgeEntry,  // 该NPC收到的内容(可能不是完整对话)
      access_rule:      string           // 应用的传播规则
    }
  },
  world_state_updates: WorldStateDelta[],
  propagation_log: {
    rule_applied: string,
    reason: string
  }
}
```

**是否调用LLM**: NO

---

## 四、信息权限系统设计

### 4.1 核心原则

```
世界真实事件 (RealityEvent)
    |
    | 包含：真相、秘密、隐藏动机、不可见关系
    |
    v
InformationAccessNode (规则引擎)
    |
    | 输入：NPC身份、位置、关系、已有知识、世界规则
    | 执行：确定性规则匹配
    | 输出：每个NPC的独立信息视图
    |
    v
NPC_A 可见视图     NPC_B 可见视图     NPC_C 可见视图
    |                  |                  |
    | 只有NPC_A        | 只有NPC_B        | 只有NPC_C
    | 有权知道的        | 有权知道的        | 有权知道的
    | 事实             | 事实             | 事实
    |
    v                  v                  v
PerceptionNode       PerceptionNode     PerceptionNode
    |                  |                  |
    | LLM 调用          | LLM 调用          | LLM 调用
    | prompt只含        | prompt只含        | prompt只含
    | 该NPC的视图        | 该NPC的视图        | 该NPC的视图
```

### 4.2 禁止行为清单

| 禁止 | 原因 |
|------|------|
| 给NPC传递 `hidden_events` 字段 | NPC不应知道自己有不知道的事 |
| 在prompt中说"这里有个秘密" | 破坏信息隔离 |
| 把完整RealityEvent传给LLM让它自己过滤 | LLM不应接触不可见信息 |
| NPC_A的LLM调用中包含NPC_B的私有信息 | 信息泄露 |

### 4.3 正确设计：信息不存在

```
RealityEvent:
{
  murderer: NPC_A,
  victim:   NPC_B,
  secret_motive: "family revenge"
}
```

NPC_C 的视图（路人）:
```
{
  visible_facts: [
    { type: "BODY_FOUND", location: "square", time: "dawn" }
  ]
}
```

注意：NPC_C的视图中没有 `murderer`、没有 `secret_motive`。这些信息对NPC_C**不存在**。

---

## 五、事件模型设计

### 5.1 两种事件

| 事件类型 | 说明 | 持有者 | 用途 |
|----------|------|--------|------|
| RealityEvent | 世界的真实事件，包含全部真相和秘密 | EventStore | 系统内部真相 |
| PerceivedEvent | NPC感知到的事件，仅包含该NPC可见的信息 | NPC维护表 form7/form9 | NPC决策依据 |

### 5.2 RealityEvent 结构

```
RealityEvent {
  event_id:         string,
  event_type:       enum,
  event_phase:      enum,
  reality_data:     object,           // 全部真相
  participants:     NPCIdentity[],
  location:         string,
  timeline:         EventMoment[],    // 事件时间线
  rules_override:   object|null,
  created_at:       number,
  resolved_at:      number|null
}
```

### 5.3 PerceivedEvent 结构

```
PerceivedEvent {
  event_id:           string,
  source_reality_id:  string,         // 来源RealityEvent
  perceiver_id:       string,         // 感知者NPC
  visible_facts:      Fact[],         // 仅该NPC可见的事实
  perception_quality: enum,           // CLEAR | VAGUE | MISINTERPRETED
  emotional_impact:   EmotionResult,
  timestamp:          number
}
```

### 5.4 关键约束

- PerceivedEvent 绝不能包含 RealityEvent 中该NPC无权访问的字段
- 不猜测"NPC可能知道什么"然后塞进去
- 设计：InformationAccessNode 决定边界 → PerceptionNode 在边界内填充内容

---

## 六、世界状态 & 维护表设计

### 6.1 世界状态（WorldState）

```
WorldState {
  global_variables:   object,         // 天气、时间、经济状态
  active_laws:        Law[],          // 当前法律
  faction_states:     FactionState[], // 阵营状态
  world_events:       WorldEvent[],   // 全局事件(战争、灾难)
  environment_rules:  EnvironmentRule[], // 各区域环境规则
  updated_at:         number
}
```

### 6.2 NPC维护表（10张表）

| 表ID | 表名 | 内容 | 更新来源 |
|------|------|------|----------|
| form1 | 记忆/知识表 | NPC知道的所有信息 | UpdateNode, 传播层 |
| form2 | 信念表 | NPC相信的命题 | UpdateNode, 推理层 |
| form3 | 性格表 | 性格特征(稳定) | 创建时定义 |
| form4 | 生活目标表 | 当前目标 | UpdateNode, 推理层 |
| form5 | 好感度表 | 对其他实体的好感度 | UpdateNode |
| form6 | 当前状态表 | 健康/情绪/位置 | UpdateNode |
| form7 | 观察表 | 当前Tick观察到的事物 | UpdateNode(每Tick清空) |
| form8 | 持有物表 | 物品清单 | UpdateNode |
| form9 | 待解释事件表 | 需要推理才能理解的观察 | UpdateNode, 推理层(消耗) |
| form10 | 表更新成本 | 元数据：更新频率/代价 | UpdateNode(自维护) |

---

## 七、数据生命周期

### 7.1 短期数据（单Tick内）

| 数据 | 产生者 | 消费者 | 生命周期 |
|------|--------|--------|----------|
| InputNodeOutput | InputNode | ContextAssembly → 后续各Node | 单Tick |
| TickContext | ContextAssemblyNode | EventOrchestration → 后续各Node | 单Tick |
| 信息视图 | InformationAccessNode | PerceptionNode | 单Tick |
| NPC感知 | PerceptionNode | UpdateNode | 单Tick |
| NPC回应 | ResponseNode | DirectorNode | 单Tick |
| 叙事输出 | DirectorNode | 信息传播层 | 单Tick |
| 传播决策 | 信息传播层 | UpdateEngine | 单Tick |

### 7.2 长期数据（跨Tick）

| 数据 | 存储位置 | 生命周期 |
|------|----------|----------|
| 全局上下文历史 | GlobalContext | 整个会话 |
| 世界状态 | WorldState | 整个会话 |
| 世界规则 | WorldRuleDB | 不变 |
| NPC维护表 | NPCMaintenanceDB | 整个会话 |
| RealityEvent | EventStore | 事件全程 |
| 导演样式 | DirectorStyleDB | 整个会话 |

---

## 八、UpdateEngine：唯一的写入入口

### 8.1 设计原则

```
Node (只读) ──→ 产出结果 ──→ UpdateEngine ──→ 数据存储
                      ↑                          ↑
                  不可变数据                  唯一写入者
```

- **Node 不直接修改 Entity**
- **所有写入操作必须通过 UpdateEngine**
- UpdateEngine 负责：写入顺序、事务一致性、冲突检测

### 8.2 UpdateEngine 接口

```
UpdateEngine {
  write_npc_table(npc_id, table_id, entries):   void
  write_world_state(delta):                     void
  append_global_context(entry):                 void
  create_reality_event(event):                  string  // 返回event_id
  update_reality_event(event_id, delta):        void
}
```

---

## 九、LLM调用策略

### 9.1 调用映射

| Node | 调用LLM | 调用对象 | 并行 |
|------|---------|----------|------|
| InputNode | NO | - | - |
| ContextAssemblyNode | NO | - | - |
| EventOrchestrationNode | YES | 单次：判断新建/延续事件 | - |
| InformationAccessNode | NO(规则引擎) | - | - |
| PerceptionNode | YES | 每个NPC独立调用 | 可并行 |
| UpdateNode | NO | - | - |
| ReasoningNode | YES | 有待解释事件的NPC | 可并行 |
| ResponseNode | YES | 每个NPC独立调用 | 可并行 |
| DirectorNode | YES | 单次：叙事编排 | - |
| 传播策略层 | NO | - | - |

### 9.2 LLM调用安全原则

- 每个LLM prompt只包含被调用对象**有权知道**的信息
- prompt构建由InformationAccessNode的信息视图驱动
- 不允许在prompt中告诉LLM"忽略某些信息"
- 不允许跨NPC的信息在prompt中泄露

---

## 十、后续代码实现建议

### 10.1 实现顺序

```
Phase 1: 基础架构
  1. Node 基类/接口定义
  2. UpdateEngine
  3. EventStore
  4. NPC维护表存储

Phase 2: 不含LLM的Node
  5. InputNode
  6. ContextAssemblyNode
  7. InformationAccessNode (规则引擎)
  8. UpdateNode
  9. InformationPropagationNode (含传播策略层)

Phase 3: 含LLM的Node
  10. EventOrchestrationNode
  11. PerceptionNode
  12. ReasoningNode
  13. ResponseNode
  14. DirectorNode

Phase 4: 系统集成
  15. TickOrchestrator (串联所有Node)
  16. WorldMaintenanceNode (定时任务)
  17. 配置文件/规则配置
```

### 10.2 目录结构建议

```
src/
  core/
    node.ts              // Node基类
    engine.ts            // TickOrchestrator, UpdateEngine
    event_store.ts       // EventStore
  storage/
    npc_tables.ts        // 10张NPC维护表
    world_state.ts       // 世界状态
    rules.ts             // 世界规则
    context.ts           // 全局上下文
    director_styles.ts   // 导演样式
  nodes/
    input.ts
    context_assembly.ts
    event_orchestration.ts
    information_access.ts
    perception.ts
    update.ts
    reasoning.ts
    response.ts
    director.ts
    propagation.ts
    world_maintenance.ts
  rules/
    access_rules.ts      // 信息权限规则
    propagation_rules.ts // 传播策略规则
  types/
    events.ts            // RealityEvent, PerceivedEvent
    npc.ts               // NPCIdentity, NPCStatus, 各表类型
    world.ts             // WorldState, WorldRule
    context.ts           // ContextEntry
```

### 10.3 关键约束

1. **Node不可变输入**: 每个Node只读取数据，不可修改输入
2. **UpdateEngine唯一写入者**: 所有数据修改走UpdateEngine
3. **LLM prompt构建由Node自己完成**: Node负责从信息视图构建安全的prompt
4. **InformationAccessNode无副作用**: 必须是纯函数
5. **测试优先**: 每个Node可以独立测试（Mock输入，验证输出）

---

## 附录：Tick流程图（结构化）

```
玩家输入
    |
    v
[InputNode] ──── 格式标准化
    |
    v
[ContextAssemblyNode] ──── 汇聚上下文(世界规则、世界状态、环境、NPC列表、事件上下文)
    |
    v
[EventOrchestrationNode] ──── [LLM] 新建/延续 RealityEvent
    |
    v
[InformationAccessNode] ──── 规则引擎：每个NPC → 独立信息视图
    |
    v
[PerceptionNode] ──── [LLM × N NPCs] 各NPC从视图中感知
    |
    v
[UpdateNode] ──── UpdateEngine → 更新NPC维护表(10张表)
    |
    v
[ReasoningNode] ──── [LLM] 检查form9待解释事件 → 推理
    |
    v
[ResponseNode] ──── [LLM × N NPCs] 各NPC生成回应
    |
    v
[DirectorNode] ──── [LLM] 导演样式 → NPC说话 + 旁白
    |
    v
[InformationPropagationNode] ──── 传播策略 → Write form1 & world2
    |
    v
本轮结束
```

---

> **设计完成。所有设计均基于流程图.mmd，未参考任何旧代码或历史文档。**
\"\"\"", "len": 30977}