from app.services.note_parser import split_note_units


def test_uses_lines_when_document_has_no_blank_line() -> None:
    assert split_note_units(" first \nsecond\n") == [" first ", "second"]


def test_preserves_paragraph_text() -> None:
    assert split_note_units("one\ncontinued\n\ntwo") == ["one\ncontinued", "two"]
