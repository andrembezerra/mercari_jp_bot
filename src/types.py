from dataclasses import dataclass
from typing import TypedDict


class ItemData(TypedDict):
    id: str
    title: str
    price: str
    url: str
    image_url: str
    keyword: str


class NotificationItem(TypedDict):
    title: str
    url: str
    image_url: str
    price: str
    item_id: str
    numeric_price: int
    keyword: str
    timestamp: str


@dataclass(frozen=True)
class CommandContext:
    chat_id: str
    message_id: int | None
    text: str
    args: str
    reply_to_message_id: int | None
    photo_file_id: str | None
    replied_photo_file_id: str | None

