from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Slot = Literal[1, 2, 3]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(InputModel):
    text: MessageText
    at: date | None = None


class HistoryMessage(Message):
    role: Literal["customer", "advisor"]


class GenerateInput(InputModel):
    scenario: Literal["PO"]
    client_id: str = Field(min_length=1, max_length=80)
    product_id: str = Field(min_length=1, max_length=80)
    color: str = Field(min_length=1, max_length=30)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=12)
    current_message: Message


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
    product_name: str
    price: Price
    strategy_labels: list[str]


class Reply(BaseModel):
    slot: Slot
    strategy: str
    text: str


class GenerateResponse(BaseModel):
    request_id: str
    scenario: Literal["PO"]
    workflow_version: str
    options: tuple[Reply, Reply, Reply]
