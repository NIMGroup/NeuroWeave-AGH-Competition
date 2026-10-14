"""Only complete operation records may become actionable conversation cards."""


def operation_event_payload(result):
    if result.get("kind") == "data":
        return {"data_operation": result}
    if (isinstance(result.get("id"), str) and isinstance(result.get("status"), str)
            and result.get("action") in {"mkdir", "copy", "move", "rename", "delete", "restore", "read-sensitive"}
            and isinstance(result.get("source"), str) and isinstance(result.get("request"), dict)):
        return {"operation": result}
    return {}
