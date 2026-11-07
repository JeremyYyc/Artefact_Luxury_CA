import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from operator import or_
from string import Template
from time import perf_counter
from typing import Annotated, NotRequired, TypedDict
from unicodedata import normalize

from jsonschema import Draft202012Validator
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send

from backend.deepseek import DeepSeek
from backend.models import Reply, Slot

LOGGER = logging.getLogger("uvicorn.error.liuxi")
SLOTS: tuple[Slot, Slot, Slot] = (1, 2, 3)
NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
LABELED_PRICE = re.compile(rf"(目录价(?:格)?|折后价(?:格)?)\s*(?:是|为|:)?\s*¥?\s*({NUMBER})(\s*万)?")


class ReviewError(Exception):
    pass


class Draft(TypedDict):
    slot: Slot
    strategy: str
    strategy_prompt: str
    text: str | None
    precheck_issues: list[dict]


class DraftUpdate(TypedDict):
    drafts: Annotated[dict[Slot, Draft], or_]


class GenerationTask(TypedDict):
    context: dict
    slot: Slot
    feedback: NotRequired[dict]


class WorkflowState(DraftUpdate):
    context: dict
    review: NotRequired[dict]
    round: NotRequired[int]


@dataclass(frozen=True)
class WorkflowRuntime:
    model: DeepSeek
    request_id: str


def as_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def precheck(text: str | None, context: dict) -> list[dict]:
    if not text:
        return [{
            "code": "FORMAT",
            "reason": "未收到可解析的非空微信正文。",
            "fix": "仅返回包含非空 text 的 JSON 对象。",
            "quote": None,
            "related_slot": None,
        }]
    catalog_price = context["products"][0]["price"]["amount"]
    for match in LABELED_PRICE.finditer(normalize("NFKC", text)):
        label, number, unit = match.groups()
        amount = Decimal(number.replace(",", ""))
        if unit:
            amount *= 10000
        if label.startswith("折后价") or amount != catalog_price:
            return [{
                "code": "UNSUPPORTED_FACT",
                "reason": "正文标注的价格超出了本轮已核实的目录价。",
                "fix": "仅在需要时引用准确的目录价，省略其他金额。",
                "quote": match.group(),
                "related_slot": None,
            }]
    return []


class ReplyWorkflow:
    def __init__(self, pack: dict):
        self.pack = pack
        self.version = pack["prompt_version"]
        self.strategies = {entry["slot"]: entry for entry in pack["scenarios"]["PO"]}
        self.validators = {
            kind: Draft202012Validator(pack[kind]["output_schema"])
            for kind in ("generation", "review")
        }

        worker = StateGraph(GenerationTask, output_schema=DraftUpdate, context_schema=WorkflowRuntime)
        worker.add_node("draft_reply", self.draft_reply)
        worker.add_edge(START, "draft_reply")
        worker.add_edge("draft_reply", END)

        graph = StateGraph(WorkflowState, context_schema=WorkflowRuntime)
        graph.add_node("generate_reply", worker.compile())
        graph.add_node("review_replies", self.review_replies)
        graph.add_conditional_edges(START, self.first_batch, ["generate_reply"])
        graph.add_edge("generate_reply", "review_replies")
        graph.add_conditional_edges("review_replies", self.after_review, ["generate_reply", END])
        self.graph = graph.compile()

    def messages(self, kind: str, **values: str) -> list[dict[str, str]]:
        return [
            {"role": role, "content": Template(self.pack[kind][role]).substitute(values)}
            for role in ("system", "user")
        ]

    def parse(self, kind: str, raw: str | None) -> dict | None:
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if self.validators[kind].is_valid(value) else None

    @staticmethod
    def first_batch(state: WorkflowState) -> list[Send]:
        return [Send("generate_reply", {"context": state["context"], "slot": slot}) for slot in SLOTS]

    async def draft_reply(self, task: GenerationTask, runtime: Runtime[WorkflowRuntime]) -> DraftUpdate:
        start = perf_counter()
        slot = task["slot"]
        strategy = self.strategies[slot]
        feedback = task.get("feedback")
        feedback_block = ""
        if feedback:
            feedback_block = Template(self.pack["generation"]["retry_feedback"]).substitute(
                feedback_json=as_json(feedback),
            )
        messages = self.messages(
            "generation",
            input_json=as_json({"scenario": "PO", "slot": slot, "context": task["context"]}),
            strategy_id=strategy["strategy"],
            strategy_prompt=strategy["prompt"],
            retry_feedback_block=feedback_block,
        )
        draft = self.parse("generation", await runtime.context.model.complete(messages, max_tokens=512))
        text = (draft["text"].strip() or None) if draft else None
        issues = precheck(text, task["context"])
        LOGGER.info("draft request_id=%s slot=%d attempt=%d ms=%d issues=%s", runtime.context.request_id,
                    slot, 2 if feedback else 1, (perf_counter() - start) * 1000,
                    [issue["code"] for issue in issues])
        return {"drafts": {slot: {
            "slot": slot,
            "strategy": strategy["strategy"],
            "strategy_prompt": strategy["prompt"],
            "text": text,
            "precheck_issues": issues,
        }}}

    async def review_replies(self, state: WorkflowState, runtime: Runtime[WorkflowRuntime]) -> dict:
        start = perf_counter()
        drafts = [state["drafts"][slot] for slot in SLOTS]
        previous = state.get("review")
        locked_slots = [slot for slot in SLOTS if slot not in previous["rerun_slots"]] if previous else []
        messages = self.messages("review", input_json=as_json({
            "scenario": "PO", "context": state["context"], "options": drafts, "locked_slots": locked_slots,
        }))
        review = self.parse("review", await runtime.context.model.complete(messages, max_tokens=1800))
        if not review or tuple(item["slot"] for item in review["slot_results"]) != SLOTS:
            raise ReviewError("invalid review response")
        failed = [item["slot"] for item in review["slot_results"] if item["status"] == "fail"]
        if failed != review["rerun_slots"]:
            raise ReviewError("inconsistent rerun slots")
        for item, draft in zip(review["slot_results"], drafts, strict=True):
            if draft["precheck_issues"] and item["status"] == "pass":
                raise ReviewError("review passed a precheck failure")
            if any(issue["related_slot"] == item["slot"] for issue in item["issues"]):
                raise ReviewError("review reported a self-duplicate")

        round_number = 2 if previous else 1
        LOGGER.info("review request_id=%s round=%d ms=%d rerun_slots=%s issue_codes=%s",
                    runtime.context.request_id, round_number, (perf_counter() - start) * 1000, failed,
                    [[issue["code"] for issue in item["issues"]] for item in review["slot_results"]])
        return {"review": review, "round": round_number}

    @staticmethod
    def after_review(state: WorkflowState) -> str | list[Send]:
        review = state["review"]
        failed = review["rerun_slots"]
        if state["round"] == 2 or not failed:
            return END
        passed = [
            {key: state["drafts"][slot][key] for key in ("slot", "strategy", "text")}
            for slot in SLOTS if slot not in failed
        ]
        return [
            Send("generate_reply", {
                "context": state["context"],
                "slot": item["slot"],
                "feedback": {"slot": item["slot"], "issues": item["issues"], "passed_replies": passed},
            })
            for item in review["slot_results"] if item["status"] == "fail"
        ]

    async def generate(self, context: dict, model: DeepSeek, request_id: str) -> tuple[Reply, Reply, Reply]:
        state = await self.graph.ainvoke({"context": context}, context=WorkflowRuntime(model, request_id))
        if state["review"]["rerun_slots"]:
            raise ReviewError("replies still failed review after one retry")
        first, second, third = (Reply.model_validate(state["drafts"][slot]) for slot in SLOTS)
        return first, second, third
