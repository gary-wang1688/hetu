"""
Python 3.13 .pyc Decompiler v2 — 恢复函数体逻辑
"""

import marshal
import dis
import sys
from pathlib import Path
from types import CodeType


def load_code(pyc_path: str) -> CodeType:
    with open(pyc_path, "rb") as f:
        f.read(16)
        return marshal.loads(f.read())


def dec_code(code: CodeType, indent: int = 0) -> list[str]:
    sp = "    " * indent
    out: list[str] = []
    instrs = list(dis.get_instructions(code))
    consts = code.co_consts
    names = code.co_names
    varnames = code.co_varnames
    locals_ = list(code.co_varnames)

    # --- module docstring ---
    ci = 0
    if consts and isinstance(consts[0], str) and indent == 0:
        ds = consts[0].strip()
        if len(ds) > 20 and not ds.startswith("param"):
            out.append('"""')
            out.append(ds)
            out.append('"""')
            out.append("")

    # --- imports ---
    i = 0
    while i < len(instrs):
        instr = instrs[i]

        # from __future__ import
        if i >= 2 and instr.opname == "IMPORT_NAME":
            prev1 = instrs[i - 1]
            prev2 = instrs[i - 2]
            if (prev1.opname == "LOAD_CONST" and isinstance(prev1.argval, tuple) and
                    prev2.opname == "LOAD_CONST" and prev2.argval == 0):
                for name in prev1.argval:
                    if name == "annotations":
                        out.append("from __future__ import annotations")
                i += 1
                continue

        # import X
        elif instr.opname == "IMPORT_NAME":
            # check previous for fromlist
            if i >= 1:
                prev = instrs[i - 1]
                if prev.opname == "LOAD_CONST" and isinstance(prev.argval, int):
                    # import X
                    out.append(f"import {instr.argval}")
                    i += 1
                    continue
                elif prev.opname == "LOAD_CONST" and isinstance(prev.argval, tuple):
                    # Check level
                    level = 0
                    if i >= 2 and instrs[i - 2].opname == "LOAD_CONST":
                        level = instrs[i - 2].argval
                    prefix = ""
                    if level > 0:
                        prefix = "." * level
                    for alias in prev.argval:
                        if alias == "*":
                            out.append(f"from {prefix}{instr.argval} import *")
                        else:
                            out.append(f"from {prefix}{instr.argval} import {alias}")
                    i += 1
                    continue
            i += 1
            continue

        # STORE_NAME for assignments
        elif instr.opname == "STORE_NAME" and i >= 1:
            prev = instrs[i - 1]
            if prev.opname == "LOAD_CONST":
                val = prev.argval
                if isinstance(val, (int, float, bool, str)):
                    if isinstance(val, str) and not val.startswith(("http", "/", "D:", ".", "logging.getLogger")):
                        out.append(f"{instr.argval} = {repr(val)}")
                    elif isinstance(val, str):
                        out.append(f'{instr.argval} = "{val}"')
                    elif isinstance(val, (int, float)):
                        out.append(f"{instr.argval} = {val}")
                    elif isinstance(val, bool):
                        out.append(f"{instr.argval} = {val}")

        i += 1

    if out and indent == 0 and any(l.strip() and not l.startswith(('"""',"from","import")) for l in out):
        pass  # don't add extra blank

    out.append("")
    return out


def dec_body(code: CodeType, indent: int = 0) -> list[str]:
    """Generate placeholder body with bytecode comments for complex functions."""
    sp = "    " * indent
    out: list[str] = []

    # Simple patterns
    instrs = list(dis.get_instructions(code))

    # Check for common patterns
    has_return = any(i.opname in ("RETURN_VALUE", "RETURN_CONST") for i in instrs)
    has_await = any(i.opname == "GET_AWAITABLE" for i in instrs)
    has_yield = any(i.opname in ("YIELD_VALUE", "SEND") for i in instrs)

    # Generate minimal body
    if has_return:
        returns = [i for i in instrs if i.opname in ("RETURN_VALUE", "RETURN_CONST")]
        if len(returns) == 1 and len(instrs) < 10:
            # Simple return
            return_instr = returns[0]
            idx = instrs.index(return_instr)
            if idx >= 1 and instrs[idx - 1].opname == "LOAD_CONST":
                val = instrs[idx - 1].argval
                if isinstance(val, str):
                    out.append(sp + f"return {repr(val)}")
                else:
                    out.append(sp + f"return {val}")
            elif idx >= 1 and instrs[idx - 1].opname == "LOAD_FAST":
                out.append(sp + f"return {instrs[idx-1].argval}")
            else:
                out.append(sp + "return ...")
        else:
            out.append(sp + "return ...")
    elif has_yield:
        out.append(sp + "yield ...")
    else:
        out.append(sp + "..." if not has_await else sp + "pass")

    return out


def rec_build(code: CodeType, indent: int = 0) -> list[str]:
    """Full recursive build: structure + body."""
    sp = "    " * indent
    out: list[str] = []

    # Constants
    consts = code.co_consts

    # Top-level structure
    if indent == 0:
        out.extend(dec_code(code, indent))

    # Process nested code objects (classes, functions)
    for const in consts:
        if not isinstance(const, CodeType):
            continue
        name = const.co_name
        if name.startswith("<"):
            continue

        # Class or function?
        is_class = name[0].isupper() or "__module__" in const.co_names

        if is_class:
            # Find bases
            bases = [
                n for n in const.co_names
                if n in ("BaseModel", "Enum", "StrEnum", "Broker", "BaseStrategy",
                         "DataProvider", "BaseDataProvider", "object")
            ]
            base_str = f"({', '.join(bases)})" if bases else ""
            out.append("")
            out.append(sp + f"class {name}{base_str}:")
            # Class body
            for sub_const in const.co_consts:
                if isinstance(sub_const, CodeType) and not sub_const.co_name.startswith("<"):
                    is_async = any(
                        i.opname == "GET_AWAITABLE"
                        for i in dis.get_instructions(sub_const)
                    )
                    prefix = "async def" if is_async else "def"
                    args = list(sub_const.co_varnames[:sub_const.co_argcount])
                    arg_str = ", ".join(args)
                    out.append(sp + f"    {prefix} {sub_const.co_name}({arg_str}):")
                    body = dec_body(sub_const, indent + 2)
                    if body:
                        out.extend(body)
                    else:
                        out.append(sp + "        ...")
            if not any(l.strip().startswith(("def ", "async def")) for l in out[-5:]):
                pass  # empty class or only constants

        else:
            # Module-level function
            is_async = any(
                i.opname == "GET_AWAITABLE"
                for i in dis.get_instructions(const)
            )
            prefix = "async def" if is_async else "def"
            args = list(const.co_varnames[:const.co_argcount])
            arg_str = ", ".join(args)
            out.append("")
            out.append(sp + f"{prefix} {name}({arg_str}):")
            body = dec_body(const, indent + 1)
            out.extend(body)

    return out


def main():
    project = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("D:/a_stock_research/hetu")

    for pyc in sorted(project.rglob("__pycache__/*.cpython-313.pyc")):
        rel = pyc.relative_to(project)
        parts = rel.parts
        parent = Path(*parts[:-2]) if len(parts) >= 3 else Path(".")
        stem = parts[-1].replace(".cpython-313", "").replace(".pyc", "") + ".py"
        out_path = project / parent / stem

        # Skip tests, alembic
        if "tests" in str(out_path) or "alembic" in str(out_path):
            continue

        code = load_code(str(pyc))
        lines = rec_build(code)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"  {out_path.relative_to(project)} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
