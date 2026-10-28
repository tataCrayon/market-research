"""数据层契约：表结构、封闭枚举、指标口径登记。

为什么列定义要集中在一个文件：校验器（validate.py）与统计（stats.py）都按列名读表。
列名飘一处，V1–V8 就静默失效——那正是「纪律只活在自然语言里」的老毛病。
"""

from __future__ import annotations

PLATFORM = "xiaohongshu"

# 小红书页面上的时间戳是北京时间；解析不依赖机器本地时区。
TZ_OFFSET_HOURS = 8

# ---- 封闭枚举（判决集合封闭 = 仓库铁律 1，不允许「其他」） ----
GRADES = ("①", "②", "③-a", "③-b", "⬜")
SOURCE_KINDS = ("公开页", "复制文本", "后台导出", "截图", "第三方工具", "不可得")
FORMS = ("图文", "视频", "未知")
VERDICTS = ("✅ 直接对标", "🔧 部分对标", "🧪 先验证", "⏸ 只观察", "❌ 换对标")
PAGE_KINDS = ("profile", "note_detail", "search_page", "home_feed", "empty_skeleton", "unknown")
HYPOTHESIS_STATUS = ("未验证", "验证中", "已证伪", "已证实")

# ---- 表结构（顺序即 CSV 列序，禁止随手重排：列序变动会让历史文件错位） ----
ACCOUNT_COLUMNS = (
    "account_id", "nickname", "red_id", "desc", "ip_location",
    "fans", "follows", "interactions", "liked", "collected", "posted",
    "platform", "source_file", "source_kind", "collected_on", "grade", "note",
)

NOTE_COLUMNS = (
    "account_id", "note_id", "published_at", "title", "form",
    "likes", "collects", "comments", "shares", "pinned", "topic",
    "source_file", "source_kind", "collected_on", "grade",
    # duration / human_note 由「详情页人工补数」写入（列表页拿不到）：
    # 视频时长相较于视频本体，是拆解真正需要的字段（最小充分数据原则）
    "duration", "human_note",
    # 详情页/扩展采集块里的作者 id。用途只有一个：**归属校验**——
    # 一个窗口混采多个账号时，靠它把笔记分到正确的账号，而不是"库里唯一那个"。
    # 空值 = 来源没带这个字段（主页存档不带，因为账号已知）。
    "author_id",
)

FIELD_COLUMNS = (
    "metric", "scope", "availability", "grade_when_available", "use_for", "checked_on", "note",
)

VERDICT_COLUMNS = (
    "account_id", "verdict", "reason", "next_step", "cheapest_test", "abandon_signal",
    "decided_on", "due_on", "mirror_method", "mirror_practice",
)

FINDING_COLUMNS = (
    "finding_id", "account_id", "layer", "statement", "grade",
    "used_in_verdict", "evidence_ref", "sampled_on", "sample_basis", "applicability",
)

PLAYBOOK_COLUMNS = (
    "action_id", "action", "mechanism", "requires", "failure_condition",
    "supporting_accounts", "counterexample", "evidence_ids", "verifiable",
)

HYPOTHESIS_COLUMNS = (
    "hypothesis_id", "statement", "support", "counter", "experiment", "status", "due_on",
)

# 追加型表的唯一键：同键不重复写，异键只追加（对应「台账只追加」纪律）
TABLE_KEYS = {
    "accounts": ("account_id", "collected_on"),
    # 主页与详情页是两条独立观测通道：同一天读到的数字不同时点，不能互相覆盖
    "notes": ("account_id", "note_id", "collected_on", "source_kind"),
    "fields": ("metric",),
    "verdicts": ("account_id", "decided_on"),
    "findings": ("finding_id",),
    "playbook": ("action_id",),
    "hypotheses": ("hypothesis_id",),
}

TABLE_COLUMNS = {
    "accounts": ACCOUNT_COLUMNS,
    "notes": NOTE_COLUMNS,
    "fields": FIELD_COLUMNS,
    "verdicts": VERDICT_COLUMNS,
    "findings": FINDING_COLUMNS,
    "playbook": PLAYBOOK_COLUMNS,
    "hypotheses": HYPOTHESIS_COLUMNS,
}

# ---- 指标口径登记（来源：skill/references/xiaohongshu-metrics.md，现场须复核） ----
# 这里只写「可不可得 + 等级」，不写任何基准数值——基准分数必须现场取证（铁律）。
METRIC_REGISTRY = (
    # 账号层
    {"metric": "fans", "scope": "账号层", "availability": "公开页", "grade_when_available": "②",
     "use_for": "体量档位", "note": "存量粉丝数；不含增长曲线"},
    {"metric": "follows", "scope": "账号层", "availability": "公开页", "grade_when_available": "②",
     "use_for": "体量档位", "note": ""},
    {"metric": "interactions", "scope": "账号层", "availability": "公开页", "grade_when_available": "②",
     "use_for": "内容资产厚度", "note": "主页累计获赞与收藏"},
    {"metric": "liked", "scope": "账号层", "availability": "公开页", "grade_when_available": "②",
     "use_for": "内容资产厚度", "note": "累计点赞"},
    {"metric": "collected", "scope": "账号层", "availability": "公开页", "grade_when_available": "②",
     "use_for": "内容资产厚度", "note": "累计收藏"},
    {"metric": "posted", "scope": "账号层", "availability": "公开页", "grade_when_available": "②",
     "use_for": "产能 / 更新频率", "note": "主页可见笔记总数"},
    {"metric": "fans_growth", "scope": "账号层", "availability": "仅作者后台", "grade_when_available": "⬜",
     "use_for": "阶段判断", "note": "对标拿不到；禁用粉丝数差值粗估"},
    {"metric": "fans_profile", "scope": "账号层", "availability": "仅作者后台", "grade_when_available": "⬜",
     "use_for": "定位卡「为谁」", "note": "第三方工具给的是估算口径，引用须标工具名 + 日期"},
    # 内容层
    {"metric": "likes", "scope": "内容层", "availability": "公开页", "grade_when_available": "②",
     "use_for": "数据底稿主字段", "note": "列表页可得"},
    {"metric": "collects", "scope": "内容层", "availability": "仅详情页", "grade_when_available": "②",
     "use_for": "赞藏比", "note": "列表页结构里不存在 → 默认 ⬜"},
    {"metric": "comments", "scope": "内容层", "availability": "仅详情页", "grade_when_available": "②",
     "use_for": "互动结构 / 需求信号", "note": "列表页结构里不存在 → 默认 ⬜"},
    {"metric": "shares", "scope": "内容层", "availability": "仅详情页", "grade_when_available": "②",
     "use_for": "传播性", "note": "列表页结构里不存在 → 默认 ⬜"},
    {"metric": "impressions", "scope": "内容层", "availability": "仅作者后台", "grade_when_available": "⬜",
     "use_for": "一切比率的分母", "note": "拿不到曝光就不得编互动率"},
    {"metric": "prompt", "scope": "内容层", "availability": "公开页", "grade_when_available": "②",
     "use_for": "形式对比", "note": "图文 / 视频分形式比较，不混算"},
)
