"""User-owned task edits. Editing a goal never submits computation or file changes."""
import json
import time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .data_auth import check_conversation, principal
from .agent_identity import public_understanding, task_closure, closed_task_phase


class TaskEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    user_requirements: str = Field(min_length=1, max_length=8000)
    goal: str = Field(min_length=1, max_length=1200)
    scope_notes: str = Field(default='', max_length=1200)
    success_criteria: list[Annotated[str, Field(min_length=1,max_length=1200)]] = Field(default_factory=list, max_length=8)


class TaskActionError(ValueError):
    recoverable = False

    def __init__(self, code, owner, message):
        super().__init__(message)
        self.error_code, self.action_owner = code, owner


def assert_task_open(value):
    if task_closure(value):
        raise TaskActionError('task_user_closed', 'user',
            '此目标已由用户结束或确认完成，方案已归档；后续操作需开启新目标，不能修改或运行归档方案。')


def requires_goal_confirmation(store, value):
    if task_closure(value) or value.get('task_deleted') or not value.get('task_id'):
        return False
    if value.get('active_plan') or value.get('deliverable','answer') != 'answer':
        return True
    # Changing a model field to "answer" cannot abandon an established goal.
    with store.connection() as db:
        return bool(db.execute('SELECT 1 FROM managed_tasks WHERE task_id=? AND actor=?',
            (value['task_id'],principal.get().name)).fetchone())


def assert_plan_open(store, kind, identity):
    with store.connection() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='managed_task_plans'").fetchone():
            return
        row = db.execute("""SELECT t.payload FROM managed_task_plans p JOIN managed_tasks t
            ON t.task_id=p.task_id AND t.actor=p.actor
            WHERE p.kind=? AND p.plan_id=? AND
              (json_extract(t.payload,'$.user_completion') IS NOT NULL OR json_extract(t.payload,'$.user_closure') IS NOT NULL)""",
            (kind,identity)).fetchone()
        if row:
            assert_task_open(json.loads(row[0]))


class TaskConfirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    expected_plan: dict | None = None
    confirmed: Literal[True]


class GoalRunActive(ValueError):
    def __init__(self, workflow_ids):
        super().__init__('该目标仍有执行在进行；请先停止计算，再结束并归档')
        self.workflow_ids = workflow_ids


def require_idle(db, conversation_id, identity):
    from .managed_tasks import ACTIVE
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'chat_turns' in tables and db.execute(
            "SELECT 1 FROM chat_turns WHERE conversation_id=? AND status IN ('queued','running')",
            (conversation_id,)).fetchone():
        raise ValueError('请先停止当前请求，等待停止完成后再结束或确认任务')
    links = db.execute('SELECT kind,plan_id FROM managed_task_plans WHERE task_id=? AND actor=?',
                       (identity,principal.get().name)).fetchall()
    active_workflows = []
    for kind, plan_id in links:
        for table, column in [('runs','workflow_id')] if kind == 'workflow' else (
                [('data_operations','id')] if kind == 'data' else [('workspace_operations','id')]):
            if table in tables and db.execute(
                    f"SELECT 1 FROM {table} WHERE {column}=? AND status IN ({','.join('?' for _ in ACTIVE)})",
                    (plan_id,*ACTIVE)).fetchone():
                if kind == 'workflow':
                    active_workflows.append(plan_id)
                else:
                    raise ValueError('该目标仍有执行或检查在进行；请先等待结束或停止，再归档')
        if kind == 'workflow' and 'operations' in tables and db.execute(
                f"SELECT 1 FROM operations WHERE substr(entity_key,1,?)=? AND status IN ({','.join('?' for _ in ACTIVE)})",
                (len(plan_id)+1,plan_id+':',*ACTIVE)).fetchone():
            raise ValueError('流程仍在检查或更新中，请等待结束或停止后再确认完成')
    if active_workflows:
        raise GoalRunActive(active_workflows)


def confirm_task(store, conversation_id, confirmation, *, outcome='completed'):
    """Human-owned closure, distinct from a computed or model-reported success."""
    from .managed_tasks import ensure_schema, sync_state
    if outcome not in {'completed','ended'}:
        raise ValueError('未知目标结束方式')
    ensure_schema(store)
    check_conversation(store, conversation_id)
    with store.transaction() as db:
        row = db.execute('SELECT payload FROM assistant_tasks WHERE conversation_id=? AND actor=?',
                         (conversation_id,principal.get().name)).fetchone()
        value = json.loads(row[0]) if row else {}
        if value.get('task_id') != confirmation.task_id or value.get('task_revision',1) != confirmation.expected_revision:
            raise ValueError('当前任务已更新，请刷新后再确认完成')
        if task_closure(value):
            return public_understanding(value)  # Repeat clicks cannot close a later goal.
        if value.get('task_deleted'):
            raise ValueError('任务已删除，不能确认完成')
        active = value.get('active_plan')
        expected = confirmation.expected_plan
        if bool(active) != bool(expected) or active and any(
                str(active.get(k)) != str(expected.get(k)) for k in ('id','kind','revision')):
            raise ValueError('方案版本已更新，请查看当前方案后再确认完成')
        require_idle(db, conversation_id, confirmation.task_id)
        if outcome == 'completed' and active and active.get('task_revision',1) != value.get('task_revision',1):
            raise ValueError('当前方案对应旧目标，请更新方案后再确认完成')
        snapshot = None
        if active and active['kind'] == 'workflow':
            plan = db.execute('SELECT payload_json FROM workflows WHERE workflow_id=?',(active['id'],)).fetchone()
            if not plan and outcome == 'completed' or plan and str(json.loads(plan[0])['revision']) != str(active.get('revision')):
                raise ValueError('流程版本已更新，请刷新后再确认完成')
            snapshot = json.loads(plan[0]) if plan else None
        now = time.time()
        value['user_closure'] = {'closed_at':now,'actor':principal.get().name,'outcome':outcome,
            'task_revision':value['task_revision'],'plan':active,'previous_phase':value.get('phase')}
        if outcome == 'completed':
            value['user_completion'] = {'confirmed_at':now,'actor':principal.get().name,
                'task_revision':value['task_revision'],'plan':active}
        value.update(phase=closed_task_phase(value), awaiting_results=[],
            next_step='此目标已归档；后续目标将开启新任务。',
            closed_plan_snapshot=snapshot, updated_at=now)
        sync_state(db,value,conversation_id,principal.get().name)
        db.execute('UPDATE assistant_tasks SET payload=? WHERE conversation_id=? AND actor=?',
            (json.dumps(value,ensure_ascii=False),conversation_id,principal.get().name))
    return public_understanding(value)


def clear_result_waiting(store, conversation_id, confirmation):
    """Reserve the user's End intent before cancelling a paused computation."""
    with store.transaction() as db:
        row=db.execute('SELECT payload FROM assistant_tasks WHERE conversation_id=? AND actor=?',
            (conversation_id,principal.get().name)).fetchone()
        value=json.loads(row[0]) if row else {}
        if value.get('task_id') != confirmation.task_id or value.get('task_revision',1) != confirmation.expected_revision:
            raise ValueError('当前任务已更新，请刷新后再结束目标')
        active,expected=value.get('active_plan') or {},confirmation.expected_plan or {}
        if any(str(active.get(k)) != str(expected.get(k)) for k in ('id','kind','revision')):
            raise ValueError('方案版本已更新，请刷新后再结束目标')
        value['awaiting_results']=[]
        db.execute('UPDATE assistant_tasks SET payload=? WHERE conversation_id=? AND actor=?',
            (json.dumps(value,ensure_ascii=False),conversation_id,principal.get().name))


def edit_task(store, conversation_id, edit, *, clear=False):
    from .managed_tasks import ensure_schema, sync_state
    ensure_schema(store)
    check_conversation(store, conversation_id)
    with store.transaction() as db:
        if db.execute("SELECT 1 FROM chat_turns WHERE conversation_id=? AND status IN ('queued','running')", (conversation_id,)).fetchone():
            raise ValueError('请先停止当前请求，等待停止完成后再编辑或删除任务')
        row = db.execute('SELECT payload FROM assistant_tasks WHERE conversation_id=? AND actor=?', (conversation_id, principal.get().name)).fetchone()
        value = json.loads(row[0]) if row else {}
        if not clear:
            assert_task_open(value)
        if value.get('task_id') != edit.task_id or value.get('task_revision', 1) != edit.expected_revision:
            raise ValueError('当前任务已更新，请刷新后再编辑')
        revision = edit.expected_revision + 1
        if clear:
            if not task_closure(value):
                raise ValueError('请先确认完成或结束当前目标，再删除任务记录')
            sync_state(db,{**value,'task_deleted':True,'task_revision':revision},conversation_id,principal.get().name)
            value = {k:value[k] for k in ('scope','actor','protocol_version') if k in value}
            value.update(task_revision=revision, phase='answered', task_deleted=True)
        else:
            if not edit.goal.strip() or not edit.user_requirements.strip():
                raise ValueError('用户要求和任务目标不能为空')
            value.update(user_requirements=edit.user_requirements.strip(), goal=edit.goal.strip(),
                         scope_notes=edit.scope_notes, success_criteria=edit.success_criteria,
                         task_revision=revision, phase='needs_replan', goal_source='user',
                         next_step='目标已更新，请更新方案；实际执行仍需点击运行')
            # Preserve raw facts, input choices and the active plan. Old search
            # assumptions cannot become the contract of the user's new goal.
            for key in ('method_planning','method_selection','candidate_review','task_contract','information_needs'):
                value.pop(key, None)
        value['updated_at'] = time.time()
        if not clear:
            sync_state(db,value,conversation_id,principal.get().name)
        db.execute('UPDATE assistant_tasks SET payload=? WHERE conversation_id=? AND actor=?', (json.dumps(value, ensure_ascii=False), conversation_id, principal.get().name))
    return public_understanding(value)
