"""Board text shown to an agent as data, never as instructions: a fence it can't close."""


def quoted(text: str, limit: int) -> str:
    # the fence can't be closed or reopened from inside: board text never contains it intact
    text = (text or "").strip().replace(">>>", "> > >").replace("<<<", "< < <")
    if len(text) > limit:
        text = text[:limit] + "…[clipped]"
    return f"<<<quoted\n{text}\n>>>"
