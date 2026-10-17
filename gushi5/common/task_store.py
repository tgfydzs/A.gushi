"""
任务表 — 差事与任务的**唯一真源**。

为什么必须单独一张表（而不是继续塞在 `npc_tasks` 里）：

旧实现是 `npc_tasks = {NPC名: {目标, 去向, 行程}}`，把"任务"挂在了 NPC 的键上，
于是三件事一起错：
  1. **一个 NPC 只能有一条**——写新的直接覆盖旧的，旧的那条既没完成也没失败就消失了
     （实测：白鹤的"认出那个人"被"教导韩铁柱"顶掉，日志里一句"办完了"都没有）。
  2. **没有 id / 委托人 / 期限 / 状态 / 历史**，任务完成只 `pop` 一下就没了，
     世界账本上不留任何痕迹。
  3. **玩家自己接的任务没有落脚处**（玩家不是 NPC，进不了那张表的键）。

现在每个任务是一条**独立记录**：按 `id` 引用、按状态机流转、完成后**归档留痕**
（写一条世界状态），并且期限一到就自动判失败——"逾期"这才成为一个可能的结局。

状态机（单向，只进不退）：

    进行中 ──了结──> 已完成
       └────逾期/失败──> 已失败
       └────主动放弃──> 已放弃

没有"待接"这个状态：世界既然已经把任务摆出来了，它就是进行中的；少一个状态就少一条
谁都不写的死通道。
"""

from common.log import get_logger

logger = get_logger('TaskStore')

# ---- 状态 ----
ACTIVE = '进行中'
DONE = '已完成'
FAILED = '已失败'
ABANDONED = '已放弃'

_LIVE = (ACTIVE,)
_FINAL = (DONE, FAILED, ABANDONED)

# ---- 类型 ----
#: 一次交付型：动身 → 到 → 交付，需要**连续**推进，每轮都该进锁定名单。
KIND_DELIVER = '交付'
#: 长期挂机型：养成 / 守卫 / 潜伏。两年的时间不可能一轮走完，每轮推只会白烧 API。
KIND_LONG = '长期'
_KINDS = (KIND_DELIVER, KIND_LONG)


class TaskStore:
    """全世界的任务表。只认 `id`，不认"谁身上的第几条"。"""

    #: 进行中的任务上限（防 LLM 每轮新建一条把表撑爆）
    MAX_LIVE = 40
    #: 已结束任务的归档上限（超了从最老的开始丢）
    MAX_ARCHIVE = 60

    def __init__(self):
        self.tasks: list[dict] = []
        self._seq = 0

    # ===================== 新建 / 查询 =====================

    def _next_id(self) -> str:
        self._seq += 1
        return f'T{self._seq:03d}'

    def new(self, 目标: str, 执行者: str, 标题: str = '', 委托人: str = '',
            类型: str = KIND_DELIVER, 期限: dict = None, 进度: str = '',
            涉及地点: list = None, 涉及NPC: list = None, 完成条件: str = '',
            来源: str = '对话', 预设id: str = '', 登记时间: str = '',
            clock=None) -> dict | None:
        """
        新建一条任务。目标或执行者为空 → 拒绝（记 WARN，不许静默建出一条空任务）。

        :param 期限: `{"值":2,"单位":"年"}` 或 `{"类型":"绝对","时间":"太初历…"}`
        :param clock: GameClock，用来算绝对截止时间（不给就只记相对期限）
        """
        目标 = str(目标 or '').strip()
        执行者 = str(执行者 or '').strip()
        if not 目标 or not 执行者:
            logger.warn(f'任务新建被拒：目标或执行者为空（目标={目标!r} 执行者={执行者!r}）')
            return None

        # 同一执行者 + 同一目标 = 同一条。模型常把"新建"每轮重说一遍，
        # 不在这里挡的话，两轮就能长出三条一模一样的任务。
        for t in self.tasks:
            if (t.get('状态') == ACTIVE and t.get('执行者') == 执行者
                    and t.get('目标') == 目标):
                logger.info(f'📋 任务已存在（{t.get("id")}），不重复新建：{目标}')
                return t

        if len(self.open_tasks()) >= self.MAX_LIVE:
            logger.warn(f'任务新建被拒：进行中的任务已达上限 {self.MAX_LIVE} 条'
                        f'（目标「{目标}」执行者「{执行者}」）')
            return None

        类型 = 类型 if 类型 in _KINDS else KIND_DELIVER
        rec = {
            'id': self._next_id(),
            '标题': str(标题 or '').strip() or 目标[:14],
            '目标': 目标,
            '来源': 来源,
            '预设id': str(预设id or ''),
            '委托人': str(委托人 or '').strip(),
            '执行者': 执行者,
            '状态': ACTIVE,
            '类型': 类型,
            '登记时间': 登记时间,
            '期限': 期限 if isinstance(期限, dict) else {},
            '截止分钟': None,
            '截止时间': '',
            '进度': str(进度 or '').strip(),
            '涉及地点': [str(x) for x in (涉及地点 or [])],
            '涉及NPC': [str(x) for x in (涉及NPC or [])],
            '完成条件': str(完成条件 or '').strip(),
            '结果': '',
            '结算时间': '',
            '最近推进轮': 0,
        }
        self._set_deadline(rec, clock)
        self.tasks.append(rec)
        logger.info(f'📋 新任务 {rec["id"]}｜{rec["标题"]}｜执行者：{执行者}'
                    f'｜类型：{类型}｜委托人：{rec["委托人"] or "未记"}'
                    f'｜期限：{rec["截止时间"] or "无"}')
        return rec

    def _set_deadline(self, rec: dict, clock):
        """把 `期限` 解析成**绝对**截止时间（分钟 + 显示串）。"""
        spec = rec.get('期限') or {}
        kind = str(spec.get('类型') or ('时长' if spec.get('值') else '')).strip()

        if kind == '时长':
            if clock is None:
                logger.warn(f'任务 {rec["id"]} 有期限但没给时钟，只记相对期限、不自动判逾期')
                return
            minutes = clock.span_to_minutes(spec.get('值'), spec.get('单位', '日'))
            if not minutes:
                logger.warn(f'任务 {rec["id"]} 的期限「{spec}」换算不出分钟'
                            f'（单位只认 分钟/刻/时辰/日/周/月/年），只记录、不自动判逾期')
                return
            due = clock.elapsed_minutes + int(minutes)
            rec['截止分钟'] = due
            rec['截止时间'] = clock.str_at(due)
            return

        if kind == '绝对':
            rec['截止时间'] = str(spec.get('时间') or '').strip()
            logger.info(f'任务 {rec["id"]} 的期限是绝对时间「{rec["截止时间"]}」，'
                        f'只做展示、不自动判逾期（比较绝对时间串不可靠）')
            return

        if spec:
            logger.warn(f'任务 {rec["id"]} 的期限「{spec}」认不出类型，按无期限处理')

    def get(self, task_id: str) -> dict | None:
        key = str(task_id or '').strip().upper()
        for t in self.tasks:
            if str(t.get('id') or '').upper() == key:
                return t
        return None

    def open_tasks(self) -> list[dict]:
        return [t for t in self.tasks if t.get('状态') == ACTIVE]

    def owned(self, owner: str) -> list[dict]:
        key = str(owner or '').strip()
        return [t for t in self.open_tasks() if t.get('执行者') == key]

    def npc_open_map(self) -> dict:
        """
        `{执行者: 最早的一条进行中任务}`。

        兼容旧 `npc_tasks` 的两处用法（N6 输入的 `task` 参数、锁定名单），
        所以不用把那些调用点全改一遍——但**不再是第二份真源**，它是现算的。
        """
        out = {}
        for t in self.open_tasks():
            out.setdefault(t.get('执行者'), t)
        return out

    # ===================== 流转 =====================

    def update(self, task_id: str, 进度: str = None, **fields) -> bool:
        """更新进度（只对进行中的任务有效）。"""
        t = self.get(task_id)
        if t is None:
            logger.warn(f'任务更新未命中：找不到 {task_id}')
            return False
        if t.get('状态') != ACTIVE:
            logger.warn(f'任务 {t["id"]} 已经是「{t.get("状态")}」，不再更新进度')
            return False
        if 进度 is not None and str(进度).strip():
            old = t.get('进度') or ''
            t['进度'] = str(进度).strip()
            logger.info(f'📋 任务 {t["id"]} 进度：{old or "（空）"} → {t["进度"]}')
        for k, v in fields.items():
            if k in t and v is not None:
                t[k] = v
        return True

    def _close(self, t: dict, status: str, 结果: str, clock=None) -> dict:
        t['状态'] = status
        t['结果'] = str(结果 or '').strip()
        t['结算时间'] = clock.str_at(clock.elapsed_minutes) if clock else ''
        logger.info(f'📋 任务 {t["id"]}｜{t.get("标题")} → {status}'
                    f'（{t["结果"] or "无结论"}）')
        return t

    def finish(self, task_id: str, 结果: str = '', clock=None) -> dict | None:
        t = self.get(task_id)
        if t is None:
            logger.warn(f'任务了结未命中：找不到 {task_id}')
            return None
        if t.get('状态') != ACTIVE:
            logger.warn(f'任务 {t["id"]} 已经是「{t.get("状态")}」，重复了结被忽略')
            return None
        return self._close(t, DONE, 结果, clock)

    def fail(self, task_id: str, 原因: str = '', clock=None) -> dict | None:
        t = self.get(task_id)
        if t is None:
            logger.warn(f'任务失败未命中：找不到 {task_id}')
            return None
        if t.get('状态') != ACTIVE:
            logger.warn(f'任务 {t["id"]} 已经是「{t.get("状态")}」，重复标记被忽略')
            return None
        return self._close(t, FAILED, 原因, clock)

    def abandon(self, task_id: str, 原因: str = '', clock=None) -> dict | None:
        t = self.get(task_id)
        if t is None or t.get('状态') != ACTIVE:
            return None
        return self._close(t, ABANDONED, 原因, clock)

    def settle_expired(self, now_minutes: int, clock=None) -> list[dict]:
        """
        逾期结算：过了截止时间的进行中任务一律判失败。

        ⚠️ 只在时间**真的**流逝之后调（N8 推完时钟）。大跨度跳过时一次逾期一批
        是**正确的**——那两年确实过去了，任务确实没办成。
        """
        out = []
        for t in self.open_tasks():
            due = t.get('截止分钟')
            if not isinstance(due, (int, float)) or not due:
                continue
            if now_minutes >= due:
                out.append(self._close(
                    t, FAILED,
                    f'逾期：期限 {t.get("截止时间") or due}，届时未办成', clock))
        if out:
            logger.info(f'📋 逾期结算 {len(out)} 条：'
                        f'{", ".join(t["id"] for t in out)}')
        return out

    # ===================== 预设任务 =====================

    def sync_presets(self, presets: list, places: list, npc_present: list,
                     登记时间: str = '', clock=None) -> list[dict]:
        """
        按触发条件激活**预设任务**（世界自带的、不依赖对话产生的任务）。

        :param presets: `global.json` 的 `预设任务` 列表
        :param places: 玩家当前能代表位置的地名（短名与全路径都给）
        :param npc_present: 本轮在场的 NPC 名
        :return: 本次新激活的任务列表

        触发条件是**纯规则**的（地点 / 在场NPC / 前置任务），不花一次 LLM 调用。
        每次检查都记日志（含"为什么没触发"），否则这张预设表就是一张死表。
        """
        if not presets:
            return []
        place_set = {str(p) for p in (places or [])}
        npc_set = {str(n) for n in (npc_present or [])}
        done_ids = {str(t.get('预设id')) for t in self.tasks if t.get('状态') == DONE}
        activated = {str(t.get('预设id')) for t in self.tasks if t.get('预设id')}

        made, skipped = [], []
        for p in presets:
            if not isinstance(p, dict):
                continue
            pid = str(p.get('id') or '').strip()
            if not pid:
                logger.warn('预设任务缺少 id，跳过（没有 id 就没法幂等，会每轮重复激活）')
                continue
            if pid in activated:
                continue

            触发 = p.get('触发') if isinstance(p.get('触发'), dict) else {}
            want_places = [str(x) for x in (触发.get('地点') or [])]
            if want_places and not (set(want_places) & place_set):
                skipped.append(f'{pid}:地点不在{"/".join(want_places)}')
                continue
            want_npcs = [str(x) for x in (触发.get('NPC') or [])]
            if want_npcs and not (set(want_npcs) & npc_set):
                skipped.append(f'{pid}:NPC不在场')
                continue
            pre = [str(x) for x in (触发.get('前置任务') or [])]
            missing = [x for x in pre if x not in done_ids]
            if missing:
                skipped.append(f'{pid}:前置任务未完成{"/".join(missing)}')
                continue

            rec = self.new(
                目标=p.get('目标') or p.get('标题') or '',
                执行者=p.get('执行者') or '玩家',
                标题=p.get('标题') or '',
                委托人=p.get('委托人') or '',
                类型=p.get('类型') or KIND_DELIVER,
                期限=p.get('期限'),
                进度=p.get('进度') or '',
                涉及地点=p.get('涉及地点'),
                涉及NPC=p.get('涉及NPC'),
                完成条件=p.get('完成条件') or '',
                来源='预设',
                预设id=pid,
                登记时间=登记时间,
                clock=clock,
            )
            if rec:
                made.append(rec)

        if made:
            logger.info(f'📋 预设任务激活 {len(made)} 条：'
                        f'{", ".join(t["id"] + "｜" + t["标题"] for t in made)}')
        elif skipped:
            logger.info(f'📋 预设任务检查 {len(presets)} 条，本轮无触发（{"; ".join(skipped[:4])}'
                        f'{"等" if len(skipped) > 4 else ""}）')
        return made

    # ===================== 序列化 =====================

    def to_dict(self) -> dict:
        return {'seq': self._seq, 'tasks': self.tasks}

    def from_dict(self, d: dict) -> "TaskStore":
        d = d if isinstance(d, dict) else {}
        tasks = d.get('tasks')
        if isinstance(tasks, list):
            self.tasks = [t for t in tasks if isinstance(t, dict)]
        self._seq = int(d.get('seq') or 0)
        # seq 比现有 id 小 → 按最大 id 补上，否则新任务会和旧 id 撞车
        for t in self.tasks:
            tid = str(t.get('id') or '')
            if tid[:1].upper() == 'T' and tid[1:].isdigit():
                self._seq = max(self._seq, int(tid[1:]))
        return self

    def migrate_legacy(self, npc_tasks: dict, 登记时间: str = '', clock=None) -> int:
        """
        旧档迁移：`npc_tasks = {NPC名: {目标, 去向, 行程}}` → 任务记录。

        只迁"目标"这一件事（旧结构里也只有它有语义）；去向/行程属于位置账，
        本来就不该记在任务上。**不升 SAVE_VERSION**：能就地迁就就地迁。
        """
        n = 0
        for owner, rec in (npc_tasks or {}).items():
            if not isinstance(rec, dict):
                continue
            made = self.new(
                目标=rec.get('目标') or '',
                执行者=owner,
                委托人='（旧档：委托人未记）',
                来源='旧档迁移',
                登记时间=登记时间,
                clock=clock,
            )
            if made:
                n += 1
        if n:
            logger.info(f'📋 旧档迁移：npc_tasks → 任务表 {n} 条')
        return n

    # ===================== 限额 =====================

    def trim(self):
        """归档超上限时从最老的开始丢（进行中的一条都不动）。"""
        finished = [t for t in self.tasks if t.get('状态') in _FINAL]
        if len(finished) <= self.MAX_ARCHIVE:
            return
        drop = {id(t) for t in finished[:len(finished) - self.MAX_ARCHIVE]}
        before = len(self.tasks)
        self.tasks = [t for t in self.tasks if id(t) not in drop]
        logger.info(f'📋 任务归档超上限，丢弃最老 {before - len(self.tasks)} 条'
                    f'（保留 {len(self.tasks)} 条）')

    # ===================== 展示 =====================

    @staticmethod
    def describe(t: dict) -> str:
        """一行文字，给日志 / 只读视图 / N6 输入用。"""
        bits = [f'{t.get("id")}｜{t.get("标题") or t.get("目标")}｜{t.get("状态")}']
        if t.get('类型') == KIND_LONG:
            bits.append('长期')
        if t.get('进度'):
            bits.append(f'进度：{t["进度"]}')
        if t.get('截止时间'):
            bits.append(f'期限：{t["截止时间"]}')
        elif t.get('期限'):
            bits.append(f'期限：{t["期限"]}')
        if t.get('涉及地点'):
            bits.append('地点：' + '、'.join(t['涉及地点']))
        if t.get('结果'):
            bits.append(f'结果：{t["结果"]}')
        return '｜'.join(bits)

    def describe_all(self, owner: str = None, include_final: bool = False) -> list[str]:
        pool = self.tasks if owner is None else [t for t in self.tasks if t.get('执行者') == owner]
        if not include_final:
            pool = [t for t in pool if t.get('状态') == ACTIVE]
        return [self.describe(t) for t in pool]
