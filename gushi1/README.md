# AI 多NPC世界模拟框架

一个基于分层架构的AI多NPC世界模拟框架。最终目标不是聊天机器人，而是**世界独立运行**。

## 核心理念

```
玩家输入 → 事件 → 世界 → NPC独立感知 → NPC维护自身状态 → NPC推理 → NPC回应 → 世界持续演化
```

**LLM只是智能模块，不是整个系统。**
Python负责状态维护和调度，LLM只负责需要智能的部分。

## 架构

### 分层架构（固定10层）

```
WorldLayer → WorldStateLayer → EventLayer → PerceptionLayer
→ UpdateLayer → ReasoningLayer → UpdateLayer → ResponseLayer
→ DirectorLayer → PropagationLayer
```

### 数据流

```
玩家输入 → EventLayer → PerceptionLayer → UpdateLayer → ReasoningLayer
→ UpdateLayer → ResponseLayer → DirectorLayer → PropagationLayer → 等待下一轮
```

### 每层职责

| 层 | 职责 | 需要LLM |
|---|------|---------|
| WorldLayer | 世界定义、规则、隐藏事件 | 否 |
| WorldStateLayer | 时间、天气、全局事件 | 否 |
| EventLayer | 解析玩家输入为结构化事件 | 是 |
| PerceptionLayer | NPC独立感知判断 | 是 |
| UpdateLayer | **唯一**修改NPC维护表的入口 | 否 |
| ReasoningLayer | NPC推理未知信息 | 是 |
| ResponseLayer | NPC生成回应 | 是 |
| DirectorLayer | 整合润色NPC回应为叙事 | 是 |
| PropagationLayer | 信息传播判断 | 否 |

### NPC维护表（仅UpdateLayer可写）

- Memory — 记忆
- Knowledge — 知识
- Belief — 信念
- Observation — 观察
- PendingReason — 待解释事件
- Relationship — 关系
- Inventory — 持有物
- CurrentState — 当前状态
- Goal — 目标
- Personality — 性格

## 设计原则

1. **Python负责**：状态维护、调度、数据结构、Prompt管理、JSON解析
2. **LLM负责**：感知、推理、回应、导演润色
3. **LLM输出必须JSON**：禁止Markdown、自然语言
4. **单一职责**：每层只做一件事，Prompt只做一件事
5. **最小上下文**：不把整个世界发给LLM
6. **按需调用LLM**：Python先判断，只在需要时调用LLM
7. **组合优于继承**：Dataclass优先，禁止大量dict
8. **禁止魔法字符串**：使用枚举和常量

## 项目结构

```
E:\A.gushi\
├── src/
│   ├── core/          # 引擎、LLM客户端、配置
│   ├── models/        # 数据模型（NPC、世界、事件等）
│   ├── layers/        # 9个层次（每层5个文件）
│   └── utils/         # 工具函数
├── tests/             # 测试
├── requirements.txt
└── README.md
```

## 快速开始

```python
from src.core.engine import Engine, Config
from src.models.npc import NPC, NPCRole, CurrentStateData
from src.core.config import LLMConfig

# 创建引擎
config = Config()
engine = Engine(config=config)

# 初始化世界
world_def = {
    "world_definition": {
        "world_id": "my_world",
        "name": "我的世界",
        "rules": [
            {"rule_id": "r1", "description": "偷窃犯法", "penalty": "监禁"}
        ],
        "hidden_events": [],
        "known_events": ["今天是集市日"],
    },
}
engine.initialize_world(world_def)

# 添加NPC
merchant = NPC(
    npc_id="merchant_1",
    name="王五",
    role=NPCRole.MERCHANT,
    current_state=CurrentStateData(location="镇广场", action="叫卖"),
)
engine.add_npc(merchant)

# 运行
result = engine.tick("你好，老板！")
print(result.get_narrative())
```

## 集成LLM

```python
from src.core.llm_client import LLMClient, LLMConfig

# 实现具体的LLM客户端
class MyLLMClient(LLMClient):
    def send(self, prompt: str):
        # 调用实际的LLM API
        ...

# 注入引擎
llm_client = MyLLMClient(config=LLMConfig(model="gpt-4"))
engine = Engine(config=Config(), llm_client=llm_client)
```

## 运行测试

```bash
pytest tests/ -v
```

## 架构原则

- **架构优先于功能**：如果需求会破坏架构，必须调整需求设计
- **禁止跨层**：每层只能调用下一层，不能跳过
- **禁止Hack**：不能为实现功能临时增加特殊判断
- **UpdateLayer唯一**：所有状态修改必须经过UpdateLayer
