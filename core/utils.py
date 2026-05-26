"""工具函数。"""


def normalize_code(raw: str) -> str:
    """标准化股票代码: 600519 → 600519.SH, 000001 → 000001.SZ"""
    code = raw.strip().upper()
    if "." in code:
        return code
    if code.startswith(("6", "5", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"
