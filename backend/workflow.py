import asyncio
import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from difflib import SequenceMatcher
from itertools import combinations
from operator import or_
from string import Template
from time import perf_counter
from typing import Annotated, Literal, NotRequired, TypedDict
from unicodedata import normalize

from jsonschema import Draft202012Validator
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Send

from backend.deepseek import DeepSeek, ModelError, ModelRateLimited
from backend.models import GenerateInput, Reply, RoutingDecision, Scenario, Slot

LOGGER = logging.getLogger("uvicorn.error.liuxi")
SLOTS: tuple[Slot, Slot, Slot] = (1, 2, 3)
# Each phase stops before the next one needs the remaining request time.
DRAFT_DEADLINE_FRACTIONS = (0.45, 0.84, 0.99)
REVIEW_DEADLINE_FRACTION = 0.65
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
NO_COMPANION = re.compile(r"(?:不能|不可|不允许|不支持|不接受|无法|禁止|谢绝)[^。！？；]{0,12}(?:携伴|同行|一起|带|陪同)|(?:仅限|只限|只对)[^。！？；]{0,10}(?:受邀本人|受邀顾客本人|受邀者本人)")
GUEST_ACTION = r"(?:携伴|同行|陪同|一起(?:来|去|参加|进|入场)|带.{0,6}(?:参加|入场|来|去|姐姐|朋友|家人))"
GUEST_HEDGE = re.compile(rf"(?:能否|能不能|是否能|可否|申请|确认|核实|问一下)[^,，。]{{0,12}}{GUEST_ACTION}|{GUEST_ACTION}(?:名额|资格|安排)?(?:仍|还)?(?:待确认|需确认|需要确认|再确认|待核实|需要核实)")
GUEST_ALLOWED = re.compile(rf"(?:^|[^不])(?:可以|允许|能带|可带).{{0,8}}{GUEST_ACTION}")
PUBLIC_DISPLAY = re.compile(r"正式陈列|正式开放|公开陈列|公开开放|(?:陈列.{0,12})?对公众开放")
STOCK_WORD = r"(?:现货|库存|有货)"
STOCK_CHECK = r"(?:(?:需要|仍需|还需|得|待|再|未|另行|另|帮您|无法|不能).{0,6}(?:确认|核实|核对|查))"
CURRENT_STOCK_CHECK = re.compile(rf"{STOCK_WORD}{CLAUSE}{{0,12}}{STOCK_CHECK}|{STOCK_CHECK}{CLAUSE}{{0,12}}{STOCK_WORD}")
STORE_STOCK_CHECK = re.compile(rf"(?:向|请|和|跟|让)\s*(?:门店|店里|精品店){CLAUSE}{{0,10}}(?:确认|核实|核对|查){CLAUSE}{{0,12}}{STOCK_WORD}")
REPLY_CLAUSE = re.compile(r"[，。！？；,.!?;]")
CARE_ACTION = re.compile(r"加油|上油|润滑|滴油|涂油|抹油|硬拉|强拉|停手|退回|退开|往回退|回拉|移开纸|擦拭|清洁|清洗|修理|拆卸|自行处理|湿巾|酒精|吹风|用水|刷洗")
EVENT_EXPERIENCE = re.compile(r"演示(?!区)|展示样品|试背|上身体验|雨滴|肩带收纳|包形支撑|取物动作|厚外套|光线下的色泽")
PURE_GREETING_GOAL = re.compile(r"(?:只|仅)(?:是|想|需|要|发个|发一条|简单|做个|做)?(?:问候|打招呼|打个招呼|祝福)")
GREETING_REQUEST = re.compile(r"[?？]|(?:最近|近来|后来).{0,14}(?:还好|顺心|顺利|有空|来过)(?:吗|吧)|(?:等|请|记得|方便时|有空)[^，。]{0,10}(?:回复|联系|微信|来店|到店)|有空再聊|随时(?:联系|微信|找我)|来店(?:试|聊)|到店(?:试|聊)|我帮您安排")
PICKING_REQUEST = re.compile(r"挑法|怎么(?:挑|选)|如何(?:挑|选)|从哪.{0,5}(?:挑|选)")
FIT_QUESTION = re.compile(r"尺寸|厚度|长宽|放得下|放不下|装得下|装不下|合得上|合不上|试放")
MEASURING = re.compile(r"量(?:一|下|个|一量|一下|好)|测(?:一|量)|量.{0,10}(?:厚度|尺寸|长宽)|到店.{0,10}试放")
HARD_COVER = re.compile(r"硬(?:皮|壳|封面?|本)[^，。！？]{0,8}(?:本|A5)|硬(?:皮|封面|壳)本|硬封\s*A5")
FIT_GUARANTEE = re.compile(r"正合适|刚刚?好|没问题|放得下|装得下|装得进|能一起放|可以一起放|一并放得下|能都放|装进去没问题")
FIT_UNCERTAINTY = re.compile(r"不(?:能|敢|可|保证|确定)|无法|尚未|未必|待核实|需.{0,5}(?:确认|试放|核对)|能否|是否|能不能|不一定")


class ReviewError(Exception):
    pass


class Draft(TypedDict):
    slot: Slot
    strategy: str
    strategy_prompt: str
    text: str | None
    precheck_issues: list[dict]
    attempt: int
    error_status: Literal[429, 502, 504] | None


class DraftUpdate(TypedDict):
    drafts: Annotated[dict[Slot, Draft], or_]


class GenerationTask(TypedDict):
    scenario: Scenario
    context: dict
    slot: Slot
    attempt: NotRequired[int]
    feedback: NotRequired[dict]


class WorkflowState(DraftUpdate):
    scenario: Scenario
    context: dict
    review: NotRequired[dict]
    model_reviewed: NotRequired[bool]


@dataclass(frozen=True)
class WorkflowRuntime:
    model: DeepSeek
    request_id: str
    deadline: float | None = None
    started_at: float = 0.0

    def stage_deadline(self, fraction: float) -> float | None:
        if self.deadline is None:
            return None
        return self.started_at + (self.deadline - self.started_at) * fraction


def as_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def generation_mode(scenario: Scenario, context: dict) -> str | None:
    current = context["current_message"]
    if scenario == "EV" and not current and context["events"]:
        return "event_invitation"
    if scenario == "RE" and not current and PURE_GREETING_GOAL.search(context["advisor_goal"] or ""):
        return "pure_greeting"
    if scenario == "PR" and current and PICKING_REQUEST.search(current["text"]) and not FIT_QUESTION.search(current["text"]):
        return "product_picking"
    return None


def contract_issues(text: str, scenario: Scenario, slot: Slot, mode: str | None, context: dict) -> list[dict]:
    issues = []
    text = normalize("NFKC", text)

    def add(code: str, reason: str, fix: str, match: re.Match) -> None:
        issues.append({"code": code, "reason": reason, "fix": fix, "quote": match.group(), "related_slot": None})

    if mode == "event_invitation" and slot in (1, 3):
        # An event name can itself mention an experience. The name is a required fact.
        event_name = normalize("NFKC", context["events"][0]["name"])
        wording = text.replace(event_name, "")
        if match := EVENT_EXPERIENCE.search(wording):
            add("STRATEGY_MISMATCH", "主动邀约的体验亮点由第 2 路承担。",
                "删除体验和偏好，只保留场次、资格和报名事实。第 1 路陈述信息，第 3 路从如何回复开始。", match)
        if not re.search(GUEST_ACTION, context["advisor_goal"] or "") and (match := re.search(GUEST_ACTION, wording)):
            add("STRATEGY_MISMATCH", "顾问本轮未要求主动讨论携伴。", "删除同行、同行人数及额外安排。", match)
    if mode == "pure_greeting" and (match := GREETING_REQUEST.search(text)):
        add("STRATEGY_MISMATCH", "本轮只问候，正文含问句或后续联系动作。",
            "采用当前纯问候分路。写陈述句，省掉旧事追问、回复要求和到店安排。", match)
    if mode == "product_picking" and slot == 3 and (match := MEASURING.search(text)):
        add("STRATEGY_MISMATCH", "顾客只要挑选方法，尚无具体物件可验证。",
            "给一个可以直接做的挑法：回想一次实际出门，选出最想改善的一个用包动作。不要求测量或到店。", match)
    if scenario in ("PR", "PO") and any("size_and_capacity" in product for product in context["products"]):
        for clause in REPLY_CLAUSE.split(text):
            if HARD_COVER.search(clause) and not FIT_UNCERTAINTY.search(clause) and (match := FIT_GUARANTEE.search(clause)):
                add("UNSUPPORTED_FACT", "尺寸和典型容量不能证明顾客的硬封面本与其他物品能一起放下。",
                    "删除适配保证。可说明尺寸口径和典型用途；顾客这本的适配仍待实物确认。", match)
                break
    return issues


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


def mentions_date(text: str, iso_date: str) -> bool:
    _, month, day = iso_date.split("-")
    return iso_date in text or bool(re.search(
        rf"(?<!\d)0?{int(month)}\s*(?:月\s*0?{int(day)}\s*(?:日|号)|/\s*0?{int(day)}(?!\d))", text,
    ))


def required_answer_issue(text: str, context: dict) -> dict | None:
    if context.get("companion_prohibited"):
        sentences = re.split(r"[。！？；.!?;]", text)
        private_clauses = [clause for clause in REPLY_CLAUSE.split(text) if not PUBLIC_DISPLAY.search(clause)]
        if not any(NO_COMPANION.search(clause) for clause in private_clauses) or any(
            GUEST_HEDGE.search(clause) or (GUEST_ALLOWED.search(clause) and not PUBLIC_DISPLAY.search(sentence))
            for sentence in sentences for clause in REPLY_CLAUSE.split(sentence)
        ):
            return {
                "code": "UNSUPPORTED_FACT", "reason": "已核实该场次仅限受邀本人，不能携伴。",
                "fix": "明确说明该私享场仅限受邀本人，不能携伴。公开陈列可作为另一个场次，不提申请或核实私享携伴。",
                "quote": None, "related_slot": None,
            }
        milestone = context["events"][0].get("public_milestone", {})
        if milestone.get("type") == "boutique_display_opening" and not any(
            PUBLIC_DISPLAY.search(sentence) and mentions_date(sentence, milestone["date"]) for sentence in sentences
        ):
            return {
                "code": "UNANSWERED", "reason": "携伴受限，草稿漏掉已核实的公开陈列安排。",
                "fix": f"另起一句说明 {milestone['date']} 起陈列正式对公众开放，可一起去看。",
                "quote": None, "related_slot": None,
            }
    for product in context["products"]:
        snapshot = product.get("inventory_snapshot")
        if not snapshot:
            continue
        colors = snapshot["colors"]
        snapshot_colors = {item["color"] for item in colors}
        other_colors = set(product.get("available_colors", [])) - snapshot_colors
        current_unverified = any(
            CURRENT_STOCK_CHECK.search(clause) and not any(color in clause for color in other_colors)
            for clause in REPLY_CLAUSE.split(text)
        )
        quantities = all(re.search(
            rf"{re.escape(item['color'])}{CLAUSE}{{0,16}}(?<!\d){item['stock_quantity']}\s*件"
            rf"|{re.escape(item['color'])}{CLAUSE}{{0,12}}(?:没货|缺货|无货|无库存|没有库存|没有现货|没有货)"
            if item["stock_quantity"] == 0 else
            rf"{re.escape(item['color'])}{CLAUSE}{{0,16}}(?<!\d){item['stock_quantity']}\s*件", text,
        ) for item in colors)
        if not (mentions_date(text, snapshot["as_of_date"]) and snapshot["location"] in text and quantities and current_unverified):
            stock = "、".join(f"{color['color']} {color['stock_quantity']} 件" for color in colors)
            return {
                "code": "UNANSWERED", "reason": "顾客明确问库存，草稿没有完整交代对应库存快照及现货仍需核实。",
                "fix": f"先说明 {snapshot['as_of_date']} {snapshot['location']}库存记录（{stock}），再说明现货仍需核实。其他问题也要回答。",
                "quote": None, "related_slot": None,
            }
    return None


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
    required_issue = required_answer_issue(normalized, context)
    if required_issue:
        return [required_issue]
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


def review_issues(state: WorkflowState, drafts: list[Draft], review: dict) -> None:
    results = review["slot_results"]
    context = state["context"]
    if context.get("companion_prohibited"):
        for item, draft in zip(results, drafts, strict=True):
            if draft["text"] and not draft["precheck_issues"]:
                item["issues"] = [issue for issue in item["issues"] if not (
                    issue["code"] == "UNANSWERED"
                    and re.search(r"(?:只|仅)(?:说明|回答|写)[^。]{0,18}(?:不能|不可)(?:携伴|同行)", issue["reason"])
                    and re.search(r"(?:能否|是否)[^。]{0,12}(?:同行|携伴|一起)", issue["reason"])
                )]
    if state["scenario"] == "PR" and context.get("allow_inventory_snapshot"):
        item, draft = results[2], drafts[2]
        if draft["text"] and not draft["precheck_issues"] and STORE_STOCK_CHECK.search(draft["text"]):
            item["issues"] = [issue for issue in item["issues"] if not (
                issue["code"] == "STRATEGY_MISMATCH"
                and re.search(r"没有|未给出|没给出|缺少", issue["reason"])
                and re.search(r"验证动作|核实动作", issue["reason"])
            )]
    if state["scenario"] == "RE" and not context["current_message"] and not CARE_ACTION.search(context["advisor_goal"] or ""):
        for item, draft in zip(results, drafts, strict=True):
            if draft["text"] and not draft["precheck_issues"] and not CARE_ACTION.search(draft["text"]):
                item["issues"] = [issue for issue in item["issues"] if not (
                    issue["code"] == "UNSAFE_CARE" and CARE_ACTION.search(issue["reason"])
                    and re.search(r"缺少|缺失|未|没有|应(?:当|先|明确|提示)", issue["reason"])
                )]

    check_group(drafts, review)


def check_group(drafts: list[Draft], review: dict, locked: frozenset[Slot] = frozenset()) -> None:
    results = review["slot_results"]
    texts = {draft["slot"]: re.sub(r"[\W_]", "", normalize("NFKC", draft["text"] or ""))
             for draft, item in zip(drafts, results, strict=True) if not item["issues"]}
    duplicates = {frozenset((a, b)) for a, b in combinations(texts, 2) if texts[a] == texts[b] or (
        min(len(texts[a]), len(texts[b])) >= 25 and SequenceMatcher(None, texts[a], texts[b], autojunk=False).ratio() >= 0.84
    )}
    if duplicates:
        compatible = (group for size in range(1, len(texts) + 1) for group in combinations(texts, size)
                      if all(frozenset(pair) not in duplicates for pair in combinations(group, 2)))
        retained = max(compatible, key=lambda group: (
            len(locked.intersection(group)), len(group), tuple(-slot for slot in group)
        ))
        for item in results:
            slot = item["slot"]
            if slot in texts and slot not in retained:
                related = next(other for other in retained if frozenset((slot, other)) in duplicates)
                item["issues"].append({
                    "code": "DUPLICATE_STRATEGY", "reason": f"正文与第 {related} 条高度相似。",
                    "fix": "保留必答事实，按本路策略改写核心切入点或下一步动作。不要只改字词或顺序。",
                    "quote": None, "related_slot": related,
                })
    for item in results:
        item["status"] = "fail" if item["issues"] else "pass"
    review["rerun_slots"] = [item["slot"] for item in results if item["issues"]]


class ReplyWorkflow:
    def __init__(self, pack: dict):
        self.pack = pack
        self.version = f'{pack["prompt_version"]}+slot-recovery.1'
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
        draft_worker = worker.compile()
        graph.add_node("generate_reply", draft_worker)
        graph.add_node("regenerate_reply", draft_worker)
        graph.add_node("review_replies", self.review_replies)
        graph.add_node("check_regenerated", self.check_regenerated)
        graph.add_conditional_edges(START, self.first_batch, ["generate_reply"])
        graph.add_edge("generate_reply", "review_replies")
        graph.add_conditional_edges("review_replies", self.after_review, ["regenerate_reply", END])
        graph.add_edge("regenerate_reply", "check_regenerated")
        graph.add_conditional_edges("check_regenerated", self.after_review, ["regenerate_reply", END])
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
        attempt = task.get("attempt", 1)
        scenario = task["scenario"]
        strategy = self.strategies[scenario][slot]
        context = task["context"]
        rules = self.pack.get("scenario_rules", {}).get(scenario)
        strategy_prompt = strategy["prompt"]
        mode = generation_mode(scenario, context)
        if override := self.pack.get("generation_modes", {}).get(mode, {}).get(str(slot)):
            strategy_prompt = override
        if rules:
            strategy_prompt = f"{rules}\n\n{strategy_prompt}"
        if context.get("companion_prohibited") and self.pack.get("companion_public"):
            primary_event = context["events"][0]
            if primary_event.get("public_milestone", {}).get("type") == "boutique_display_opening":
                scoped = self.pack["companion_public"][slot - 1]
                strategy_prompt = f"{strategy_prompt}\n\n{scoped}"
        feedback = task.get("feedback")
        feedback_block = ""
        if feedback:
            feedback_block = Template(self.pack["generation"]["retry_feedback"]).substitute(
                feedback_json=as_json(feedback),
            )
        prompt_context = context
        if mode == "event_invitation" and slot in (1, 3):
            prompt_context = {
                **context, "history": [], "products": [],
                "events": [{key: value for key, value in event.items() if key != "highlights"} for event in context["events"]],
            }
        messages = self.messages(
            "generation",
            input_json=as_json({"scenario": scenario, "slot": slot, "context": prompt_context}),
            strategy_id=strategy["strategy"],
            strategy_prompt=strategy_prompt,
            retry_feedback_block=feedback_block,
        )
        raw = None
        error_status = None
        try:
            async with asyncio.timeout_at(runtime.context.stage_deadline(DRAFT_DEADLINE_FRACTIONS[attempt - 1])):
                raw = await runtime.context.model.complete(messages, max_tokens=512)
        except ModelRateLimited:
            error_status = 429
        except TimeoutError:
            error_status = 504
        except ModelError:
            error_status = 502
        draft = self.parse("generation", raw)
        text = (draft["text"].strip() or None) if draft else None
        issues = precheck(text, context)
        if text:
            issues.extend(contract_issues(text, scenario, slot, mode, context))
        LOGGER.info("draft request_id=%s slot=%d attempt=%d ms=%d upstream_status=%s issues=%s", runtime.context.request_id,
                    slot, attempt, (perf_counter() - start) * 1000, error_status,
                    [issue["code"] for issue in issues])
        return {"drafts": {slot: {
            "slot": slot,
            "strategy": strategy["strategy"],
            "strategy_prompt": strategy_prompt,
            "text": text,
            "precheck_issues": issues,
            "attempt": attempt,
            "error_status": error_status,
        }}}

    def validated_review(self, raw: str | None, drafts: list[Draft]) -> dict:
        review = self.parse("review", raw)
        if not review or tuple(item["slot"] for item in review["slot_results"]) != SLOTS:
            raise ReviewError("invalid review response")
        failed = [item["slot"] for item in review["slot_results"] if item["status"] == "fail"]
        if failed != review["rerun_slots"]:
            raise ReviewError("inconsistent rerun slots")
        for item, draft in zip(review["slot_results"], drafts, strict=True):
            if any(issue["related_slot"] == item["slot"] for issue in item["issues"]):
                raise ReviewError("review reported a self-duplicate")
            prechecks = draft["precheck_issues"]
            if prechecks:
                codes = {issue["code"] for issue in prechecks}
                item["issues"] = [*prechecks, *(issue for issue in item["issues"] if issue["code"] not in codes)]
        return review

    async def review_replies(self, state: WorkflowState, runtime: Runtime[WorkflowRuntime]) -> dict:
        start = perf_counter()
        drafts = [state["drafts"][slot] for slot in SLOTS]
        options = [
            {key: value for key, value in draft.items() if key not in ("attempt", "error_status")}
            for draft in drafts
        ]
        messages = self.messages("review", input_json=as_json({
            "scenario": state["scenario"], "context": state["context"], "options": options, "locked_slots": [],
        }))
        model_reviewed = False
        try:
            async with asyncio.timeout_at(runtime.context.stage_deadline(REVIEW_DEADLINE_FRACTION)):
                raw = await runtime.context.model.complete(messages, max_tokens=1800)
            review = self.validated_review(raw, drafts)
        except (ModelError, TimeoutError, ReviewError) as error:
            LOGGER.warning("review request_id=%s fallback=local_only error=%s", runtime.context.request_id, type(error).__name__)
            review = self.local_review(drafts)
        else:
            review_issues(state, drafts, review)
            model_reviewed = True
        LOGGER.info("review request_id=%s round=1 ms=%d rerun_slots=%s issue_codes=%s",
                    runtime.context.request_id, (perf_counter() - start) * 1000, review["rerun_slots"],
                    [[issue["code"] for issue in item["issues"]] for item in review["slot_results"]])
        return {"review": review, "model_reviewed": model_reviewed}

    @staticmethod
    def local_review(drafts: list[Draft], previous: dict | None = None) -> dict:
        passed = {} if previous is None else {
            item["slot"]: item["primary_action"] for item in previous["slot_results"] if not item["issues"]
        }
        review = {"slot_results": [
            {"slot": draft["slot"], "primary_action": passed.get(draft["slot"]), "issues": list(draft["precheck_issues"])}
            for draft in drafts
        ]}
        check_group(drafts, review, frozenset(passed))
        return review

    def check_regenerated(self, state: WorkflowState, runtime: Runtime[WorkflowRuntime]) -> dict:
        drafts = [state["drafts"][slot] for slot in SLOTS]
        review = self.local_review(drafts, state["review"])
        LOGGER.info("final_check request_id=%s rerun_slots=%s issue_codes=%s", runtime.context.request_id,
                    review["rerun_slots"], [[issue["code"] for issue in item["issues"]] for item in review["slot_results"]])
        return {"review": review}

    @staticmethod
    def after_review(state: WorkflowState) -> str | list[Send]:
        review = state["review"]
        failed = review["rerun_slots"]
        if not failed or any(state["drafts"][slot]["attempt"] >= len(DRAFT_DEADLINE_FRACTIONS) for slot in failed):
            return END
        passed = [
            {key: state["drafts"][slot][key] for key in ("slot", "strategy", "text")}
            for slot in SLOTS if slot not in failed
        ]
        occupied_actions = [
            {"slot": item["slot"], "action": item["primary_action"]}
            for item in review["slot_results"]
            if item["slot"] not in failed and item.get("primary_action")
        ]
        tasks = []
        for item in review["slot_results"]:
            slot = item["slot"]
            if slot not in failed:
                continue
            precheck_issues = state["drafts"][slot]["precheck_issues"]
            issues = item["issues"]
            mode = generation_mode(state["scenario"], state["context"])
            if mode == "pure_greeting" or (mode == "product_picking" and slot == 3):
                # The current task's scoped instructions own the action. A review can still cite a failure.
                issues = [dict(issue, fix="按当前分路的动作和备用收尾改写。") if issue["code"] in (
                    "STRATEGY_MISMATCH", "DUPLICATE_STRATEGY",
                ) and issue not in precheck_issues else issue for issue in issues]
            tasks.append(Send("regenerate_reply", {
                "scenario": state["scenario"],
                "context": state["context"],
                "slot": slot,
                "attempt": state["drafts"][slot]["attempt"] + 1,
                "feedback": {"slot": slot, "issues": issues, "passed_replies": passed,
                             "occupied_actions": occupied_actions},
            }))
        return tasks

    async def generate(self, scenario: Scenario, context: dict, model: DeepSeek,
                       request_id: str, *, deadline: float | None = None) -> tuple[Reply, Reply, Reply]:
        runtime = WorkflowRuntime(model, request_id, deadline, asyncio.get_running_loop().time())
        state = await self.graph.ainvoke({"scenario": scenario, "context": context},
                                         context=runtime)
        if failed := state["review"]["rerun_slots"]:
            errors = {state["drafts"][slot]["error_status"] for slot in failed}
            if 504 in errors:
                raise TimeoutError("reply slot recovery timed out")
            if 429 in errors:
                raise ModelRateLimited("reply slot recovery was rate limited")
            raise ReviewError("could not produce three replies that pass local checks")
        initial_status = "passed" if state["model_reviewed"] else "local_only"
        first, second, third = (
            Reply.model_validate({
                **state["drafts"][slot],
                "review_status": "regenerated" if state["drafts"][slot]["attempt"] > 1 else initial_status,
            })
            for slot in SLOTS
        )
        return first, second, third
