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

