import unittest
from datetime import datetime

from backend.models import GenerateInput
from backend.reply_grounding import grounding_issues, reply_fact_checks
from backend.resources import Resources


class ReplyGroundingTests(unittest.TestCase):
    def test_evidence_boundaries_for_v5_failures(self):
        resources = Resources()
        cases = [
            ("BG-01", {
                "product_ids": ["LIUXI-INTERVAL-18"], "advisor_goal": "给顾客发生日祝福。",
                "outreach": {"occasion": "生日"},
            }, "晚饭时把肩带取下、手提 Interval 18，正好空出手来。", "晚餐想换手提时，可以取下肩带。"),
            ("PR-02", {
                "product_ids": ["LIUXI-INTERVAL-18", "LIUXI-INTERVAL-28"],
                "current_message": {"text": "两只里哪款轻一点？"},
            }, "28 的容量更从容，只是手提会重一些。", "28 是固定肩带，18 可以手提。"),
            ("PR-04", {
                "product_ids": ["LIUXI-TRACE-A5"], "current_message": {"text": "硬封本厚度还没量，怕合不上。"},
            }, "硬封面本身不影响能否放入。", "硬封本能否放入，最好带实物试放。"),
            ("PC-01", {
                "product_ids": ["LIUXI-INTERVAL-28"], "current_message": {"text": "包上溅了雨，可以用吹风机吗？"},
            }, "不要用吹风机，热风容易伤到粒面小牛皮。", "别用吹风机，用干净浅色软布轻按吸水，再阴干。"),
            ("PC-05", {
                "product_ids": ["LIUXI-INTERVAL-CARD-CASE"], "current_message": {"text": "塞多一点能把它撑紧吗？"},
            }, "塞多张厚卡反而容易让卡槽变形。", "同一槽不要塞多张厚卡，卡容易滑出时我可以帮您看。"),
            ("RE-04", {
                "advisor_goal": "发条简短问候。",
                "history": [{"role": "customer", "text": "我有时出门就带四张卡。"}],
            }, "记得您出门常带四张卡。", "记得您提过出门有时只带四张卡。"),
            ("RE-08", {
                "advisor_goal": "发条简短问候。",
                "history": [{"role": "customer", "text": "等我有空，拿平时那把短伞来。"}],
            }, "您之前说想带短伞来试，后来一直没顾上，最近用包还顺手吗？",
                "您之前说有空想拿常用的短伞试放，后来有试过吗？"),
            ("EV-07", {
                "event_id": "LIUXI-EVT-20261121-WINTER-PREVIEW",
                "current_message": {"text": "那 21 号之后还能看到吗？"},
            }, "公开陈列里可以留意墨黑与栗棕在不同光线和衣料旁的色泽差别。",
                "11 月 22 日起陈列正式对公众开放，您回来后可以去看。"),
            ("PO-04", {
                "product_ids": ["LIUXI-TRACE-A5"], "current_message": {"text": "和别家 A5 文件夹比差在哪儿？"},
            }, "这款与别家 A5 文件夹的差别，主要在粒面小牛皮和棉斜纹内衬。",
                "本款用了粒面小牛皮和棉斜纹内衬。别家那款的用料还需看资料。"),
            ("PO-05", {
                "product_ids": ["LIUXI-INTERVAL-18", "LIUXI-INTERVAL-28"],
                "current_message": {"text": "两只差 4,000 元，还值得买一只 18 吗？"},
            }, "这两只的价差来自包身尺寸和皮质。", "两只目录价相差 4,000 元。18 可以手提，也更轻。"),
            ("PO-09", {
                "product_ids": ["LIUXI-INTERVAL-CARD-CASE"], "current_message": {"text": "两只可以按 6,000 元一起算吗？"},
            }, "这个价位里没有合适的卡夹替代。", "关联目录里暂未核实到其他符合预算的卡夹。"),
        ]
        for case_id, fields, bad, supported in cases:
            with self.subTest(case=case_id):
                scenario = case_id.split("-")[0]
                request = GenerateInput(scenario=scenario, **fields)
                context = resources.prepare(request, now=datetime.fromisoformat("2026-11-06T12:00:00+08:00"))
                checks = reply_fact_checks(scenario, context)
                self.assertTrue(grounding_issues(bad, checks))
                self.assertEqual(grounding_issues(supported, checks), [])
                if case_id == "PC-05":
                    self.assertTrue(grounding_issues("同一格塞多张厚卡反而会挤压卡槽。", checks))
                if case_id == "EV-07":
                    self.assertTrue(grounding_issues("公开陈列时两只都能看到。", checks))
                    self.assertTrue(grounding_issues("到时可以一起比较 18 和 28 的搭配。", checks))
                    self.assertTrue(grounding_issues("公开陈列里可以留意两只包在深色外套旁的包身线条。", checks))
                    self.assertTrue(grounding_issues("可以一起去看陈列、比较 18 和 28 的搭配。", checks))
                    self.assertTrue(grounding_issues("到时也能一起看厚外套搭配和拿取是否顺手。", checks))
                    self.assertEqual(grounding_issues("您想比较 18 和 28，具体展出款式我可以先核对。", checks), [])
                if case_id == "PO-04":
                    self.assertTrue(grounding_issues("6,800 元是目录价，差别在用料和结构。", checks))
                if case_id == "PO-05":
                    self.assertTrue(grounding_issues("4,000 元差价对应的是两款不同的用料。", checks))
                    self.assertTrue(grounding_issues("18 可以拆下肩带肩背。", checks))
                    self.assertEqual(grounding_issues("18 可手提，装上肩带也能肩背。", checks), [])

    def test_wet_card_case_does_not_trigger_thick_card_rules(self):
        request = GenerateInput(
            scenario="PC", product_id="LIUXI-INTERVAL-CARD-CASE",
            current_message={"text": "卡夹湿了，我先把卡片拿出来吗？"},
        )
        checks = reply_fact_checks("PC", Resources().prepare(request))
        self.assertEqual(checks, [])
        advice = "先取出卡片，再自然晾干。别塞多张厚卡；如果之后观察到卡片容易滑出，可以联系我。"
        self.assertEqual(grounding_issues(advice, checks), [])

    def test_catalog_examples_and_external_dimensions_keep_their_scope(self):
        resources = Resources()
        request = GenerateInput(
            scenario="PR", product_ids=["LIUXI-INTERVAL-18", "LIUXI-INTERVAL-28"],
            current_message={"text": "想看有拉链的，能装下我的硬壳眼镜盒吗？"},
        )
        checks = reply_fact_checks("PR", resources.prepare(request))
        for claim in (
            "您提到的手机、钥匙、硬壳眼镜盒和短伞，目录里列为典型可装物品。",
            "目录列有硬壳眼镜盒和短折伞。",
            "主仓仅按包身外部测量为 18×14×8 厘米。",
            "18 的内径是 18×14×8 厘米。",
        ):
            with self.subTest(claim=claim):
                self.assertTrue(grounding_issues(claim, checks))
        for supported in (
            "目录列有眼镜盒，您的硬壳眼镜盒能否放下需要实物核对。",
            "目录没有列硬壳眼镜盒。主仓不能按包身外部尺寸推算。",
            "18 的包身外尺寸是 18×14×8 厘米，主仓尺寸未提供。",
        ):
            with self.subTest(supported=supported):
                self.assertEqual(grounding_issues(supported, checks), [])
        trace = request.model_copy(update={"product_ids": ["LIUXI-TRACE-A5"]})
        checks = reply_fact_checks("PR", resources.prepare(trace))
        self.assertEqual(grounding_issues("主仓内尺寸约 22.5×16×2 厘米。", checks), [])

    def test_item_frequency_is_preserved_when_restating_customer_history(self):
        request = GenerateInput(
            scenario="PO", product_id="LIUXI-INTERVAL-28",
            current_message={"text": "18,800 元比我想的高一点，我想再想想。"},
            history=[{"role": "customer", "text": "常带眼镜盒，有时还要带把短伞。"}],
        )
        resources = Resources()
        checks = reply_fact_checks("PO", resources.prepare(request))
        self.assertTrue(grounding_issues("您通勤常带眼镜盒和短伞，下次可以带来实际试放。", checks))
        self.assertTrue(grounding_issues("您平时经常带一把短折伞。", checks))
        self.assertEqual(grounding_issues("您常带眼镜盒，偶尔也会带短伞。", checks), [])
        self.assertEqual(grounding_issues("如果您以后经常带短伞，也可以带来试放。", checks), [])
        current = request.model_copy(update={"history": []})
        checks = reply_fact_checks("PO", resources.prepare(current))
        self.assertEqual(grounding_issues("如果常带短折伞，可以用实物核对。", checks), [])
