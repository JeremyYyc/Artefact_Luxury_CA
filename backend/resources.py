import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from backend.models import GenerateInput, Preset, PresetDefinition, Scenario

ROOT = Path(__file__).resolve().parent.parent
SHANGHAI = ZoneInfo("Asia/Shanghai")
PRODUCT_FIELDS: dict[Scenario, tuple[str, ...]] = {
    "BG": ("use_cases_and_features",),
    "PR": ("price", "weight", "size_and_capacity", "use_cases_and_features", "materials_and_craft"),
    "PC": ("care_manual",),
    "RE": ("use_cases_and_features",),
    "EV": ("care_manual",),
    "PO": ("price", "weight", "size_and_capacity", "use_cases_and_features", "materials_and_craft"),
}
EVENT_FIELDS = ("event_id", "name", "starts_at", "ends_at", "venue", "highlights", "public_milestone")
CARE_FIELDS = ("version", "applicable_product_ids", "applicable_materials", "applicable_parts",
               "care_topics", "avoid", "contact_advisor_when")
STOCK_REQUEST = re.compile(r"现货|库存|有货|没货|缺货|断货|补货|到货|还有吗|(?:有|能买到)吗$")
COMPANION_REQUEST = re.compile(r"携伴|同行|带.{0,8}(?:来|去|参加|入场|吗)|(?:一起|一块).{0,8}(?:去|来|参加|看|入场|进)")


class InvalidInput(ValueError):
    pass


def invite_state(event: dict, now: datetime) -> str:
    if not event.get("starts_at") or not event.get("ends_at"):
        return "unknown"
    starts = datetime.fromisoformat(event["starts_at"])
    ends = datetime.fromisoformat(event["ends_at"])
    if now >= ends:
        return "ended"
    if now >= starts:
        return "started"
    rsvp = event.get("rsvp", {})
    deadline = rsvp.get("deadline_at")
    if rsvp.get("status") == "closed" or (deadline and now >= datetime.fromisoformat(deadline)):
        return "closed"
    return "open" if rsvp.get("status") == "open" else "unknown"


def stock_snapshot(request: GenerateInput, products: list[dict], now: datetime) -> dict | None:
    if not products or not request.current_message:
        return None
    clauses = [clause for clause in re.split(r"[，。！？；,!?;]", request.current_message.text)
               if STOCK_REQUEST.search(clause.strip())]
    if not clauses:
        return None
    product = products[0]
    for item in products:
        short_name = item["name"].split(" ")[:2]
        if len(short_name) == 2 and any(item["name"] in clause or " ".join(short_name) in clause
                                       or (short_name[1].isdigit() and re.search(rf"(?<!\d){short_name[1]}(?!\d)", clause))
                                       for clause in clauses):
            product = item
            break
    options = product["color_options"]
    colors = [item["color"] for item in options if any(item["color"] in clause for clause in clauses)]
    if not colors and product is products[0] and request.color:
        colors = [request.color]
    inventory = product.get("inventory", {})
    as_of = inventory.get("as_of_date")
    if not as_of or as_of > now.astimezone(SHANGHAI).date().isoformat() or not inventory.get("location"):
        return None
    return {
        "product_id": product["product_id"], "name": product["name"],
        "as_of_date": as_of, "location": inventory["location"],
        "colors": [item for item in options if not colors or item["color"] in colors],
    }


class Resources:
    def __init__(self):
        data = ROOT / "data"
        clients = json.loads((data / "LIUXI_Client_Profiles.json").read_text(encoding="utf-8"))
        catalog = json.loads((data / "LIUXI_Product_Catalog.json").read_text(encoding="utf-8"))
        events = json.loads((data / "LIUXI_Event_Catalog.json").read_text(encoding="utf-8"))
        self.clients = {item["client_id"]: item for item in clients["clients"]}
        self.segment_labels = {key: item["label"] for key, item in clients["segment_definitions"].items()}
        self.brand = catalog["brand"]
        self.products = {item["product_id"]: item for item in catalog["products"]}
        self.events = {item["event_id"]: item for item in events["events"]}
        self.prompts = json.loads((ROOT / "backend/prompts/liuxi_v3.json").read_text(encoding="utf-8"))
        definitions = TypeAdapter(list[PresetDefinition]).validate_json((data / "presets.json").read_bytes())
        self.presets = []
        for preset in definitions:
            if preset.input.scenario is None or preset.input.client_id is None:
                raise ValueError(f"Preset {preset.id} requires an explicit scenario and client")
            client, products, event = self.resolve(preset.input)
            self.presets.append(Preset(
                **preset.model_dump(),
                client_name=client["full_name"],
                salutation=client["salutation"],
                product_name=products[0]["name"] if products else None,
                product_names=[product["name"] for product in products],
                price=products[0]["price"] if products and preset.input.scenario == "PO" else None,
                event_name=event["name"] if event else None,
                strategy_labels=[item["label"] for item in self.prompts["scenarios"][preset.input.scenario]],
            ))

    def library(self) -> dict:
        client_fields = ("client_id", "full_name", "salutation", "segment", "first_contact_date",
                         "last_contact_date", "recent_purchases", "event_participation")
        now = datetime.now(SHANGHAI)
        return {
            "clients": [{field: client[field] for field in client_fields} for client in self.clients.values()],
            "segment_labels": self.segment_labels,
            "products": list(self.products.values()),
            "events": [{**event, "invite_state": invite_state(event, now)} for event in self.events.values()],
            "brand": self.brand,
            "strategies": {scenario: [item["label"] for item in entries]
                           for scenario, entries in self.prompts["scenarios"].items()},
        }

    def resolve(self, request: GenerateInput) -> tuple[dict | None, list[dict], dict | None]:
        client = self.clients.get(request.client_id)
        ids = dict.fromkeys(filter(None, [request.product_id, *request.product_ids]))
        if (request.client_id and client is None) or any(product_id not in self.products for product_id in ids):
            raise InvalidInput("客户或商品资料无法找到，请重新载入预设。")
        products = [self.products[product_id] for product_id in ids]
        if request.scenario in ("PR", "PC", "PO") and not products:
            raise InvalidInput("请先选择要讨论的商品。")
        if request.color and (not products or request.color not in {item["color"] for item in products[0]["color_options"]}):
            raise InvalidInput("所选颜色不在商品目录中，请核对后重试。")
        event = self.events.get(request.event_id)
        if event is None and (request.event_id or request.scenario == "EV"):
            raise InvalidInput("请先选择要讨论的活动场次。")
        if any(event_id not in self.events for event_id in request.related_event_ids):
            raise InvalidInput("关联的其他活动场次不存在。")
        return client, products, event

    def prepare(self, request: GenerateInput, *, scenario: Scenario | None = None, now: datetime | None = None) -> dict:
        scenario = scenario or request.scenario
        if scenario is None:
            raise InvalidInput("请先确认本轮任务的场景。")
        client, products, event = self.resolve(request)
        now = now or datetime.now(SHANGHAI)
        if request.current_message is None and (not request.advisor_goal or request.scenario == "PO"):
            raise InvalidInput("请填写本轮待回复消息或顾问任务。")
        if request.scenario == "BG" and request.outreach is None:
            raise InvalidInput("请填写问候类型。")
        if scenario == "EV" and not event and request.current_message is None:
            raise InvalidInput("请先选择要邀约的活动场次。")

        selected_products = []
        for product in products:
            fields = ("product_id", "name", *PRODUCT_FIELDS[scenario])
            selected = {field: product[field] for field in fields if field in product}
            if scenario in ("PR", "PO"):
                selected["available_colors"] = [item["color"] for item in product["color_options"]]
            if "care_manual" in selected:
                manual = selected["care_manual"]
                selected["care_manual"] = {field: manual[field] for field in CARE_FIELDS if field in manual}
            selected_products.append(selected)
        if request.color:
            selected_products[0]["selected_color"] = request.color
        snapshot = stock_snapshot(request, products, now) if scenario in ("PR", "PO") else None
        if snapshot:
            for selected in selected_products:
                if selected["product_id"] == snapshot["product_id"]:
                    selected["inventory_snapshot"] = snapshot

        selected_events = []
        verified_facts = []
        companion_prohibited = False
        if event and scenario == "EV":
            state = invite_state(event, now)
            rsvp = event.get("rsvp", {})
            if request.current_message is None:
                if state != "open" or not event.get("venue"):
                    raise InvalidInput("该场次当前不可报名，请核实或更换活动后再发出邀约。")
                if rsvp.get("access") == "invite_only":
                    confirmed = request.confirmed_invitation
                    if not confirmed or confirmed.event_id != request.event_id or confirmed.client_id != request.client_id:
                        raise InvalidInput("请先核实私享活动的拟邀约资格。")
                    verified_facts.append({
                        "topic": "私享场拟邀约对象", "applies_to": request.event_id,
                        "value": {"client_id": confirmed.client_id, "scope": "仅限本人，席位仍需确认"},
                        "source": "顾问确认",
                    })
            companion_prohibited = bool(request.current_message and rsvp.get("max_guests_per_invitee") == 0
                                        and COMPANION_REQUEST.search(request.current_message.text))
            for event_id in dict.fromkeys([request.event_id, *request.related_event_ids]):
                source = self.events[event_id]
                selected = {field: source[field] for field in EVENT_FIELDS if field in source}
                selected["invite_state"] = invite_state(source, now)
                selected["rsvp"] = {key: value for key, value in source.get("rsvp", {}).items()
                                    if key not in ("status", "capacity")}
                if selected.get("public_milestone", {}).get("type") == "boutique_display_opening":
                    selected["public_milestone"] = {**selected["public_milestone"], "meaning": "店内陈列正式对公众开放"}
                selected_events.append(selected)

        to_confirm = []
        if scenario in ("PR", "PC", "PO") and not selected_products:
            to_confirm.append({
                "topic": "款式资料",
                "owner": "advisor",
                "note": "本轮未关联商品目录。款式、规格、报价和护理方法未经核实，请按任务说明需要补什么。",
            })
        if scenario == "PO":
            to_confirm.append({
                "topic": "折扣或报价授权",
                "owner": "advisor",
                "note": "本轮无已核实的优惠。若客户询问折扣，可直说目前还不能确认，省略内部审批、折后金额及承诺。",
            })
        if selected_events:
            to_confirm.append({
                "topic": "活动线上形式",
                "owner": "advisor",
                "note": "目录只提供线下场次资料。线上安排、直播及回放均未核实。",
            })
        elif scenario == "EV":
            to_confirm.append({"topic": "活动场次", "owner": "advisor", "note": "本轮未关联已核实的活动场次。"})
        return {
            "effective_at": now.astimezone(SHANGHAI).isoformat(timespec="seconds"),
            "advisor_goal": request.advisor_goal,
            "current_message": request.current_message.model_dump(mode="json") if request.current_message else None,
            "history": [message.model_dump(mode="json") for message in request.history],
            "client": {field: client[field] for field in ("client_id", "salutation")} if client else None,
            "client_background": request.client_background,
            "outreach": request.outreach.model_dump(mode="json") if request.outreach else None,
            "products": selected_products,
            "events": selected_events,
            "verified_facts": verified_facts,
            "to_confirm": to_confirm,
            "allow_inventory_snapshot": snapshot is not None,
            "companion_prohibited": companion_prohibited,
        }
