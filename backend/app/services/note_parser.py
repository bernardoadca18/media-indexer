import re

_BLANK_LINE = re.compile(r"\r?\n[ \t]*\r?\n|\r[ \t]*\r")


def split_note_units(raw: str) -> list[str]:
    if not raw:
        return []
    if _BLANK_LINE.search(raw):
        return [part for part in _BLANK_LINE.split(raw) if part.strip()]
    return [line for line in raw.splitlines() if line.strip()]
