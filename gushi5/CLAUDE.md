# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 这是什么

**gushi5** —— LLM 驱动的多 NPC 交互式叙事引擎。玩家自由文本输入，引擎按 N1→N9 九层流水线处理
感知 → 角色 → 结算，输出小说式旁白并推动世界演进。

**它是"gushi3 的架构 + gushi4 的地图"**：

| 来源 | 拿的是什么 |
|------|-----------|
| **gushi3** | 整套分层与判据（N2 事件 / N3 感知 / N45 回应 / N6 结算 / N7 传播 / N8 耗时 / N9 导演）、`GameController`、存档、`{移动}` 交互式导航 |
| **gushi4** | **地图系统**（`.map` 格式、`map_io`、写盘三查、坐标是真源）、**LLM 客户端**（分层模型路由 + `thinking` 开关）、`web/` 前端与编辑器、`on_output` 输出接收器、位置原语（`set_position` / `migrate_positions`） |

⚠️ **三个目录是同一个项目的版本迭代，不是三个项目。** gushi1/gushi2/gushi3 是历史版本（冻结备查），
gushi4 是被取用的**素材来源**，gushi5 是**唯一在维护**的那个。

**为什么从 gushi3 起手**：gushi3 的每一层判据是**跑通过、可靠性已知**的；gushi4 按"信息性质"
重划了层（三域、失真感知、3 跳），信息架构更漂亮，但审计发现**十条确定性规则被删掉后没有替代者**
（见 `../gushi4/移植缺口.md`）。所以这一版的选择是：**用 gushi3 的地基，把 gushi4 那两件真的好东西
（地图、LLM 客户端）搬过来**，而不是反过来。

**开发分支**：会话开始时先 `git rev-parse --abbrev-ref HEAD` 确认。

## 常用命令

```bash
python start.py                 # 网页版（默认 8765，通了自动开浏览器）
python start.py web 9000        # 指定端口
python start.py term            # 终端版
python start.py test            # 跑离线套件（不花 API 额度）
python start.py help

python main.py                  # 终端版（等价 start.py term）
python web/server.py --port 8765  # 网页版（等价 start.py web）
```

### 测试

**跑单个测试 = 单独执行对应文件**（每个套件自带 `sys.path` 处理，可从项目根直接跑）：

```bash
python start.py test                      # 两个离线套件
python src/test/map_edit_test.py          # 编辑器往返无损 / 改一条只动一行 / 写盘三查 / 前端语法
python src/test/map_migration_test.py     # 与冻结的地点语义基线逐条对照（换格式的硬门）
python src/test/map_migration_test.py --freeze   # 有意改了世界内容后重冻基线
```

⚠️ 这两个是**从 gushi4 搬来的地图套件**；gushi3 原有的其余套件（`smoke_test` / `location_test` /
`travel_test` / `opportunity_test` / `dry_run` / `background_test` / `ui_test`）**还没搬**，
它们依赖 gushi4 的 `GameEngine`/`L1`/`L3` 或旧的主循环形状。搬一个算一个，别照抄文件名。

### 环境（本机实操）

- **API key 从注册表取**（机器级 HKLM 变量，App 进程早于它启动、继承不到；`reg query` 的 `/v` 会被
  Git Bash 当路径吞掉，所以整表查完再 grep；**值不要打印**，会进对话记录）：

  ```bash
  export DEEPSEEK_API_KEY="$(reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" | grep -i DEEPSEEK_API_KEY | sed 's/.*REG_SZ[[:space:]]*//' | tr -d '\r\n')"
  ```

- **Git Bash 下跑任何东西都要 `PYTHONIOENCODING=utf-8`**，否则 `✓/✗/📍` 直接 `UnicodeEncodeError`
  ——**看着像程序崩了，其实是打印崩了**。`start.py` 已给子进程设好。
- **别用 `curl -d '中文'` 手工测接口**：Git Bash 是 GBK，中文被转坏后服务端 JSON 解析失败，
  只回一句 `{"action":"empty"}`——和被测代码毫无关系的假故障。要发中文就从前端发，或让 Python 发。

## 架构

### 九层流水线（关键路径 4 跳）

```
N0 消费后台产出 + 显示场景
N1 读输入 + 命令
── 快照（供 !! 回滚）──
N2 事件层            ← 关键路径第 1 跳（LLM）
N3 感知层            ← 第 2 跳（LLM）
N45→N6 并行 per NPC  ← 第 3/4 跳（LLM；两者同一线程池并行）
N7 地段传播（纯规则，在移动落盘**之前**、按出发地判定）
玩家自主行动（仅无 NPC 时；该层自行估算耗时）
N8 耗时聚合（纯规则）→ 推进时钟 → 移动落盘
心跳触发（后台线程，立即返回）
```

串行长度 ≈ `N2 + N3 + max(N45) + max(N6)`——**不是四层相加**，N45/N6 是 per-NPC 并行的。

| 层 | 文件 | LLM | 职责 |
|----|------|-----|------|
| N2 事件 | `common/N2_contextRetrieval.py` | 是 | 判新事件/延续 · 增删 `event_npc`/`related_npc` · 判移动方式与声明耗时（供 N8 折算） |
| N3 感知 | `common/N3_informationFiltering.py` | 是 | 为每个 NPC 分发感知内容 · 产出地段传播数据 |
| N45 理解回应 | `common/N45_reason_response.py` | 是 | 推理待解释事件 → 据此生成回应 + 内心想法（原 N4+N5 合并） |
| N6 更新 | `common/N6_update.py` | 是 | NPC 收尾 / 玩家自主行动。**唯一接受玩家实例的层**；出增量 + 旁白 + `本轮耗时分钟` |
| N7 传播 | `common/N7_propagation.py` | 否 | 纯规则：地段消息沿地点树扩散成传闻 |
| N8 时间 | `common/N8_time.py` | 否 | 纯规则聚合：`活动耗时(max) + 移动耗时(段数×系数)`，推进 `GameClock` |
| N9 导演 | `common/N9_director.py` + `common/heartbeat.py` | 是 | 后台三路：N9a 规则触发 / N9b 环境叙事 / N9c 幕后行为+剧情级传播 |

**信息隔离**（硬约束，看着改）：
- `N2/N3` **不接受玩家实例**——只拿玩家的名称/位置/实力。合 N2+N3 是为了减一跳（跳数 4），
  但**N5+N6 不能合**：那会把 `玩家实例` 喂给生成对白的那一层。
- `N45` 的越权增量经 `_sanitize_npc_delta()` **只放行 `待解释事件PendingReasoning`**，其余字段丢弃。
- **N6 是唯一接受玩家实例的层**。NPC 只能表达意图，落实由 N6 判（它同时看得到双方）。

### 引擎与两个前端

`common/engine.py` 的 `GameEngine` 是**一轮流水线的唯一实现**；终端（`main.py`）和网页
（`web/server.py`）**共用同一份 `run_round()`**，各写各的渲染器，不会两边各改各的漂移。

```
engine.attach(terminal_renderer)   ← 终端：把事件还原成 print
engine.subscribe(queue)            ← 网页：把事件塞进 Queue 推 SSE
```

**这是同一接口的两个实现**。事件类型：`scene` / `dialogue` / `narration` / `dim` / `info` / `error` /
`time` / `round_start` / `round_done` / `stage`。其中 `stage`（进度条）与 `round_done`/`round_queued`
是**瞬时事件**，不进叙事流历史。

⚠️ `main.py` **自己处理 `{移动}`**（导航要一问一答，见下），交给 `handle_input` 就晚了。
`engine.handle_input` 碰到标记会回一句"只有终端能做"并跳过——网页端不能在那里阻塞等输入。

**输出一律走 `self._out(kind, text)`，不许直接 `print`**：`GameController` 的命令回显（`/npcs`、
`/player`、存档提示）如果直接 print，网页端就看不到。`kind` 是**语义标签不是样式**，
ANSI 由 `common/progress.py` 的 `terminal_output` 加。

**并发约定**：写（`run_round`）全程持 `self._lock`，一轮一次；只读视图**不加锁**——
写回路径整体重绑定而非原地改，组装前先浅拷贝。

### 三种上下文

| 上下文 | 可见性 | 生命周期 |
|--------|--------|----------|
| `global_context` | 玩家可见（进叙事流） | 持续累积，仿 F5 后靠它重建叙事流 |
| `event_context` | 仅 AI | **新事件时清空**（`new_context()`） |
| `director_context` | 仅 AI（N9） | 持久，不随事件清空 |

⚠️ **散场 = 清名单 + 清事件上下文，两件一起做。** 只清一半（只 `new_list()` 不 `new_context()`）
就是"事件永不翻篇"：上下文一直连着，名单也一直连着——这是 gushi4 栽过的形状（见
`../gushi4/移植缺口.md` §1.2）。

## 地图系统

世界 = **节点**（能停留的**地点** / 一块**面**的**地区**）+ **路段**（只能穿过的通路，内部切**段**）。
判据一句话：**能停下来的就是地点，只能穿过的就是路段**。

⚠️ **地理与世界观配置的真源是 `data/B1_worldState/世界.map`**，不是 `global.json`。
JSON 里只剩四份"没有几何、也不常手改"的列表（世界规则/世界状态/演变规则/知识）。

⚠️ **几何只给眼睛，语义只给 LLM**：节点坐标与路段 `路径线` **绝不进任何 LLM 的输入**。
项目已删过一轮"距离→结论"（N8 的段数折算在 gushi4 被删、S1 旁路退役、R3 的距离过滤删除）。
**不得**用几何距离算听不听得见 / 消息传多远 / 谁先动手。

⚠️ **坐标是真源，`当前所处位置` 是派生**，唯一写入点是 `GameController.set_position()`
——它一次写两样，于是两样不可能各自漂。**绝不允许别处直接写 `当前所处位置`**：
那正是 2026-10-08 事故的形状（往真路径后面接话 → 当轮所有节点能力静默失效）。
载入时 `migrate_positions()` 把"路径字符串"形式的旧数据就地转成坐标（**幂等**）。

⚠️ **改 `.map` 只有一条路：`map_io.save_map`**。写盘**三查**，任一不过**一个字节都不写**：
① `validate_edit` 结构 ② `emit → parse → flatten` 往返自检（含键序）③ 备份到 `_备份/`（不进版本库）。
硬指标：**改一条属性 → `git diff` 只有那一行**。为此 `app.json.sort_keys = False`
+ 落盘前 `canonicalize` 保序对齐 + 改名留在原位。

**编辑器的不变量：视图是快照，表单是活的。** `S.edit.view` 只在开编辑器时取一次，凡"表单改了"
的地方都得问 `POST /api/map/preview`（只看不写，走保存同一条流水线 `map_io.prepare_edit`
→ `map_io.map_view_of`）。级联规则（删地块连带删路、改名连带改引用）**只该有 Python 那一份**，
别往前端加补丁。

**`map_view_of` 归位说明**：它原来住在 gushi4 的 `engine.py` 里，而它自己的 docstring 就写着
"只认一个世界层 + 玩家在哪，不碰引擎的其它部分"——是**放错地方的地图逻辑**。在 gushi5 里它
在 `common/map_io.py`，因为 gushi5 的引擎是另一套（N 层），而它跟层无关。

**重画/重置地图**（`--drop-roads --drop-geo` → `--skeleton` → `--layout`，一次只跑一条，各自先自动备份）：

```bash
python -m common.map_io --drop-roads --drop-geo   # 清掉几何与路，留地点与配置
python -m common.map_io --skeleton                # 铺带 `自动生成=是` 的骨架路草稿
python -m common.map_io --layout                  # 铺坐标/范围
python src/test/map_migration_test.py --freeze    # 重冻地点语义基线
```

## 配置

**两处调参入口，分工明确：**

| | 在哪 | 管什么 |
|---|---|---|
| **程序级** | `common/base_cnf.py` | 路径 · 主模型 · 超时重试 · 日志等级 · 并行度 · `LAYER_MODEL_OVERRIDE` |
| **世界观级** | `data/B1_worldState/世界.map` 的 **`配置` 行** | 表上限 · 心跳间隔 · 移动每段分钟 · 玩家初始数据 · 生命值体系 |

⚠️ **`世界配置` 的键名有新旧两套，别用错。** NPC 表上限的键是 `NPC_上限_<属性名>`
（如 `NPC_上限_MAX_MEMORY`），**不是** `NPC_记忆上限`。旧名与代码查的键**零交集**，
那几项**从来没生效过**——只因值恰好等于类默认值才没暴露。上限是**类属性**，改配置只把字典
盖一遍没用，必须重跑 `apply_config_caps()`（编辑器保存后的热重载也走它）。

**分层模型路由 + `thinking` 开关**（`common/base_cnf.LAYER_MODEL_OVERRIDE`，`LLMClient('层名')`
构造时查表，未命中回落主模型）：

- 值是个**字典**，可填 `thinking` / `model` / `temperature` / `timeout` / `max_retries`。
- `thinking=False` → 请求体里塞 `payload['thinking'] = {'type': 'disabled'}`。
  **推理 token 实测占输出的 67~96%，按输出计费**，关掉同时省延迟和账单。
- 层名只有这几个：**`N2` / `N3` / `N45` / `N6` / `N9` / `N9c`**。
- **实验钩子** `G5_THINKING_OFF=N2,N6`（或 `ALL`）在运行时覆写，不改文件——扫档位用，
  配置表仍是唯一事实来源。
- 推荐档位（依据 gushi4 阶段 1 消融，⚠️ **外推**：那是它把"事件+感知"合成一层跑出来的）：
  **`N3` 的推理是承重的**（关掉"看不透"式表达 92% → 58%），别关；其余层的关掉**未测出质量代价**。

## 关键代码模式

- **分层 handler 模式**：`__init__` 建 `LLMClient('层名')` + logger → 读 `prompt/` 里的 system prompt
  → 组装上下文 → `cli.chat_by_context()` → `safe_json_loads()`。改 prompt 只需改 `prompt/*.txt`。
- **LLM 输出必须 JSON**：`safe_json_loads()` 四级降级（剥 markdown 代码块 → strict → 截断到末括号
  → `strict=False`）。
- **增量更新**：`{"增加": {...}, "删减": {...}}`，`small_tool` 的 `apply_npc_delta` /
  `apply_player_delta` / `merge_delta` 消费（N45/N6 的玩家增量在 `merge_delta` 处合并）。
- **代码层防幻觉**：LLM 返回的 NPC 名走子串匹配校验，不存在的名字滤掉（`get_npc()`）。
- **`{移动}` / `/move` 交互式导航**（**仅终端**）：`[数字]` 下钻 / `[0]` 自定义 / `[c]` 确认 /
  `[b]` 返回 / `[x]` 取消，任意深度。它由 `main.py` 在 `handle_input` **之前**处理。

## 目录

```
gushi5/
├── main.py · start.py · start.bat     ← 终端入口 / 启动器 / 转发（.bat 见下）
├── common/                            ← 各层 + 引擎 + 基础设施
│   ├── engine.py            GameEngine：流水线 + 事件流 + 只读视图（循环体在这，不在 main.py）
│   ├── game_controller.py   状态 / 存档 / 命令 / 位置原语（输出走 on_output 接收器）
│   ├── map_io.py            .map ↔ 世界字典 + 编辑器写盘三查 + 地图视图 + 布局动词
│   ├── progress.py          终端输出接收器 + 阶段进度 + 终端渲染器
│   ├── base_cnf.py          全部程序级配置
│   ├── N2/N3/N45/N6/N7/N8/N9_*.py · heartbeat.py · deferred_queue.py
│   └── LLMclient.py · log.py · small_tool.py · game_clock.py · save_load.py · debug_tools.py
├── Schema/                  B1_worldState · B3_1_npc(11表) · B3_npclist · B4_player · Context · N1/N2
├── prompt/                  各层 system prompt（改这里不改代码）
├── web/                     server.py（Flask + SSE）+ static/（app.js / index.html / style.css）
├── data/
│   ├── B1_worldState/       世界.map（地理+配置的真源）· global.json（四份列表）· _备份/
│   ├── B3_npc/              按势力分文件 · player/
│   └── data_template/ · data_异能都市/   备用世界观数据（代码里还没人用）
├── src/test/                离线套件（目前只有地图那两个）+ N2_contextRetrieval.py（转发桩）
├── 迁移基线/                冻结的地点语义基线（map_migration_test 对照用）
└── log/ · save/
```

## 踩过的坑 / 纪律

- **`start.bat` 里一个非 ASCII 字符都不能有**：cmd.exe 按当前代码页逐字节解析批处理，
  UTF-8 中文会被解析错乱（注释行会被当命令执行）。所以那个 .bat 只有两行转发。
- **`.map` 的行尾被钉死了**（仓库根的 `../.gitattributes` 里 `*.map text eol=lf`）——`.map` 永远
  LF，`map_io` 写盘一律 `newline='\n'`。**手工改 `.map` 要按字节改**，别让编辑器把行尾折成 CRLF：
  一去一回就是"255 行全被重写"，而"改一条属性只动一行"是花了两道防线换来的硬指标。
- **用 `ast` 批量改代码时，`col_offset` 是 UTF-8 字节偏移**，不是字符偏移。含 emoji 的行
  按字符算偏移会整片错位、把行焊在一起（本项目栽过：56 处 `print` 的改写里坏了两行）。
  要么全程按字节算，要么别用 ast 做偏移替换。
- **源码行尾是混合的**：`.map` 是 LF，`.py` 是 CRLF（gushi3 的字节拷贝沿袭而来）。
  做文本处理时先 `open(..., newline='')`，否则一次读写就把整个文件的行尾改掉，
  造出"看不出原因的 N 百行改动"。
- **注释里的 `#` 会被解析后丢弃**；想留下来的写行尾 `|` 后面（地块叫 `描述`，路与段叫 `说明`），
  **别写成 `说明=某字`**（引擎只认行尾那个位置）。
- **拼错保留键 = 能力静默失效**（`属于` 写成 `上级`）。`map_io.RESERVED_KEYS` +
  `dead_reserved_keys()` / `key_notices()` 就是去核这件事的。⚠️ `dead_reserved_keys()`
  **只在测试里跑**，运行时/编辑器不报——手改 `.map` 时当场看不到"写了没人读的键"。
- **`SAVE_VERSION` 不轻易升**：能就地迁移的就迁移，升了只会让本来能用的旧档报"可能不兼容"的**假警报**。
- **存档写在后台线程里**（daemon），进程一退没写完就丢——`GameEngine.close()` 会兜底等落盘，
  别绕过它直接 `sys.exit`。
- **浏览器缓存会骗人**：改了前端看不到效果时，先看顶栏的**构建号**（`app.js` 的 `BUILD`）——
  改了前端就把它改掉，否则"改了没生效"和"没改对"分不清。

## 已知未决

1. **机缘导演（R4）没搬**。地图数据里有 `产出`（机缘概率表），而 gushi4 的消费者
   `common/R4_opportunity.py` 不在。所以 `map_edit_test` 里那条死键断言是**点名待接**的
   （`PENDING_NO_CONSUMER = {'产出'}`）——不是删断言假绿，**出现新的死键照样会失败**。
2. **gushi3 原有的测试套件没搬**（`smoke_test` / `location_test` / `travel_test` / `ui_test` …）。
   它们依赖 gushi4 的 `GameEngine`/`L1`/`L3` 或旧主循环形状，得逐个改造。
3. **前端是从 gushi4 整份搬来的**，契约相同所以能用；但 `stage` 事件的**秒数**在 gushi5 是
   `progress.Stage` 现算的（每秒 tick），而 gushi4 那份有更细的分段计时。
4. **`gushi3/data/player/剑无锋.json` 是坏 JSON**（第 21 行 `"纯粹"` 引号没转义）——
   gushi5 已经换成 gushi4 那份修好的。gushi3 里选剑无锋当玩家仍会当场崩。
5. **单 NPC 场景下 N2 若不加人，N3 就没有分发对象**（N3 只在 `event_npc`/`related_npc`
   两份名单里分发，不看"附近"）——这是 gushi3 的既有形状，不是搬过来的 bug，但值得知道：
   玩家在场有人时说了句不指向任何人的话，那人不会反应。
