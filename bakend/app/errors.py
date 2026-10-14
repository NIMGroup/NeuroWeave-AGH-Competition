"""Application failures independent of HTTP; only the API boundary maps status codes."""


class ServiceError(Exception):
    def __init__(self, status_code: int, detail: object):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail
