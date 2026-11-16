"""详情页的 state 通道：`__INITIAL_STATE__` 里的 JSON 才是分享数/发布日/时长的来源。

两条实证是这一组测试的由来（2026-11-03）：
1. 7 篇真实详情页里 **5 篇**的 `__INITIAL_STATE__` 因 `new Map([])` 整份解析失败，
   于是只能退回 DOM 正则，丢掉 shareCount / 绝对发布时间 / 视频秒数——而 DOM 里都没有这三样。
2. state 里有 `shareCount`。**这推翻了「分享数永远 ⬜」这条旧结论**：
   页面上确实不显示，但数据一直在页面里。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research.detail import Comment, extract_comments, extract_detail, note_from_state  # noqa: E402
from account_research.initialstate import clean_js_literals, open_archive, parse_html  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
STATE_FIXTURE = FIXTURES / "minimal_detail_state.html"


class TestNewMapInState(unittest.TestCase):
    def test_empty_map_constructor_becomes_object(self):
        self.assertEqual(
            clean_js_literals('{"a":new Map([])}'),
            '{"a":{}}',
        )

    def test_empty_map_with_spaces(self):
        self.assertEqual(clean_js_literals('{"a":new Map( [] )}'), '{"a":{}}')

    def test_non_empty_map_refused_not_silently_emptied(self):
        """非空 Map 里有真实数据，转成 `{}` 就是静默丢数据——宁可让它大声失败。"""
        literal = '{"a":new Map([["k",1]])}'
        self.assertEqual(clean_js_literals(literal), literal)

    def test_map_inside_string_untouched(self):
        literal = '{"a":"示例：new Map([]) 这种写法"}'
        self.assertEqual(clean_js_literals(literal), literal)

    def test_whole_archive_now_parses(self):
        """这条钉住的是根因：不清洗的话 open_archive 连 state 都拿不到。"""
        archive = open_archive(STATE_FIXTURE)
        self.assertEqual(archive.kind, "note_detail")
        self.assertTrue(archive.state, "state 不该是空对象")
        self.assertIn("note", archive.state)


class TestNoteFromState(unittest.TestCase):
    def setUp(self):
        self.state = parse_html(STATE_FIXTURE.read_text(encoding="utf-8"))

    def test_picks_note_by_id_not_first(self):
        """noteDetailMap 里混着别人的笔记，取第一个就是拿错人的数据。"""
        note = note_from_state(self.state, "0000000000000000000000f2")
        self.assertEqual(note["title"], "带 state 的测试详情页")

    def test_unknown_id_returns_empty(self):
        self.assertEqual(note_from_state(self.state, "ffffffffffffffffffffffff"), {})

    def test_missing_state_is_safe(self):
        self.assertEqual(note_from_state(None, "0000000000000000000000f2"), {})
        self.assertEqual(note_from_state({}, "0000000000000000000000f2"), {})


class TestStateChannel(unittest.TestCase):
    def setUp(self):
        archive = open_archive(STATE_FIXTURE)
        self.fields = extract_detail(archive.html, archive.state)

    def test_share_count_comes_from_state(self):
        """DOM 里 share-wrapper 只有图标没有数字；分享数只在 state 里。"""
        self.assertEqual(self.fields.shares, "42")
        self.assertEqual(self.fields.sources["shares"], "state")

    def test_published_at_is_absolute_date_from_timestamp(self):
        # 1761000000000 ms = 2025-10-21 05:20 (UTC+8)
        self.assertEqual(self.fields.published_at, "2025-10-21")
        self.assertEqual(self.fields.sources["published_at"], "state")

    def test_rounded_dom_count_and_exact_state_count_are_not_a_conflict(self):
        """DOM 给 1.2万、state 给 12000 —— 同一件事，不是冲突。"""
        self.assertEqual(self.fields.conflicts, {})
        self.assertEqual(self.fields.sources["likes"], "state+dom")

    def test_duration_prefers_og_meta_over_player_seconds(self):
        """`og:videotime` 是平台标准 meta；`video.capa.duration=14` 是播放器精度。"""
        self.assertEqual(self.fields.duration, "00:15")
        self.assertEqual(self.fields.sources["duration"], "dom")

    def test_form_from_state_type(self):
        self.assertEqual(self.fields.form, "视频")

    def test_other_note_never_leaks_in(self):
        for name in ("likes", "collects", "comments", "shares"):
            self.assertNotIn("99", getattr(self.fields, name))

    def test_count_text_marks_approximation(self):
        """圆整值逐篇引用时必须带 ≈，不许当精确值写。"""
        archive = open_archive(STATE_FIXTURE)
        fields = extract_detail(archive.html, {})  # 不给 state → 只能读 DOM 的 1.2万
        self.assertEqual(fields.count_text("likes"), "≈12,000")


class TestStateFallbacks(unittest.TestCase):
    """state 缺项时退回 DOM；DOM 缺项时退回 state。两条腿都要能站。"""

    def test_dom_only_still_works_without_state(self):
        html = STATE_FIXTURE.read_text(encoding="utf-8")
        fields = extract_detail(html, None)
        self.assertEqual(fields.likes, "1.2万")      # DOM 原样保留，换算交给 counts
        self.assertEqual(fields.collects, "3567")
        self.assertEqual(fields.shares, "")          # 没有 state 就拿不到
        self.assertEqual(fields.published_at, "")    # 也不拿「3天前」倒推

    def test_body_falls_back_to_state_desc_when_dom_missing(self):
        html = STATE_FIXTURE.read_text(encoding="utf-8").replace(
            'id="detail-desc"', 'id="detail-desc-removed"')
        archive = open_archive(STATE_FIXTURE)
        fields = extract_detail(html, archive.state)
        self.assertIn("第一段正文", fields.body)
        self.assertEqual(fields.sources["body"], "state")

    def test_state_desc_tags_stripped_from_body(self):
        """state 的 desc 把 `#标签[话题]#` 内联在正文尾部，不剥就会混进末段。"""
        html = STATE_FIXTURE.read_text(encoding="utf-8").replace(
            'id="detail-desc"', 'id="detail-desc-removed"')
        archive = open_archive(STATE_FIXTURE)
        fields = extract_detail(html, archive.state)
        self.assertNotIn("[话题]", fields.body)
        self.assertEqual(fields.paragraphs[-1], "第二段正文。")


class TestZeroComments(unittest.TestCase):
    """零评论 ≠ 没采到。这两件事在屏幕上都显示成空，但一个是观测、一个是缺口。

    实测（2026-11-04）：小红书的 `interactInfo.commentCount` 在零评论时是**空串**不是 `"0"`，
    同时页面渲染空态占位「这是一片荒地 点击评论」。9 篇详情页里 commentCount 为空的 2 篇
    都带这个占位、且 `comment-item` 计数为 0；其余 7 篇有值的一个都没有。

    不判零的代价是**丢数据**：`ar gaps` 会把这 2 篇永远列进待补清单，人白点两次。
    乱判零的代价是**编数据**。所以判据必须两条同时成立。
    """

    def _fields(self, html: str):
        return extract_detail(html, None)

    def test_empty_comment_count_with_placeholder_is_zero(self):
        html = ('<meta property="og:url" content="https://www.xiaohongshu.com/explore/'
                '0000000000000000000000aa" />'
                '<div id="detail-desc">正文</div>'
                '<div class="interact-container"><span class="like-wrapper">'
                '<span class="count">10</span></span></div>'
                '<div class="no-comments">这是一片荒地</div>')
        fields = self._fields(html)
        self.assertEqual(fields.comments, "0")
        self.assertEqual(fields.sources["comments"], "dom-zero")
        self.assertIn("comments", fields.zeros)
        self.assertNotIn("comments", fields.missing)

    def test_placeholder_but_comment_items_exist_is_not_zero(self):
        """有评论项却数不出数 → 是没采到，必须留空。判零就是编。"""
        html = ('<meta property="og:url" content="https://www.xiaohongshu.com/explore/'
                '0000000000000000000000ab" />'
                '<div id="detail-desc">正文</div>'
                '<div class="interact-container"></div>'
                '<div class="no-comments">占位</div>'
                '<div class="comment-item">某条评论</div>')
        fields = self._fields(html)
        self.assertEqual(fields.comments, "")
        self.assertIn("comments", fields.missing)

    def test_no_placeholder_stays_unknown(self):
        """页面连空态占位都没有 → 懒加载没触发，不能判零。"""
        html = ('<meta property="og:url" content="https://www.xiaohongshu.com/explore/'
                '0000000000000000000000ac" />'
                '<div id="detail-desc">正文</div>'
                '<div class="interact-container"></div>')
        fields = self._fields(html)
        self.assertEqual(fields.comments, "")
        self.assertNotIn("comments", fields.zeros)

    def test_real_count_wins_over_placeholder(self):
        """有真数时占位不该覆盖它。"""
        html = ('<meta property="og:url" content="https://www.xiaohongshu.com/explore/'
                '0000000000000000000000ad" />'
                '<div id="detail-desc">正文</div>'
                '<div class="interact-container"><span class="chat-wrapper">'
                '<span class="count">45</span></span></div>'
                '<div class="no-comments">占位</div>')
        fields = self._fields(html)
        self.assertEqual(fields.comments, "45")
        self.assertNotIn("comments", fields.zeros)


class TestCommentText(unittest.TestCase):
    """评论原文一直在存档里——「一条都没采」这个旧结论是**错的**。

    2026-11-04 实测：11 篇详情页里 9 篇含 `comment-item`，共 95 条评论原文躺在
    `<div class="content"><span class="note-text">…</span></div>` 里，从头到尾没被解析过。
    之前的判断依据是「统计 comment-item 出现 16–28 次」，且把它当成了**数量**——
    却从没打开看过里面有没有字。**数了节点不等于读了内容。**

    这个错直接导致两份报告的人群画像层全部留 ⬜，而其实是有证据的。
    """

    ITEM = (
        '<div class="comment-item"><div class="comment-inner-container">'
        '<div class="avatar"><a href="https://www.xiaohongshu.com/user/profile/'
        '6258a6c500000000210260c8" data-user-id="6258a6c500000000210260c8">头像</a></div>'
        '<div class="right"><div class="author-wrapper">'
        '<div class="author"><a class="name" data-user-id="6258a6c500000000210260c8">石门简牍</a></div>'
        '</div><div class="content"><span class="note-text"><span>工业革命放大体力。</span></span></div>'
        '<div class="info"><div class="date"><span>3天前</span>'
        '<span class="location">河北</span></div></div></div></div></div>'
    )

    def test_extracts_author_text_time_location(self):
        comments = extract_comments(self.ITEM)
        self.assertEqual(len(comments), 1)
        comment = comments[0]
        self.assertEqual(comment.author, "石门简牍")
        self.assertIn("工业革命放大体力", comment.text)
        self.assertEqual(comment.meta, "3天前")
        self.assertEqual(comment.location, "河北")
        self.assertEqual(comment.author_id, "6258a6c500000000210260c8")

    def test_location_not_duplicated_into_meta(self):
        """`date` 容器里同时装着时间与属地；剪错就把「3天前 河北 河北」写进报告。"""
        comment = extract_comments(self.ITEM)[0]
        self.assertNotIn("河北", comment.meta)
        self.assertNotIn("<span", comment.location)

    def test_item_without_text_is_skipped(self):
        """有容器没正文 = 骨架没渲染，不算一条评论。"""
        skeleton = '<div class="comment-item"><div class="content"></div></div>'
        self.assertEqual(extract_comments(skeleton), ())

    def test_multiple_items_do_not_leak_across(self):
        """按 item 切片：不切的话全局正则会跨条取到上一条的作者名。"""
        second = self.ITEM.replace("石门简牍", "另一个人").replace("工业革命放大体力", "第二条")
        comments = extract_comments(self.ITEM + second)
        self.assertEqual([c.author for c in comments], ["石门简牍", "另一个人"])
        self.assertEqual(comments[1].text, "第二条。")

    def test_empty_html_returns_nothing(self):
        self.assertEqual(extract_comments(""), ())

    def test_comment_is_immutable(self):
        """评论是证据，不许被下游改写；frozen 把这条纪律落进类型。"""
        comment = Comment(author="a", text="b")
        with self.assertRaises(Exception):
            comment.text = "改写"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()