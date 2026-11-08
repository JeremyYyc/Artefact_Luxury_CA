import asyncio
import json
import unittest
from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import app
from backend.deepseek import ModelError, ModelRateLimited
from backend.models import GenerateInput
from backend.resources import InvalidInput, Resources, invite_state
from backend.workflow import ReplyWorkflow, ReviewError, WorkflowRuntime, contract_issues, generation_mode, precheck

DRAFTS = {
    1: "沈女士，墨黑 18 的目录价是 14,800 元，九折现在还不能确认。",
    2: "沈女士，九折我现在还不能确认。我可以帮您核实这款是否有适用的优惠。",
    3: "沈女士，九折目前还不能确认。如果这是您的预算线，可以先放一放。",
}
ACTIONS = {1: "说明目录价", 2: "核实适用优惠", 3: "按预算暂缓"}


def reviewed_slot(slot, issues=(), *, action="说明当前信息"):
    return {"slot": slot, "status": "fail" if issues else "pass", "issues": list(issues), "primary_action": action}


class StubModel:
    def __init__(self, *, persist_failure=False, ignore_precheck=False, blank_retry=False):
        self.persist_failure = persist_failure
        self.ignore_precheck = ignore_precheck
        self.blank_retry = blank_retry
        self.initial_slots = []
        self.retried_slots = []
        self.review_payloads = []
        self.feedback = None
        self.all_started = asyncio.Event()

    async def complete(self, messages, *, max_tokens):
        body = messages[1]["content"].split("\n", 1)[1]
        payload, offset = json.JSONDecoder().raw_decode(body)
        if "options" in payload:
            self.review_payloads.append(payload)
            issues_by_slot = {
                item["slot"]: [] if self.ignore_precheck else item["precheck_issues"]
                for item in payload["options"]
            }
            return json.dumps({
                "slot_results": [
                    reviewed_slot(slot, issues, action=ACTIONS[slot])
                    for slot, issues in issues_by_slot.items()
                ],
                "rerun_slots": [slot for slot, issues in issues_by_slot.items() if issues],
            })

        slot = payload["slot"]
        retry = bool(body[offset:].strip())
        if retry:
            self.retried_slots.append(slot)
            self.feedback = json.loads(body[offset:].strip().split("\n", 1)[1])
            if self.blank_retry:
                return json.dumps({"text": " "})
        else:
            self.initial_slots.append(slot)
            if len(self.initial_slots) == 3:
                self.all_started.set()
            await asyncio.wait_for(self.all_started.wait(), timeout=2)

        text = DRAFTS[slot]
        if slot == 1 and (not retry or self.persist_failure):
            text = "折后价 13,320 元，您现在就可以下单。"
        return json.dumps({"text": text})


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.resources = Resources()
        self.presets = {item.id: item.input for item in self.resources.presets}
        self.context = self.resources.prepare(self.presets["PO-02"])
        self.workflow = ReplyWorkflow(self.resources.prompts)

    async def test_runs_in_parallel_and_retries_only_failed_slot(self):
        model = StubModel()
        replies = await self.workflow.generate("PO", self.context, model, "test")
        self.assertCountEqual(model.initial_slots, [1, 2, 3])
        self.assertEqual(model.retried_slots, [1])
        self.assertEqual(len(model.review_payloads), 1)
        self.assertEqual([item["text"] for item in model.feedback["passed_replies"]], [DRAFTS[2], DRAFTS[3]])
        self.assertEqual(model.feedback["occupied_actions"], [
            {"slot": 2, "action": ACTIONS[2]}, {"slot": 3, "action": ACTIONS[3]},
        ])
        self.assertEqual([(reply.slot, reply.text) for reply in replies], list(DRAFTS.items()))
        self.assertEqual([reply.review_status for reply in replies], ["regenerated", "passed", "passed"])

    async def test_does_not_return_partial_replies_when_retry_has_no_text(self):
        model = StubModel(blank_retry=True)
        with self.assertRaisesRegex(ReviewError, "three replies"):
            await self.workflow.generate("PO", self.context, model, "test")
        self.assertEqual(model.retried_slots, [1, 1])
        self.assertEqual(len(model.review_payloads), 1)

    async def test_precheck_retries_even_if_reviewer_passes_a_rejected_draft(self):
        model = StubModel(ignore_precheck=True)
        replies = await self.workflow.generate("PO", self.context, model, "test")
        self.assertEqual(model.retried_slots, [1])
        self.assertEqual(replies[0].text, DRAFTS[1])

    async def test_regeneration_cannot_return_an_unauthorized_price(self):
        model = StubModel(ignore_precheck=True, persist_failure=True)
        with self.assertRaisesRegex(ReviewError, "three replies"):
            await self.workflow.generate("PO", self.context, model, "test")
        self.assertEqual(model.retried_slots, [1, 1])
        self.assertEqual(len(model.review_payloads), 1)

    async def test_duplicate_retry_does_not_displace_the_locked_slot(self):
        class DuplicateRetry(StubModel):
            async def complete(self, messages, *, max_tokens):
                raw = await super().complete(messages, max_tokens=max_tokens)
                if self.retried_slots == [1]:
                    return json.dumps({"text": DRAFTS[2]})
                return raw

        model = DuplicateRetry()
        replies = await self.workflow.generate("PO", self.context, model, "test")
        self.assertEqual(model.retried_slots, [1, 1])
        self.assertEqual([(reply.slot, reply.text) for reply in replies], list(DRAFTS.items()))
        self.assertEqual([item["slot"] for item in model.feedback["passed_replies"]], [2, 3])
        self.assertEqual(model.feedback["issues"][0]["related_slot"], 2)

    async def test_same_store_trial_in_different_wording_retries_one_slot(self):
        class TrialModel:
            def __init__(self):
                self.retried = []

            async def complete(self, messages, *, max_tokens):
                body = messages[1]["content"].split("\n", 1)[1]
                payload, offset = json.JSONDecoder().raw_decode(body)
                if "options" in payload:
                    return json.dumps({"slot_results": [reviewed_slot(i) for i in (1, 2, 3)], "rerun_slots": []})
                slot = payload["slot"]
                if body[offset:].strip():
                    self.retried.append(slot)
                    return json.dumps({"text": "外尺寸无法判断。方便时量一下镜盒最厚的地方，我先帮您初筛。"})
                texts = {
                    1: "这款的外尺寸不代表开口大小。建议把镜盒带到店试放，留意拉链能否自然合上。",
                    2: "晚餐只带手机钥匙，18 更轻便。如果一定要带硬壳眼镜盒，28 的常见用途包括眼镜盒，不过仍需实物确认。",
                    3: "现在不能保证放得下。您下次带上平常用的硬壳眼镜盒过来，我当面替您把它放进去看一下。",
                }
                return json.dumps({"text": texts[slot]})

        model = TrialModel()
        context = self.resources.prepare(self.presets["PR-06"])
        replies = await self.workflow.generate("PR", context, model, "test")
        self.assertEqual(model.retried, [3])
        self.assertEqual([reply.review_status for reply in replies], ["passed", "passed", "regenerated"])
        self.assertIn("最厚的地方", replies[2].text)

    def test_precheck_distinguishes_catalog_price_from_customer_budget(self):
        self.assertEqual(precheck("目录价是 1.48 万元，可先按您的 13,000 元预算考虑。", self.context), [])
        self.assertEqual(precheck("目录价为 14,000 元。", self.context)[0]["code"], "UNSUPPORTED_FACT")

    def test_care_answers_cover_each_requested_operation(self):
        context = self.resources.prepare(GenerateInput(
            scenario="PC", product_id="LIUXI-INTERVAL-28",
            current_message={"text": "已经干了。叠层边有点灰，固定肩带是平放吗？"},
        ))
        self.assertEqual(precheck("叠层边缘用干燥软布轻拭。", context)[0]["code"], "UNANSWERED")
        self.assertEqual(precheck("肩带不用平放，叠层边缘用软布轻拭。", context)[0]["code"], "UNANSWERED")
        self.assertEqual(precheck("固定肩带自然平放，叠层边缘用干燥软布沿外缘轻拭。", context), [])

    def test_uninvited_customer_cannot_be_directed_to_private_signup(self):
        context = self.resources.prepare(GenerateInput(
            scenario="EV", event_id="LIUXI-EVT-20261121-WINTER-PREVIEW",
            current_message={"text": "我没收到邀请，直接拿截图去可以吗？"},
        ), now=datetime.fromisoformat("2026-11-06T12:00:00+08:00"))
        denial = "私享预览仅限受邀者，截图不能入场。"
        self.assertEqual(precheck(denial + "您没收到邀请，我这边无法确认您的席位。", context), [])
        self.assertEqual(precheck(denial + "请在截止前回复，我帮您核对席位。", context)[0]["code"],
                         "UNVERIFIED_COMMITMENT")
        self.assertEqual(precheck(denial + "公开陈列从11月22日开始，我可以帮您核对当天的开放时间。", context), [])
        self.assertEqual(precheck("11月22日有公开陈列。", context)[0]["code"], "UNANSWERED")

    async def test_selected_scenario_reaches_all_workers_and_reviewer(self):
        class RecordingModel:
            def __init__(self):
                self.payloads = []

            async def complete(self, messages, *, max_tokens):
                payload = json.loads(messages[1]["content"].split("\n", 1)[1])
                self.payloads.append(payload)
                if "slot" in payload:
                    prefix = "先别给拉链加油。" if payload["scenario"] == "PC" else ""
                    return json.dumps({"text": f"{prefix}第 {payload['slot']} 条草稿"})
                return json.dumps({
                    "slot_results": [reviewed_slot(slot) for slot in (1, 2, 3)],
                    "rerun_slots": [],
                })

        self.assertCountEqual([item.scenario for item in self.presets.values()], ["BG", "PR", "PC", "RE", "EV", "PO"])
        for request in self.presets.values():
            with self.subTest(scenario=request.scenario):
                model = RecordingModel()
                context = self.resources.prepare(request)
                replies = await self.workflow.generate(request.scenario, context, model, "test")
                self.assertEqual([payload["scenario"] for payload in model.payloads], [request.scenario] * 4)
                expected = self.resources.prompts["scenarios"][request.scenario]
                self.assertEqual([reply.strategy for reply in replies], [entry["strategy"] for entry in expected])

    def test_context_keeps_proactive_tasks_separate_from_history(self):
        for case_id in ("BG-03", "RE-03"):
            context = self.resources.prepare(self.presets[case_id])
            self.assertIsNone(context["current_message"])
            self.assertTrue(context["advisor_goal"])
            self.assertTrue(context["history"])
        self.assertEqual(self.resources.prepare(self.presets["RE-03"])["products"], [])

    async def test_reactivation_ignores_historical_care_omissions_but_retries_duplicates(self):
        class ReactivationModel:
            async def complete(self, messages, *, max_tokens):
                payload, offset = json.JSONDecoder().raw_decode(messages[1]["content"].split("\n", 1)[1])
                if "options" not in payload:
                    texts = [
                        "闻女士，许久没联系，祝您近来一切顺心。",
                        "闻女士，您之前提过长夹拉链快拉到底时有点紧，后来用着还顺手吗？",
                        "闻女士，哪天顺路，您可以带长夹来给我看看，按您的时间就好。",
                    ]
                    feedback = messages[1]["content"].split("\n", 1)[1][offset:]
                    if feedback:
                        self.retry = payload["slot"]
                        return json.dumps({"text": texts[2]})
                    return json.dumps({"text": texts[min(payload["slot"], 2) - 1]})
                issue = {"code": "UNSAFE_CARE", "reason": "未提醒不要加油和停手", "fix": "补护理禁忌",
                         "quote": None, "related_slot": None}
                return json.dumps({
                    "slot_results": [reviewed_slot(slot, [issue]) for slot in (1, 2, 3)],
                    "rerun_slots": [1, 2, 3],
                })

        model = ReactivationModel()
        context = self.resources.prepare(self.presets["RE-03"])
        replies = await self.workflow.generate("RE", context, model, "test")
        self.assertEqual(model.retry, 3)
        self.assertEqual(len({reply.text for reply in replies}), 3)

    def test_private_companion_rule_requires_clear_denial_and_public_date(self):
        request = GenerateInput(
            scenario="EV", event_id="LIUXI-EVT-20261121-WINTER-PREVIEW",
            related_event_ids=["LIUXI-EVT-20261017-TRACE-PREVIEW"],
            current_message={"text": "姐姐想给我参谋一下。我们能一起去预览吗？"},
        )
        now = datetime.fromisoformat("2026-11-06T12:00:00+08:00")
        context = self.resources.prepare(request, now=now)
        self.assertEqual([event["invite_state"] for event in context["events"]], ["open", "ended"])
        self.assertEqual(precheck("我帮您问一下姐姐能否同行。", context)[0]["code"], "UNSUPPORTED_FACT")
        self.assertEqual(precheck("私享场仅限受邀本人。", context)[0]["code"], "UNANSWERED")
        self.assertEqual(precheck("私享场仅限受邀本人，不能携伴。11 月 22 日起公开陈列，可一起去看。", context), [])
        self.assertEqual(precheck(
            "私享场不能携伴，但我可以帮您确认能否带姐姐。11月22日起公开陈列。", context,
        )[0]["code"], "UNSUPPORTED_FACT")

    async def test_public_companion_guidance_only_reaches_matching_request(self):
        class DraftModel:
            async def complete(self, messages, *, max_tokens):
                self.prompt = messages[0]["content"]
                return json.dumps({"text": "不能携伴。11月22日起公开陈列。"})

        now = datetime.fromisoformat("2026-11-06T12:00:00+08:00")
        private = GenerateInput(
            scenario="EV", event_id="LIUXI-EVT-20261121-WINTER-PREVIEW",
            current_message={"text": "姐姐想一起去预览，可以带她去吗？"},
        )
        regular = private.model_copy(update={"event_id": self.presets["EV-03"].event_id})
        model = DraftModel()
        runtime = SimpleNamespace(context=WorkflowRuntime(model, "test"))
        for request, expected in ((private, True), (regular, False)):
            await self.workflow.draft_reply(
                {"scenario": "EV", "context": self.resources.prepare(request, now=now), "slot": 3}, runtime,
            )
            self.assertEqual("以这条专用分路为准" in model.prompt, expected)

    async def test_invitation_experience_is_retried_instead_of_locked(self):
        class InvitationModel:
            def __init__(self):
                self.generation_contexts = {}

            async def complete(self, messages, *, max_tokens):
                body = messages[1]["content"].split("\n", 1)[1]
                payload, offset = json.JSONDecoder().raw_decode(body)
                if "options" in payload:
                    self.review_calls = getattr(self, "review_calls", 0) + 1
                    return json.dumps({"slot_results": [reviewed_slot(slot) for slot in (1, 2, 3)],
                                       "rerun_slots": []})
                slot = payload["slot"]
                self.generation_contexts[slot] = payload["context"]
                texts = {
                    1: "11月14日护理小课在上海精品店举行，报名截止后由顾问核对席位。",
                    2: "您之前关心的肩带收纳，这次护理小课会有相关演示。",
                    3: "如果想参加，可以在截止前微信回复，我收到后会再核对席位。",
                }
                text = texts[slot]
                if slot == 1 and not body[offset:].strip():
                    text += "现场会演示肩带收纳。"
                return json.dumps({"text": text})

        request = GenerateInput(
            scenario="EV", event_id=self.presets["EV-03"].event_id, advisor_goal="邀请顾客参加护理课。",
        )
        context = self.resources.prepare(request, now=datetime.fromisoformat("2026-11-06T12:00:00+08:00"))
        model = InvitationModel()
        replies = await self.workflow.generate("EV", context, model, "test")
        self.assertEqual(model.review_calls, 1)
        self.assertEqual([reply.review_status for reply in replies], ["regenerated", "passed", "passed"])
        self.assertNotIn("演示", replies[0].text)
        self.assertNotIn("highlights", model.generation_contexts[1]["events"][0])
        self.assertIn("highlights", model.generation_contexts[2]["events"][0])
        self.assertIn("highlights", context["events"][0])

    def test_pure_greeting_and_picking_prechecks_allow_the_scoped_fallbacks(self):
        greeting = {"current_message": None, "advisor_goal": "这次只问候。", "products": [], "events": []}
        greeting_mode = generation_mode("RE", greeting)
        self.assertEqual(greeting_mode, "pure_greeting")
        self.assertEqual(contract_issues("想起您，来打个招呼。", "RE", 3, greeting_mode, greeting), [])
        self.assertEqual(contract_issues("最近有顺路来过吗？", "RE", 2, greeting_mode, greeting)[0]["code"],
                         "STRATEGY_MISMATCH")
        picking = {"current_message": {"text": "我还没数过平时带什么，能先给个挑法吗？"}, "products": []}
        mode = generation_mode("PR", picking)
        self.assertEqual(mode, "product_picking")
        self.assertEqual(contract_issues("方便时量一下物品厚度。", "PR", 3, mode, picking)[0]["code"],
                         "STRATEGY_MISMATCH")
        self.assertEqual(contract_issues("先选一个最想改善的用包动作。", "PR", 3, mode, picking), [])
        self.assertIsNone(generation_mode("PR", {
            **picking, "current_message": {"text": "这两款怎么挑，硬封本的厚度要量吗？"},
        }))

    def test_hardcover_fit_requires_a_verified_fit_rather_than_cavity_dimensions(self):
        context = {"products": [{"size_and_capacity": {"measurement_basis": "主仓可用空间"}}]}
        self.assertEqual(contract_issues("硬皮本、会议纸和一支笔能一起放。", "PO", 2, None, context)[0]["code"],
                         "UNSUPPORTED_FACT")
        self.assertEqual(contract_issues("主仓约22.5×16×2厘米，硬皮本能否和纸笔一起放仍需试放。",
                                         "PO", 2, None, context), [])

    def test_private_invitation_confirmation_is_bound_to_client_and_event(self):
        request = GenerateInput(scenario="EV", client_id=self.presets["PO-02"].client_id,
                                event_id="LIUXI-EVT-20261121-WINTER-PREVIEW", advisor_goal="邀约客户本人")
        now = datetime.fromisoformat("2026-11-06T12:00:00+08:00")
        with self.assertRaisesRegex(InvalidInput, "核实私享活动"):
            self.resources.prepare(request, now=now)
        wrong = request.model_copy(update={"client_id": self.presets["EV-03"].client_id})
        payload = request.model_dump()
        payload["confirmed_invitation"] = {"client_id": request.client_id, "event_id": request.event_id}
        valid = GenerateInput.model_validate(payload)
        self.assertIn("席位仍需确认", self.resources.prepare(valid, now=now)["verified_facts"][0]["value"]["scope"])
        with self.assertRaisesRegex(InvalidInput, "核实私享活动"):
            self.resources.prepare(valid.model_copy(update={"client_id": wrong.client_id}), now=now)

    def test_stock_answer_distinguishes_snapshot_from_live_stock_and_discount(self):
        request = GenerateInput(
            scenario="PO", product_ids=["LIUXI-INTERVAL-18"], color="栗棕",
            current_message={"text": "岩灰没货的话，栗棕能便宜一点吗？"},
        )
        now = datetime.fromisoformat("2026-11-06T12:00:00+08:00")
        context = self.resources.prepare(request, now=now)
        snapshot = context["products"][0]["inventory_snapshot"]
        self.assertEqual(snapshot["colors"], [{"color": "岩灰", "stock_quantity": 0}])
        base = "11月6日上海精品店库存记录，岩灰为 0 件。"
        discount = "栗棕的优惠目前无法确认。"
        self.assertEqual(precheck(base + discount, context)[0]["code"], "UNANSWERED")
        self.assertEqual(precheck(base + discount + "栗棕现货仍需核实。", context)[0]["code"], "UNANSWERED")
        self.assertEqual(precheck(base + "岩灰现货仍需核实。" + discount, context), [])
        before_snapshot = datetime.fromisoformat("2026-11-05T12:00:00+08:00")
        self.assertNotIn("inventory_snapshot", self.resources.prepare(request, now=before_snapshot)["products"][0])

    def test_care_uses_the_selected_products_manual(self):
        products = self.resources.prepare(self.presets["PC-07"])["products"]
        self.assertEqual([item["product_id"] for item in products], ["LIUXI-TRACE-A5"])
        self.assertEqual(products[0]["care_manual"]["applicable_product_ids"], ["LIUXI-TRACE-A5"])

    def test_zipper_care_rejects_unsafe_steps_before_review(self):
        context = self.resources.prepare(self.presets["PC-07"])
        self.assertEqual(precheck("加一点油试试。", context)[0]["code"], "UNSAFE_CARE")
        unconditional = "先别给拉链加油。您轻轻把拉头退回一点，把纸角移出来再试，仍卡就停手。"
        self.assertEqual(precheck(unconditional, context)[0]["code"], "UNSAFE_CARE")
        self.assertEqual(precheck("不要自行加油。如果拉头能轻轻退回，再移开纸角。退不动或仍卡就停手。", context), [])
        self.assertEqual(precheck("先别加油。如果能把拉头轻轻往回退一点，再移开纸角。退不动就先停手。", context), [])
        self.assertEqual(precheck("先别上油，也别硬拉。您方便时可以把文件夹带来，我看看卡住的位置。", context), [])

    def test_expired_event_can_be_answered_but_cannot_be_invited_to(self):
        request = self.presets["EV-03"]
        event = self.resources.events[request.event_id]
        times = {
            "open": "2026-11-12T18:00:00+08:00",
            "closed": "2026-11-13T18:00:00+08:00",
            "started": "2026-11-14T14:30:00+08:00",
            "ended": "2026-11-14T15:15:00+08:00",
        }
        for expected, timestamp in times.items():
            self.assertEqual(invite_state(event, datetime.fromisoformat(timestamp)), expected)
        now = datetime.fromisoformat(times["ended"])
        self.assertEqual(self.resources.prepare(request, now=now)["events"][0]["invite_state"], "ended")
        invitation = request.model_copy(update={"current_message": None, "advisor_goal": "邀请顾客参加护理课"})
        with self.assertRaisesRegex(InvalidInput, "不可报名"):
            self.resources.prepare(invitation, now=now)


class ApiModel:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    async def complete(self, messages, *, max_tokens):
        payload = json.loads(messages[1]["content"].split("\n", 1)[1])
        self.calls.append(payload)
        if "slot" in payload:
            return json.dumps({"text": f"第 {payload['slot']} 条草稿"})
        if "options" in payload:
            return json.dumps({
                "slot_results": [reviewed_slot(slot) for slot in (1, 2, 3)],
                "rerun_slots": [],
            })
        return json.dumps(self.decision)


class WorkbenchApiTests(unittest.TestCase):
    def test_generate_returns_three_replies_with_the_regenerated_slot_marked(self):
        model = StubModel()
        with TestClient(app) as client:
            app.state.model = model
            preset = next(item for item in client.get("/api/presets").json() if item["id"] == "PO-02")
            response = client.post("/api/generate", json=preset["input"])
        self.assertEqual(response.status_code, 200)
        options = response.json()["options"]
        self.assertEqual([(item["slot"], item["text"]) for item in options], list(DRAFTS.items()))
        self.assertEqual([item["review_status"] for item in options], ["regenerated", "passed", "passed"])
        self.assertEqual(len(model.review_payloads), 1)

    def test_recovers_only_the_failed_slot_when_model_review_is_unavailable(self):
        class FaultModel:
            def __init__(self, fault):
                self.fault = fault
                self.drafts = []

            async def complete(self, messages, *, max_tokens):
                body = messages[1]["content"].split("\n", 1)[1]
                payload, offset = json.JSONDecoder().raw_decode(body)
                if "options" in payload:
                    await asyncio.Event().wait()
                slot = payload["slot"]
                retry = bool(body[offset:].strip())
                self.drafts.append((slot, retry))
                if slot == 1 and not retry:
                    if self.fault is None:
                        await asyncio.Event().wait()
                    else:
                        raise self.fault("initial draft failure")
                return json.dumps({"text": DRAFTS[slot]})

        for fault in (ModelRateLimited, ModelError, None):
            with self.subTest(fault=fault), TestClient(app) as client:
                model = FaultModel(fault)
                app.state.model = model
                app.state.timeout = 0.5
                preset = next(item for item in client.get("/api/presets").json() if item["id"] == "PO-02")
                response = client.post("/api/generate", json=preset["input"])
                self.assertEqual(response.status_code, 200)
                options = response.json()["options"]
                self.assertEqual([(item["slot"], item["text"]) for item in options], list(DRAFTS.items()))
                self.assertEqual([item["review_status"] for item in options], ["regenerated", "local_only", "local_only"])
                self.assertCountEqual(model.drafts, [(1, False), (2, False), (3, False), (1, True)])

    def test_malformed_review_does_not_discard_the_initial_drafts(self):
        class MalformedReview:
            async def complete(self, messages, *, max_tokens):
                payload = json.loads(messages[1]["content"].split("\n", 1)[1])
                if "options" in payload:
                    return "invalid JSON"
                return json.dumps({"text": DRAFTS[payload["slot"]]})

        with TestClient(app) as client:
            app.state.model = MalformedReview()
            preset = next(item for item in client.get("/api/presets").json() if item["id"] == "PO-02")
            response = client.post("/api/generate", json=preset["input"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["review_status"] for item in response.json()["options"]], ["local_only"] * 3)

    def test_free_input_routes_and_returns_prepared_basis(self):
        model = ApiModel({"scenario": "PR", "reason": "本轮咨询肩背包。", "question": None})
        with TestClient(app) as client:
            app.state.model = model
            response = client.post("/api/generate", json={
                "current_message": {"text": "想看看适合通勤的肩背包。"},
                "client_background": "顾客平时带短伞。",
                "product_ids": ["LIUXI-INTERVAL-28"],
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.json()
        self.assertEqual(body["scenario"], "PR")
        self.assertEqual(body["routing"], {"mode": "automatic", "reason": "本轮咨询肩背包。"})
        self.assertEqual(set(model.calls[0]), {"current_message", "advisor_goal", "history"})
        self.assertEqual(len(model.calls), 5)
        self.assertEqual(body["basis"], model.calls[-1]["context"])
        self.assertEqual(body["basis"]["client_background"], "顾客平时带短伞。")
        self.assertIsNone(body["basis"]["client"])
        self.assertEqual(body["basis"]["verified_facts"], [])
        self.assertEqual(body["basis"]["products"][0]["product_id"], "LIUXI-INTERVAL-28")
        self.assertNotIn("inventory", body["basis"]["products"][0])

    def test_free_proactive_task_does_not_turn_history_into_current_message(self):
        model = ApiModel({"scenario": "BG", "reason": "顾问要发送生日问候。", "question": None})
        with TestClient(app) as client:
            app.state.model = model
            response = client.post("/api/generate", json={
                "advisor_goal": "给顾客发一条生日祝福。",
                "history": [{"role": "customer", "text": "上次那款包还有货吗？"}],
            })
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(model.calls[0]["current_message"])
        self.assertEqual(response.json()["basis"]["advisor_goal"], "给顾客发一条生日祝福。")
        self.assertIsNone(response.json()["basis"]["current_message"])

    def test_ambiguous_or_invalid_route_cannot_start_generation(self):
        question = "顾客想具体解决什么问题？"
        decisions = [(422, {"scenario": "needs_clarification", "reason": "指代不清。", "question": question}), (502, None)]
        for status, decision in decisions:
            with self.subTest(status=status), TestClient(app) as client:
                model = ApiModel(decision)
                app.state.model = model
                response = client.post("/api/generate", json={"current_message": {"text": "这个怎么弄？"}})
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(len(model.calls), 1)
                if status == 422:
                    self.assertEqual(response.json()["detail"], question)

    def test_presets_keep_explicit_routing_and_library_exposes_display_fields(self):
        with TestClient(app) as client:
            model = ApiModel(None)
            app.state.model = model
            presets = client.get("/api/presets").json()
            self.assertEqual(len(presets), 6)
            payload = next(item["input"] for item in presets if item["id"] == "PO-02")
            payload.pop("color")
            response = client.post("/api/generate", json=payload)
            library = client.get("/api/library").json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["routing"]["mode"], "selected")
        self.assertEqual(len(model.calls), 4)
        self.assertNotIn("age", library["clients"][0])
        self.assertTrue(library["products"][0]["care_manual"]["care_topics"])
        self.assertIn("invite_state", library["events"][0])
