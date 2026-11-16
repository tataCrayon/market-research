"""一键拆解（S2）：任务拼装、事件流解析、产物收回。

这一层的风险不在「模型会不会答」，在**拼装方会不会把红线丢掉**：
列名手抄会漂、忘了关网络工具、把图片路径当正文塞进任务。
所以测试集中在这三件事上，不去模拟模型输出质量。
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from account_research import debrief, schema, store  # noqa: E402
from account_research.stats import summarize  # noqa: E402

# 「路径 + 图片扩展名」才算诱饵；单纯列扩展名是禁令，必须允许
IMAGE_PATH_RE = re.compile(r"[\w./\\-]+\.(?:jpg|jpeg|png|webp|gif|bmp|heic)\b", re.IGNORECASE)

SOURCE_PATCH = """\
- id: llm-pi-ai
  name: "@deepseek-ai/dsh-llm-pi-ai"
  config:
    providers:
      fake:
        apiKeyEnv: FAKE_API_KEY
        models:
          - id: fake-model
- id: agent-default-model
  name: "@deepseek-ai/dsh-agent-default-model"
  config:
    provider: fake
    model: fake-model
# 这行注释紧跟在条目后面，缩进归零——按块切时会被粘到上一块尾部
"""


class TestRuntimePatch(unittest.TestCase):
    def test_patch_restores_credentials_and_closes_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cordis.patch.yml"
            source.write_text(SOURCE_PATCH, encoding="utf-8")
            text = debrief.build_runtime_patch(source)
        self.assertIn("- id: credentials\n  disabled: false", text)
        self.assertIn("- id: agent-default-model", text)
        for row in ("tool-web", "tool-pwsh", "tool-bash"):
            self.assertIn(f"- id: {row}\n  disabled: true", text)
        self.assertIn("readImage: false", text)
        self.assertIn("write: false", text)
        # 尾部注释不该被搬过来
        self.assertNotIn("缩进归零", text)

    def test_missing_source_patch_is_explicit_error(self):
        with self.assertRaises(debrief.DebriefError):
            debrief.build_runtime_patch(Path("不存在.yml"))

    def test_source_patch_without_model_route_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "cordis.patch.yml"
            source.write_text("- id: ui-chat\n  config: {}\n", encoding="utf-8")
            with self.assertRaises(debrief.DebriefError):
                debrief.build_runtime_patch(source)


class TestTaskPayload(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp())
        self.text = Path(tempfile.mkdtemp())
        (self.data / "accounts.csv").write_text(
            "account_id,nickname,red_id,desc,ip_location,fans,follows,interactions,liked,collected,posted,"
            "platform,source_file,source_kind,collected_on,grade,note\n"
            "acc1,测试号,testid,简介,,1000,10,5000,,,,xiaohongshu,f.html,公开页,2026-10-22,②,\n",
            encoding="utf-8",
        )
        (self.data / "notes.csv").write_text(
            "account_id,note_id,published_at,title,form,likes,collects,comments,shares,pinned,topic,"
            "source_file,source_kind,collected_on,grade,duration,human_note,author_id\n"
            "acc1,n1,2026-10-01,第一篇,图文,100,,,0,,,f.html,公开页,2026-10-22,②,,,\n"
            "acc1,n2,2026-10-02,第二篇,视频,900,,,0,,,f.html,公开页,2026-10-22,②,,,\n",
            encoding="utf-8",
        )
        (self.text / "n1.txt").write_text("正文\n", encoding="utf-8")
        (self.text / "n1.images.txt").write_text("# 图上文字\n", encoding="utf-8")

    def tearDown(self):
        for path in (self.data, self.text):
            for item in path.iterdir():
                item.unlink()
            path.rmdir()

    def test_snapshot_separates_body_from_images(self):
        snapshot = debrief.collect_snapshot(self.data, "acc1", self.text)
        self.assertEqual(snapshot.body_ready, {"n1"})
        self.assertEqual(snapshot.images_ready, {"n1"})
        self.assertEqual(snapshot.stats.n_sample, 2)

    def test_task_carries_no_image_paths(self):
        """R7 的回归线：任务里可以**列出**被禁的扩展名，但绝不能出现某个图片的路径。

        差别就是「（.jpg / .png）」和「account/notes-text/n1.jpg」——
        前者是禁令，后者是诱饵：模型看到路径就会去读它。
        """
        snapshot = debrief.collect_snapshot(self.data, "acc1", self.text)
        task = debrief.build_task(snapshot, self.data.parent)
        self.assertIsNone(IMAGE_PATH_RE.search(task), IMAGE_PATH_RE.search(task).group(0) if IMAGE_PATH_RE.search(task) else "")
        self.assertNotIn("_files", task)

    def test_task_columns_match_schema(self):
        """列名必须来自 schema，不许手抄——手抄会漂，漂了是静默错数据。"""
        snapshot = debrief.collect_snapshot(self.data, "acc1", self.text)
        task = debrief.build_task(snapshot, self.data.parent)
        for table in debrief.TARGET_TABLES:
            for column in schema.TABLE_COLUMNS[table]:
                self.assertIn(f'"{column}"', task, f"{table}.{column} 没出现在输出契约里")

    def test_task_warns_when_no_image_text(self):
        snapshot = debrief.collect_snapshot(self.data, "acc1", Path(tempfile.mkdtemp()))
        task = debrief.build_task(snapshot, self.data.parent)
        self.assertIn("一篇图上文字都没有", task)


class TestBodyGate(unittest.TestCase):
    """「深拆 3 篇」是验证清单上的硬纪律，代码必须自己说，不能靠模型自觉。

    实测（2026-11-04）：知新那个号 30 篇样本里只有 1 篇有正文，`ar debrief` 首行照样报
    「样本 30 篇」，一个警告都不打——而一轮真跑要烧 60–114 万 tokens。
    不出声的代价不是「结果差」，是「结果看起来一样好」：模型会用标题 + 点赞
    硬写出一份结构拆解，读起来完全合理，而正文根本没读过。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "accounts.csv").write_text(
            "account_id,nickname,collected_on\nacc1,测试号,2026-10-22\n", encoding="utf-8")
        self.header = ("account_id,note_id,published_at,title,form,likes,collects,comments,shares,pinned,topic,"
                       "source_file,source_kind,collected_on,grade,duration,human_note,author_id\n")
        rows = [f"acc1,n{i},2026-10-0{i},标题{i},图文,{i * 100},,,,,,f.html,公开页,2026-10-22,②,,,"
                for i in range(1, 7)]
        (self.data / "notes.csv").write_text(self.header + "\n".join(rows) + "\n", encoding="utf-8")
        self.text = Path(tempfile.mkdtemp())

    def tearDown(self):
        for item in self.text.iterdir():
            item.unlink()
        self.text.rmdir()
        self.tmp.cleanup()

    def _gate(self, bodies: int, images: int = 0) -> str:
        for i in range(1, bodies + 1):
            (self.text / f"n{i}.txt").write_text("正文", encoding="utf-8")
        for i in range(1, images + 1):
            (self.text / f"n{i}.images.txt").write_text("图上文字", encoding="utf-8")
        snapshot = debrief.collect_snapshot(self.data, "acc1", self.text)
        return debrief.render_body_gate(snapshot)

    def test_three_or_more_bodies_pass_quietly(self):
        gate = self._gate(bodies=3)
        self.assertIn("满足", gate)
        self.assertNotIn("⛔", gate)
        self.assertNotIn("不足", gate)

    def test_two_bodies_is_flagged_as_insufficient(self):
        gate = self._gate(bodies=2)
        self.assertIn("⚠️", gate)
        self.assertIn("不足", gate)
        self.assertIn("深拆 3 篇", gate)

    def test_zero_readable_text_demands_structure_layer_be_blank(self):
        """一篇都读不到时，结构层必须整层 ⬜——不许用标题 + 点赞反推结构。"""
        gate = self._gate(bodies=0)
        self.assertIn("⛔", gate)
        self.assertIn("必须整层留 ⬜", gate)

    def test_names_the_readable_notes_so_model_knows_which(self):
        """光说「只有 2 篇」不够：模型得知道是哪 2 篇，否则它会自己去读整目录（很贵）。"""
        gate = self._gate(bodies=2)
        self.assertIn("n1", gate)
        self.assertIn("n2", gate)

    def test_images_text_counts_as_readable(self):
        """图上文字和正文是同一层可用文本（R7 之后图只走 OCR），都算「读得到」。

        但要按**篇**取并集，不能把两个数相加：同一篇可能两层都有，相加就重复计数。
        """
        gate = self._gate(bodies=1, images=2)          # n1 两层都有 → 并集 2 篇
        self.assertIn("⚠️", gate)
        self.assertIn("能读到的文本只有 2 篇", gate)

    def test_union_not_sum_of_layers(self):
        """1 层正文 + 2 层图上，若其中 1 篇重复，读得到的只有 2 篇而不是 3。

        相加会虚报「达到 3 篇」从而放行一个其实只有 2 篇的结构结论。
        """
        gate = self._gate(bodies=1, images=2)
        self.assertNotIn("满足", gate, "n1 同时有正文与图上文字，不该被数成 3 篇")

    def test_gate_appears_in_task_text(self):
        for i in range(1, 2):
            (self.text / f"n{i}.txt").write_text("正文", encoding="utf-8")
        snapshot = debrief.collect_snapshot(self.data, "acc1", self.text)
        task = debrief.build_task(snapshot, self.data.parent)
        self.assertIn("正文可读性闸门", task)
        self.assertIn("⚠️", task)

    def test_task_closes_the_read_surface(self):
        """「该读什么」清单有长度上限，超了就变成暗示「这些都可以读」。

        实测上一轮 34 次工具调用、114 万 input tokens，其中二十多万花在读
        `src/`、`data/*.csv`、`overall-workflow.md`——它们对本步判断没有增量。
        所以任务的 §1 必须**短**，且显式列出禁读项：只写「读什么」，模型仍会
        自行发挥去翻别的（尤其想「核对一下」时）。
        """
        snapshot = debrief.collect_snapshot(self.data, "acc1", self.text)
        task = debrief.build_task(snapshot, self.data.parent)
        self.assertIn("不要读的东西", task)
        for banned in ("account-research/src/", "data/*.csv", "overall-workflow.md"):
            self.assertIn(banned, task)
        # 已有 id 必须由任务直接给，否则模型会为了防重号去读 findings.csv / playbook.csv
        self.assertIn("已占用", task)

    def test_unknown_account_lists_known_ones(self):
        with self.assertRaises(debrief.DebriefError) as caught:
            debrief.collect_snapshot(self.data, "不存在", self.text)
        self.assertIn("acc1", str(caught.exception))


class TestEventStream(unittest.TestCase):
    def test_parses_success_stream(self):
        stream = "\n".join((
            '{"type":"session","sessionId":"s1"}',
            '{"type":"status","phase":"step_end","usage":{"inputTokens":10,"outputTokens":2}}',
            '{"type":"tool_call","name":"read"}',
            '{"type":"text","text":"收到"}',
            '{"type":"status","phase":"turn_end","reason":{"kind":"completed"}}',
            '{"type":"final","text":"收到"}',
        ))
        result = debrief.parse_events(stream)
        self.assertEqual(result.session_id, "s1")
        self.assertEqual(result.final_text, "收到")
        self.assertEqual(result.turn_reason, "completed")
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual((result.input_tokens, result.output_tokens), (10, 2))

    def test_surfaces_turn_failure_reason(self):
        stream = ('{"type":"status","phase":"turn_end","reason":{"kind":"error",'
                  '"error":{"message":"MISSING_CREDENTIAL"}}}')
        result = debrief.parse_events(stream)
        self.assertEqual(result.error, "MISSING_CREDENTIAL")

    def test_ignores_non_json_noise(self):
        result = debrief.parse_events("noise\n{\"type\":\"final\",\"text\":\"ok\"}\n")
        self.assertEqual(result.final_text, "ok")


class TestPayloadRecovery(unittest.TestCase):
    def test_extracts_fenced_json(self):
        text = '说明\n```json\n{"findings": [{"finding_id": "F-1"}], "verdicts": [], ' \
               '"playbook": [], "hypotheses": []}\n```\n尾巴'
        payload = debrief.extract_payload(text)
        self.assertEqual(payload["findings"], [{"finding_id": "F-1"}])
        self.assertEqual(payload["verdicts"], [])

    def test_missing_json_is_an_error_not_a_guess(self):
        with self.assertRaises(debrief.DebriefError):
            debrief.extract_payload("我觉得这个账号很不错。")

    def test_unknown_table_is_rejected(self):
        text = '```json\n{"findings": [], "verdicts": [], "playbook": [], "hypotheses": [], "extra": []}\n```'
        with self.assertRaises(debrief.DebriefError):
            debrief.extract_payload(text)

    def test_drops_all_blank_template_rows(self):
        payload = {"findings": [{key: "" for key in schema.FINDING_COLUMNS}, {"finding_id": "F-1"}],
                   "verdicts": [], "playbook": [], "hypotheses": []}
        cleaned, dropped = debrief.drop_empty_rows(payload)
        self.assertEqual(dropped, 1)
        self.assertEqual(len(cleaned["findings"]), 1)

    def test_normalize_fills_account_and_date_only(self):
        payload = {"verdicts": [{"account_id": "", "decided_on": "", "verdict": "🧪 先验证", "reason": ""}],
                   "findings": [], "playbook": [], "hypotheses": []}
        debrief.normalize_rows(payload, "acc1", "2026-11-01")
        row = payload["verdicts"][0]
        self.assertEqual(row["account_id"], "acc1")
        self.assertEqual(row["decided_on"], "2026-11-01")
        self.assertEqual(row["reason"], "", "证据缺口不许被顺手填上")

    def test_unknown_column_is_reported_before_write(self):
        payload = {"findings": [{"finding_id": "F-1", "自造字段": "x"}], "verdicts": [], "playbook": [], "hypotheses": []}
        problems = debrief.check_payload(payload)
        self.assertTrue(any("自造字段" in problem for problem in problems))


class TestDateIsDeterministic(unittest.TestCase):
    """`decided_on` / `sampled_on` 是「拆解发生在哪天」——代码知道，模型不知道。

    实测（2026-11-04，知新那轮）：模型自己填了 `2026-10-28`，那是**数据窗口的末日**
    而不是拆解日（真实是 11-04）。这个错读起来完全合理，也不会让任何校验报错，
    但它会把判决的到期日整体前移，直接污染「到期该复核」的告警（D1/D2 按 decided_on 算天数）。
    """

    def test_model_written_date_is_overridden(self):
        payload = {"verdicts": [{"account_id": "acc1", "decided_on": "2026-10-28", "verdict": "🧪 先验证"}],
                   "findings": [{"finding_id": "F-1", "sampled_on": "2026-10-28"}],
                   "playbook": [], "hypotheses": []}
        debrief.normalize_rows(payload, "acc1", "2026-11-04")
        self.assertEqual(payload["verdicts"][0]["decided_on"], "2026-11-04")
        self.assertEqual(payload["findings"][0]["sampled_on"], "2026-11-04")

    def test_override_is_reported_not_silent(self):
        """静默纠正也是编数据——只不过编的是「模型没写错」这个假象。"""
        payload = {"verdicts": [{"account_id": "acc1", "decided_on": "2026-10-28"}],
                   "findings": [], "playbook": [], "hypotheses": []}
        debrief.normalize_rows(payload, "acc1", "2026-11-04")
        overrides = debrief.collect_date_overrides(payload)
        self.assertEqual(overrides, [("verdicts", "decided_on", "2026-10-28")])

    def test_override_does_not_leak_into_row(self):
        """临时键必须在收集后摘掉，否则会被 check_payload 当成「schema 里没有的列」。"""
        payload = {"verdicts": [{"account_id": "acc1", "decided_on": "2026-10-28"}],
                   "findings": [], "playbook": [], "hypotheses": []}
        debrief.normalize_rows(payload, "acc1", "2026-11-04")
        debrief.collect_date_overrides(payload)
        self.assertEqual(set(payload["verdicts"][0]), {"account_id", "decided_on"})
        self.assertEqual(debrief.check_payload(payload), [])

    def test_correct_date_is_not_reported_as_override(self):
        payload = {"verdicts": [{"account_id": "acc1", "decided_on": "2026-11-04"}],
                   "findings": [], "playbook": [], "hypotheses": []}
        debrief.normalize_rows(payload, "acc1", "2026-11-04")
        self.assertEqual(debrief.collect_date_overrides(payload), [])

    def test_account_id_is_not_silently_rewritten(self):
        """账号填错该由 V7 报出来让人看，不该被静默改写——那会把归属错误藏起来。"""
        payload = {"verdicts": [{"account_id": "wrong", "decided_on": ""}],
                   "findings": [], "playbook": [], "hypotheses": []}
        debrief.normalize_rows(payload, "acc1", "2026-11-04")
        self.assertEqual(payload["verdicts"][0]["account_id"], "wrong")


class TestDueOnIsAnchored(unittest.TestCase):
    """覆盖 `decided_on` 之后必须按**原间隔**重锚 `due_on`。

    实测事故（2026-11-06）：模型写 10-28 判 / 11-04 复核（间隔 7 天），
    E21 的修复把决定日覆盖成 11-04，`due_on` 没动 → **间隔 0 天**。
    后果不是报错，是 D1 在判决落库当天就开始喊「到期未复核」——
    一条永远不可能按时执行的纪律，喊两天就没人看了（E12）。

    覆盖起点不覆盖终点，等于把「他给了 7 天」改写成「他要求当天复核」。
    """

    def _verdict(self, **extra):
        row = {"account_id": "acc1", "verdict": "🧪 先验证", "decided_on": "2026-10-28",
               "due_on": "2026-11-04"}
        row.update(extra)
        return {"verdicts": [row], "findings": [], "playbook": [], "hypotheses": []}

    def test_span_is_preserved(self):
        payload = self._verdict()
        debrief.normalize_rows(payload, "acc1", "2026-11-06")
        row = payload["verdicts"][0]
        self.assertEqual(row["decided_on"], "2026-11-06")
        self.assertEqual(row["due_on"], "2026-11-13", "7 天的间隔必须保住，不能被压成 0")

    def test_zero_span_stays_zero(self):
        """模型真要当天复核就保持当天——重锚不是替它改主意。"""
        payload = self._verdict(due_on="2026-10-28")
        debrief.normalize_rows(payload, "acc1", "2026-11-06")
        self.assertEqual(payload["verdicts"][0]["due_on"], "2026-11-06")

    def test_already_correct_date_is_still_reanchored(self):
        """模型日期没写错时也要重锚？**不要**——日期本身没错，间隔也没变。

        只有 decided_on 被改写才需要动 due_on。这条防的是「无差别平移」，
        那会把模型给对的到期日也一起挪走。
        """
        payload = self._verdict(decided_on="2026-11-06", due_on="2026-11-13")
        debrief.normalize_rows(payload, "acc1", "2026-11-06")
        self.assertEqual(payload["verdicts"][0]["due_on"], "2026-11-13")

    def test_empty_due_on_is_not_invented(self):
        """模型没写 due_on 就是交白卷，不许我们替它编一个。"""
        payload = self._verdict(due_on="")
        debrief.normalize_rows(payload, "acc1", "2026-11-06")
        self.assertEqual(payload["verdicts"][0]["due_on"], "")

    def test_bad_date_is_left_alone(self):
        """`due_on` 是「下周」这种非日期文本时不动它——V8 会报出来，别在这里猜。"""
        payload = self._verdict(due_on="下周")
        debrief.normalize_rows(payload, "acc1", "2026-11-06")
        self.assertEqual(payload["verdicts"][0]["due_on"], "下周")

    def test_shift_is_reported(self):
        payload = self._verdict()
        debrief.normalize_rows(payload, "acc1", "2026-11-06")
        overrides = debrief.collect_date_overrides(payload)
        self.assertIn(("verdicts", "due_on", "2026-11-04"), overrides)

    def test_hypotheses_are_not_reanchored(self):
        """假设表的 `due_on` 是独立填的（可以比拆解日早很多），没有起点可锚。"""
        payload = {"verdicts": [], "findings": [], "playbook": [],
                   "hypotheses": [{"hypothesis_id": "H-1", "status": "未验证",
                                   "due_on": "2026-11-11"}]}
        debrief.normalize_rows(payload, "acc1", "2026-11-06")
        self.assertEqual(payload["hypotheses"][0]["due_on"], "2026-11-11")


class TestRatioAudit(unittest.TestCase):
    """说的是「某个覆盖字段」的 a/b，分子必须能由表数出来。

    这条闸门来自两次真实漂移：产出写「29/31 篇无收藏与评论」（表里 27/31），
    下一轮又写「首图通道覆盖 2/31」——后者是代码没有建模的子集，不该拦。
    所以闸门按「数字旁边有没有字段词」定点校验，而不是全量扫 a/b。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        (self.data / "accounts.csv").write_text(
            "account_id,nickname,collected_on\nacc1,测试号,2026-10-22\n", encoding="utf-8")
        header = ("account_id,note_id,published_at,title,form,likes,collects,comments,shares,pinned,topic,"
                  "source_file,source_kind,collected_on,grade,duration,human_note,author_id\n")
        rows = [
            "acc1,n1,2026-10-01,a,图文,100,10,3,0,,,f.html,公开页,2026-10-22,②,,,",
            "acc1,n2,2026-10-02,b,图文,200,,,,0,,,f.html,公开页,2026-10-22,②,,,",
            "acc1,n3,2026-10-03,c,图文,300,,,,0,,,f.html,公开页,2026-10-22,②,,,",
            "acc1,n4,,,,500,,,,0,,,f.html,公开页,2026-10-22,②,,,",
        ]
        (self.data / "notes.csv").write_text(header + "\n".join(rows) + "\n", encoding="utf-8")
        self.text = Path(tempfile.mkdtemp())
        self.snapshot = debrief.collect_snapshot(self.data, "acc1", self.text)

    def tearDown(self):
        self.tmp.cleanup()
        for item in self.text.iterdir():
            item.unlink()
        self.text.rmdir()

    def test_counts_come_from_table(self):
        counts = debrief.coverage_counts(self.snapshot)
        self.assertEqual((counts["n_sample"], counts["collects"], counts["comments"]), (4, 1, 1))
        self.assertEqual(counts["body"], 0)

    def test_denominator_matches_stats_block(self):
        """§5 覆盖块与 §4 统计块的「样本 N 篇」必须是同一个数。

        实测（2026-11-04，知新那个号）：§4 报 30、§5 报 31。原因是两边排除规则不同——
        `stats.summarize` 排了「置顶 + 无点赞」，`coverage_counts` 只排了置顶。
        于是同一个任务里两个分母，模型引用 a/b 时被迫二选一，闸门又按 §5 判，
        **两个口径互相打架**。这条钉住它们必须一致。
        """
        stats = summarize(store.read_table(self.data, "notes"), "acc1")
        counts = debrief.coverage_counts(self.snapshot)
        self.assertEqual(counts["n_sample"], stats.n_sample)

    def test_rows_without_likes_are_not_in_the_denominator(self):
        """没点赞的行进不了统计，也就不该出现在覆盖分母里——否则分母虚高、覆盖率被低估。

        实测来源：知新那个号有一行「卡兹克做到快100万粉」是详情页顺手建的占位行，
        点赞为空。它被算进覆盖分母时，§5 报 31 而 §4 报 30。
        """
        with (self.data / "notes.csv").open("a", encoding="utf-8") as handle:
            handle.write("acc1,n5,2026-10-05,e,图文,,,,0,,,f.html,公开页,2026-10-22,②,,,\n")
        snapshot = debrief.collect_snapshot(self.data, "acc1", self.text)
        counts = debrief.coverage_counts(snapshot)
        self.assertEqual(counts["n_sample"], 4, "n5 点赞为空，不该算进分母")
        self.assertEqual(counts["n_sample"], summarize(
            store.read_table(self.data, "notes"), "acc1").n_sample)

    def test_impossible_ratio_is_caught(self):
        """2/4 数不出来：表里是有收藏 1 / 无收藏 3，没有「2」这个数。"""
        payload = {"findings": [{"finding_id": "F-1", "statement": "2/4 篇无收藏"}],
                   "verdicts": [], "playbook": [], "hypotheses": []}
        problems = debrief.audit_ratios(self.snapshot, payload)
        self.assertEqual(len(problems), 1)
        self.assertIn("2/4", problems[0])

    def test_complement_of_a_count_is_allowed(self):
        """「1/4 有收藏」与「3/4 无收藏」是同一件事的两面，两个都放行。"""
        payload = {"findings": [{"finding_id": "F-1", "statement": "1/4 有收藏，3/4 无收藏"}],
                   "verdicts": [], "playbook": [], "hypotheses": []}
        self.assertEqual(debrief.audit_ratios(self.snapshot, payload), [])

    def test_correct_ratio_passes(self):
        payload = {"findings": [{"finding_id": "F-1", "statement": "3/4 篇无收藏与评论，4/4 篇无分享"}],
                   "verdicts": [], "playbook": [], "hypotheses": []}
        self.assertEqual(debrief.audit_ratios(self.snapshot, payload), [])

    def test_other_denominators_are_not_guessed_at(self):
        """分母不是样本量（如「4 篇有图样本里的 2/2」）时，闸门不猜也不拦。"""
        payload = {"findings": [{"finding_id": "F-1", "statement": "4 篇里 2/2 有封面文字"}],
                   "verdicts": [], "playbook": [], "hypotheses": []}
        self.assertEqual(debrief.audit_ratios(self.snapshot, payload), [])

    def test_ratio_without_a_field_word_is_left_alone(self):
        """「首图通道覆盖 2/31」是代码没建模的子集真话，不能误杀。

        实测代价：上一版把这句判成错，整轮产出被退回，而句子本身是对的。
        """
        payload = {"findings": [{"finding_id": "F-1", "statement": "首图通道覆盖 2/31，另 29 篇首图未读 ⬜"}],
                   "verdicts": [], "playbook": [], "hypotheses": []}
        self.assertEqual(debrief.audit_ratios(self.snapshot, payload), [])

    def test_ratio_before_a_field_word_is_caught_too(self):
        """「有收藏 2/4」这种倒装也认。"""
        payload = {"findings": [{"finding_id": "F-1", "statement": "有收藏 2/4"}],
                   "verdicts": [], "playbook": [], "hypotheses": []}
        problems = debrief.audit_ratios(self.snapshot, payload)
        self.assertEqual(len(problems), 1)
        self.assertIn("2/4", problems[0])

    def test_coverage_block_states_denominators(self):
        block = debrief.render_coverage_block(self.snapshot)
        self.assertIn("有收藏 1/4", block)
        self.assertIn("缺 4/4", block)
        self.assertIn("有发布日期 3/4", block)


class TestDryRunLeavesNoTrace(unittest.TestCase):
    """--dry-run 只回答「任务长什么样」，不该在 data/debrief 留下一批空运行目录。

    实测踩到过：连着 dry-run 三次，产物目录里多了三个只有 task.md 的目录，
    和真跑过的两次混在一起——事后分不清哪次花过模型钱，也就无从复核。
    """

    def setUp(self):
        from account_research import cli

        self.cli = cli
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.text = Path(self.tmp.name) / "text"
        self.data.mkdir()
        self.text.mkdir()
        (self.data / "accounts.csv").write_text(
            "account_id,nickname,collected_on\nacc1,测试号,2026-10-22\n", encoding="utf-8")
        (self.data / "notes.csv").write_text(
            "account_id,note_id,published_at,title,form,likes,collects,comments,shares,pinned,topic,"
            "source_file,source_kind,collected_on,grade,duration,human_note,author_id\n"
            "acc1,n1,2026-10-01,a,图文,100,,,0,,,f.html,公开页,2026-10-22,②,,,\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = self.cli.main([
                "debrief", "--data", str(self.data), "--text-dir", str(self.text),
                "--account", "acc1", "--dry-run",
            ])
        return code, buffer.getvalue()

    def test_no_run_directory_is_created(self):
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn("# 拆解任务", output, "dry-run 还得让人看见任务全文")
        self.assertFalse((self.data / "debrief").exists(), "dry-run 不该建产物目录")

    def test_dry_run_does_not_call_the_model(self):
        """没配 dsh 也能跑完——真调模型的话这里会去找可执行文件。"""
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertNotIn("调用 dsh headless", output)


class TestLauncher(unittest.TestCase):
    def test_cmd_shim_goes_through_comspec(self):
        command = debrief.launcher_command("dsh.cmd")
        self.assertTrue(command[0].lower().endswith(("cmd.exe", "cmd")))
        self.assertIn("/c", command)

    def test_plain_binary_is_used_directly(self):
        command = debrief.launcher_command("/usr/local/bin/dsh")
        self.assertTrue(command[0].endswith("dsh"), command)


if __name__ == "__main__":
    unittest.main()
