"""主页 DOM 腿：`pageCapture` 剥掉 `<script>` 之后，还能不能按主页入库。

## 为什么这次必须新增一条腿

[`采集端-v8`](../../docs/采集端-v8-三环死锁-20261118.md) 的修法是「扩展把
`__INITIAL_STATE__` 带回来落成旁车」。真机验证后发现一个前提错了：

> 小红书是 **CSR**。首屏的 `__INITIAL_STATE__.user.userPageData` 是**空的**，
> 账号头部由挂载后的接口请求填进 DOM。

于是「state 里有昵称」这个门槛**永远过不了**——用户在屏幕前看着一个完整的
主页（头像、粉丝数、32 张卡片全在），面板却说「主页还没渲染出账号信息」。

修法不是放宽判据，而是**承认有两条腿**：

| 腿 | 数据来源 | 能给的字段 | 缺什么 |
|---|---|---|---|
| state | 存档里的 `__INITIAL_STATE__`（或旁车） | 全部 | —— |
| DOM | 渲染后的 HTML | 昵称/小红书号/简介/IP/粉丝/关注/获赞 + 全部卡片（id/标题/赞/形式） | **发布日、置顶** |

**缺两列不等于整份存档不能用。** 发布日页面上是「3 天前」这种相对值，
置顶卡在 DOM 里与普通卡长得一模一样（实测 32 张逐张比对，`pin`/`top`/`置顶`/
`sticky` 一个都不出现）。这两个字段**宁可写 ⬜ 也不猜**——`validate` 会 WARN，
`ar selftest` 的 AC1（置顶 2 / 样本 30）会红，那正是「该补旁车了」的信号。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import profile_dom  # noqa: E402
from account_research.initialstate import (  # noqa: E402
    STATE_ABSENT,
    open_archive,
)

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures"

# 一段**结构照抄真实存档**的主页 DOM：头部 + 三张卡片（图文 / 视频 / 另一张图文）。
# 卡片的嵌套关系、class 名、`<!---->` 占位注释、`&amp;` 转义都保留，
# 因为这些正是解析器要踩的地方。
PROFILE_DOM = """<!DOCTYPE html>
<html><head><title>暖暖爱分享 - 小红书</title>
<meta property="og:url" content="https://xiaohongshu.com/user/profile/aaaabbbbccccddddeeee0001">
</head><body>
<div class="user-info"><div class="info-part">
  <div class="user-image"><img class="avatar-item" src="a.jpg"></div>
  <div class="user-basic">
    <div class="user-nickname"><div class="user-name">暖暖爱分享<!----></div></div>
    <div class="user-content">
      <span class="user-redId">小红书号：6203626684</span>
      <span class="user-IP"> IP属地：山东</span>
    </div>
  </div>
  <div class="user-desc">🎯 痴迷挖掘各种好用的效率工具和AI<br>可以不光芒万丈，但不要停止发光</div>
  <div class="user-interactions">
    <div class="clickable"><span class="count">33</span><span class="shows">关注</span><!----></div>
    <div class="clickable"><span class="count">1016</span><span class="shows">粉丝</span><!----></div>
    <div class="clickable"><span class="count">14.2万</span><span class="shows">获赞与收藏</span><!----></div>
  </div>
</div></div>

<div class="feeds-container">
<section data-v-f5c4d41e="" class="note-item" data-index="0" data-note-id="aaaabbbbccccddddeeee1111">
  <a class="cover mask ld" href="https://www.xiaohongshu.com/user/profile/aaaabbbbccccddddeeee0001/aaaabbbbccccddddeeee1111?xsec_token=T1&amp;xsec_source=pc_user"><img src="c1.jpg"></a>
  <div class="footer">
    <a class="title"><span>逼自己跑完这套 WorkBuddy 工作流</span></a>
    <div class="author-wrapper">
      <a class="author"><span class="name">暖暖爱分享</span></a>
      <span class="like-wrapper like-active"><svg class="reds-icon"></svg><span class="count">140</span></span>
    </div>
  </div>
</section>
<section data-v-f5c4d41e="" class="note-item" data-index="1" data-note-id="aaaabbbbccccddddeeee2222">
  <a class="cover mask ld" href="https://www.xiaohongshu.com/user/profile/aaaabbbbccccddddeeee0001/aaaabbbbccccddddeeee2222"><img src="c2.jpg"><span class="play-icon"><svg></svg></span></a>
  <div class="footer">
    <a class="title"><span>简单3步🔥用AI做动画</span></a>
    <div class="author-wrapper">
      <a class="author"><span class="name">暖暖爱分享</span></a>
      <span class="like-wrapper like-active"><span class="count">1.2万</span></span>
    </div>
  </div>
</section>
<section data-v-f5c4d41e="" class="note-item" data-index="2" data-note-id="aaaabbbbccccddddeeee3333">
  <a class="cover mask ld" href="#"><img src="c3.jpg"></a>
  <div class="footer">
    <a class="title"><span>没有赞的这篇</span></a>
    <div class="author-wrapper">
      <a class="author"><span class="name">暖暖爱分享</span></a>
      <span class="like-wrapper"><span class="count"></span></span>
    </div>
  </div>
</section>
</div>
</body></html>"""


class TestLooksLikeProfile(unittest.TestCase):
    def test_detects_the_header_container(self):
        self.assertTrue(profile_dom.looks_like_profile(PROFILE_DOM))

    def test_a_detail_page_is_not_a_profile(self):
        html = (FIXTURES / "minimal_detail.html").read_text(encoding="utf-8")
        self.assertFalse(profile_dom.looks_like_profile(html))

    def test_an_empty_page_is_not_a_profile(self):
        self.assertFalse(profile_dom.looks_like_profile(""))


class TestParseAccount(unittest.TestCase):
    def setUp(self):
        self.row = profile_dom.parse_account(
            PROFILE_DOM, "2026-11-19", source_file="x.html",
            account_id="aaaabbbbccccddddeeee0001")

    def test_nickname_does_not_keep_the_vue_placeholder(self):
        """`<!---->` 是 Vue 的占位注释，不清掉昵称会变成「暖暖爱分享<!---->」。

        它与真正的 `<!-- 注释 -->` 是同一类东西，所以清注释要一起清，
        而不是专门 replace 那五个字符——后者在平台换渲染方式时就不管用了。
        """
        self.assertEqual(self.row["nickname"], "暖暖爱分享")

    def test_red_id_drops_the_label(self):
        self.assertEqual(self.row["red_id"], "6203626684")

    def test_ip_location_drops_the_label_and_the_leading_space(self):
        self.assertEqual(self.row["ip_location"], "山东")

    def test_html_entities_are_unescaped(self):
        """`&amp;` 必须解成 `&`。

        实测（2026-11-19，知新那份真实存档）：简介在 HTML 里是
        `AI 大厂动态 &amp; 行业趋势`。不解实体，DOM 腿会把 `&amp;` 原样写进 CSV，
        而 state 腿读的是 JSON 里的 `&`——**同一个账号，两条腿给出不同的字符串**，
        下游按文本去重/检索时就会对不上。
        """
        html = PROFILE_DOM.replace("痴迷挖掘", "效率 &amp; 工具")
        row = profile_dom.parse_account(html, "2026-11-19", account_id="x")
        self.assertIn("效率 & 工具", row["desc"])
        self.assertNotIn("&amp;", row["desc"])

    def test_desc_is_flattened_to_one_line(self):
        """简介里有 `<br>`。落进 CSV 的换行会成为**假的行分割**——读表时多出半行。"""
        self.assertIn("痴迷挖掘", self.row["desc"])
        self.assertNotIn("\n", self.row["desc"])
        self.assertIn("可以不光芒万丈", self.row["desc"])

    def test_counts_are_parsed_by_label_not_by_order(self):
        """三个计数必须按 `shows` 的中文标签取，不能按出现顺序。

        顺序看起来是固定的（关注/粉丝/获赞），但那是**今天的**顺序；
        按标签取，平台换顺序也不会错位——而错位不会报错，只会把粉丝数写成关注数。
        """
        self.assertEqual(self.row["fans"], 1016)
        self.assertEqual(self.row["follows"], 33)
        self.assertEqual(self.row["interactions"], 142000)  # 14.2万 → 近似值

    def test_page_level_zeros_are_left_blank(self):
        """`获赞与收藏` 是**合计**，不是「赞」也不是「藏」。

        把它拆成 liked / collected 就是编数据。头部本来就没有这两项，写 ⬜。
        """
        for column in ("liked", "collected", "posted"):
            self.assertIsNone(self.row[column], f"{column} 不该有值")

    def test_account_id_comes_from_the_caller_not_from_the_nickname(self):
        """id 只能由 URL 给（`/user/profile/<id>`）。按昵称猜 id = 编归属。"""
        row = profile_dom.parse_account(PROFILE_DOM, "2026-11-19")
        self.assertEqual(row["account_id"], "")
        self.assertEqual(row["nickname"], "暖暖爱分享")


class TestParseNotes(unittest.TestCase):
    def setUp(self):
        self.rows, self.defects = profile_dom.parse_notes(
            PROFILE_DOM, "aaaabbbbccccddddeeee0001", "2026-11-19", source_file="x.html")

    def test_one_row_per_card(self):
        self.assertEqual([row["note_id"] for row in self.rows],
                         ["aaaabbbbccccddddeeee1111", "aaaabbbbccccddddeeee2222",
                          "aaaabbbbccccddddeeee3333"])

    def test_title_is_taken_from_the_title_anchor_only(self):
        """卡片里有两处文字（标题、作者名）。取错就变成「标题 = 暖暖爱分享」。"""
        self.assertEqual(self.rows[0]["title"], "逼自己跑完这套 WorkBuddy 工作流")

    def test_video_is_detected_by_the_play_icon(self):
        self.assertEqual(self.rows[0]["form"], "图文")
        self.assertEqual(self.rows[1]["form"], "视频")

    def test_rounded_like_counts_are_converted(self):
        self.assertEqual(self.rows[0]["likes"], 140)
        self.assertEqual(self.rows[1]["likes"], 12000)

    def test_zero_likes_is_blank_not_zero(self):
        """空 `.count` 是「没渲染出来」，不是「0 个赞」。写成 0 就是编数据。"""
        self.assertIsNone(self.rows[2]["likes"])

    def test_published_at_is_blank_not_guessed(self):
        """DOM 里没有绝对时间。按卡片序号或文件顺序推 = 编数据。

        这条是整个模块**最贵的一条**：它让 DOM 腿不能替代旁车。
        """
        self.assertTrue(all(row["published_at"] == "" for row in self.rows))

    def test_pinned_is_blank_not_false(self):
        """`""` 与 `"0"` 是两件事：前者是「不知道」，后者是「确认不是置顶」。

        实测：置顶卡在 DOM 里与普通卡完全一样（32 张逐张比对，
        `pin`/`top`/`置顶`/`sticky` 一个都不出现）。写成 `"0"` 会让
        「置顶 2 / 样本 30」静默变成「置顶 0 / 样本 32」——数字全对得上，结论全错。
        """
        self.assertTrue(all(row["pinned"] == "" for row in self.rows))

    def test_collects_comments_shares_stay_blank(self):
        for column in ("collects", "comments", "shares"):
            self.assertTrue(all(row[column] is None for row in self.rows), column)

    def test_defects_say_what_is_missing_and_how_to_fix_it(self):
        """缺什么必须说出来，而且给出补法。

        静默留空的话，人只会看到「32 篇入库成功」，然后在报告里看到一片空白的发布日期，
        却不知道原因是「这条通道给不了」还是「解析器坏了」。
        """
        text = " ".join(self.defects)
        self.assertIn("发布日", text)
        self.assertIn("置顶", text)
        self.assertIn("state.json", text)


class TestSlicesAreClosed(unittest.TestCase):
    """**每张卡片必须自己闭包**，绝不「找不到就用邻居的」。

    `images.py` 的 `dom_slides` 当年就是越过边界借了邻居的本地路径，
    16 张图算成 3 张还报「已绑全」（[采集端-v6 复盘]）。同样的错在卡片上更隐蔽：
    标题与赞数会串行，而表格里看起来一切正常。
    """

    def test_a_card_without_a_title_does_not_borrow_the_neighbours(self):
        html = PROFILE_DOM.replace(
            "<a class=\"title\"><span>没有赞的这篇</span></a>", "")
        rows, _ = profile_dom.parse_notes(html, "acc", "2026-11-19")
        self.assertEqual(rows[2]["title"], "")
        self.assertEqual(rows[0]["title"], "逼自己跑完这套 WorkBuddy 工作流")

    def test_duplicate_note_ids_are_collapsed(self):
        """滚动加载会让同一篇出现两次（实测滚动位 1/2 的 data-index 有重叠）。"""
        html = PROFILE_DOM.replace(
            '<section data-v-f5c4d41e="" class="note-item" data-index="2" '
            'data-note-id="aaaabbbbccccddddeeee3333">',
            '<section data-v-f5c4d41e="" class="note-item" data-index="2" '
            'data-note-id="aaaabbbbccccddddeeee1111">')
        rows, _ = profile_dom.parse_notes(html, "acc", "2026-11-19")
        self.assertEqual(len(rows), 2)

    def test_a_page_without_cards_says_so(self):
        rows, defects = profile_dom.parse_notes("<html><body>空</body></html>", "acc", "2026-11-19")
        self.assertEqual(rows, [])
        self.assertTrue(any("选择器" in text for text in defects))


class TestDiagnose(unittest.TestCase):
    """面板只能显示一句话，所以「为什么采不了」必须按原因分开说。"""

    def test_a_rendered_profile_has_no_complaint(self):
        self.assertEqual(profile_dom.diagnose(PROFILE_DOM), "")

    def test_a_profile_still_loading_says_so(self):
        """有卡片、没头部 = 还在渲染。说「往下滚一屏再试」比说「识别失败」有用。"""
        message = profile_dom.diagnose('<div class="feeds-container"><section class="note-item">')
        self.assertIn("往下滚", message)

    def test_a_detail_page_is_called_a_detail_page(self):
        html = (FIXTURES / "minimal_detail.html").read_text(encoding="utf-8")
        self.assertIn("详情页", profile_dom.diagnose(html))


class TestOpenArchiveFallsBackToDom(unittest.TestCase):
    """`open_archive` 的第三档：没有 state、也不是详情页，但 DOM 里有头部。

    这一档以前会**直接抛错**，于是「扩展采主页」必须依赖旁车，
    而旁车里也未必有数据（CSR 首屏 state 是空的）。
    """

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, html: str) -> Path:
        path = self.dir / name
        path.write_text(html, encoding="utf-8")
        return path

    def test_kind_is_profile_dom(self):
        archive = open_archive(self.write("p.html", PROFILE_DOM))
        self.assertEqual(archive.kind, "profile_dom")
        self.assertTrue(archive.usable, "DOM 腿的存档必须算「可入库」")
        self.assertEqual(archive.state_source, STATE_ABSENT)

    def test_account_id_comes_from_og_url(self):
        """主页存档的 `og:url` 是 `https://xiaohongshu.com/user/profile/<id>`——
        **没有 `www`**。只按 `/explore/` 判详情页的旧逻辑在这条上是空值。"""
        archive = open_archive(self.write("p.html", PROFILE_DOM))
        self.assertEqual(archive.target_user_ids, ("aaaabbbbccccddddeeee0001",))

    def test_a_page_without_a_header_still_raises_with_advice(self):
        """连头部都没有 = 真的没渲染。报错里要说清「等加载完」这个动作。"""
        from account_research.initialstate import ArchiveError

        with self.assertRaises(ArchiveError) as ctx:
            open_archive(self.write("blank.html", "<html><body><div>加载中</div></body></html>"))
        self.assertIn("加载", str(ctx.exception))

    def test_state_still_wins_when_present(self):
        """有 state 时**不走** DOM 腿：发布日与置顶只有它给得出。"""
        html = (FIXTURES / "minimal_profile.html").read_text(encoding="utf-8")
        archive = open_archive(self.write("s.html", html))
        self.assertEqual(archive.kind, "profile")
        self.assertTrue(archive.state)


if __name__ == "__main__":
    unittest.main()
