"""Small allowlist for expected reference-input errors across process boundaries."""

REFERENCE_ERRORS = frozenset({"reference_no_object", "reference_region_no_match"})


class ReferenceQueryError(Exception):
    def __init__(self, code):
        if not isinstance(code, str) or code not in REFERENCE_ERRORS:
            raise ValueError("Unknown reference query error")
        self.code = code
        super().__init__(code)
