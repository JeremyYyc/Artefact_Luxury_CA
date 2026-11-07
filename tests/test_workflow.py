import asyncio
import json
import unittest
from datetime import datetime

from backend.resources import InvalidInput, Resources, invite_state
from backend.workflow import ReplyWorkflow, ReviewError, precheck

DRAFTS = {
    1: "沈女士，墨黑 18 的目录价是 14,800 元，九折现在还不能确认。",
    2: "沈女士，九折我现在还不能确认。我可以帮您核实这款是否有适用的优惠。",
    3: "沈女士，九折目前还不能确认。如果这是您的预算线，可以先放一放。",
}


class StubModel:
    def __init__(self, *, persist_failure=False, ignore_precheck=False):
        self.persist_failure = persist_failure
        self.ignore_precheck = ignore_precheck
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
                    {"slot": slot, "status": "fail" if issues else "pass", "issues": issues}
                    for slot, issues in issues_by_slot.items()
                ],
                "rerun_slots": [slot for slot, issues in issues_by_slot.items() if issues],
            })

        slot = payload["slot"]
        retry = bool(body[offset:].strip())
        if retry:
            self.retried_slots.append(slot)
            self.feedback = json.loads(body[offset:].strip().split("\n", 1)[1])
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
        self.assertEqual(model.review_payloads[1]["locked_slots"], [2, 3])
        self.assertEqual([item["text"] for item in model.feedback["passed_replies"]], [DRAFTS[2], DRAFTS[3]])
        self.assertEqual([(reply.slot, reply.text) for reply in replies], list(DRAFTS.items()))

    async def test_does_not_return_partial_replies_after_retry(self):
        model = StubModel(persist_failure=True)
        with self.assertRaises(ReviewError):
            await self.workflow.generate("PO", self.context, model, "test")
        self.assertEqual(model.retried_slots, [1])
        self.assertEqual(len(model.review_payloads), 2)

    async def test_reviewer_cannot_override_precheck_failure(self):
        model = StubModel(ignore_precheck=True)
        with self.assertRaisesRegex(ReviewError, "passed a precheck failure"):
            await self.workflow.generate("PO", self.context, model, "test")
        self.assertEqual(model.retried_slots, [])

    def test_precheck_distinguishes_catalog_price_from_customer_budget(self):
        self.assertEqual(precheck("目录价是 1.48 万元，可先按您的 13,000 元预算考虑。", self.context), [])
        self.assertEqual(precheck("目录价为 14,000 元。", self.context)[0]["code"], "UNSUPPORTED_FACT")

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
                    "slot_results": [{"slot": slot, "status": "pass", "issues": []} for slot in (1, 2, 3)],
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
