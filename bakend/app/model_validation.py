"""Static, field-specific correction hints for structured model responses."""


class ModelValidationError(ValueError):
    def __init__(self, location, code, correction):
        super().__init__(correction)
        self.schema_errors = [{'location': list(location), 'type': code, 'correction': correction}]
