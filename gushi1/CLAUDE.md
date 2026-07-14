# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

AI 多NPC世界模拟框架。一个分层架构的回合制世界模拟器——玩家输入自然语言，经过10层流水线处理后输出小说般的叙事文本。LLM只负责需要智能的部分（感知、推理、回应、润色），Python负责调度和状态管理。

## 常用命令

```bash
# 运行交互式Demo
python main.py

# 单次运行
python main.py --once "你的输入"

# 指定世界和NPC数据
python main.py --world cangyue --npcs cangyue_npcs

# 指定API密钥
python main.py --api-key sk-xxx

# 运行所有测试
python -m pytest tests/ -v

# 运行单个测试文件
python -m pytest tests/test_layers.py -v

# 运行单个测试方法
python -m pytest tests/test_layers.py::TestUpdateLayer::test_update_npc_observation -v

# 安装依赖
pip install -r requirements.txt
```

## 架构核心

### 固定数据流（不可改变顺序）

```
玩家输入
  ↓
EventLayer         → 产出【事件上下文表】{event_id, summary, scope, location, involved_npcs}
  ↓
PerceptionLayer    → 【分发闸门】用LLM判断每个NPC感知到什么，只分发相关部分
  ↓
UpdateLayer        → 将感知内容写入NPC的observation/pending_reason表（唯一写入口）
  ↓
ReasoningLayer     → 有pending的NPC用LLM推理，产出新知识和信念
  ↓
UpdateLayer        → 将推理结果写入knowledge/belief表（唯一写入口）
  ↓
ResponseLayer      → 每个NPC收到自己的perceived_summary，LLM决定是否回应
  ↓
DirectorLayer      → 整合所有回应为连贯叙事文本
  ↓
PropagationLayer   → 判断信息是否跨位置传播（纯Python）
```

### 关键约束

1. **UpdateLayer是唯一写入口**——所有NPC维护表（Memory/Knowledge/Belief/Observation/PendingReason/Relationship/Inventory/Goal/CurrentState）只能通过UpdateLayer修改。其他层只能读取，禁止直接写入。
2. **PerceptionLayer是信息闸门**——所有事件必须经过PerceptionLayer分发。每个NPC只拿到自己感知到的内容。后续层（Reasoning/Response）绝对不能拿到完整事件表。
3. **Python不做思考**——scope判断、感知判断、谁回应、怎么回应，全部交给LLM。Python只做：构建Prompt、调用LLM、解析JSON、组装Result。
4. **LLM输出必须JSON**——禁止Markdown、自然语言。
5. **每个Prompt只做一件事**——禁止一个Prompt同时推理+回应+更新。

### CurrentEvent — 跨tick上下文持久化

EventLayer维护一个`CurrentEvent`对象，在多次tick之间持久化：

- **Python只存不判**——只存储AI返回的上下文数据，不自行判断是否延续。
- **LLM决定更新**——每次tick，LLM判断上下文是否延续（`continue_context`）、话题是否变化、NPC是否加入/离开（`added_npc_ids`/`removed_npc_ids`）。
- **场景切换自动处理**——当玩家切换对话对象或地点时，LLM重置上下文（`continue_context: false`）。

Engine通过`self.event_layer.current_event`访问当前上下文，用于ResponseLayer判断对话目标和DirectorLayer过滤场景NPC。

### Engine跨tick状态

Engine维护以下跨tick持久状态，保证叙事连续性：

- **scene_memory** — `{位置: 上一次场景描述}`，DirectorLayer传入`previous_scene`做场景衔接。
- **dialogue_history** — `[{npc_id, npc_name, text, tick}, ...]`，ResponseLayer按NPC过滤后传给LLM作为对话上下文。
- **last_event_location** — 用于判断场景是否切换（位置变了则`previous_scene`为空）。

### 层设计模式

每个层是一个单文件（`src/layers/xxx_layer.py`），包含4个部分：

- **Result dataclass** — 产出什么数据（继承`BaseResult`）
- **Input Schema dataclass** — 接收什么数据
- **Parser类** — JSON → Python对象，纯转换无逻辑
- **Layer类** — 继承BaseLayer，实现`process()`和`layer_name`

BaseLayer定义在`src/layers/base.py`，所有层必须实现`process(input_data, context) -> LayerResult`。

### 需要LLM vs 纯Python的层

| 需要LLM | 纯Python |
|----------|----------|
| EventLayer, PerceptionLayer, ReasoningLayer, ResponseLayer, DirectorLayer | WorldLayer, WorldStateLayer, UpdateLayer, PropagationLayer |

### 数据组织

- `src/models/` — 纯数据结构定义（dataclass）。NPC、世界、事件、世界状态。`BaseResult`是所有层Result的基类。
- `data/worlds/` — JSON世界定义文件（如`cangyue.json`）。
- `data/npcs/` — JSON NPC初始数据文件（如`cangyue_npcs.json`）。
- `prompts/` — 5个Prompt模板（event/perception/reasoning/response/director）。使用`{variable_name}`占位符，由`render_prompt()`用`str.replace()`渲染（非`str.format()`，因此JSON示例中的`{}`无需转义）。修改Prompt无需改代码。
- `src/utils/data_loader.py` — `load_world()`、`load_npcs()`从JSON加载数据，`render_prompt()`加载并渲染Prompt模板。
- `src/utils/json_utils.py` — `clean_json()`提取LLM输出中的JSON（自动处理Markdown代码块包裹），`parse_json()`安全解析。

### Engine调度模式

`Engine.tick(player_input)`执行一轮完整流水线。关键设计：

- **MiniContext原则**——发给LLM的上下文最小化：`_get_npcs_summary()`只发id/name/role/location/emotion/pending_count，`_get_world_state_summary()`只发day/time/weather/location。
- **LLM注入**——各层通过`__post_init__`中的`_inject_llm_client()`注入LLM client。层间数据通过`context`字典传递NPC对象和感知结果。
- **Debug日志**——每轮tick自动写入`logs/tick_XXXX.json`，包含事件、感知、回应和最终叙事的完整记录。

### LLM集成

`src/core/deepseek_client.py` 继承 `src/core/llm_client.py` 的 `LLMClient`，使用OpenAI SDK对接DeepSeek API。`LLMClient.send_and_parse()` 自动调用 `json_utils.parse_json()` 解析响应。未来换其他LLM只需创建新的子类实现`send(prompt) -> LLMResponse`。
