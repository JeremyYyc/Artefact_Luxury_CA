import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RequiredAnswer:
    guidance: str
    wording: str
    contradiction: str | None = None

    def answered_by(self, text: str) -> bool:
        return bool(re.search(self.wording, text)
                    and not (self.contradiction and re.search(self.contradiction, text)))


# The catalog must support each answer before it becomes a requirement.
CARE_ANSWERS = (
    ("肩带自然平放", r"肩带.*(?:平放|收|放)|固定肩带",
     RequiredAnswer("固定肩带收纳时自然平放。",
                    r"肩带[^。！？]{0,16}(?:平放|平铺|放平)|(?:平放|平铺|放平)[^。！？]{0,12}肩带",
                    r"(?:不要|别|不用|不必|不能)[^。！？]{0,8}(?:平放|平铺|放平)")),
    ("用软布沿外缘轻拭", r"叠层.{0,8}灰",
     RequiredAnswer("叠层浮灰用干软布沿外缘轻拭。", r"软布[^。！？]{0,16}(?:轻拭|轻擦|轻轻擦|轻轻拭)")),
    ("包住金属扣", r"收纳|收起来|怎么收|怎么放",
     RequiredAnswer("可拆肩带取下，金属扣用软布包好。", r"软布[^。！？]{0,18}包|包[^。！？]{0,18}软布")),
    ("将肩带和包身分开放", r"收纳|收起来|怎么收|怎么放",
     RequiredAnswer("肩带和包身分开放。", r"(?:分开|分别|单独)[^。！？]{0,14}(?:放|收)")),
    ("取出卡片，用干净软布轻按吸水", r"卡.{0,12}(?:拿|取|抽)出|(?:拿|取|抽)出.{0,10}卡",
     RequiredAnswer("卡夹沾水时先取出卡片。",
                    r"(?:拿|取|抽)出.{0,5}卡|把卡.{0,8}(?:拿|取|抽)出|卡片.{0,9}(?:拿|取|抽)出",
                    r"(?:不用|不必|别|不要)[^。！？]{0,10}(?:拿|取|抽)出")),
    ("打开放平", r"内衬.{0,15}(?:湿|水).{0,20}(?:合|关|拉)|合起来放",
     RequiredAnswer("内衬沾清水时先别合上，打开放平晾干。", r"(?:别|不要|不用|无需|不能)[^。！？]{0,8}(?:合|关|拉上)|(?:打开|敞开|摊开)[^。！？]{0,12}(?:放|晾|干)")),
)

NO_INVITATION = re.compile(r"(?:没(?:有)?|未)(?:正式)?(?:收到|接到|获得|获|被发出).{0,5}(?:邀请|邀约)|没被邀请|没人邀请")
SCREENSHOT_ENTRY = re.compile(r"截图.{0,14}(?:入场|进场|进去|能去|可以去|直接去|凭证|参加)")
NO_SCREENSHOT_ENTRY = RequiredAnswer(
    "截图不能当入场凭证。",
    r"截图[^。！？]{0,16}(?:不能|不可|无效|不算|不作|无法)|(?:不能|不可|无法)[^。！？]{0,15}(?:凭|拿|用|以)截图",
)
NO_PRIVATE_ENTRY = RequiredAnswer(
    "私享场仅限正式受邀者参加。",
    r"(?:仅限|只限|仅对|只对|只有)[^。！？]{0,12}(?:受邀|收到邀请)|邀请制|"
    r"(?:未|没)[^。！？]{0,9}(?:受邀|收到邀请)[^。！？]{0,10}(?:不能|不可|无法)",
)
PRIVATE_SIGNUP = re.compile(
    r"(?:请|可以|可在|方便|只要|想来|想参加|帮您|替您|给您|为您|我来)"
    r"[^。！？]{0,22}(?:报名|申请|登记|(?:核对|确认)[^。！？]{0,6}(?:资格|席位)|留位|(?:截止|前)[^。！？]{0,9}回复)"
    r"|(?:回复|报名|登记)[^。！？]{0,16}(?:即可|席位|名额)"
)


def private_entry_unverified(context: dict) -> bool:
    message = (context.get("current_message") or {}).get("text", "")
    events = context.get("events", [])
    return bool(events and events[0].get("rsvp", {}).get("access") == "invite_only"
                and (NO_INVITATION.search(message) or SCREENSHOT_ENTRY.search(message)))


def required_answers(context: dict) -> list[RequiredAnswer]:
    message = (context.get("current_message") or {}).get("text", "")
    if not message:
        return []
    steps = " ".join(
        step for product in context.get("products", [])
        for topic in product.get("care_manual", {}).get("care_topics", [])
        for step in topic["steps"]
    )
    answers = [answer for source, question, answer in CARE_ANSWERS if source in steps and re.search(question, message)]
    if private_entry_unverified(context):
        answers.append(NO_PRIVATE_ENTRY)
        if "截图" in message:
            answers.append(NO_SCREENSHOT_ENTRY)
    return answers


def invites_unverified_guest(text: str, context: dict) -> bool:
    if not private_entry_unverified(context):
        return False
    for sentence in re.split(r"[。！？!?；;]", text):
        for match in PRIVATE_SIGNUP.finditer(sentence):
            clause = re.split(r"[，,]", sentence[:match.start()])[-1]
            if not re.search(r"不能|不可|不可以|无法|别|请勿|不接受|不开放", clause[-8:]):
                return True
    return False
