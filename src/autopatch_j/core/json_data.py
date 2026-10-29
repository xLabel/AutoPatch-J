"""持久 JSON 边界的容器校验；业务字段与语义由所属模型校验。"""

from collections.abc import Mapping


def object_fields(value: object) -> dict[str, object]:
    """接受字符串键对象，拒绝将列表或其他值隐式转换为字典。"""
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise TypeError("期望字符串键的 JSON object。")
    return {key: item for key, item in value.items()}


def array_items(value: object) -> list[object]:
    """接受 JSON array；不能将字符串逐字符当作字段列表。"""
    if not isinstance(value, list):
        raise TypeError("期望 JSON array。")
    return list(value)
