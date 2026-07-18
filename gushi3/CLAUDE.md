# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目简介

"故事3"（gushi3）是一个 **LLM驱动的交互式叙事/游戏引擎**，让玩家在"霓虹暗流·都市异能档案"世界观中进行自由扮演。系统通过多层流水线处理玩家输入，逐步确定事件、过滤信息、分发感知。

## 架构总览：分层流水线

```
玩家输入 (N1) → 事件层 (N2) → 感知层 (N3) → NPC信息分发
```

每层由 **Schema（数据结构）** + **Process（处理逻辑，调用LLM）** 组成。

### N1 — 玩家输入层
- `Schema/N1_playerInput_sch.py` — `N1PlayerInput` 类，接收原始文本、生成时间戳和会话ID

### N2 — 事件层（Context Retrieval）
- **数据结构**: `Schema/N2_contextRetrieval_sch.py` — `N2ContextRetrievalLayer` 封装世界层+NPC列表+事件上下文
- **处理逻辑**: `common/N2_contextRetrieval.py` — `N2_CE_Process.judge()` 调用 LLM 判断是否为新事件、增删主动NPC和关联NPC
- **System Prompt**: `prompt/N2_contextRetrieval_sys.txt`

### N3 — 感知层（Information Filtering）
- **处理逻辑**: `common/N3_informationFiltering.py` — `N3_Info_Filtering.judge_for_npc()` 调用 LLM 判断每个NPC能感知到什么
- **System Prompt**: `prompt/N3_informationFiltering_sys.txt`

### 基础数据层 (Schema)

| 层级 | 文件 | 类 | 作用 |
|------|------|-----|------|
| B1 世界层 | `Schema/B1_worldState_sch.py` | `B1WorldLayer` | 世界规则、地点树形导航、位置NPC查找 |
| B3.1 单个NPC | `Schema/B3_1_npc_sch.py` | `B3NPCLayer` | NPC内部状态：记忆、信念、性格、目标、好感度、持有物等10张表 |
| B3 NPC列表 | `Schema/B3_npclist_sch.py` | `B3NPCListLayer` | NPC集合管理，提供增删查改和序列化 |
| B4 玩家层 | `Schema/B4_player_sch.py` | `B4PlayerLayer` | 玩家数据：状态、持有物、描述 |
| 上下文 | `Schema/Context_sch.py` | `ContextHistory` | 对话历史管理，"轮次号 \| 时间 \| 发言人：内容" 格式 |

## 数据流（主循环）

见 `src/test/N2_contextRetrieval.py`：

1. 玩家输入 → `resolve_move_markers()` 解析 `{移动}`/`/move` 标记
2. 添加上下文 → `event_context.add_context()`
3. `N2_CE_Process.judge()` → 判断是否新事件，更新 `event_npc` / `related_npc` 列表
4. `N3_Info_Filtering.judge_for_npc()` → LLM 过滤并分发信息给各NPC，含地段传播计算
5. 返回结果，进入下一轮循环

## 关键设计原则

- **LLM无状态** — LLMClient 不做任何上下文存储，调用方全权管理对话历史
- **NPC有丰富内部状态** — 每个NPC维护10张表，包括知识记忆、信念、性格、待解释事件、好感度等
- **信息局限** — N3层确保NPC只能感知到合理范围内的信息（依赖对话对象、交流方式、行为公开程度、位置关系）
- **地点树** — 地点以 `·` 分隔层级（如"华国·东南沿海市·旧城区·街边炒粉摊"），支持前缀匹配 NPC 位置查询

## 运行

### 启动交互测试
```bash
cd "E:/A.gushi/gushi3"
python src/test/N2_contextRetrieval.py
```

### 前置条件
- 配置 `common/base_cnf.py` 中的模型选择（默认 DeepSeek）
- 设置环境变量 `DEEPSEEK_API_KEY` 或 `MODELSCOPE_API_KEY`
- Python 3.14+（项目使用 3.14，注意 f-string 不带引号等新特性）

### 依赖
```bash
pip install requests
```

## 配置文件
- `common/base_cnf.py` — 路径、模型、日志等级等全局配置
- 日志文件输出到 `log/N2.log`、`log/N3.log` 等
- 日志引擎单例在 `common/log.py`，通过 `get_logger(name)` 获取日志代理

## 数据目录
- `data/B1_worldState/` — 世界状态 JSON（规则、地点树）
- `data/B3_npc/势力分组/` — NPC 数据，按势力分组存储为 JSON 数组
- `data/player/` — 玩家数据 JSON
- `data/*/` — 各层的数据结构说明 `.md` 文件
