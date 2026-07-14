# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**gushi2** — AI 多NPC世界模拟框架。玩家输入自然语言，系统通过 LLM 驱动的流水线逐层处理事件感知→推理→回应→导演叙事→传播，最终生成连贯的叙事输出。

## 常用命令

```bash
# 交互模式（Mock 模式测试）
python main.py

# 交互模式 + 真实 LLM（需设置 DEEPSEEK_API_KEY 环境变量）
python main.py --world cangyue

# 单次执行
python main.py --once "你好！"

# 强制 Mock 模式
python main.py --mock

# Debug 模式（输出每步中间结果）
python main.py --mock --debug

# 诊断脚本（验证 N7→N18 数据流正确性）
python diagnose.py          # Mock 模式
python diagnose.py --real   # 真实 LLM

# 运行全部测试
pytest tests/ -v

# 运行单个测试文件
pytest tests/test_integration.py -v

# 运行单个测试
pytest tests/test_integration.py::TestEndToEnd::test_create_engine_and_tick -v
```

### LLM 模式切换

修改 `config/runtime.yaml` 中的 `llm_mode` 字段：
- `mock` — 使用 MockLLMManager（无需 API Key，测试用）
- `real` — 使用真实 LLM（DeepSeek 或 OpenAI，需设置对应环境变量）

Provider 选择在 `config/llm.yaml` 的 `provider` 字段：`deepseek` 或 `openai`。

## 核心架构

### Tick 流水线（固定不可变更）

每个 Tick 按顺序执行六层 LLM 节点，每层结果通过 UpdateEngine 写入状态：

```
玩家输入 → N7 事件层 → N11 感知层 → N15 推理层 → N16 回应层 → N17 导演层 → N18 传播层
                    ↕ N12 UpdateEngine（唯一写入，层层生效）
```

| 节点 | 编号 | 职责 | 调用LLM? |
|------|------|------|----------|
| EventNode | N7 | 判断玩家输入是延续还是新建事件 | 是 |
| PerceptionNode | N11 | 每个NPC感知到了什么（信息闸门） | 是 |
| ReasoningNode | N15 | NPC对有pending的事件进行推理 | 是 |
| ResponseNode | N16 | NPC生成回应 | 是 |
| DirectorNode | N17 | 整合回应为连贯叙事 | 是 |
| PropagationNode | N18 | 判断信息传播（口口相传/全局事件） | 否（程序逻辑） |

- **N15 有前置检查**：仅当有 NPC 存在 pending（待解释事件）时才调用 LLM。
- **N18 不调用 LLM**：传播策略由程序逻辑实现（scope 决定传播范围，hop 数控制失真度）。
- **UpdateEngine (N12)** 是唯一数据写入口——各 Node 的暂态输出必须通过它写入 Entity。

### 数据架构（三层分离）

1. **Schema/** — 数据定义层。定义所有 Entity（NPC、World、Event、Memory 等）和暂态容器（各层的中间输出结构）。`schema_mapping.md` 记录了完整的节点读写权限映射。
2. **engine/** — 引擎层。
   - `WorldEngine` — 主调度器，驱动 Tick 流水线
   - `WorldContext` — 运行时上下文容器（单 Tick 存活），持有所有 Node 可读的状态引用
   - `MemoryStore` — 持久数据存储（跨 Tick 存活），持有 NPC/WorldState/CurrentEvent 等
   - `UpdateEngine` — N12，唯一数据写入口，所有写入前经 `StateValidator` 校验
   - `LLMManager` — LLM 调用管理层，Node 只能通过它调用 LLM
   - `LLMClient` — LLM 调用抽象基类，具体实现见 engine/providers/
3. **nodes/** — LLM 节点层。所有节点继承 `BaseNode`，遵循 `execute(context) → NodeResult` 接口。

### 关键设计约束

- **读/写权限严格分离**：每个 Node 只能读取 `schema_mapping.md` 中规定的 Entity 字段。Node 产生的暂态结果必须通过 UpdateEngine 写入。Node 绝不能直接修改 Entity。
- **Node 不直接持有 LLMClient**：Node 通过 `LLMManager.chat(node_name, system_prompt, user_prompt)` 调用 LLM。
- **Prompt 模板**：`prompts/*.md` 是完整的 LLM 提示词，包含 Role/Goal/Input/Reasoning Task/Rules/Edge Cases/Output 七个部分。Node 通过 `PromptLoader` 加载并填充变量。
- **Provider 架构**：新增 LLM 厂商只需在 `engine/providers/` 下创建继承 `LLMClient` 的实现，并在 `LLMManager.create_from_config()` 中注册。

### NPC 维护表（form1~10）

NPC 的完整数据模型分散在多个 Schema 文件中：

| 表 | Entity | Schema 文件 | 说明 |
|----|--------|------------|------|
| form1 | MemoryEntry / KnowledgeEntry | memory_schema | 记忆/知识 |
| form2 | BeliefEntry | belief_schema | 信念 |
| form3 | PersonalityData | npc_schema | 性格（大五人格 + traits） |
| form4 | GoalEntry | action_schema | 生活目标 |
| form5 | RelationshipValue | relationship_schema | 好感度/信任度 |
| form6 | CurrentStateData | npc_schema | 位置/情绪/动作/生命值 |
| form7 | ObservationEntry | memory_schema | 观察记录 |
| form8 | Item | action_schema | 持有物 |
| form9 | PendingEntry | belief_schema | 待解释事件（触发推理的前置条件） |
| form10 | UpdateCost | action_schema | 更新成本（暂未启用） |

### 配置系统

`config/` 目录下三个 YAML 文件：
- `llm.yaml` — LLM Provider 选择、模型参数、各节点的温度覆盖
- `runtime.yaml` — Mock/Real 模式切换、引擎参数
- `world.yaml` — 世界名称、传播参数（max_hops/base_distortion_rate）、NPC 容量限制

配置通过 `config/__init__.py` 的类型化 dataclass 加载。支持 `pyyaml`，也有纯 Python 的回退解析器。

### 数据文件

- `data/worlds/{name}.json` — 世界定义（规则、隐藏事件、已知事件）
- `data/npcs/{name}_npcs.json` — NPC 定义（身份、性格、初始知识/信念/记忆/目标/关系/物品）
- `saves/` — 运行时存档（通过 `engine/persistence.py` 的 `save()`/`load()` 函数）
- `logs/` — Tick 日志（JSON 格式，含各节点输入输出）


### 测试

测试文件在 `tests/` 目录。Mock 模式是测试的基础——通过 `MockLLMManager.register_response()` 预注册各节点的期望响应来模拟 LLM 行为。`test_integration.py` 中的 `TestEndToEnd` 通过 `main.create_engine(force_mock=True)` 验证完整 Tick 流程。
