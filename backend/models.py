from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Slot = Literal[1, 2, 3]
Scenario = Literal["BG", "PR", "PC", "RE", "EV", "PO"]
CatalogId = Annotated[str, StringConstraints(min_length=1, max_length=80)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(InputModel):
    text: MessageText
    at: date | None = None


class HistoryMessage(Message):
    role: Literal["customer", "advisor"]


class Outreach(InputModel):
    occasion: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    send_on: date | None = None


class InvitationConfirmation(InputModel):
    event_id: CatalogId
    client_id: CatalogId


class GenerateInput(InputModel):
    scenario: Scenario | None = None
    client_id: CatalogId | None = None
    client_background: MessageText | None = None
    product_id: CatalogId | None = None
    product_ids: list[CatalogId] = Field(default_factory=list, max_length=3)
    color: str | None = Field(default=None, min_length=1, max_length=30)
    event_id: CatalogId | None = None
    related_event_ids: list[CatalogId] = Field(default_factory=list, max_length=3)
    confirmed_invitation: InvitationConfirmation | None = None
    advisor_goal: MessageText | None = None
    outreach: Outreach | None = None
    history: list[HistoryMessage] = Field(default_factory=list, max_length=12)
    current_message: Message | None = None


class PresetDefinition(InputModel):
    id: str
    title: str
    input: GenerateInput


class Price(BaseModel):
    amount: int
    currency: Literal["CNY"]


class Preset(PresetDefinition):
    client_name: str
    salutation: str
    product_name: str | None = None
    product_names: list[str]
    price: Price | None = None
    event_name: str | None = None
    strategy_labels: list[str]


class Reply(BaseModel):
    slot: Slot
    strategy: str
    text: str
    review_status: Literal["passed", "regenerated"] = "passed"


class RoutingDecision(BaseModel):
    scenario: Scenario | Literal["needs_clarification"]
    reason: str
    question: str | None


class RoutingInfo(BaseModel):
    mode: Literal["selected", "automatic"]
    reason: str | None = None


class GenerateResponse(BaseModel):
    request_id: str
    scenario: Scenario
    workflow_version: str
    options: tuple[Reply, Reply, Reply]
    routing: RoutingInfo
    basis: dict
