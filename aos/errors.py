class AosError(Exception):
    """A user-facing failure: a message plus an optional hint on how to fix it."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        d = {"error": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d
