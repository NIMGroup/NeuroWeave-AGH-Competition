import pytest
from app.operation_events import operation_event_payload


@pytest.mark.parametrize("result", [
    {"id": "batch", "kind": "preserved", "status": "pending", "items": []},
    {"id": "batch", "kind": "changed", "status": "completed"},
    {"id": "workflow", "kind": "workflow", "status": "pending"},
    {"id": "fs", "action": "rename", "status": "pending", "source": "/old"},
])
def test_query_summaries_and_incomplete_records_do_not_become_action_cards(result):
    assert operation_event_payload(result) == {}


def test_complete_file_and_data_operations_keep_their_own_card_types():
    fs = {"id": "fs", "action": "rename", "status": "pending", "source": "/old", "request": {}}
    data = {"id": "batch", "kind": "data", "status": "pending"}
    assert operation_event_payload(fs) == {"operation": fs}
    assert operation_event_payload(data) == {"data_operation": data}
