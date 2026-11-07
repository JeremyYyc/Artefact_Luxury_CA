import asyncio
import json
import unittest

from backend.resources import Resources
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
        resources = Resources()
        self.context = resources.prepare(resources.presets[0].input)
        self.workflow = ReplyWorkflow(resources.prompts)

    async def test_runs_in_parallel_and_retries_only_failed_slot(self):
        model = StubModel()
        replies = await self.workflow.generate(self.context, model, "test")
        self.assertCountEqual(model.initial_slots, [1, 2, 3])
        self.assertEqual(model.retried_slots, [1])
        self.assertEqual(model.review_payloads[1]["locked_slots"], [2, 3])
        self.assertEqual([item["text"] for item in model.feedback["passed_replies"]], [DRAFTS[2], DRAFTS[3]])
        self.assertEqual([(reply.slot, reply.text) for reply in replies], list(DRAFTS.items()))

    async def test_does_not_return_partial_replies_after_retry(self):
        model = StubModel(persist_failure=True)
        with self.assertRaises(ReviewError):
            await self.workflow.generate(self.context, model, "test")
        self.assertEqual(model.retried_slots, [1])
        self.assertEqual(len(model.review_payloads), 2)

    async def test_reviewer_cannot_override_precheck_failure(self):
        model = StubModel(ignore_precheck=True)
        with self.assertRaisesRegex(ReviewError, "passed a precheck failure"):
            await self.workflow.generate(self.context, model, "test")
        self.assertEqual(model.retried_slots, [])

    def test_precheck_distinguishes_catalog_price_from_customer_budget(self):
        self.assertEqual(precheck("目录价是 1.48 万元，可先按您的 13,000 元预算考虑。", self.context), [])
        self.assertEqual(precheck("目录价为 14,000 元。", self.context)[0]["code"], "UNSUPPORTED_FACT")
