# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**gushi4** —— LLM 驱动的多 NPC 交互式叙事引擎。玩家自由文本输入，引擎按层处理感知 → 角色 → 结算，输出小说式旁白并推动世界演进。

与前三版的定位差别（各版另有自己的 CLAUDE.md）：

| 版本 | 状态 | 一句话 |
|------|------|--------|
| gushi1 | 冻结 | 十层固定流水线，Engine 调度 |
| gushi2 | 冻结 | Node 架构，UpdateEngine 唯一写入口 |
| gushi3 | 冻结 | `N2→N9` + 导演层 |
| **gushi4** | **最新、唯一在维护** | 分层依据从"实体是谁"改成「信息性质」 |

⚠️ **四个目录是同一个项目的版本迭代，不是四个项目。** gushi1/2/3 是历史版本（冻结备查），**后续说的"gushi"不带版本号时都指 gushi4**；默认只在 `gushi4/` 下工作，不要在旧版本上改动。

gushi4 相对前三版的关键差别：**分层依据从"实体是谁"改成了「信息性质」**——这是整个设计的枢纽（见下）。关键路径 LLM 跳数 **3**（gushi3 是 4）。⚠️ 跳数是**假设不是结论**，按 `设计方案.md` §八由实验定。

设计依据在 `设计方案.md`（为什么这样做）· `地图格式.md`（`.map` 格式与术语）· `地图实施计划.md`（地图重做的阶段与验收）；README 写"怎么跑"。

**开发分支是 `game-engine`，不是 `master`**（会话开始时先 `git rev-parse --abbrev-ref HEAD` 确认）。

## 常用命令

前置：Python 3.10+、`DEEPSEEK_API_KEY`（或 `MODELSCOPE_API_KEY`）。依赖只有 `requests` + `flask`，缺了启动器会自己装。

```bash
python start.py                 # 网页版（默认 8765，端口通了自动开浏览器）
python start.py term            # 终端版
python start.py test            # 跑 9 个离线套件（不花 API 额度）
python start.py web 9000        # 网页版，指定端口
python start.py help
```

手动跑：`python main.py`（终端）· `python web/server.py --port 8765`（网页）。

### 测试

**跑单个测试 = 单独执行对应文件**（每个套件自带 `sys.path` 处理，可从项目根直接跑）：

```bash
python start.py test                     # 全部 9 个套件
python src/test/smoke_test.py            # 模块/配置/纯规则/信息域边界/存档往返
python src/test/map_edit_test.py         # 编辑器往返无损/改一条只动一行/写盘三查
python src/test/travel_test.py           # 沿路推进/落点在线上/方向不反/孤儿不卡人
python src/test/map_migration_test.py    # 与冻结的地点语义基线逐条对照（换格式的硬门）
```

`start.py test` 覆盖的 9 个套件：`smoke_test` · `location_test` · `map_migration_test` · `map_edit_test` · `travel_test` · `opportunity_test` · `dry_run` · `background_test` · `ui_test`。（项数随套件改动而变，别照抄某个数字。）

**真实调用 API 的测试**（花钱花时间，需先起服务）：

```bash
python start.py                          # 先起服务，另开一个终端
python src/test/web_test.py              # 路由 + SSE 事件序列 + 编辑器接口
python src/test/run_scenario.py          # 8 轮验收剧本，约 6~9 分钟
python -u src/test/run_ablation.py --repeat 3   # 推理消融 AB（⚠️ repeat 必须 ≥3，n=1 时同配置两遍差 77%）
python    src/test/run_ablation.py --report-only  # 只从缓存出报告，不调 API
```

### 环境（本机实操）

- **API key 从注册表取**（机器级 HKLM 变量，App 进程早于它启动、继承不到；`reg query` 的 `/v` 会被 Git Bash 当路径吞掉，所以整表查完再 grep；**别打印值**，会进对话记录）：

  ```bash
  export DEEPSEEK_API_KEY="$(reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" | grep -i DEEPSEEK_API_KEY | sed 's/.*REG_SZ[[:space:]]*//' | tr -d '\r\n')"
  ```

- **Git Bash 下跑任何套件都要 `PYTHONIOENCODING=utf-8`**，否则断言里的 `✓/✗` 直接 `UnicodeEncodeError`——**看着像测试失败，其实是打印失败**。`start.py` 已给子进程设好。
- **别用 `curl -d '中文'` 手工测接口**：Git Bash 是 GBK，中文被转坏后服务端 JSON 解析失败，只回一句 `{"action":"empty"}`——和被测代码毫无关系的假故障。

## 架构

### 三个信息域（唯一的分层依据）

一个 LLM 调用只能属于一个域，**跨域合并一律非法**。边界由代码强制，不靠 LLM 自觉：

| 信息域 | 谁 | 看得到 | 能写 |
|--------|-----|--------|------|
| **世界域** | L1 | 事件 · 全员名册（名字+**真实**实力）· 玩家真值 · 幕后情报 | 失真感知 · 事件边界 · scope |
| **角色域** | L2 / B2 | **一个** NPC 的 11 张表 + **它自己那份**失真感知 | 8 张**主观**表 + 对白 + 意图 |
| **结算域** | L3 | 玩家**全量**实况 + 各 NPC 的**客观侧（2 表）** + 所有意图 | 2 张**客观**表 + 旁白 + 耗时 |

```python
L2.sanitize_npc_delta()        # 只放行 8 张主观表，客观表写了也丢
L3.sanitize_objective_delta()  # 只放行 2 张客观表，主观表写了也丢
```

**L3 是唯一的客观写入口**（D5/D12）：物品转移、战斗伤害、位置变化本质跨实体，从任何单方视角都不完整。所以 L2 只出 `动作意图`（"我打算给他"），L3 同时看双方持有物判定成立与否——**双方一起变或一起不变**；交易破裂时 L3 注入一条「破裂观察」，NPC 的反应下一轮由 L2 自然发生（复用"观察 → 感知"通道，零新机制）。

**失真感知**是核心机制：玩家实力是主观认知，每个观察者持有一份**失真**认知（低阶看高阶="气息深沉，看不透深浅"）。⚠️ **不给刻度**——不告诉 LLM "等级差大于 X 就看不透"，给了就成可反推的公式。

**属性/数值是"描述不是规则"**：`危险=高` **不等于**"某人一定受伤"；数值只记录结果、不参与叙事判断。**越级战斗必须始终可能**——这条和"不给刻度"一样是硬底线，别用规则去算胜负。

### 一轮的调用链

```
N0 消费后台产出 + 显示场景
N1 读输入 + 命令
── 快照（供 !! 回滚）──
L1 感知过滤                  ← 关键路径第 1 跳（开推理，见下）
L2 角色层（每 NPC 一次，并行） ← 第 2 跳
R1 节点传播（纯规则）
R4 机缘导演（纯规则，按出发地掷骰）→ 结果进 L3 输入
L3 结算层                    ← 第 3 跳
R2 推进时钟（纯规则，耗时由 L3 判）
心跳触发（后台线程，立即返回）
```

### 各层

| 层 | 文件 | LLM | 职责 |
|----|------|-----|------|
| L1 感知过滤 | `common/L1_perception.py` | 是 | 判断"谁听得到"，产出**失真**感知；持有玩家真值但只输出失真版 |
| L2 角色 | `common/L2_character.py` | 是 | 每 NPC 一次、并行（`ThreadPoolExecutor(max_workers=6)`）；只出主观增量 + 对白 + 意图 |
| L3 结算 | `common/L3_settlement.py` | 是 | 唯一客观写入口；出 `去向`（地名）+ `行程` + `本轮耗时分钟` + 旁白 |
| R1 传播 | `common/R1_propagation.py` | 否 | 节点传播；名字防幻觉 · 排除已亲身经历者 |
| R2 时间 | `common/R2_time.py` | 否 | **只推进时钟**（耗时由 L3 判） |
| R3 感知硬约束 | `common/R3_perception_filter.py` | 否 | 位置在不在场 / scope 可见性 / `involved` 必感知 |
| R4 机缘导演 | `common/R4_opportunity.py` | 否 | 按地点 `产出` 概率表掷骰。**只掷不写**，兑现走 L3 |
| B1a/B1b/B2 | `common/background.py` | 是 | 心跳后台：规则触发 / 环境叙事 / NPC 幕后 |

**S1 旁路已退役**（`common/S1_quick_judge.py` 保留备查）：移动耗时改由 L3 判断后它没有存在理由；`/s1` 现在只回一句说明。

**L1 开推理是经过实验的，别顺手关掉。** 阶段 1 消融（75 轮）结论：关 L1 省得最多（1.7×），但"看不透"式表达从 **92% 掉到 58%**——L1 的推理是承重的。见 `src/test/ablation_report.md`。

### 引擎与两个前端

`common/engine.py` 的 `GameEngine` 是唯一的流水线；终端（`main.py`）和网页（`web/server.py`）**共用同一份 `run_round()`**，不会各改各的漂移。`main.py` 只剩"读键盘 + 画事件"，引擎**自己从不调用 `input()`**。

所有输出去向统一成 `_emit(event)`：终端渲染器把事件还原成 `print`，网页渲染器塞 `queue.Queue` 推 SSE——**同一接口的两个实现**。事件类型：`scene` / `stage` / `dialogue` / `narration` / `dim` / `info` / `error` / `time` / `round_start`。

并发约定：写（`run_round()`）全程持 `self._lock`，一轮一次；只读视图**不加锁**——写回路径整体重绑定而非原地改，组装前先浅拷贝。

## 配置

**两处调参入口，分工明确：**

| | 在哪 | 管什么 |
|---|---|---|
| **程序级** | `common/base_cnf.py` | 路径 · 主模型 · 超时重试 · 日志等级 · 并行度 · `LAYER_MODEL_OVERRIDE` |
| **世界观级** | `data/B1_worldState/世界.map` 的 **`配置` 行** | 表上限 · 心跳间隔 · 机缘节奏 · `生命值体系` |

⚠️ **世界观配置的真源是 `世界.map`，不是 `global.json`**——地理与配置在阶段 0 一起搬走了；`global.json` 现在只剩四份字符串列表（世界规则/世界状态/演变规则/知识）。**改世界观配置不用重启**（编辑器保存后热重载）。新增配置项只需在世界配置里加键值对，代码用 `get_config(key, default)` 读、有兜底。

切换世界观改 `base_cnf.WORLD_DATA_SELECT`（改这个**要重启**，模块级常量）。（`base_cnf.py` 的注释里仍写着"真源是 global.json"，是文档漂移，以 `.map` 为准。）

**分层模型路由 + thinking 开关**（`base_cnf.LAYER_MODEL_OVERRIDE`，`LLMClient('层名')` 构造时查表，未命中回落主模型）：

- **所有层用同一个 DeepSeek 模型，区别只在开不开推理**——"极简模型"就是"同一个模型关掉思考"。推理 token 实测占输出 **67~96%**，按输出计费。
- `'S1': {'thinking': False, ...}` → 请求体塞 `payload['thinking'] = {'type': 'disabled'}`。
- 温度是"质量需求"，即便同一个模型也显式设：`B1a` 0.3（严谨因果链）· `B1b` 0.9（创意写作）· `B2` 0.6 · `MEM` 0.4。
- **实验钩子** `G4_THINKING_OFF=L1,L3`（或 `ALL`）在运行时覆写，不改文件——消融实验用，配置表仍是唯一事实来源。

## 地图系统

世界 = **节点**（能停留的**地点** / 一块**面**的**地区**）+ **路段**（只能穿过的通路，内部切**段**）。判据一句话：**能停下来的就是地点，只能穿过的就是路段**。`属于=` 画的是**节点树**（喂 L1/L3 的"节点属性链"），`路` 画的是**路段网**，两者是两张图，别混。

⚠️ **几何只给眼睛，语义只给 LLM**：节点坐标与路段 `路径线` **绝不进 L1/L3/R4 的输入**。项目已删过一轮"距离→结论"（R2 的段数折算、S1 旁路、R3 的距离过滤）。**不得**用几何距离算听不听得见 / 消息传多远 / 走多久 / 谁先动手。

**位置的"表示"归 Python，"判断"归 LLM**（D18）：位置存坐标（`当前状态CurrentState.位置 = [x,y]`），L3 吐的仍是**地名/去向**（以及语义进度 `行程`）。`gc.player_location` 是 property：坐标 → `locate()` → 地块名。历史上 L3 写自由文本位置（`青木山脉方向（离天道宗三日脚程）`）导致那轮**所有节点能力静默全失效**——这就是 `去向` 存在的原因。

**改 `.map` 只有一条路：`map_io.save_map`**。写盘**三查**，任一不过**一个字节都不写**：① `validate_edit` 结构 ② `emit → parse → flatten` 往返自检（含键序）③ 备份到 `_备份/`（不进版本库）。硬指标：**改一条属性 → `git diff` 只有那一行**（为此 `app.json.sort_keys = False` + 落盘前 `canonicalize` 保序对齐 + 改名留在原位）。

**「在路上」的判据是 `on_road_at()`，不是 `locate()['在路上']`**——自动布局的 `范围` 半径是画图用的（天道宗 r=104），整个走廊都在某个区域的圈里，`locate()` 那个字段**永远为假**。`locate()` 本身没动（13 个调用点 + 迁移基线依赖它）。

**编辑器的不变量：视图是快照，表单是活的。** `S.edit.view` 只在开编辑器时取一次，凡"表单改了"的地方都得问 `POST /api/map/preview`（只看不写，走保存同一条流水线 `map_io.prepare_edit` → `engine.map_view_of`）。级联规则（删地块连带删路、改名连带改引用）**只该有 Python 那一份**，别往前端加补丁。

**重画/重置地图**（`--drop-roads --drop-geo` → `--skeleton` → `--layout`，一次只跑一条，各自先自动备份）：

```bash
python -m common.map_io --drop-roads --drop-geo   # 清掉几何与路，留地点与配置
python -m common.map_io --skeleton                # 铺带 `自动生成=是` 的骨架路草稿
python -m common.map_io --layout                  # 铺坐标/范围
python src/test/map_migration_test.py --freeze    # 重冻地点语义基线
```

现在的坐标/范围/路大多是布局算法生成的**初值**，用户计划整体重置——所以**不要在现数据上做"修订"**（改的是一份马上要丢掉的数据）。

## 关键代码模式

- **分层 handler 模式**：`__init__` 建 `LLMClient('层名')` + logger → 读 `prompt/` 里的 system prompt → 组装上下文 → `cli.chat_by_context()` → `safe_json_loads()`。改 prompt 只需改 `prompt/*.txt`，不改代码。`data/prompt/*_custom/` 是按世界观的客制化资料（占位符注入）。
- **LLM 输出必须 JSON**：`safe_json_loads()` 四级降级（剥 markdown 代码块 → strict → 非 strict → 报错抛日志）。
- **增量更新**：`{"增加": {...}, "删减": {...}}`，`small_tool` 的 `apply_npc_delta` / `apply_player_delta` / `merge_delta` / `strip_empty_delta` 消费。
- **代码层防幻觉**：LLM 返回的 NPC 名走子串匹配校验，不存在的名字滤掉。
- **地点树遍历只有一个归一化点**：`B1WorldLayer.normalize_node()`。直接读原始节点的键会把 `_属性` 当成一个叫"_属性"的子地点渲染出来——`ui_test` 的 `total_nodes > 20` 就是这条的探测器。
- **B1 世界层**（`Schema/B1_worldState_sch.py`）：几何原语 `locate / in_range / nearest_place / on_road_at / road_between / route / road_point / segment_at / stop_in / is_region`，名字↔路径双射 `place_paths / name_of / chain_of_name`。

## 目录

```
gushi4/
├── main.py · start.py · start.bat        ← 入口与启动器（start.bat 只剩两行转发，见下）
├── common/                               ← 各层 + 引擎 + 基础设施
│   ├── engine.py            GameEngine：流水线 + 事件流 + 只读视图（循环体在这，不在 main.py）
│   ├── base_cnf.py          全部程序级配置
│   ├── game_controller.py   状态/存档/命令（输出走 on_output 接收器）
│   ├── map_io.py            .map ↔ 世界字典 + 编辑器写盘三查
│   ├── L1/L2/L3_*.py · R1~R4_*.py · S1_quick_judge.py(退役) · background.py
│   └── LLMclient.py · log.py · small_tool.py · game_clock.py · save_load.py
│       deferred_queue.py · memory.py · progress.py · debug_tools.py
├── Schema/                  B1_worldState · B3_1_npc(11表) · B3_npclist · B4_player · Context · Event
├── prompt/                 各层 system prompt（改这里不改代码）
├── web/                    server.py（Flask + SSE）+ static/（app.js / index.html / style.css）
├── data/
│   ├── B1_worldState/      世界.map（地理+配置的真源）· global.json（四份列表）· _备份/
│   ├── B3_npc/             按势力分文件 · player/ · prompt/*_custom/
├── src/test/               9 个离线套件 + web_test + run_scenario + run_ablation + scenarios/
├── 迁移基线/               冻结的地点语义基线（map_migration_test 对照用）
└── debug_rounds/ · log/ · save/
```

## 踩过的坑 / 纪律

- **`start.bat` 里一个非 ASCII 字符都不能有**：cmd.exe 按当前代码页逐字节解析批处理，UTF-8 中文会被解析错乱（注释行会被当命令执行）。启动器写成 Python 就是为了绕开这个。
- **`.map` 的行尾被钉死了**（仓库根的 `../.gitattributes` 里 `*.map text eol=lf`）——`.map` 永远 LF，`map_io` 写盘一律 `newline='\n'`。改这条规矩要一起改 `.gitattributes`。
- **热重载按"什么时候生效"分三类**，不笼统说"已保存并热重载"：立即生效（地点/路/几何/`NPC_上限_*`——上限是**类属性**，必须重跑 `apply_config_caps`）/ 需重启（`玩家初始数据`/`游戏开始时间`/`时间格式`，它们是**顶层键、不在 `世界配置` 里**）/ 一直成立（改 `产出` 或节点属性**不会重算场上 NPC 已有的记忆**）。
- **`SAVE_VERSION` 不轻易升**：能就地迁移的就迁移（如位置字符串 → 坐标），升了只会让本来能用的旧档报"可能不兼容"的**假警报**。真有不可迁移的变更时才升。
- **别用"版本"组织实验，用"配置"**——`G4_THINKING_OFF` 是运行时覆写，不是第二份配置。
- **`#` 注释解析后丢弃**；想留下来的写行尾 `|` 后面（地块叫 `描述`，路与段叫 `说明`），**别写成 `说明=某字`**（引擎只认行尾那个位置）。
- **拼错保留键 = 能力静默失效**（`属于` 写成 `上级`）。项目栽过同款：`NPC_上限_MAX_MEMORY` 对不上 `NPC_记忆上限`，七项配置从来没生效过。`map_io.RESERVED_KEYS` + `dead_reserved_keys()` / `key_notices()` 就是去核这件事的。
- **已知未决**：`设计方案.md` §八 **阶段 2（层数消融）未做**；`地图实施计划.md` 阶段 4（生成器）未开始；`地段` 一词在代码 / 提示词 / 测试里约 80 处还没改成 `节点`（占位符与 `data/prompt/L1_custom/` 的文件名配对，得一起改）。
