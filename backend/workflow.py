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
from backend.models import GenerateInput, Reply, RoutingDecision, Scenario, Slot

LOGGER = logging.getLogger("uvicorn.error.liuxi")
SLOTS: tuple[Slot, Slot, Slot] = (1, 2, 3)
NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
LABELED_PRICE = re.compile(rf"(目录价(?:格)?|折后价(?:格)?)\s*(?:是|为|:)?\s*¥?\s*({NUMBER})(\s*万)?")
ZIPPER_MOVEMENT = r"(?:退回|退开|往回退|向后退|回拉|退一点)"
ZIPPER_STEPS = re.compile(rf"{ZIPPER_MOVEMENT}|(?:移开|移出|取出|抽出)纸角|把纸角(?:移开|移出|取出)")
CLAUSE = r"[^,，。.!！?？;；]"
GENTLE_RETREAT = rf"(?:能|可以)(?:把拉头)?轻轻{CLAUSE}{{0,8}}(?:退|回拉)"
CONDITIONAL_RETREAT = re.compile(
    rf"(?:只有|只在|如果|若|要是){CLAUSE}{{0,8}}{GENTLE_RETREAT}|{GENTLE_RETREAT}{CLAUSE}{{0,5}}(?:时|才|就|再|的话)"
)
OIL_REFUSAL = re.compile(
    r"(?:别|不要|不能|不可(?:以)?|不建议|请勿|避免)\s*(?:自行|自己)?"
    r"(?:(?:给|往|在)?(?:这条|这款|这个)?(?:拉链|拉头)(?:上)?)?(?:自行|自己)?"
    r"(?:加|上|抹|滴|用|涂)\s*(?:一?点儿?|少量|任何)?(?:润滑)?油"
)
STOP_IF_STUCK = re.compile(r"(?:退不动|仍(?:然|旧)?卡|还(?:是)?卡|仍拉不动|还是拉不动)[^。.!！?？;；]{0,20}(?:停手|别再拉|不要再拉|别继续拉|别硬拉|不要硬拉)")


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
    scenario: Scenario
    context: dict
    slot: Slot
    feedback: NotRequired[dict]


class WorkflowState(DraftUpdate):
    scenario: Scenario
    context: dict
    review: NotRequired[dict]
    round: NotRequired[int]


@dataclass(frozen=True)
class WorkflowRuntime:
    model: DeepSeek
    request_id: str


def as_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def zipper_oil_issue(text: str, context: dict) -> dict | None:
    current = context["current_message"]
    if not current or "油" not in current["text"] or not any(word in current["text"] for word in ("拉链", "拉头")):
        return None
    prohibited = any(
        "拉链加油" in item
        for product in context["products"]
        for item in product.get("care_manual", {}).get("avoid", [])
    )
    if not prohibited:
        return None
    if not OIL_REFUSAL.search(text):
        reason = "顾客问能否给卡住的拉链加油，回复需要明确拒绝。"
    elif ZIPPER_STEPS.search(text) and not (CONDITIONAL_RETREAT.search(text) and STOP_IF_STUCK.search(text)):
        reason = "回复提出退拉头或移纸，却缺少轻轻退得动的前提或停手条件。"
    else:
        return None
    return {
        "code": "UNSAFE_CARE",
        "reason": reason,
        "fix": "明确别加油。只有能轻轻退回拉头时才移纸再试，退不动或仍卡就停手。也可以只建议停手并交由顾问查看。",
        "quote": None,
        "related_slot": None,
    }


def precheck(text: str | None, context: dict) -> list[dict]:
    if not text:
        return [{
            "code": "FORMAT",
            "reason": "未收到可解析的非空微信正文。",
            "fix": "仅返回包含非空 text 的 JSON 对象。",
            "quote": None,
            "related_slot": None,
        }]
    normalized = normalize("NFKC", text)
    safety_issue = zipper_oil_issue(normalized, context)
    if safety_issue:
        return [safety_issue]
    catalog_prices = {product["price"]["amount"] for product in context["products"] if "price" in product}
    for match in LABELED_PRICE.finditer(normalized):
        label, number, unit = match.groups()
        amount = Decimal(number.replace(",", ""))
        if unit:
            amount *= 10000
        if label.startswith("折后价") or amount not in catalog_prices:
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
        self.strategies = {
            scenario: {entry["slot"]: entry for entry in entries}
            for scenario, entries in pack["scenarios"].items()
        }
        self.validators = {
            kind: Draft202012Validator(pack[kind]["output_schema"])
            for kind in ("routing", "generation", "review")
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

    async def identify(self, request: GenerateInput, model: DeepSeek, request_id: str) -> RoutingDecision:
        start = perf_counter()
        payload = request.model_dump(mode="json", include={"advisor_goal", "current_message", "history"})
        messages = self.messages("routing", input_json=as_json(payload))
        data = self.parse("routing", await model.complete(messages, max_tokens=384))
        if not data or not data["reason"].strip() or (data["question"] is not None and not data["question"].strip()):
            raise ReviewError("invalid scene identification response")
        LOGGER.info("route request_id=%s scenario=%s ms=%d", request_id, data["scenario"],
                    (perf_counter() - start) * 1000)
        return RoutingDecision.model_validate(data)

    @staticmethod
    def first_batch(state: WorkflowState) -> list[Send]:
        return [
            Send("generate_reply", {"scenario": state["scenario"], "context": state["context"], "slot": slot})
            for slot in SLOTS
        ]

    async def draft_reply(self, task: GenerationTask, runtime: Runtime[WorkflowRuntime]) -> DraftUpdate:
        start = perf_counter()
        slot = task["slot"]
        scenario = task["scenario"]
        strategy = self.strategies[scenario][slot]
        feedback = task.get("feedback")
        feedback_block = ""
        if feedback:
            feedback_block = Template(self.pack["generation"]["retry_feedback"]).substitute(
                feedback_json=as_json(feedback),
            )
        messages = self.messages(
            "generation",
            input_json=as_json({"scenario": scenario, "slot": slot, "context": task["context"]}),
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
            "scenario": state["scenario"], "context": state["context"], "options": drafts, "locked_slots": locked_slots,
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
        tasks = []
        for item in review["slot_results"]:
            slot = item["slot"]
            if slot not in failed:
                continue
            precheck_issues = state["drafts"][slot]["precheck_issues"]
            precheck_codes = {issue["code"] for issue in precheck_issues}
            issues = [*precheck_issues, *(issue for issue in item["issues"] if issue["code"] not in precheck_codes)]
            tasks.append(Send("generate_reply", {
                "scenario": state["scenario"],
                "context": state["context"],
                "slot": slot,
                "feedback": {"slot": slot, "issues": issues, "passed_replies": passed},
            }))
        return tasks

    async def generate(self, scenario: Scenario, context: dict, model: DeepSeek,
                       request_id: str) -> tuple[Reply, Reply, Reply]:
        state = await self.graph.ainvoke({"scenario": scenario, "context": context},
                                         context=WorkflowRuntime(model, request_id))
        if state["review"]["rerun_slots"]:
            raise ReviewError("replies still failed review after one retry")
        first, second, third = (Reply.model_validate(state["drafts"][slot]) for slot in SLOTS)
        return first, second, third
