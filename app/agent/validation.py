"""参数校验：按工具的 JSON schema 在「执行前」做类型强转 + 必填报检。

模型常把数字写成字符串（"30"）、漏必填项、或给错类型。这里统一在执行前
按 schema 校验并强转，把脏参数转干净或优雅报错，避免工具 run 内崩溃，
而非由 registry 的异常兜底去接一个本可预防的错误。
"""
from typing import Any, Optional, Tuple

# JSON schema 的 type 字符串 → Python 强转函数
_TYPE_COERCERS = {
    "string": lambda v: str(v),
    "integer": lambda v: int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else int(float(v)),
    "number": lambda v: float(v) if not isinstance(v, bool) else float(v),
    "boolean": lambda v: bool(v) if not isinstance(v, str) else (str(v).strip().lower() in ("true", "1", "yes", "是", "y")),
}


def validate_arguments(schema: dict, arguments: dict) -> Tuple[bool, dict, Optional[str]]:
    """校验并强转模型给的工具参数。

    返回 (ok, coerced_arguments, error_message)。
    - 必填项缺失 → ok=False，error 指明缺哪个。
    - 类型不符（且无法强转）→ ok=False，error 指明字段。
    - 通过 → ok=True，coerced 为类型已修正的 dict（schema 未声明的字段透传）。
    """
    props: dict = (schema or {}).get("properties", {}) or {}
    required: list = (schema or {}).get("required", []) or []
    coerced: dict = {}

    # 必填报检
    for key in required:
        val = arguments.get(key)
        if val is None or val == "":
            return False, {}, f"缺少必填参数：{key}"

    # 类型强转（只处理 schema 声明的字段；多余字段原样透传，避免过度拒绝）
    for key, val in (arguments or {}).items():
        spec = props.get(key)
        if spec is None:
            coerced[key] = val
            continue
        t = spec.get("type")
        if t in _TYPE_COERCERS and val is not None:
            try:
                coerced[key] = _TYPE_COERCERS[t](val)
            except (ValueError, TypeError):
                return False, {}, f"参数 {key} 应为 {t}，但收到：{val!r}"
        else:
            coerced[key] = val

    return True, coerced, None
