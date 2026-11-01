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

from account_research import debrief, schema  # noqa: E402

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
