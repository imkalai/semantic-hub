"""Small text helpers shared by the planner and the LookML generator."""

import re


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "shes", "ches", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def snake(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return re.sub(r"_+", "_", s) or "item"


def humanize(identifier: str) -> str:
    """order_items -> Order items"""
    text = identifier.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else text


def lookml_string(text: str) -> str:
    """A value safe to put inside double quotes in LookML."""
    return " ".join(text.replace('"', "'").replace("\\", "/").split())


def format_bytes(n: int | float | None) -> str:
    if n is None:
        return "unknown size"
    units = ["bytes", "KB", "MB", "GB", "TB", "PB"]
    value = float(n)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def format_count(n: int | None) -> str:
    if n is None:
        return ""
    for div, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= div:
            v = n / div
            return f"{v:.1f}".rstrip("0").rstrip(".") + suffix
    return str(n)
