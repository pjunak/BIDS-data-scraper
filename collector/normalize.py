"""Best-effort parsing of free text (titles, prices). Raw payloads are stored too, so mistakes can be fixed in ETL."""
import re

_AREA = re.compile(r"(?<![\d+])(\d{1,3}(?:[\s ]\d{3})+|\d+)((?:[.,]\d+)?)\s*m(?:²|2)\b", re.I)
_DISPOSITION = re.compile(r"\b(\d\s*\+\s*(?:kk|\d)|garsoni[eé]ra|atypick[ýy])\b", re.I)
_DIGITS = re.compile(r"\d+")


def area_from_text(text: str | None) -> float | None:
    """First area in the text: 'Prodej bytu 3+1 80 m²' -> 80.0, 'pozemek 1 200 m²' -> 1200.0."""
    match = _AREA.search(text or "")
    if not match:
        return None
    whole = re.sub(r"[\s ]", "", match.group(1))
    return float(whole + match.group(2).replace(",", "."))


def disposition_from_text(text: str | None) -> str | None:
    """'Prodej bytu 4 + 1' -> '4+1', 'Garsoniéra' -> 'garsoniéra'; None when the text has none."""
    match = _DISPOSITION.search(text or "")
    return re.sub(r"\s", "", match.group(1)).lower() if match else None


def price_from_text(text: str | None) -> int | None:
    """'5 490 000 Kč' -> 5490000, '16 500 Kč/měsíc' -> 16500, 'Cena na vyžádání' -> None."""
    digits = "".join(_DIGITS.findall((text or "").split("Kč")[0]))
    return int(digits) if digits else None
