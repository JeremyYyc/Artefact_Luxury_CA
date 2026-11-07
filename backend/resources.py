import json
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
    "PO": ("price",),
}
EVENT_FIELDS = ("event_id", "name", "starts_at", "ends_at", "venue", "highlights", "public_milestone")
CARE_FIELDS = ("version", "applicable_product_ids", "applicable_materials", "applicable_parts",
               "care_topics", "avoid", "contact_advisor_when")


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


class Resources:
    def __init__(self):
        data = ROOT / "data"
        clients = json.loads((data / "LIUXI_Client_Profiles.json").read_text(encoding="utf-8"))
        catalog = json.loads((data / "LIUXI_Product_Catalog.json").read_text(encoding="utf-8"))
        events = json.loads((data / "LIUXI_Event_Catalog.json").read_text(encoding="utf-8"))
        self.clients = {item["client_id"]: item for item in clients["clients"]}
        self.products = {item["product_id"]: item for item in catalog["products"]}
        self.events = {item["event_id"]: item for item in events["events"]}
        self.prompts = json.loads((ROOT / "backend/prompts/liuxi_v1.json").read_text(encoding="utf-8"))
        definitions = TypeAdapter(list[PresetDefinition]).validate_json((data / "presets.json").read_bytes())
        self.presets = []
        for preset in definitions:
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

    def resolve(self, request: GenerateInput) -> tuple[dict, list[dict], dict | None]:
        client = self.clients.get(request.client_id)
        ids = dict.fromkeys(filter(None, [request.product_id, *request.product_ids]))
        if client is None or any(product_id not in self.products for product_id in ids):
            raise InvalidInput("客户或商品资料无法找到，请重新载入预设。")
        products = [self.products[product_id] for product_id in ids]
        if request.scenario in ("PR", "PC", "PO") and not products:
            raise InvalidInput("请先选择要讨论的商品。")
        if request.scenario == "PO" and not request.color:
            raise InvalidInput("请先确认商品颜色。")
        if request.color and (not products or request.color not in {item["color"] for item in products[0]["color_options"]}):
            raise InvalidInput("所选颜色不在商品目录中，请核对后重试。")
        event = self.events.get(request.event_id)
        if event is None and (request.event_id or request.scenario == "EV"):
            raise InvalidInput("请先选择要讨论的活动场次。")
        return client, products, event

    def prepare(self, request: GenerateInput, *, now: datetime | None = None) -> dict:
        client, products, event = self.resolve(request)
        now = now or datetime.now(SHANGHAI)
        if request.current_message is None and (not request.advisor_goal or request.scenario == "PO"):
            raise InvalidInput("请填写本轮待回复消息或顾问任务。")
        if request.scenario == "BG" and request.outreach is None:
            raise InvalidInput("请填写问候类型。")

        selected_products = []
        for product in products:
            fields = ("product_id", "name", *PRODUCT_FIELDS[request.scenario])
            selected = {field: product[field] for field in fields if field in product}
            if "care_manual" in selected:
                manual = selected["care_manual"]
                selected["care_manual"] = {field: manual[field] for field in CARE_FIELDS if field in manual}
            selected_products.append(selected)
        if request.color:
            selected_products[0]["selected_color"] = request.color

        selected_events = []
        if event and request.scenario == "EV":
            state = invite_state(event, now)
            rsvp = event.get("rsvp", {})
            if request.current_message is None:
                if state != "open" or not event.get("venue"):
                    raise InvalidInput("该场次当前不可报名，请核实或更换活动后再发出邀约。")
                if rsvp.get("access") == "invite_only":
                    raise InvalidInput("请先核实私享活动的拟邀约资格。")
            selected = {field: event[field] for field in EVENT_FIELDS if field in event}
            selected["invite_state"] = state
            selected["rsvp"] = {key: value for key, value in rsvp.items() if key not in ("status", "capacity")}
            selected_events.append(selected)

        to_confirm = []
        if request.scenario == "PO":
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
        return {
            "effective_at": now.astimezone(SHANGHAI).isoformat(timespec="seconds"),
            "advisor_goal": request.advisor_goal,
            "current_message": request.current_message.model_dump(mode="json") if request.current_message else None,
            "history": [message.model_dump(mode="json") for message in request.history],
            "client": {field: client[field] for field in ("client_id", "salutation")},
            "outreach": request.outreach.model_dump(mode="json") if request.outreach else None,
            "products": selected_products,
            "events": selected_events,
            "verified_facts": [],
            "to_confirm": to_confirm,
            "allow_inventory_snapshot": False,
        }
