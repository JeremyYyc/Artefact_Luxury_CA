import re
from dataclasses import dataclass
from unicodedata import normalize

from backend.models import Scenario
from backend.reply_actions import asks_about_public_display
from backend.reply_requirements import private_entry_unverified


@dataclass(frozen=True)
class FactCheck:
    claim: re.Pattern[str]
    guidance: str

    def issue(self, text: str) -> dict | None:
        match = self.claim.search(text)
        if match is None:
            return None
        return {
            "code": "UNSUPPORTED_FACT",
            "reason": "该表述超出了本轮资料能支持的事实。",
            "fix": self.guidance,
            "quote": match.group(),
            "related_slot": None,
        }


def check(pattern: str, guidance: str) -> FactCheck:
    return FactCheck(re.compile(pattern), guidance)


HAND_CARRY_FREES_HANDS = check(
    r"(?:(?:取下|拆(?:下|掉)|摘下)[^。！？]{0,8}肩带|肩带[^。！？]{0,8}(?:取下|拆(?:下|掉)|摘下))"
    r"[^。！？]{0,24}手提[^。！？]{0,18}(?:空出手|腾出手|解放双手)",
    "手提和肩背分别描述。只有已知肩背方式可腾出手，拆下肩带改手提时不要说能空出手。",
)
DETACHED_STRAP_SHOULDER_CARRY = check(
    r"(?:拆下|取下|摘下|卸下|卸掉|拆掉)肩带[^，,。！？；;]{0,9}(?:可|可以|能|继续|还能|再)?肩背"
    r"|肩带(?:拆下|取下|摘下|卸下|拆掉)后?[^，,。！？；;]{0,6}(?:可|可以|能|继续|还能|再)?肩背",
    "可拆肩带款装上肩带后肩背，取下肩带后手提。分别说明两种背法。",
)
HARD_COVER_IS_IRRELEVANT = check(
    r"硬(?:皮本|壳|封面|封本)[^，。！？]{0,10}(?:不影响|没有影响|无关|无所谓)",
    "只说明主仓的已知尺寸与测量口径。硬封本的形状、厚度和实际开合需用实物核对。",
)
DRYER_CAUSE = check(
    r"(?:热风|吹风机)[^。！？]{0,17}(?:伤(?:害|到|及|皮)|损伤|受损|损坏|变硬|变形|开裂|掉色)",
    "按手册说明不用吹风机及安全晾干方式，省略手册未记载的热风损伤后果。",
)
CARD_SLOT_CAUSE = check(
    r"(?:多塞|塞多|厚卡|多放)[^，,。！？；;]{0,26}(?:变形|撑(?:坏|大|松)|挤压卡槽|挤松卡槽|(?:导致|容易)[^。！？；;]{0,12}滑出)"
    r"|(?:多塞|塞多|厚卡|多放)[^，,。！？；;]{0,14}[，,]\s*(?:反而|会|可能|容易|会让)[^。！？；;]{0,12}(?:变形|滑出|挤压卡槽)",
    "用「不建议靠多塞来撑紧，同一个卡槽不要塞多张厚卡」作共同答复。把滑卡当作顾客已观察到的现象。"
    "只引用手册的禁忌，不解释多塞会造成什么，包括挤压、变形、撑松或滑卡。",
)
VISIT_OUTCOME = check(
    r"(?<!如果)(?:后来|之后|最近)[^。！？]{0,10}(?:一直)?没(?:顾上|来(?:过|店)|去(?:过|店)|试(?:过|放|背)|能来)"
    r"(?![吗呢吧])(?=[，,。！？!?；;]|$)",
    "只复述客户当时说有空再来或已聊过的偏好。之后是否到店或试过没有记录，可以轻问，也可以省略。",
)
RECENT_WEATHER = check(
    r"(?:这几天|近来|最近|今天|这两天)[^。！？]{0,8}(?:天气(?:转凉|降温|升温|变冷|变热)|降温|连日下雨|一直下雨)",
    "没有本轮地点和天气资料时，只写问候或祝愿，省略近期天气判断。",
)
PUBLIC_ONLY = check(
    r"(?:不同光线|不同灯光|衣料旁|灯光对比|多种光线)"
    r"|(?:届时|现场|公开陈列(?:时|期间)?)[^。！？]{0,15}(?:可以|可直接|能当场)[^。！？]{0,8}(?:试背|试用|上身)"
    r"|(?:两只|两款|两款包|18\s*(?:和|与|、)\s*28|28\s*(?:和|与|、)\s*18)[^，,。！？]{0,6}都(?:能|可(?:以)?)(?:看到|见到|试背|上身)"
    r"|(?:可(?:以)?|能)(?:和[^，,。！？]{0,5})?(?:一起)?比较\s*(?:18\s*(?:和|与|、)\s*28|28\s*(?:和|与|、)\s*18)"
    r"|(?:去看|到店看)陈列[、，,]\s*(?:一起)?比较\s*\d+"
    r"|(?:到时|届时)[^，,。！？]{0,10}(?:能|可(?:以)?)[^，,。！？]{0,12}(?:取物|拿取)[^，,。！？]{0,6}顺手"
    r"|(?:公开陈列|公开展示|陈列现场)[^。！？]{0,8}(?:可以|可|能)(?:留意|欣赏|看到|观察)[^。！？]{0,24}"
    r"(?:两只|两款|色泽|包身线条|外套旁|灯光|取物动作)",
    "公开陈列的资料只确认开放日期。省略具体展出款式和试背、灯光或衣料对比等体验。",
)
UNVERIFIED_INVITE_LOOKUP = check(
    r"(?:没有|查不到|未查到|没查到|未找到)\s*(?:正式)?(?:的)?邀请记录",
    "按顾客所说的没收到正式邀请和截图不能作为凭证来答。不要说核对过邀请记录。",
)
PRICE_CAUSE = check(
    r"(?:价差|差价|价格差异)[^，,。！？]{0,8}(?:来自|源于|在于|是因为|主要在|主要差在|差在|对应的是|由[^。！？]{0,10}造成)"
    r"|(?:这(?:款|只)|与别家|和别家|比别家|跟别家)[^，,。！？]{0,29}"
    r"(?:的差别|的差异|贵|的价格)[^。！？]{0,9}(?:主要在|在于|来自|因为)"
    r"|(?:元|块)[^，,。！？]{0,6}(?:主要)?差在",
    "分别用完整句说明目录价和产品特征，例如「目录价为 X 元。这款采用……」。省略「价差来自」「差价对应」等定价归因。",
)
UNVERIFIED_COMPETITOR = check(
    r"(?:^|[，,。！？；;])\s*(?:这两款|两者|这款的)?(?:差别|差异|区别)(?:主要)?(?:在|来自|源于)"
    r"|(?:比|与|和|跟)(?:别家|其他品牌)[^。！？]{0,25}(?:材质更|工艺更|更耐|更结实|用料更)",
    "顾客问与别家商品的区别时，先说明别家用料或结构尚未核实，再介绍本款。没有对照资料时避免用「差别在于」作对比结论。",
)
UNSCOPED_ALTERNATIVE = check(
    r"(?:没有|找不到|不存在|无)[^，,。！？]{0,14}(?:合适的|可用的)?[^，,。！？]{0,8}(?:替代(?:款|品)?|相近(?:的)?(?:款|包|卡夹))",
    "只有本轮关联目录可作判断依据。没有核实到预算内替代时，限定在已核实的关联款式，或只说明已知报价。",
)
ALTERNATIVE_SCOPE = re.compile(r"目录|关联|核实|确认|已知|核对|现有资料")
CLAUSE_BREAK = re.compile(r"[，,。！？!?；;]")
OCCASIONAL_CARDS = re.compile(r"(?:有时|偶尔)[^，。！？]{0,10}带\s*([\d一二两三四五六七八九十]+)\s*张卡")


def public_display_context(context: dict) -> bool:
    events = context.get("events", [])
    if not events or events[0].get("public_milestone", {}).get("type") != "boutique_display_opening":
        return False
    return bool(asks_about_public_display(context) or private_entry_unverified(context) or context.get("companion_prohibited"))


def reply_fact_checks(scenario: Scenario, context: dict) -> list[FactCheck]:
    products = context.get("products", [])
    history = "。".join(item["text"] for item in context.get("history", []) if item["role"] == "customer")
    current = (context.get("current_message") or {}).get("text", "")
    detachable = any("可拆肩带" in feature for p in products
                     for feature in p.get("use_cases_and_features", {}).get("features", []))
    if scenario == "BG" and detachable:
        return [HAND_CARRY_FREES_HANDS, DETACHED_STRAP_SHOULDER_CARRY]
    if scenario == "RE":
        known = history + "。" + (context.get("advisor_goal") or "") + "。" + (context.get("client_background") or "")
        checks = [] if VISIT_OUTCOME.claim.search(known) else [VISIT_OUTCOME]
        if not RECENT_WEATHER.claim.search((context.get("advisor_goal") or "") + "。" + current):
            checks.append(RECENT_WEATHER)
        for count in dict.fromkeys(OCCASIONAL_CARDS.findall(history)):
            variants = r"(?:四|4)" if count in ("四", "4") else re.escape(count)
            stronger = check(
                rf"(?:经常|常常|常|总是|每次|平时都)[^，。！？]{{0,6}}带\s*{variants}\s*张卡",
                "用卡频率仅用于校验，不是共同必答点。若本路确实引用了带卡习惯，不得把偶尔发生说成常态。",
            )
            if not stronger.claim.search(known):
                checks.append(stronger)
        return checks
    if scenario == "PC":
        avoid = [item for p in products for item in p.get("care_manual", {}).get("avoid", [])]
        return [
            rule for term, question, rule in (
                ("吹风机", r"吹风|热风|雨滴|溅[^。！？]{0,5}雨", DRYER_CAUSE),
                ("多张厚卡", r"塞|厚卡|撑紧|滑出|滑出来", CARD_SLOT_CAUSE),
            )
            if re.search(question, current) and any(term in item for item in avoid)
        ]
    if scenario == "EV" and public_display_context(context):
        return [PUBLIC_ONLY, UNVERIFIED_INVITE_LOOKUP] if private_entry_unverified(context) else [PUBLIC_ONLY]
    if scenario in ("PR", "PO"):
        checks = [DETACHED_STRAP_SHOULDER_CARRY] if detachable else []
        if any("size_and_capacity" in p for p in products):
            checks.append(HARD_COVER_IS_IRRELEVANT)
        for product in products:
            features = product.get("use_cases_and_features", {}).get("features", [])
            if not any("固定肩带" in feature for feature in features) or any("手提" in feature for feature in features):
                continue
            name = product["name"].split()
            if len(name) < 2 or not name[1].isdigit():
                continue
            number = re.escape(name[1])
            checks.append(check(
                rf"(?<!\d){number}(?!\d)(?![^。！？]{{0,18}}(?:不能|无法|不支持|没写|未写|未说明)[^。！？]{{0,6}}手提)"
                rf"[^。！？\d]{{0,26}}(?:可以|可|能|适合|支持)?手提"
                rf"(?:会|更|也|使用|方式|出门|比较|较|时|不便|不方便|就|[，,。！？]|$)",
                f"{name[0]} {name[1]} 的目录只支持固定肩带肩背。可按相同测量口径比较重量，省略该款手提体验。",
            ))
        if scenario == "PO":
            checks.extend((PRICE_CAUSE, UNSCOPED_ALTERNATIVE))
            if re.search(r"别家|其他品牌|其它品牌|竞品|别的品牌", current):
                checks.append(UNVERIFIED_COMPETITOR)
        return checks
    return []


def grounding_issues(text: str, checks: list[FactCheck]) -> list[dict]:
    text = normalize("NFKC", text)
    issues = []
    for rule in checks:
        if rule is UNSCOPED_ALTERNATIVE:
            text_for_rule = "。".join(clause for clause in CLAUSE_BREAK.split(text) if not ALTERNATIVE_SCOPE.search(clause))
        else:
            text_for_rule = text
        if issue := rule.issue(text_for_rule):
            issues.append(issue)
    return issues
