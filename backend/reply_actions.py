import re

from backend.models import Scenario, Slot


RECOMMENDATION_GUIDANCE: dict[Slot, str] = {
    1: "只给直接判断和一项决定性依据。试放、测量、代查等具体动作留给第三路。",
    2: "只说明两种使用条件下的取舍。若只问配色或库存，区分坚持所问配色、愿意考虑其他已知配色两种选择。"
       "不要重复直答，也不要引入包型、尺寸、容量或没提到的随身物品。",
    3: "给一个与本轮决定直接相关的验证动作。选实物试放、补一个关键尺寸、或门店代查中的一项。"
       "只问哪款更轻时，答完重量后，可用顾客关心的背法或携带方式给出一个实用选择，不要求测量。",
}
PRIVATE_EVENT_GUIDANCE: dict[Slot, str] = {
    1: "先直答入场边界，按目录说明公开开放日即可。",
    2: "保留相同边界，用一项已知偏好说明公开陈列可看什么，省掉到店安排和试背承诺。",
    3: "保留相同边界。若目录列明公开陈列，可提出核对公开开放时段这个后续动作，省掉商品体验介绍。",
}
PUBLIC_DISPLAY_GUIDANCE: dict[Slot, str] = {
    1: "直答何时起可看公开陈列。只说已核实的公开日期，省掉到店安排。",
    2: "先答公开日期，用顾客在意的一项外观或搭配细节说明公开陈列值得看什么。省掉到店核对和试背安排。",
    3: "先答公开日期。按顾客返程节奏提出一个可选动作，只核对公开到店安排，省掉商品卖点和私享报名。",
}
PUBLIC_DISPLAY_QUESTION = re.compile(r"(?:之后|以后|结束后|过了|回来)[^。！？]{0,18}(?:还能|还可|能看|看得到|再去|再看)")

# Match offered follow-ups, not bare facts such as “适配仍需实物确认”.
ACTION_PATTERNS: dict[Scenario, tuple[tuple[str, str], ...]] = {
    "PR": (
        ("带实物到店试放", r"(?:到店|带[^。！？]{0,26}(?:来|过来)|拿[^。！？]{0,16}(?:来|过来))[^。！？]{0,26}(?:试放|试装|放进去|试一下)"),
        ("向门店核对现货", r"(?:帮您|替您|我来|向门店)[^。！？]{0,24}(?:核对|确认|核实|查)[^。！？]{0,16}(?:现货|库存|有货)"),
    ),
    "PC": (
        ("发送静态近照", r"(?:拍|发|传)[^。！？]{0,16}(?:近照|照片)"),
        ("把实物交给顾问", r"(?:带|交|拿)[^。！？]{0,19}(?:过来|给我|到店|来给我)[^。！？]{0,14}(?:看|确认|检查)"),
    ),
    "EV": (
        ("核对线上参与方式", r"(?:我|帮您|替您)[^。！？]{0,22}(?:核对|核实|问|确认)[^。！？]{0,16}(?:线上|直播|回放)"),
        ("核对公开到店安排", r"(?:我|帮您|替您)[^。！？]{0,26}(?:核对|核实|确认)[^。！？]{0,16}(?:公开|开放|到店|试背时|陈列安排)"
         r"|(?:公开|开放|到店|试背时)[^。！？]{0,15}(?:我|帮您|替您)[^。！？]{0,10}(?:核对|核实|确认)"),
    ),
    "PO": (
        ("发送比价商品链接或资料", r"(?:发|给)[^。！？]{0,16}(?:链接|商品信息|商品资料)|(?:链接|商品信息|商品资料)[^。！？]{0,14}(?:发给我|发我|给我)"),
    ),
}


def offered_followups(scenario: Scenario, text: str) -> set[str]:
    actions = set()
    for label, pattern in ACTION_PATTERNS.get(scenario, ()):
        for match in re.finditer(pattern, text):
            before = re.split(r"[，。！？；,.!?;]", text[:match.start()])[-1]
            if not re.search(r"无需|不必|不用|先别|不建议|不要|不能|无法", before[-10:]):
                actions.add(label)
                break
    return actions


def asks_about_public_display(context: dict) -> bool:
    events = context.get("events", [])
    message = (context.get("current_message") or {}).get("text", "")
    return bool(events and events[0].get("public_milestone", {}).get("type") == "boutique_display_opening"
                and PUBLIC_DISPLAY_QUESTION.search(message))
