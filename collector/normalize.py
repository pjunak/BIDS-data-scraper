"""Small best-effort parsers. Raw payloads are always stored too, so mistakes here can be fixed later in ETL."""
import re

_AREA = re.compile(r"(?<![\d+])(\d{1,3}(?:[\s\u00a0]\d{3})+|\d+)((?:[.,]\d+)?)\s*m(?:²|2)\b", re.I)
_DISP = re.compile(r"\b(\d\s*\+\s*(?:kk|\d)|garsoni[eé]ra|atypick[ýy])\b", re.I)
_DIGITS = re.compile(r"\d+")


def area_from_text(text: str | None) -> float | None:
    if not text:
        return None
    m = _AREA.search(text)
    if not m:
        return None
    raw = re.sub(r"[\s\u00a0]", "", m.group(1)) + m.group(2).replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def disposition_from_text(text: str | None) -> str | None:
    if not text:
        return None
    m = _DISP.search(text)
    return re.sub(r"\s", "", m.group(1)).lower() if m else None


def price_from_text(text: str | None) -> int | None:
    """'5 490 000 Kč' -> 5490000; 'Cena na vyžádání' -> None."""
    if not text:
        return None
    digits = "".join(_DIGITS.findall(text.split("Kč")[0]))
    return int(digits) if digits else None
