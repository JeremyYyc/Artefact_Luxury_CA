import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from backend.models import GenerateInput, Preset, PresetDefinition

ROOT = Path(__file__).resolve().parent.parent


class InvalidInput(ValueError):
    pass


class Resources:
    def __init__(self):
        data = ROOT / "data"
        clients = json.loads((data / "LIUXI_Client_Profiles.json").read_text(encoding="utf-8"))
        catalog = json.loads((data / "LIUXI_Product_Catalog.json").read_text(encoding="utf-8"))
        self.clients = {item["client_id"]: item for item in clients["clients"]}
        self.products = {item["product_id"]: item for item in catalog["products"]}
        self.prompts = json.loads((ROOT / "backend/prompts/liuxi_v1.json").read_text(encoding="utf-8"))
        definitions = TypeAdapter(list[PresetDefinition]).validate_json((data / "presets.json").read_bytes())
        self.presets = []
        for preset in definitions:
            client, product = self.resolve(preset.input)
            self.presets.append(Preset(
                **preset.model_dump(),
                client_name=client["full_name"],
                salutation=client["salutation"],
                product_name=product["name"],
                price=product["price"],
                strategy_labels=[item["label"] for item in self.prompts["scenarios"][preset.input.scenario]],
            ))

    def resolve(self, request: GenerateInput) -> tuple[dict, dict]:
        client = self.clients.get(request.client_id)
        product = self.products.get(request.product_id)
        if client is None or product is None:
            raise InvalidInput("客户或商品资料无法找到，请重新载入预设。")
        if request.color not in {item["color"] for item in product["color_options"]}:
            raise InvalidInput("所选颜色不在商品目录中，请核对后重试。")
        return client, product

    def prepare(self, request: GenerateInput) -> dict:
        client, product = self.resolve(request)
        product_fields = ("product_id", "name", "price")
        selected_product = {field: product[field] for field in product_fields}
        selected_product["selected_color"] = request.color
        return {
            "effective_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
            "advisor_goal": None,
            "current_message": request.current_message.model_dump(mode="json"),
            "history": [message.model_dump(mode="json") for message in request.history],
            "client": {field: client[field] for field in ("client_id", "salutation")},
            "outreach": None,
            "products": [selected_product],
            "events": [],
            "verified_facts": [],
            "to_confirm": [{
                "topic": "折扣或报价授权",
                "owner": "advisor",
                "note": "本轮无已核实的优惠。若客户询问折扣，可直说目前还不能确认，省略内部审批、折后金额及承诺。",
            }],
            "allow_inventory_snapshot": False,
        }
