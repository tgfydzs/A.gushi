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
python start.py test                      # 六个离线套件
python src/test/map_edit_test.py          # 编辑器往返无损 / 改一条只动一行 / 写盘三查 / 前端语法
python src/test/map_migration_test.py     # 与冻结的地点语义基线逐条对照（换格式的硬门）
python src/test/map_migration_test.py --freeze   # 有意改了世界内容后重冻基线
python src/test/clock_calendar_test.py    # 历法：时辰制行为不变 + 换数据换纪年（公历风/干支/年号）
python src/test/time_skip_test.py         # 时间跳过换算 / N8 取 max / 大跳结清在途 / 心跳兜底
python src/test/task_store_test.py        # 任务表状态机 / 期限逾期 / 预设触发 / 通道兼容 / 存档往返
python src/test/place_resolve_test.py     # 地名解析：精确/全路径/末段/段/子串，歧义与编造一律拒绝
```

⚠️ 前两个是**从 gushi4 搬来的地图套件**；后四个是时间和任务改造、地名容错时补的，**要构造
`GameController`（会读 `base_cnf` 的 API_KEY）**，所以它们自带护栏：没 key 就打一行 `SKIP`
并 `exit 0`——缺外部依赖不算失败。

⚠️ `map_migration_test` 的扫描根**只含 `data/`**，不含 `save/`：存档是运行期产物，把它纳入基线
会让这个套件"每存一次档就红一次"（实测一天两次存档贡献 60 处假差异）。存档里的位置引用由
读档路径自己保证（`migrate_positions` 幂等归一）。

⚠️ gushi3 原有的其余套件（`smoke_test` / `location_test` / `travel_test` / `opportunity_test` /
`dry_run` / `background_test` / `ui_test`）**还没搬**，它们依赖 gushi4 的 `GameEngine`/`L1`/`L3`
或旧主循环格式，得逐个改造。搬一个算一个，别照抄文件名。

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
| N2 事件 | `common/N2_contextRetrieval.py` | 是 | 判新事件/延续 · 增删 `event_npc`/`related_npc` · 判去向/行程与移动耗时（`move_declared_minutes` 玩家明说 / `move_minutes` 它自己判） |
| N3 感知 | `common/N3_informationFiltering.py` | 是 | 为每个 NPC 分发感知内容 · 产出地段传播数据 |
| N45 理解回应 | `common/N45_reason_response.py` | 是 | 推理待解释事件 → 据此生成回应 + 内心想法（原 N4+N5 合并） |
| N6 更新 | `common/N6_update.py` | 是 | NPC 收尾 / 玩家自主行动。**唯一接受玩家实例的层**；出增量 + 旁白 + `本轮耗时分钟` |
| N7 传播 | `common/N7_propagation.py` | 否 | 纯规则：地段消息沿地点树扩散成传闻 |
| N8 时间 | `common/N8_time.py` | 否 | 纯规则聚合：`活动耗时(max) + 移动耗时(声明 > 判定 > 段数兜底)`，再与**时间跳过**取 max，推进 `GameClock` |
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
JSON 里只剩五份"没有几何、也不常手改"的列表（世界规则/世界状态/演变规则/知识/预设任务）。

⚠️ **几何只给眼睛，语义只给 LLM**：节点坐标与路段 `路径线` **绝不进任何 LLM 的输入**。
项目已删过一轮"距离→结论"（N8 的段数折算在 gushi4 被删、S1 旁路退役、R3 的距离过滤删除）。
**不得**用几何距离算听不听得见 / 消息传多远 / 谁先动手。

⚠️ **坐标是真源，`当前所处位置` 是派生**，唯一写入点是 `GameController.set_position()`
——它一次写两样，于是两样不可能各自漂。**绝不允许别处直接写 `当前所处位置`**：
那正是 2026-10-08 事故的形状（往真路径后面接话 → 当轮所有节点能力静默失效）。
载入时 `migrate_positions()` 把"路径字符串"形式的旧数据就地转成坐标（**幂等**）。

⚠️ **移动的唯一通道是「去向」+ 代码落坐标**（从 gushi4 搬过来的那一套）：
玩家用自由文本说去哪，N2 只能从输入里的「已知地点」挑一个**地名**填 `move_to`
（自己编的"天道宗上空"解析不出 → 位置不动 + WARNING），落坐标由 `move_player_to()`
→ `advance_travel()` 做。`行程`（`刚上路/半路/将到/到了`）决定走到整段路程的几成
——`frac` 按**整段**算且落点走 `set_position_on_road()`，所以"半路"能真的落在路上
（"在路上"由坐标 + `on_road_at()` 派生，不另存字段）。
**位置字段按「谁」分两条口径，两条都只让 LLM 给语义、坐标一律代码落**：

| 谁 | 入口 | 实现 |
|----|------|------|
| **玩家** | N2 的 `move_to`（去向）+ `行程` | `move_player_to()` → `advance_travel()` |
| **NPC** | 增量里的 `去向` + `行程`（新写法）／ `当前所处位置`（旧写法，等价"到了"） | `apply_npc_grounded_delta()` → `advance_travel()` / `set_position()` |

- 玩家增量里写的 `位置` / `当前所处位置` / `行进` 一律被 `strip_position_keys()` 丢弃并记 WARNING
  ——**玩家的去向只有一个权威来源（N2）**；`N6_player_sys` 里明写了不许写。
- NPC 没有 N2 那样的通道，所以它的位置入口就是增量本身：`N6` 的 NPC 收尾与 `N9c` 的幕后行动
  都走 `apply_npc_grounded_delta()`（解析不出地图里的真地名 → **原地不动** + WARNING）。
  普通 NPC 没有主动走位的必要；真正会走位的是**在办差事的**和 N9c 那些推动暗线的幕后行动。
- **任务（`gc.tasks`，`common/task_store.py`）**：**差事与任务的唯一真源**。
  N6 的回复里有独立的 `任务` 通道（`新建` / `更新` / `了结` / `失败`），`更新/了结/失败`
  **必须带 `id`**。已结束的任务**不 pop**，标状态 + 写 `结果` + 写一条 `世界状态` 归档。
  有未了结任务的 NPC 会被强制并进名单（`engine._with_task_npcs`，上限 2 个）——
  否则玩家派的差事永远停在"应命"那一步（实测白鹤连着四轮口头答应、位置一步没动）。
  ⚠️ **交付型每轮推，长期型每 `LONG_TASK_PUSH_INTERVAL`(6) 轮推一次**：长期任务
  （"外门两年"）不可能一轮走完，每轮拉进名单只是每轮白烧一组 N45+N6。
  ⚠️ `gc.npc_tasks` **不再是存储**，是 `tasks.npc_open_map()` 现算的派生视图
  （老调用点因此不用改），所以不存在"两份账对不上"。
- **在途进度（`gc.npc_travel`）只进不退**：LLM 看不到自己"在路上"（它拿到的位置永远退到
  最近地块），于是每轮都重新说一次"刚上路"，坐标原地打转（实测连接三轮）。
  `npc_move_toward()` 因此取 `max(它说的, 上一轮 + NPC_TRAVEL_STEP)`，
  并把"你现在的行程（朝 X 走，已经半路）"喂回输入——档位按**相对目的地**算，
  不用 `location_desc`（后者按路的哪一端报档，方向一反会把"刚上路"说成"将到"）。
- **随行（`gc.follow_map`）**：`跟随` 写 `"玩家"` 或某个 NPC 名，`同行` 写"我带着谁走"。
  被跟随者每次移动落盘，跟随者就被一起带过去（`_carry_followers`）——
  所以"白鹤把韩铁柱带回天道宗"是两步：白鹤 `去向: 天道宗`，韩铁柱已在 `follow_map` 里跟着他。
- **世界状态（公开事实）**：`gc.apply_world_state_delta()` 是 `世界状态列表` 的**唯一写入点**
  ——后台 N9a（演变规则触发）与前台 N6（本轮收尾）都走它。N6 的多输出里因此多了个
  `世界状态` 通道（**list 型**增删，**不是** `npc实例` 那种字段字典），每轮上限
  `MAX_WORLD_FACTS_PER_ROUND = 2`，同轮按值去重。
  这是"玩家的动作真的发生了"**唯一的落盘处**：`旁白` 只是给人读的文字，读完就过去了；
  落进账本的事实**下一轮会喂回 N45/N6**（输入键 `当前世界状态`）。少了这条通道，NPC 只能吃
  自己上一轮写的心情，于是永远停在"准备接"（实测连着几轮如此）。无 NPC 参与时走
  `N6-Player` 的同一条通道。
- **地名白名单（`gc.known_places()`）**：N2 一直有一份（`move_to` 只能从中挑），N6 **以前没有**
  ——于是它凭记忆拼路径（实测把 `天道宗·执法堂·天刑殿` 写成 `天道宗·天刑殿`）。
  现在 N6 的输入里也有这份全路径列表（`可用地点名（写位置/去向只能从这里原样复制）`）。
- **地名解析 `B1WorldLayer.resolve_place()`——容错，但只认唯一**：
  ⚠️ 只认一字不差是不行的：模型不会抄 `南疆凡人国·云梦国`，它会写「云梦国」（人话就这么说），
  于是"我要去云梦国"被判定"不在已知地点里"，**位置不动**（实测日志）。
  现在的匹配顺序：**精确 → 全路径 → 末段 → 任一段 → 子串**，每层**只有唯一命中才采纳**。
  - `云梦国` → `南疆凡人国·云梦国`（末段）、`古藤废墟` → `荒废传送阵·古藤废墟`（末段）、
    `王宫` → `王宫·承天殿`（段）
  - `云梦` → **拒绝**并回报歧义候选（同时命中 云梦国 / 渔火渡 / 云梦城）——宁可不动，也不猜
  - `天道宗上空`、`南面三里`、`幽冥界` → 拒绝：方位表达式与编造地名都不是能落脚的地块
  - 非精确命中会记一条 INFO（`去向「云梦国」→「南疆凡人国·云梦国」（末段匹配）`），可复查
  ⚠️ 两条移动入口（玩家 `move_player_to` / NPC `npc_move_toward` / 大跳结清）都汇到
  `advance_travel`，容错**只在那里做一次**，别在外层再加一份。
- **在途即"没办完"**：`engine._with_task_npcs` 除了任务表，还强制并进**在途**的 NPC
  （`gc.npc_travel` 非空，上限同样 2 个）。任务表依赖模型写 `任务` 通道，而它并不总写
  （实测把"接下差事"写进 `世界状态` 却漏掉任务字段）；只靠任务表的话，玩家一不再点他，
  他就掉出锁定名单、走到半路再没人算。到了目的地 `npc_travel` 自然清空，就不再被强制。
- **移动落点失败/空转不清账**：`npc_move_toward` 先判"目的地 == 自己脚下"直接返回（模型常把
  `去向` 写成自己所在处配 `行程: 到了`），且**记账放在落点成功之后**——否则一次失败的移动
  就把"他走到哪了"抹掉，下一轮又从"刚上路"重来。
- 坐标（`位置`）与 `行进` 任何一层都不许写，写了就丢——**重演 2026-10-08 的那条路已经被堵死**。

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

## 时间

**时间的唯一真源是 `GameClock._elapsed`（已流逝分钟）**，年/月/日/时辰/刻全部由它派生
（`str_at(n)` 能对任意时刻求值，任务期限就算它的绝对值）。

⚠️ **历法必须是数据，不是代码**：这一层不许知道"时辰""仲夏""刻"是什么。
`GameClock` 只认四样东西（全从世界的 `历法` 块读）：`单位` 阶梯（每级 `进制` / `名表` /
`起点` / `循环名表` / `显示偏移` / `值为1时省略`）、`模板`（如
`"{纪元}{年}年·{月}·{日}日·{时辰}{刻}刻"`）、`数字`（中文 / 阿拉伯）、`夜间`（哪些名字算夜里）。
年/月/日/时/刻全部由 `elapsed_minutes` 派生，**不单独落盘**（存第二份就是开第二个真源）。

- **没写 `历法` 块就用内置的时辰制默认**（`_DEFAULT_CALENDAR`），所以老世界 / 老存档零改动。
- **换纪年 = 改数据**：公历风（`分/时/日/月/年` + 阿拉伯数字）、干支（年的 `循环名表` + 60 周期）、
  年号（年的 `显示偏移`，绝对 627 → 贞观元年）、自定义进制（一天三时段、一月四十五日）。
  `src/test/clock_calendar_test.py` 里有这三套的现存用例，加新历法照着写。
- ⚠️ 最底层那一级的 `进制` = **多少个分钟**（`刻` 是 30、`分` 是 1、只有「日」这级时要写 1440）。
  名表级的 `起点` = **下标 0 对应的显示值**（子时=0、孟春=1）；数字级默认 1。
- 起始点：`历法.起始` 优先，否则从 `游戏开始时间` 串按这份阶梯解析。解析不出年份 →
  显示降级成「前缀（N日后）」，原因写进 `calendar_error`，`init_clock` 会打一条 WARN。
- **真农历做不到**：月长 29/30 不定 + 闰月，需要万年历表 / 算法，不是阶梯能表达的；本层不假装支持。

⚠️ **"跳过时间"和"提及时间"是两回事，这是最容易做错的一处**：
- **跳过**（`时间判定: 跳过`）：时间**真的流逝**——"（一个月后）""等一年""修炼一年""闭关三年"。
  N2 出 `时间跨度 {"值":1,"单位":"月"}`，N8 换算成分钟，与"活动+移动"**取 max**（不叠加）。
- **提及**（`时间判定: 提及`）：只是说到某个时间点/期限——"一年后我要你做到""三天之内给我答复"。
  时钟一分钟都不动。
- 判据写在 `prompt/N2_contextRetrieval_sys.txt`，**拿不准一律按提及**；判定结果每轮都在
  `N2 结果` 那行日志里，可以逐轮复查。
- 通道选 N2 而不是 N6-Player：N6-Player **只在没有 NPC 时**才跑，而"一个月后"这类话
  绝大多数发生在有人在场的时候——实测就是这个原因让三轮跳时全丢了
  （日志里 `活动2分（NPC2个取大 / 玩家0分）`，叙事走了两个月零两年，时钟走了 7 分钟）。

⚠️ **大跨度跳过要结清在途**：`npc_travel` 的进度是"每轮 +0.34"，它假设时间**连续**。
跳过 ≥ 1 日时 `resolve_travel_on_skip()` 把所有在途记录按"已抵达"落点并清账——
否则"（一个月后）"只走一轮，白鹤会在路上"走"一个月。

⚠️ **心跳必须有轮次兜底**：`heartbeat.try_trigger` 原本只有"游戏时间推进 ≥ 心跳间隔"一个条件
（模块说明里那句"主循环每完成 3 轮"从来没实现过）。时辰制下一轮对话才 2~5 分钟，
一整档 93 轮只走了 213 分钟 → `⏰ 心跳#` **一条都没有**，**N9a/N9b/N9c 一次都没跑过**，
世界不会自己演进。现在加了 `心跳_最少轮次`（默认 8）：连续 8 轮没触发过就强制一次。

## 配置

**两处调参入口，分工明确：**

| | 在哪 | 管什么 |
|---|---|---|
| **程序级** | `common/base_cnf.py` | 路径 · 主模型 · 超时重试 · 日志等级 · 并行度 · `LAYER_MODEL_OVERRIDE` |
| **世界观级** | `data/B1_worldState/世界.map` 的 **`配置` 行** | 表上限 · 心跳间隔 · `心跳_最少轮次` · 移动每段分钟 · 玩家初始数据 · 生命值体系 |
| **世界观级（嵌套）** | `data/B1_worldState/global.json` 的 **`历法` 块** | 单位阶梯 · 名表 · 显示模板 · 数字写法 · 夜间定义（不写就用内置时辰制，见「时间」一节） |

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
  - 语义：**list** → 增加 extend / 删减逐条移除；**dict** → 增加 update 合并 / 删减逐 key pop；
    其它（str/数值）→ 增加覆盖、**删减无效**（只能覆盖）。
  - ⚠️ **NPC 的字段集是固定的 12 个键**（`B3NPCLayer.to_dict()`）。`apply_npc_delta` 重建时
    只认这些键，**字段名拼错就整条结算消失**——现在会先记一条 WARNING（`_log_report`）。
    玩家表**没有 schema**（`B4PlayerLayer` 只包原始 dict），未知键会被真的存进存档，也记 WARNING。
  - ⚠️ **删减是精确字符串匹配**。模型引用旧文本时稍有改写就一条都删不掉（实测如此），
    所以表通常"只增不减"、靠上限截断收场——现在未命中的条目会记 WARNING。
- **性格 / 好感度是 `{名: 数值}`，不是字符串列表**：`["定力：10"]` 那种写法在
  `B3NPCLayer.__init__` 里**就地转成 dict**（`normalize_numeric_table`，**幂等**，
  所以旧档旧数据不用单独迁移）。列表形态下"改值"必须同时删旧串+加新串，只加不删就会
  同时存在 `天道宗：10` 与 `天道宗：8` 两条矛盾记录——dict 之后改值就是覆盖同名键。
  这两张表按**字典键数**截断（`MAX_RELATIONSHIP` / `MAX_PERSONALITY`）。
- **可塑性 = `表更新成本UpdateCost`**（11 个数，顺序见 `COST_TABLE_ORDER`；**正数越大越难改**）。
  这个字段 2026-08 设计后一直**没有消费者**，2026-10-17 才接上，两个口子：
  ① `apply_npc_delta` 对**身份四表**（信念/性格/目标/好感度）限流——一次结算最多改
  `3 / 2 / 1` 条（成本 ≤3 / 4~6 / ≥7），超出的裁掉并记 INFO；
  ② N6 输入里的 `你的可塑性` 摘要，让模型按"这人有多顽固"来写。
  ⚠️ 之所以只掐身份四表：观察/记忆/待解释是工作台，一轮写三五条是常态，掐了只会削正常结算。
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
│   └── LLMclient.py · log.py · small_tool.py · game_clock.py · task_store.py · save_load.py · debug_tools.py
├── Schema/                  B1_worldState · B3_1_npc(11表) · B3_npclist · B4_player · Context · N1/N2
├── prompt/                  各层 system prompt（改这里不改代码）
├── web/                     server.py（Flask + SSE）+ static/（app.js / index.html / style.css）
├── data/
│   ├── B1_worldState/       世界.map（地理+配置的真源）· global.json（五份列表 + 可选 `历法` 块）· _备份/
│   ├── B3_npc/              按势力分文件 · player/
│   └── data_template/ · data_异能都市/   备用世界观数据（代码里还没人用）
├── src/test/                离线套件 6 个（地图 2 + 历法 / 时间跳过 / 任务 / 地名解析 4）+ N2_contextRetrieval.py（转发桩）
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
- **LLM 空正文要当"可重试失败"，不能让它打死整轮**：实测出现过
  `N3 完成 | 29.6s | out=6854 reason=6854`——6854 token **全在想、正文 0 token**（想了 2.3 万字
  一个字符都没吐）。此时 `safe_json_loads` 会直接抛出去，而各层都没接，**一次空回复就崩一轮**。
  现在 `LLMclient` 在取到正文后立即判空、抛 `EmptyReplyError` 并纳入 `_is_retryable`
  （走既有的 `max_retries` + 退避）。⚠️ 判空必须放在记"完成"**之前**，否则失败会被记成成功、
  把延迟统计和失败率一起污染（gushi3 栽过"把失败请求记成完成"）。
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
