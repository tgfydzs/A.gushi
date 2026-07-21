# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**gushi3（故事3）** 是 gushi 系列的最新版本，是一个 **LLM驱动的多NPC交互式叙事/游戏引擎**。玩家通过自由文本输入（或命令），经过 N1→N9 九层流水线处理，最终输出叙事文本并推动世界演进。

核心设计理念：
- **LLM无状态** — LLMClient 不做任何上下文存储，调用方全权管理对话历史
- **Python负责调度和状态管理**，LLM只负责需要智能的部分（事件判断、感知分发、推理、回应、导演叙事）
- **信息局限** — 每层只给LLM它需要的最小上下文，NPC只能感知到合理范围内的信息

## 快速开始

```bash
# 设置环境变量
export DEEPSEEK_API_KEY=sk-xxx

# 启动交互式游戏
cd "E:/A.gushi/gushi3"
python src/test/N2_contextRetrieval.py

# 切换世界观配置（修改 common/base_cnf.py）
WORLD_DATA_SELECT = '苍云大陆·仙途诡道'  # 修仙世界
# 或
WORLD_DATA_SELECT = '霓虹暗流·都市异能档案'  # 现代异能都市
```

### 前置条件
- Python 3.14+（项目使用 3.14，注意 f-string 不带引号等新特性）
- 设置环境变量 `DEEPSEEK_API_KEY` 或 `MODELSCOPE_API_KEY`
- 依赖仅 `requests`：`pip install requests`

### 模型配置（common/base_cnf.py）
```python
name = 'deepseek'
# 或
name = '魔搭'  # 使用 ModelScope Qwen3.5-27B
```

## 架构总览：N1→N9 九层流水线

```
N1 玩家输入 ─→ N2 事件层 ─→ N3 感知层 ─→ [N4→N5→N6 并行] ─→ [N7+N8 并行] ─→ N9 导演层 ─→ 循环
                     ↑                                                                  │
                     └──────────────── 导演上下文(director_context) 持久化 ────────────────┘
```

### 每层职责速查

| 层 | 文件 | LLM? | 职责 |
|----|------|------|------|
| N1 输入 | `src/test/N2_contextRetrieval.py` | 否 | 命令处理(`/save /load /context /npcs /player`)、移动标记(`{移动}`)、撤回(`!!`) |
| N2 事件 | `common/N2_contextRetrieval.py` | 是 | 判断新事件/延续事件，增删 event_npc / related_npc |
| N3 感知 | `common/N3_informationFiltering.py` | 是 | 为每个NPC分发感知内容，产出地段传播数据 |
| N4 推理 | `common/N4_reasoning.py` | 是 | NPC更新待解释事件表 |
| N5 回应 | `common/N5_response.py` | 是 | NPC生成回应+内心想法(不接受玩家实例) |
| N6 更新 | `common/N6_update.py` | 是 | NPC收尾，唯一接受玩家实例的层，产出增量delta+旁白 |
| N7 传播 | `common/N7_propagation.py` | 否 | 纯规则：地段消息在地点树中逐级扩散为传闻 |
| N8 时间 | `common/N8_time.py` | 混合 | 规则(移动耗时)+LLM(对话估算)，推进 GameClock |
| N9 导演 | `common/N9_director.py` | 是 | 世界暗线推动、环境叙事、NPC幕后行为、剧情级传播 |

### 数据基础层

| 文件 | 类 | 作用 |
|------|-----|------|
| `Schema/B1_worldState_sch.py` | `B1WorldLayer` | 世界规则、地点树(以`·`分隔层级)、位置距离LCA计算、交互式导航 |
| `Schema/B3_1_npc_sch.py` | `B3NPCLayer` | 单个NPC内部状态：10张表（记忆/信念/性格/目标/好感度/当前状态/观察/持有物/待解释事件/更新成本） |
| `Schema/B3_npclist_sch.py` | `B3NPCListLayer` | NPC集合管理增删查改，`get_npc()` 子串匹配 |
| `Schema/B4_player_sch.py` | `B4PlayerLayer` | 玩家数据包装（字典，含名称/状态/持有物/描述）|
| `Schema/Context_sch.py` | `ContextHistory` | 对话历史管理，格式：`"轮次号 \| 时间 \| 发言人：内容"` |

### 三种上下文

| 上下文 | 变量 | 可见性 | 生命周期 |
|--------|------|--------|----------|
| 全局上下文 | `global_context` | 玩家可见(打印) + AI可见 | 持续累积 |
| 事件上下文 | `event_context` | 仅AI可见(LLM层) | 新事件时清空 |
| 导演上下文 | `director_context` | 仅AI可见(N2/N3/N9) | 持久，不随事件清空 |

### N4→N5→N6 并行流水线

每个NPC（包含事件NPC和潜在NPC）并行经过 N4→N5→N6，使用 `ThreadPoolExecutor(max_workers=6)`：

```
N3 分发信息
  ↓
submission per NPC:
  N4 reason_for_npc() → 推理，更新待解释事件表
  N5 response_say()   → 产出回应+内心想法（不接受玩家实例）
  N6 update_for_npc() → 收尾，产出增量delta + 旁白（唯一接受玩家实例的层）
  ↓
merge_delta() 合并多NPC的玩家增量 → apply_player_delta() 写回玩家
sync_npc_to_lists() 同步更新后的NPC到NPC列表
```

### N7+N8 并行

N7（传播层，纯规则零耗时）和 N8（时间层，可能有LLM调用）通过 `ThreadPoolExecutor(max_workers=2)` 并行。

### 数据驱动架构

所有可变的数值/名称/行为参数均从 `data/B1_worldState/global.json` 的世界配置（`世界配置` 节）读取：

| 配置项 | 代码中读取方式 | 默认值 |
|--------|--------------|--------|
| `NPC_附近距离_段` | `b1WorldLayer.get_config('NPC_附近距离_段', 3)` | 3 |
| `NPC_关联距离_段` | `b1WorldLayer.get_config('NPC_关联距离_段', 8)` | 8 |
| `事件_最小上下文长度` | `b1WorldLayer.get_config('事件_最小上下文长度', 2)` | 2 |
| `心跳间隔_小时` | `world_layer.get_config('心跳间隔_小时', 12)` | 12 |
| `NPC_观察上限` | B3NPCLayer.MAX_OBSERVATION (init时覆盖) | 20 |
| `NPC_记忆上限` | B3NPCLayer.MAX_MEMORY | 30 |
| `NPC_待推理上限` | B3NPCLayer.MAX_PENDING | 10 |
| `玩家初始数据` | `b1WorldLayer.get_config('玩家初始数据', 'data/player/default.json')` | 由世界配置决定 |

新增配置项只需在 global.json 的 `世界配置` 中添加键值对，代码中通过 `get_config(key, default)` 读取。
已有默认兜底，新增配置项不会破坏现有世界。

### N9 导演层（三路并行）

N9 拆分为三个独立的 LLM 调用，通过 `ThreadPoolExecutor(max_workers=3)` 并行执行，总耗时≈最慢子任务：

| 子任务 | prompt | 温度 | 职责 |
|--------|--------|------|------|
| **N9a** 规则触发 | `N9a_rule_sys.txt` | 0.3 | 世界演变规则触发判定，严格因果链推理 |
| **N9b** 环境叙事 | `N9b_narration_sys.txt` | 0.9 | 0-1条氛围旁白，纯创意写作 |
| **N9c** 行为+传播 | `N9c_behavior_sys.txt` | 0.6 | NPC幕后行为(0-3个) + 剧情级传播(0-2条) |

三路输出合并后，按可见性分层：
1. **规则触发** — 🟢玩家可见叙述(打印+global_context) + 🔴幕后详情(director_context) + 世界状态变更
2. **环境叙事** — 🟢灰字打印，主题追踪防重复
3. **NPC自主行为** — 🔴写入NPC.observation + director_context
4. **剧情级传播** — 🔴到达NPC.observation + director_context

包含代码层防幻觉：规则原文校验、NPC名称子串匹配、规则序号重复触发过滤。单路异常时返回安全空值，不中断整轮流程。

## 关键代码模式

### N层处理器模式
每个N层文件（如 `common/N4_reasoning.py`）遵循相同模式：
1. 构造函数 `__init__()` 创建 `LLMClient` 和 `logger`
2. 主方法（如 `reason_for_npc()`）读取 system prompt 文件，组装上下文，调用 `cli.chat_by_context()`，用 `safe_json_loads()` 解析JSON
3. 所有system prompt在 `prompt/` 目录，修改prompt无需改代码

### LLM调用模式
```python
# 读取system prompt
with open(os.path.join(BASE_PATH, r'prompt\N4_reasoning_sys.txt'), 'r') as f:
    sys_prompt = f.read()

# 组装上下文
all_context = [{'role': 'system', 'content': sys_prompt}]
all_context.append({'role': 'user', 'content': json.dumps(user_input, ensure_ascii=False)})

# 调用LLM
reply = self.cli.chat_by_context(all_context, label='N4')

# 解析JSON（自动剥离markdown代码块、降级解析）
result = safe_json_loads(reply, label='N4')
```

### 增量更新模式(N6层)
LLM输出 `{"增加": {字段: 值}, "删减": {字段: 值}}`，由 `small_tool.py` 的工具函数处理：
- `apply_npc_delta(npc, delta)` → 对NPC实例应用增量
- `apply_player_delta(player_dict, delta)` → 对玩家字典应用增量
- `merge_delta(target, source)` → 合并多个NPC的玩家增量
- `strip_empty_delta(delta)` → 过滤LLM产出的空值增量

### 代码层防幻觉
多处使用 `resolve_npc_name(partial, valid_names)` 进行3级NPC名称匹配（精确→子串→括号前纯名），过滤LLM返回的不存在NPC名。

### JSON安全解析
`safe_json_loads(text, label)` 自动处理：
1. 剥离 markdown 代码块（\`\`\`json ... \`\`\`）
2. strict 模式解析
3. 降级 strict=False 解析
4. 全部失败则记录日志并抛出

## 项目结构

```
gushi3/
├── src/test/N2_contextRetrieval.py    ← 主入口（交互式游戏主循环）
├── common/                            ← 各层处理逻辑
│   ├── LLMclient.py                   ← OpenAI兼容的LLM调用封装（无状态）
│   ├── N2_contextRetrieval.py         ← N2 事件层
│   ├── N3_informationFiltering.py     ← N3 感知层
│   ├── N4_reasoning.py                ← N4 推理层
│   ├── N5_response.py                 ← N5 回应层
│   ├── N6_update.py                   ← N6 更新层
│   ├── N7_propagation.py             ← N7 传播层（纯规则）
│   ├── N8_time.py                     ← N8 时间层
│   ├── N9_director.py                 ← N9 导演层（带状态：triggered_rules等）
│   ├── game_controller.py            ← GameController：状态管理/存档/命令处理
│   ├── game_clock.py                  ← GameClock：独立游戏时间（24h/时辰制）
│   ├── save_load.py                   ← SaveManager：存档/读档
│   ├── small_tool.py                  ← 工具函数（safe_json_loads/resolve_npc_name/增量操作）
│   ├── base_cnf.py                    ← 全局配置（路径/模型/日志等级）
│   ├── time_int.py                    ← 时间戳工具
│   ├── uuid.py                        ← UUID生成
│   └── log.py                         ← 日志引擎（单例，按session分文件）
├── Schema/                            ← 数据结构定义
│   ├── B1_worldState_sch.py          ← 世界层（规则/地点树/导航/距离计算）
│   ├── B3_1_npc_sch.py               ← 单个NPC（10张表）
│   ├── B3_npclist_sch.py             ← NPC列表管理
│   ├── B4_player_sch.py              ← 玩家层
│   ├── Context_sch.py                ← 对话历史
│   ├── N1_playerInput_sch.py         ← 玩家输入结构
│   └── N2_contextRetrieval_sch.py    ← N2上下文结构
├── prompt/                            ← System Prompt（txt文本，修改无需改代码）
│   ├── N2_contextRetrieval_sys.txt
│   ├── N3_informationFiltering_sys.txt
│   ├── N4_reasoning_sys.txt
│   ├── N5_response_sys.txt
│   ├── N6_update_sys.txt
│   ├── N6_player_sys.txt
│   ├── N8_time_ability_sys.txt
│   ├── N9a_rule_sys.txt          ← 规则触发（低温度）
│   ├── N9b_narration_sys.txt     ← 环境叙事（高温度）
│   └── N9c_behavior_sys.txt      ← NPC行为+情报传播（中温度）
├── data/                              ← 世界和NPC数据（JSON）
│   ├── B1_worldState/global.json     ← 世界观配置（规则/地点树/起始时间）
│   ├── B3_npc/势力分组/              ← NPC数据，按势力分文件
│   └── player/                       ← 玩家初始数据
├── save/                              ← 运行时存档（每个存档=一个文件夹）
└── log/                               ← 运行时日志
```

## 游戏控制台命令

| 命令 | 作用 |
|------|------|
| `/save <名称>` | 存档 |
| `/save del <名称>` | 删除存档 |
| `/load <名称>` | 读档 |
| `/saves` | 列出所有存档 |
| `/quick_save` / `/quick_load` | 快速存取 |
| `/context [global\|event\|director]` | 查看上下文 |
| `/npcs` | 查看NPC列表 |
| `/player [inv\|desc\|all]` | 查看玩家信息 |
| `!!` | 撤回上一条（全量状态回滚） |
| `{移动}` 或 `/move` | 移动标记，触发交互式位置导航。导航中：`[数字]`进入子节点，`[0]`手动输入自定义位置，`[c]`确认，`[b]`返回上级，`[x]`取消。叶节点可选`[c]`继续细化（创建更深一级的自定义位置）。任意深度 |
| `exit` | 退出 |

## 设计原则

1. **Python负责**：状态维护、调度、数据结构、Prompt管理、JSON解析
2. **LLM负责**：事件判断、感知分发、推理、回应、导演叙事
3. **LLM输出必须JSON**：使用 `safe_json_loads()` 自动处理Markdown代码块包裹
4. **信息隔离**：每层只给LLM最小上下文；event_context在新事件时清空；director_context不向玩家暴露
5. **代码层防幻觉**：所有LLM返回的NPC名、规则名、位置名都经过子串匹配校验
6. **增量输出**：N6层使用 `{"增加":..., "删减":...}` 增量格式，`strip_empty_delta()` 清理空值
7. **N6唯一接受玩家实例**：N4和N5不接受玩家数据，防止LLM越权修改玩家状态
8. **可撤回**：每轮执行前全量快照，`!!` 可回滚

## 版本关系

- **gushi1** — 初始版本，10层固定流水线，Engine类调度，使用 formal dataclass
- **gushi2** — 重构为Node架构，引入UpdateEngine作为唯一写入口，Schema下按数据模型分层
- **gushi3（当前）** — 简化设计，N2→N9 直接调用，GameController管理状态，增量delta更新模式，N9导演层带世界规则触发
