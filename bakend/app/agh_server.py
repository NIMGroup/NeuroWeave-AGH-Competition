"""AGH owns conversations and approvals; this adapter reuses NeuroWeave services.

The private loopback bridge accepts calls only from the reviewed AGH package.
Browser edits are distinct from user-initiated, frozen execution actions.
Session keys correlate work; the deployment credential, never a model field,
authenticates the bridge. This single-operator deployment is not a tenant sandbox.
"""
from __future__ import annotations

import asyncio
import base64
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from .application import create_app
from .conversation_models import ConversationRecord
from .data_auth import Principal, principal
from .method_planning import MethodPlan
from .models import ChatRequest
from .response_policy import ResponsePolicy
from .task_contract import TaskContract
from .workflows.assistant_bindings import AssistantInput
from .agent_state import AgentTasks
from .goal_planner import read_routes, saved_route

_internal = ContextVar('agh_business_request', default=False)
_lock = RLock()
MAX_BODY = 1024 * 1024
EXECUTION_PATH = re.compile(
    r'^/api/(?:conversations/[^/]+/task/end|workflows/[^/]+/runs|runs/[^/]+/(?:cancel|retry|recover-freesurfer)|'
    r'data-operations/[^/]+/(?:confirm|run-script|cancel|undo)|'
    r'fs/(?:operations/[^/]+/(?:confirm|cancel)|trash/[^/]+/restore))$')
BLOCKED_PATH = re.compile(r'^/api/(?:chat(?:/|$)|chat-turns(?:/|$)|auth/|workflows/draft|conversations/[^/]+/compact|agh/bridge)')


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class PanelRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(pattern=r'^/api/', max_length=8192)
    method: str = Field(default='GET', pattern=r'^(GET|POST|PUT|PATCH|DELETE)$')
    body: dict | None = None


class WorkflowProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal: str = Field(min_length=1, max_length=20000)
    route_id: str = Field(default='', max_length=200)
    contract: TaskContract | None = None
    plan: MethodPlan | None = None
    bindings: list[AssistantInput] = Field(default_factory=list, max_length=100)
    workflow_id: str | None = Field(default=None, pattern=r'^wf_[a-f0-9]{12}$')
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def planning_source(self):
        if self.route_id:
            if self.plan is not None or self.bindings:
                raise ValueError('saved_route_cannot_be_overwritten_by_model_steps_or_bindings')
        elif self.plan is None or self.contract is None:
            raise ValueError('provide_a_saved_route_or_a_complete_method_plan_and_contract')
        return self


class SavedRouteDraft(BaseModel):
    """Native planning delivers the stored graph; inputs and parameters stay in the card."""
    model_config = ConfigDict(extra='forbid')
    goal: str = Field(min_length=1, max_length=20000)
    route_id: str = Field(min_length=1, max_length=200)
    workflow_id: str | None = Field(default=None, pattern=r'^wf_[a-f0-9]{12}$')
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def existing_revision(self):
        if bool(self.workflow_id) != bool(self.expected_revision):
            raise ValueError('workflow_revision_required_for_replanning')
        return self


class AGHBusiness:
    def __init__(self, app):
        self.app, self.s = app, app.state.services
        self.requests = {}
        self.submissions = {}
        self.downloads = {}
        self.ledger = AgentTasks(self.s.local_store)
        self.baseline = json.loads((Path(__file__).resolve().parents[2] / 'integrations/agh/baseline.json').read_text())
        with self.s.local_store._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS agh_sessions (
                    session_key TEXT PRIMARY KEY, conversation_id TEXT UNIQUE NOT NULL,
                    context_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agh_actions (
                    action_id TEXT PRIMARY KEY, session_key TEXT NOT NULL,
                    digest TEXT NOT NULL, payload_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'prepared', result_json TEXT,
                    tool_use_id TEXT, created_at TEXT NOT NULL,
                    user_requested INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS agh_user_messages (
                    session_key TEXT NOT NULL, native_sequence INTEGER NOT NULL,
                    business_sequence INTEGER NOT NULL, PRIMARY KEY(session_key,native_sequence));
                CREATE TABLE IF NOT EXISTS agh_workspace_preferences (
                    path TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    updated_at TEXT NOT NULL, removed INTEGER NOT NULL DEFAULT 0);
            ''')
            if 'removed' not in {row['name'] for row in db.execute('PRAGMA table_info(agh_workspace_preferences)')}:
                db.execute('ALTER TABLE agh_workspace_preferences ADD COLUMN removed INTEGER NOT NULL DEFAULT 0')
            if 'user_requested' not in {row['name'] for row in db.execute('PRAGMA table_info(agh_actions)')}:
                db.execute('ALTER TABLE agh_actions ADD COLUMN user_requested INTEGER NOT NULL DEFAULT 0')

    def workspace_preferences(self):
        with self.s.local_store._connect() as db:
            return {'workspaces': [{**dict(row), 'removed': bool(row['removed'])} for row in db.execute(
                'SELECT path,name,description,removed FROM agh_workspace_preferences ORDER BY path')]}

    def update_workspace_preferences(self, body):
        if set(body) != {'path', 'name', 'description'}:
            raise ValueError('invalid_workspace_preferences')
        path, name, description = (body[field] for field in ('path', 'name', 'description'))
        if (not isinstance(path, str) or len(path) > 4096 or not Path(path).is_absolute()
                or any(ord(char) < 32 for char in path)):
            raise ValueError('invalid_workspace_path')
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
                or re.search(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069\u2028\u2029]', name)):
            raise ValueError('invalid_workspace_name')
        if not isinstance(description, str) or len(description) > 2000 or '\x00' in description:
            raise ValueError('invalid_workspace_description')
        # Display metadata only: never rename, move or delete the native cwd.
        with _lock, self.s.local_store._connect() as db:
            db.execute('INSERT INTO agh_workspace_preferences(path,name,description,updated_at) VALUES (?,?,?,?) '
                'ON CONFLICT(path) DO UPDATE SET name=excluded.name,description=excluded.description,updated_at=excluded.updated_at',
                (path, name.strip(), description, now()))
        return self.workspace_preferences()

    def remove_workspace(self, body):
        if set(body) != {'path', 'removed'} or not isinstance(body.get('removed'), bool):
            raise ValueError('invalid_workspace_removal')
        path = body.get('path')
        if (not isinstance(path, str) or len(path) > 4096 or not Path(path).is_absolute()
                or any(ord(char) < 32 for char in path)):
            raise ValueError('invalid_workspace_path')
        # Only the workspace list changes. Native sessions, runs and files remain intact.
        with _lock, self.s.local_store._connect() as db:
            db.execute('INSERT INTO agh_workspace_preferences(path,name,description,updated_at,removed) VALUES (?,?,?, ?,?) '
                'ON CONFLICT(path) DO UPDATE SET removed=excluded.removed,updated_at=excluded.updated_at',
                (path, Path(path).name or path, '', now(), int(body['removed'])))
        return self.workspace_preferences()

    def session_removal_check(self, body):
        key = body.get('session_id')
        if set(body) != {'session_id'} or not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', key):
            raise ValueError('invalid_agh_session')
        conversation = 'agh_' + hashlib.sha256(key.encode()).hexdigest()[:24]
        # This check is read-only, including for an empty native session.
        with self.s.local_store._connect() as db:
            runs = db.execute("SELECT count(*) FROM runs r JOIN workflows w ON w.workflow_id=r.workflow_id "
                "WHERE json_extract(w.payload_json,'$.conversation_id')=? AND r.status IN ('queued','running','cancel_requested')",
                (conversation,)).fetchone()[0]
            operations = db.execute("SELECT count(*) FROM operations o JOIN workflows w ON "
                "(o.entity_key=w.workflow_id OR substr(o.entity_key,1,length(w.workflow_id)+1)=w.workflow_id||':') "
                "WHERE json_extract(w.payload_json,'$.conversation_id')=? AND o.status IN ('queued','running','cancel_requested')",
                (conversation,)).fetchone()[0]
            data = db.execute("SELECT count(*) FROM data_operations d JOIN data_operation_lookup l ON l.id=d.id "
                "WHERE l.conversation_id=? AND d.status IN ('pending','queued','running','analyzing','validating','cancel_requested','rolling_back')",
                (conversation,)).fetchone()[0]
            actions = db.execute("SELECT count(*) FROM agh_actions WHERE session_key=? AND state IN ('queued','dispatching','unknown')",
                (key,)).fetchone()[0]
        if runs or operations or data or actions:
            raise ValueError('此会话仍有服务器任务运行，请等待任务结束后再删除会话')
        return {'allowed': True}

    def session(self, key):
        if not key or len(key) > 200 or not re.fullmatch(r'[A-Za-z0-9_.:-]+', key):
            raise ValueError('invalid_agh_session')
        conversation = 'agh_' + hashlib.sha256(key.encode()).hexdigest()[:24]
        with _lock, self.s.local_store._connect() as db:
            db.execute('INSERT OR IGNORE INTO agh_sessions VALUES (?,?,?,?)', (key, conversation, '{}', now()))
            row = db.execute('SELECT * FROM agh_sessions WHERE session_key=?', (key,)).fetchone()
        if not self.s.local_store.get_conversation(conversation):
            self.s.local_store.create_conversation(ConversationRecord(conversation_id=conversation,
                title='AGH · NeuroWeave', created_at=now(), updated_at=now()))
        selected = json.loads(row['context_json'])
        selected.pop('interaction_mode', None)
        return conversation, selected

    def native_messages(self, key, body):
        conversation = self.session(key)[0]
        messages = body.get('messages', [])
        if not isinstance(messages, list) or len(messages) > 16:
            raise ValueError('invalid_native_messages')
        with _lock:
            for message in messages:
                if set(message) != {'sequence','text'} or not isinstance(message['sequence'], int) or message['sequence'] < 1 or not isinstance(message['text'], str) or not 1 <= len(message['text']) <= 8000:
                    raise ValueError('invalid_native_user_message')
                with self.s.local_store.connection() as db:
                    exists = db.execute('SELECT 1 FROM agh_user_messages WHERE session_key=? AND native_sequence=?', (key,message['sequence'])).fetchone()
                if exists:
                    continue
                saved = self.s.local_store.append_conversation_message(conversation, role='user', content=message['text'],
                    estimated_tokens=max(1,len(message['text'])//3), metadata={'agh_native_sequence':message['sequence']}, created_at=now())
                with self.s.local_store._connect() as db:
                    db.execute('INSERT INTO agh_user_messages VALUES (?,?,?)', (key,message['sequence'],saved.sequence))
        return {'captured': len(messages)}

    def context(self, key, *, for_prompt=False):
        conversation, selected = self.session(key)
        wf = self.s.local_store.get_workflow(selected.get('workflow_id', ''))
        dataset = self.s.dataset_service.get(selected['dataset_id']) if selected.get('dataset_id') else None
        workspace = self.s.workspace_service.get(selected['workspace_id']) if selected.get('workspace_id') else None
        # Official AGH context hooks have a 1500 ms deadline. Prompt assembly
        # must not probe Slurm or enumerate full workflow/method contracts.
        workflows = [] if for_prompt else [w.model_dump(mode='json') for w in self.s.workflow_service.list(100)
                                           if w.conversation_id == conversation]
        operations = self.s.data_operations.list(conversation) if self.s.data_operations else []
        guidance = []
        for name in selected.get('selected_skills', [])[:10]:
            skill = self.s.skill_loader.find(name)
            if skill:
                guidance.append({'name': name, 'description': skill.description, 'guidance': skill.prompt_excerpt(max_chars=4000)})
        from .agent_identity import public_understanding
        request = self.request(key)
        state = request.assistant_task
        task = public_understanding(state) if state.get('goal') and state.get('deliverable') != 'answer' else None
        return {'conversation_id': conversation, 'session_id': key, 'selected': selected, 'task': task,
                'planning_only': self.s.run_service.planning_only,
                'dataset': ({'dataset_id': dataset.dataset_id, 'absolute_path': dataset.absolute_path,
                    'inspection': dataset.inspection.model_dump(mode='json', include={'sample_count', 'input_types', 'features'})}
                    if for_prompt else dataset.model_dump(mode='json')) if dataset else None,
                'workspace': workspace.model_dump(mode='json') if workspace else None,
                'workflow': ({'workflow_id': wf.workflow_id, 'revision': wf.revision, 'status': wf.status}
                    if for_prompt else wf.model_dump(mode='json')) if wf and wf.conversation_id == conversation else None,
                'workflows': workflows, 'data_operations': operations,
                'execution': None if for_prompt else self.s.run_service.execution_backends(), 'selected_guidance': guidance,
                'business_baseline': self.baseline,
                'method_routes': state.get('method_planning', {}).get('state', {}).get('summary', {}),
                'shared_facts': self.ledger.facts.context(request, budget=6000),
                'facts_are_not_permission': True}

    def select(self, key, selected):
        allowed = {'dataset_id', 'workspace_id', 'workflow_id', 'selected_skills', 'interaction_mode'}
        if set(selected) - allowed:
            raise ValueError('unknown_session_context_field')
        conversation, current = self.session(key)
        merged = {**current, **selected}
        # Accept older browser selections while removing their retired mode.
        merged.pop('interaction_mode', None)
        if merged.get('dataset_id'):
            self.s.dataset_service.get(merged['dataset_id'])
        if merged.get('workspace_id'):
            self.s.workspace_service.get(merged['workspace_id'])
        if merged.get('workflow_id'):
            wf = self.s.workflow_service.get(merged['workflow_id'])
            if wf.conversation_id != conversation:
                raise PermissionError('workflow_belongs_to_another_agh_session')
            if 'dataset_id' not in selected:
                merged['dataset_id'] = wf.dataset_id
            if 'workspace_id' not in selected:
                merged['workspace_id'] = wf.workspace_id
        ChatRequest(message='AGH context', dataset_id=merged.get('dataset_id'), workspace_id=merged.get('workspace_id'),
            selected_skills=merged.get('selected_skills', []))
        if any(not self.s.skill_loader.find(name) for name in merged.get('selected_skills', [])):
            raise ValueError('unknown_selected_skill')
        with self.s.local_store._connect() as db:
            db.execute('UPDATE agh_sessions SET context_json=?,updated_at=? WHERE session_key=?',
                       (json.dumps(merged), now(), key))
        self.s.workspace_service.bind_conversation(conversation, merged.get('workspace_id'))
        return self.context(key)

    def request(self, key):
        conversation, selected = self.session(key)
        request = self.requests.get(key) or ChatRequest(message='AGH 当前任务', conversation_id=conversation,
            dataset_id=selected.get('dataset_id'), workspace_id=selected.get('workspace_id'),
            selected_skills=selected.get('selected_skills', []))
        request.dataset_id, request.workspace_id = selected.get('dataset_id'), selected.get('workspace_id')
        request.selected_skills = selected.get('selected_skills', [])
        request.interaction_mode = 'task'
        request.response_policy = ResponsePolicy(action='operate')
        request = self.s.chat_coordinator.resolve_context(request)
        request.assistant_task = self.ledger.load(request)
        with self.s.local_store.connection() as db:
            message = db.execute('SELECT m.sequence,m.content FROM agh_user_messages a JOIN conversation_messages m '
                'ON m.conversation_id=? AND m.sequence=a.business_sequence WHERE a.session_key=? '
                'ORDER BY a.native_sequence DESC LIMIT 1', (conversation,key)).fetchone()
        if message:
            request.message, request._user_sequence = message['content'], message['sequence']
        request.task_action = 'update_plan' if request.message.startswith('请按已保存的当前任务目标更新同一方案') else 'message'
        request.assistant_task.setdefault('protocol_version', 2)
        request.assistant_task.setdefault('architecture', 'AGH')
        request.assistant_task.setdefault('task_id', 'agh_task_' + hashlib.sha256(key.encode()).hexdigest()[:24])
        self.requests[key] = request
        return request

    def method_routes(self, key, body):
        request = self.request(key)
        if not request.assistant_task.get('goal'):
            self.update_task(key, {'goal':body.get('task_contract',{}).get('goal') or request.message, 'deliverable':'workflow'})
            request = self.request(key)
        self.ledger.save(request, {**request.assistant_task, 'deliverable': 'workflow'})
        result = read_routes(self.s.workspace_tools, self.ledger, request, body)
        self.record_fact(request, 'read_method_routes', body, result)
        return result

    def shared_facts(self, key, body):
        return self.ledger.facts.read(self.request(key), body)

    def update_task(self, key, body):
        from .agent_identity import public_understanding
        return public_understanding(self.ledger.update_goal(self.request(key),body))

    def finish_task(self, key, body):
        from .agent_identity import public_understanding
        return public_understanding(self.ledger.finish(self.request(key),body))

    def artifact_types(self):
        items = [dict(item) for item in self.s.capability_registry.artifact_types()]
        return {'items': items, 'total_items': len(items), 'has_more': False, 'scope_complete': True}

    def record_fact(self, request, name, arguments, result):
        from .agent_facts import responsibility
        reference = self.ledger.record(request, name, arguments, result)
        role = responsibility(name)
        if role in {'data', 'methods', 'runtime'}:
            try:
                version = self.ledger.facts.stamp(self.s.workspace_tools, request, name, arguments, result)
            except (ValueError, KeyError, PermissionError, OSError):
                # A cache stamp is optional provenance, not part of the tool's
                # authorization or successful result. Unknown stamps remain
                # historical facts and cannot be reused as fresh observations.
                version = None
            self.ledger.facts.record(request, role, name, arguments, result, reference, version)

    def workflow_owned(self, identity, key):
        wf = self.s.workflow_service.get(identity)
        if wf.conversation_id != self.session(key)[0]:
            raise PermissionError('workflow_belongs_to_another_agh_session')
        return wf

    async def prepare_workflow(self, key, body):
        proposal = WorkflowProposal.model_validate(body)
        if bool(proposal.workflow_id) != bool(proposal.expected_revision):
            raise ValueError('workflow_revision_required_for_replanning')
        existing = None
        if proposal.workflow_id:
            with self.s.workflow_service.mutation_lock:
                existing = self.workflow_owned(proposal.workflow_id, key).model_copy(deep=True)
                if existing.revision != proposal.expected_revision:
                    raise ValueError('stale_workflow_revision')
                if existing.status == 'confirmed':
                    raise ValueError('confirmed_workflow_requires_a_new_plan')
        request = self.request(key)
        from .task_management import assert_task_open
        assert_task_open(request.assistant_task)
        if not existing and request.assistant_task.get('active_plan',{}).get('kind') == 'workflow':
            active = self.workflow_owned(request.assistant_task['active_plan']['id'],key)
            if active.status != 'confirmed':
                existing = active.model_copy(deep=True)
                proposal.workflow_id, proposal.expected_revision = active.workflow_id, active.revision
        if existing:
            request.dataset_id, request.workspace_id = existing.dataset_id, existing.workspace_id
            request.workspace_root_path = existing.workspace_root_path
        self.ledger.save(request, {**request.assistant_task, 'deliverable': 'workflow', 'goal': request.assistant_task.get('goal') or proposal.goal})
        if proposal.route_id:
            route = await run_in_threadpool(saved_route, self.s.workspace_tools, self.ledger, request, proposal.route_id, proposal.contract)
            proposal.contract = TaskContract.model_validate(route['task_contract'])
            proposal.plan = MethodPlan(status='matched', steps=route['steps'])
            proposal.bindings = [AssistantInput.model_validate({
                'method_id': next(step['method_id'] for step in route['steps'] if step['id'] == item['step_id']),
                'declaration_fact_id': item['fact_id'] if item.get('user_declared') else '',
                **{field: item[field] for field in ('step_id', 'slot_id', 'path', 'per_sample')}
            }) for item in route['bindings']]
        request.message, request.task_contract = proposal.goal, proposal.contract
        request._registered_plan = proposal.plan
        request._workflow_input_bindings = proposal.bindings
        request._input_selection = 'user'
        workflow, _ = await self.s.workflow_service.planner.build_contract_plan(request, {'capability_ids': []}, existing=existing)
        if existing:
            workflow.output_layout, workflow.output_relative_path = existing.output_layout, existing.output_relative_path
            overrides = (request.assistant_task.get('method_planning', {}).get('query', {}).get('parameters', {})
                if proposal.route_id else {step.method_id: step.parameters for step in proposal.plan.steps})
            # Draft delivery stays structural; runtime checks belong to preflight.
            self.s.workflow_service._preserve_plan_choices(workflow, existing, overrides, check_runtime=False)
        workflow.owner_principal = principal.get().name
        workflow.managed_task_id = request.assistant_task['task_id']
        workflow.planning_diagnostics['agh'] = {'session_id': key, 'source': 'AGH registered method graph'}
        with self.s.workflow_service.mutation_lock:
            if existing and self.workflow_owned(existing.workflow_id, key).revision != proposal.expected_revision:
                raise ValueError('workflow_changed_during_agh_replanning')
            self.s.local_store.save_workflow(workflow, event_type='agh_workflow_replanned' if existing else 'agh_workflow_prepared')
        from .agent_plan_config import attach_workflow_configuration
        await run_in_threadpool(attach_workflow_configuration, self.s.workspace_tools, self.ledger, request, workflow)
        self.ledger.record(request, 'prepare_workflow', {'goal': proposal.goal, 'route_id': proposal.route_id}, {
            'id': workflow.workflow_id, 'kind': 'workflow', 'revision': workflow.revision,
            'status': 'pending', 'input_selection': 'user', 'executed': False, 'draft_ready': not workflow.contract_errors,
            'title': workflow.title, 'missing_inputs': sorted({slot for step in workflow.steps for slot in step.missing_required_inputs})})
        self.select(key, {'workflow_id': workflow.workflow_id})
        return workflow.model_dump(mode='json')

    def execute_tool(self, key, name, args):
        from .workspace_tools import TOOLS
        from .data_tools import DATA_TOOLS
        from .skill_catalog import SKILL_TOOLS
        from .assistant_memory import MEMORY_TOOLS
        from .assistant_step_tools import PREPARE_STEP_TOOL
        known = {t['function']['name'] for t in [*TOOLS, *DATA_TOOLS, *SKILL_TOOLS, *MEMORY_TOOLS, PREPARE_STEP_TOOL]}
        if name not in known:
            raise ValueError('unknown_business_tool')
        request = self.request(key)
        if name not in {t['function']['name'] for t in self.s.workspace_tools.available_tools(request)}:
            raise PermissionError('tool_unavailable_in_the_current_session_mode_or_scope')
        result = self.s.workspace_tools.execute(name, args, request)
        if isinstance(result, dict):
            if name == 'read_skill_method' and result.get('section') == 'contract':
                result = {**result, 'next_step': 'Use nw_method_routes for this registered method, then '
                    'nw_prepare_workflow(goal,route_id) only. Do not add contract, plan or bindings. '
                    'The workflow card exposes editable defaults, external inputs and resources; '
                    'do not require parameter confirmation in chat before delivering the draft.'}
            self.record_fact(request, name, args, result)
        return result

    def valid_panel_path(self, path):
        from urllib.parse import urlsplit, unquote
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or parsed.fragment or BLOCKED_PATH.match(parsed.path):
            raise PermissionError('panel_endpoint_not_allowed')
        if parsed.path != unquote(parsed.path) or not parsed.path.startswith('/api/') or '..' in unquote(parsed.path).split('/') or '\\' in unquote(path):
            raise PermissionError('invalid_panel_path')
        return parsed.path

    def guard_panel_scope(self, path, body, key):
        conversation = self.session(key)[0]
        # Raw IDs are references, not authorization to another conversation.
        match = re.match(r'^/api/(workflows|runs|conversations|data-operations)/([^/?]+)', path)
        if match and match[2] not in ('index', 'preview', 'organize', 'python', 'validate'):
            kind, identity = match.groups()
            if kind == 'workflows':
                self.workflow_owned(identity, key)
            elif kind == 'runs':
                run = self.s.run_service.get(identity)
                self.workflow_owned(run.workflow_id, key)
            elif kind == 'conversations' and identity != conversation:
                raise PermissionError('conversation_belongs_to_another_agh_session')
            elif kind == 'data-operations':
                self.s.data_operations.get(identity, conversation)
        from urllib.parse import parse_qs, urlsplit
        supplied = parse_qs(urlsplit(path).query).get('conversation_id', [conversation])
        if any(value != conversation for value in supplied):
            raise PermissionError('conversation_belongs_to_another_agh_session')
        if body and body.get('conversation_id') not in (None, conversation):
            raise PermissionError('conversation_belongs_to_another_agh_session')
        if body and body.get('task_id'):
            from .managed_tasks import ManagedTasks
            row, _ = ManagedTasks(self.s.local_store).require(body['task_id'])
            if row['conversation_id'] != conversation:
                raise PermissionError('task_belongs_to_another_agh_session')
        for identity in parse_qs(urlsplit(path).query).get('workflow_id', []):
            self.workflow_owned(identity, key)
        match = re.match(r'^/api/fs/operations/([^/?]+)', path)
        if match and self.s.workspace_service.operation(match[1]).get('conversation_id') != conversation:
            raise PermissionError('file_operation_belongs_to_another_agh_session')
        match = re.match(r'^/api/tasks/([^/?]+)',path)
        if match:
            from .managed_tasks import ManagedTasks
            row, _ = ManagedTasks(self.s.local_store).require(match[1])
            if row['conversation_id'] != conversation:
                raise PermissionError('task_belongs_to_another_agh_session')

    async def panel(self, key, raw, *, execution=False):
        request = PanelRequest.model_validate(raw)
        path = self.valid_panel_path(request.path)
        self.guard_panel_scope(request.path, request.body, key)
        if re.fullmatch(r'/api/workflows/[^/]+/(?:replan|complete-dependencies)(?:-jobs)?', path):
            raise PermissionError('AGH_PLANNING_REQUIRED')
        if re.fullmatch(r'/api/tasks/[^/]+/replan',path):
            raise PermissionError('AGH_PLANNING_REQUIRED')
        if request.method == 'GET' and path == '/api/task-index':
            from urllib.parse import parse_qs, urlsplit
            from .managed_tasks import ManagedTasks
            args = parse_qs(urlsplit(request.path).query,keep_blank_values=True)
            result = ManagedTasks(self.s.local_store).index(offset=max(0,int(args.get('offset',['0'])[0])),
                limit=min(100,max(1,int(args.get('limit',['30'])[0]))),query=args.get('q',[''])[0],
                source=args.get('source',[''])[0],status=args.get('status',[''])[0],conversation_id=self.session(key)[0])
            return {'status':200,'body':result}
        if request.method == 'GET' and path == '/api/workflow-index':
            from urllib.parse import parse_qs, urlsplit
            args = parse_qs(urlsplit(request.path).query, keep_blank_values=True)
            result = self.s.local_store.workflow_index(offset=max(0, int(args.get('offset', ['0'])[0])),
                limit=min(100, max(1, int(args.get('limit', ['30'])[0]))), query=args.get('q', [''])[0],
                source=args.get('source', [''])[0], status=args.get('status', [''])[0], conversation_id=self.session(key)[0])
            return {'status': 200, 'body': result}
        if request.method == 'GET' and path == '/api/runs':
            from urllib.parse import parse_qs, urlsplit
            if not parse_qs(urlsplit(request.path).query).get('workflow_id'):
                raise PermissionError('select_an_owned_workflow_for_run_history')
        if request.method == 'GET' and path == '/api/workflows':
            return {'status': 200, 'body': {'workflows': self.context(key)['workflows']}}
        if request.body is not None and (path in ('/api/fs/mkdir', '/api/fs/copy', '/api/fs/move', '/api/fs/rename', '/api/fs/delete', '/api/fs/read-sensitive') or
                re.fullmatch(r'/api/fs/(?:operations/[^/]+/(?:confirm|cancel)|trash/[^/]+/restore)', path)):
            request.body['conversation_id'] = self.session(key)[0]
        if re.fullmatch(r'/api/library/pipelines/[^/]+/workflows', path) and request.body is not None:
            request.body['conversation_id'] = self.session(key)[0]
        if request.method == 'GET' and re.fullmatch(r'/api/data-operations/[^/]+/(?:log|artifacts/\d+)', path):
            return {'status': 200, 'body': self.download(key, request.path)}
        if request.method != 'GET' and EXECUTION_PATH.match(path) and not execution:
            raise PermissionError('USER_ACTION_DISPATCH_REQUIRED')
        if execution and self.s.run_service.planning_only and EXECUTION_PATH.match(path):
            raise PermissionError('PLANNING_ONLY: 仅规划部署不能执行服务器操作，请先接入真实计算环境')
        token = _internal.set(True)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, client=('127.0.0.1', 0)),
                                         base_url='http://nw-internal', timeout=120) as client:
                response = await client.request(request.method, request.path, json=request.body,
                    headers={'X-NeuroWeave-Contract': '2'})
            media = response.headers.get('content-type', '')
            result = None if response.status_code == 204 else (
                response.json() if 'json' in media else
                {'content_base64': base64.b64encode(response.content).decode(), 'media_type': media})
            # Pipeline launches use the same normal endpoint and immutable recipe.
            if isinstance(result, dict) and result.get('workflow_id') and result.get('steps') is not None:
                wf = self.s.local_store.get_workflow(result['workflow_id'])
                if wf and wf.conversation_id == self.session(key)[0]:
                    self.select(key, {'workflow_id': wf.workflow_id, 'dataset_id': wf.dataset_id, 'workspace_id': wf.workspace_id})
            return {'status': response.status_code, 'body': result}
        finally:
            _internal.reset(token)

    def action_snapshot(self, key, request):
        path = self.valid_panel_path(request.path)
        self.guard_panel_scope(path, request.body, key)
        if request.method == 'GET' or not EXECUTION_PATH.match(path):
            raise ValueError('only_execution_actions_can_be_prepared')
        state = {}
        if re.fullmatch(r'/api/conversations/[^/]+/task/end',path):
            from .task_management import TaskConfirmation
            from .managed_tasks import ManagedTasks
            confirmation = TaskConfirmation.model_validate(request.body or {})
            state = {'task':ManagedTasks(self.s.local_store).get(confirmation.task_id)}
        match = re.match(r'/api/workflows/([^/]+)/runs$', path)
        if match:
            wf = self.workflow_owned(match[1], key)
            from .run_models import RunCreateRequest
            RunCreateRequest.model_validate(request.body or {})
            if wf.status != 'confirmed':
                raise ValueError('workflow_requires_review_preflight_and_confirmation')
            if (request.body or {}).get('expected_revision') != wf.revision:
                raise ValueError('stale_workflow_revision')
            from .execution.external_command import ExternalCommandRunner
            inputs = {}
            for step in wf.steps:
                for binding in step.input_bindings.values():
                    for source in ExternalCommandRunner.binding_paths(binding):
                        if source.is_symlink():
                            raise PermissionError('mutable_link_input_not_allowed')
                        stat = source.stat()
                        inputs[str(source)] = [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
                        if source.is_file() and stat.st_size <= 512 * 1024 * 1024:
                            with source.open('rb') as stream:
                                inputs[str(source)].append(hashlib.file_digest(stream, 'sha256').hexdigest())
                        elif source.is_dir():
                            entries = []
                            for child in sorted(source.rglob('*')):
                                if child.is_symlink():
                                    raise PermissionError('mutable_link_input_not_allowed')
                                metadata = child.stat()
                                entries.append([str(child.relative_to(source)), metadata.st_ino, metadata.st_size, metadata.st_mtime_ns])
                                if len(entries) > 100000:
                                    raise ValueError('input_tree_too_large_for_approval_snapshot')
                            inputs[str(source)].append({'tree_metadata_sha256': digest(entries), 'entries': len(entries)})
            state = {'workflow': wf.model_dump(mode='json'),
                     'inputs': inputs}
        elif '/data-operations/' in path:
            identity = path.split('/')[3]
            operation = self.s.data_operations.get(identity, self.session(key)[0])
            if operation.get('mode') == 'inplace':
                permitted = self.s.workspace_service.paths.allowed_roots
                if not any(Path(operation['source_root']).resolve().is_relative_to(root) for root in permitted):
                    raise PermissionError('inplace_changes_require_an_isolated_competition_dataset')
            state = {k: operation.get(k) for k in ('id', 'status', 'manifest_hash', 'script_plan_hash',
                     'source_root', 'target_root', 'mode', 'file_count', 'quality')}
        elif '/runs/' in path:
            run = self.s.run_service.get(path.split('/')[3])
            state = {'run_id': run.run_id, 'workflow_revision': run.workflow_revision,
                     'execution': run.execution.model_dump(), 'output_directory': run.output_directory}
        elif '/fs/operations/' in path:
            operation = self.s.workspace_service.operation(path.split('/')[4])
            state = {k: v for k, v in operation.items() if k not in ('can_cancel', 'cancel_requested')}
        else:
            state = {'request': request.model_dump()}
        return {'request': request.model_dump(), 'state': state}

    def prepare_action(self, key, raw, *, user_requested=False):
        request = PanelRequest.model_validate(raw)
        with self.s.run_service.mutation_lock, self.s.workflow_service.mutation_lock:
            payload = self.action_snapshot(key, request)
            identity, fingerprint = 'action_' + uuid4().hex, digest(payload)
            with self.s.local_store._connect() as db:
                if user_requested:
                    outstanding = db.execute("SELECT action_id,digest FROM agh_actions WHERE session_key=? "
                        "AND state IN ('queued','dispatching','unknown') AND json_extract(payload_json,'$.request.path')=? "
                        "ORDER BY created_at DESC LIMIT 1", (key, request.path)).fetchone()
                    if outstanding:
                        if outstanding['digest'] != fingerprint:
                            raise ValueError('previous_action_unresolved_query_before_new_submission')
                        return self.action(key, outstanding['action_id'])
                db.execute('INSERT INTO agh_actions(action_id,session_key,digest,payload_json,created_at,user_requested) VALUES (?,?,?,?,?,?)',
                    (identity, key, fingerprint, json.dumps(payload, ensure_ascii=False), now(), int(user_requested)))
        return {'action_id': identity, 'action_digest': fingerprint, 'state': 'prepared',
                'proposal': payload, 'reviewed_scope': self.reviewed_scope(payload),
                'user_requested': bool(user_requested),
                'approval': 'No additional approval. Execution requires the user to click the workbench action button.'}

    @staticmethod
    def reviewed_scope(payload):
        request, state = payload['request'], payload['state']
        scope = {'operation': {'path': request['path'], 'method': request['method']},
            'execution': (request.get('body') or {}).get('execution')}
        wf = state.get('workflow')
        if wf:
            methods = []
            for step in wf['steps']:
                params = step['selected_parameters']
                methods.append({'step': step['id'], 'method': step['selected_method_id'],
                    'parameters': params if len(json.dumps(params, ensure_ascii=False)) <= 500 else 'Full parameters in nw_action_details.proposal; checksum binds them.',
                    'parameters_sha256': digest(params)})
            scope['workflow'] = {'id': wf['workflow_id'], 'revision': wf['revision'],
                'input_root': (wf.get('dataset') or {}).get('absolute_path'),
                'output_root': wf['workspace_root_path'], 'relative_output': wf['output_relative_path'],
                'input_references': len(state.get('inputs', {})), 'methods': methods}
        else:
            scope['state'] = state
            scope['request_options'] = request.get('body')
        return scope

    def download(self, key, raw):
        import time
        path = self.valid_panel_path(raw)
        self.guard_panel_scope(raw, None, key)
        identity = path.split('/')[3]
        self.s.data_operations.get(identity, self.session(key)[0])
        if path.endswith('/log'):
            target = self.s.data_operations.directory / identity / 'execution.log'
            self.s.data_operations._artifact_path(target)
            payload = {'path': target, 'filename': 'analysis-result.log'}
        else:
            index = int(path.split('/')[-1])
            item = self.s.data_operations.repository.items(identity, index, 1)
            if not item:
                raise ValueError('artifact_not_found')
            item = item[0]
            if item.get('content') is not None:
                data = item['content'].encode('utf-8')
                if hashlib.sha256(data).hexdigest() != item['content_hash']:
                    raise ValueError('artifact_changed')
                payload = {'data': data, 'filename': Path(item['destination'] or item['path']).name}
            else:
                target = Path(item['artifact'])
                self.s.data_operations._artifact_path(target)
                from .data_operations import digest as artifact_digest
                if artifact_digest(target) != item['content_hash']:
                    raise ValueError('artifact_changed')
                payload = {'path': target, 'filename': target.name}
        token = uuid4().hex + uuid4().hex
        self.downloads = {k: v for k, v in self.downloads.items() if v['expires'] > time.time()}
        self.downloads[token] = {**payload, 'expires': time.time()+600}
        origin = os.getenv('NEUROWEAVE_AGH_PUBLIC_ORIGIN', 'http://192.10.84.6:3004').rstrip('/')
        return {'download_url': f'{origin}/api/agh/download/{token}'}

    def action(self, key, identity):
        with self.s.local_store._connect() as db:
            row = db.execute('SELECT * FROM agh_actions WHERE action_id=? AND session_key=?', (identity, key)).fetchone()
        if not row:
            raise PermissionError('action_not_owned_by_session')
        return {'action_id': identity, 'action_digest': row['digest'], 'state': row['state'],
                'user_requested': bool(row['user_requested']),
                'proposal': json.loads(row['payload_json']),
                'reviewed_scope': self.reviewed_scope(json.loads(row['payload_json'])),
                'result': json.loads(row['result_json']) if row['result_json'] else None}

    def action_status(self, key, identity):
        return {k: v for k, v in self.action(key, identity).items() if k not in ('proposal', 'reviewed_scope')}

    async def submit_action(self, key, identity, fingerprint):
        """The official AGH workbench effect submits this exact user-requested action.

        Acknowledgement does not wait for Slurm or a model reply. Queued work stays
        owned by the backend; a lost UI response never submits another action.
        """
        with _lock:
            action = self.action(key, identity)
            if action['action_digest'] != fingerprint:
                raise ValueError('stale_action_digest')
            if not action['user_requested']:
                raise PermissionError('USER_WORKBENCH_RUN_REQUIRED')
            if action['state'] == 'prepared':
                with self.s.local_store._connect() as db:
                    db.execute("UPDATE agh_actions SET state='queued' WHERE action_id=?", (identity,))
            elif action['state'] != 'queued':
                return self.action_status(key, identity)
            if identity not in self.submissions:
                task = asyncio.create_task(self._dispatch_submission(key, identity, fingerprint))
                self.submissions[identity] = task
                task.add_done_callback(lambda done: self.submissions.pop(identity, None))
            return self.action_status(key, identity)

    async def _dispatch_submission(self, key, identity, fingerprint):
        try:
            await self.apply_action(key, identity, fingerprint, 'agh-service:' + identity, from_queue=True)
        except Exception:
            # A rejection before dispatch is definite. An interrupted side effect
            # is uncertain and must be reconciled by its existing action/run ID.
            with self.s.local_store._connect() as db:
                row = db.execute('SELECT state FROM agh_actions WHERE action_id=?', (identity,)).fetchone()
                uncertain = row['state'] != 'queued'
                result = {'status': 503 if uncertain else 409, 'body': {'detail': {
                    'code': 'action_dispatch_uncertain' if uncertain else 'action_validation_failed',
                    'message': '提交状态待核对，请查询原动作和运行记录，勿重复提交' if uncertain else
                        '执行前校验未通过，尚未运行，请重新检查流程版本、输入和运行资源'}}}
                db.execute('UPDATE agh_actions SET state=?,result_json=? WHERE action_id=?',
                    ('unknown' if uncertain else 'completed', json.dumps(result, ensure_ascii=False), identity))

    def _begin_action(self, key, identity, fingerprint, tool, *, from_queue=False):
        with _lock, self.s.run_service.mutation_lock, self.s.workflow_service.mutation_lock:
            action = self.action(key, identity)
            if action['action_digest'] != fingerprint:
                raise ValueError('stale_action_digest')
            if action['state'] == 'completed':
                return action['result']
            if action['state'] != 'prepared' and not (from_queue and action['state'] == 'queued'):
                raise ValueError('action_state_unknown_query_before_retry')
            if not action['user_requested']:
                raise PermissionError('USER_WORKBENCH_RUN_REQUIRED')
            request = PanelRequest.model_validate(action['proposal']['request'])
            if digest(self.action_snapshot(key, request)) != fingerprint:
                raise ValueError('execution_plan_or_inputs_changed_prepare_new_action')
            with self.s.local_store._connect() as db:
                db.execute("UPDATE agh_actions SET state='dispatching',tool_use_id=? WHERE action_id=?", (tool, identity))
        return request

    async def apply_action(self, key, identity, fingerprint, tool, *, from_queue=False):
        request = await run_in_threadpool(self._begin_action, key, identity, fingerprint, tool, from_queue=from_queue)
        if not isinstance(request, PanelRequest):
            return request
        # An interrupted dispatch stays unknown: never replay an uncertain side effect.
        result = await self.panel(key, request.model_dump(), execution=True)
        with self.s.local_store._connect() as db:
            db.execute("UPDATE agh_actions SET state='completed',result_json=? WHERE action_id=?",
                       (json.dumps(result, ensure_ascii=False), identity))
        return result

    def verify_run(self, key, identity):
        run = self.s.run_service.get(identity)
        self.workflow_owned(run.workflow_id, key)
        if run.status != 'succeeded':
            raise ValueError('run_not_succeeded')
        from .workspace_paths import WorkspacePaths
        policy = WorkspacePaths(Path(run.output_directory), allowed_roots=(Path(run.output_directory),))
        checks = []
        for artifact in run.artifacts:
            path = policy.resolve(Path(run.output_directory), artifact.absolute_path, write=True)
            value = {'artifact_id': artifact.artifact_id, 'path': artifact.relative_path,
                     'exists': path.is_file() or (artifact.artifact_type == 'generated_folder' and path.is_dir()),
                     'size_bytes': path.stat().st_size if path.is_file() else 0}
            if path.is_file():
                with path.open('rb') as stream:
                    value['sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
                value['hash_matches_record'] = bool(artifact.sha256) and value['sha256'] == artifact.sha256
            if artifact.artifact_type == 'generated_folder' and path.is_dir():
                value['recorded_child_files'] = sum(Path(a.absolute_path).is_relative_to(path) and
                    a.artifact_type != 'generated_folder' for a in run.artifacts)
                value['verification'] = 'Directory presence; recorded child file hashes are checked separately. Unlisted files are not verified.'
                value['passed'] = value['recorded_child_files'] > 0
            else:
                value['passed'] = value['exists'] and value.get('hash_matches_record', False)
            checks.append(value)
        scientific = []
        import subprocess
        for step in run.plan_snapshot.get('steps', []):
            if step.get('selected_method_id') != 'reviewed.dipy-tool.roi-stats':
                continue
            artifacts = [a for a in run.artifacts if Path(a.absolute_path).name == 'roi_stats.csv']
            # Per-sample inputs are resolved using the Runner's persisted sample identity.
            for artifact in artifacts:
                executed = next((s for s in run.steps if s.step_id == artifact.step_id), None)
                original_step = re.sub(r'^sample_\d+__', '', executed.step_id) if executed else ''
                if not executed or original_step != step['id']:
                    continue
                from .workflow_models import WorkflowStep
                runtime = WorkflowStep.model_validate(step)
                prefix = executed.step_id[:-len(step['id'])]
                for binding in runtime.input_bindings.values():
                    if binding.source == 'upstream_output' and prefix:
                        binding.upstream_step_id = prefix + (binding.upstream_step_id or '')
                        for extra in binding.upstream_sources:
                            extra.step_id = prefix + extra.step_id
                runtime = self.s.run_service._resolve_runtime_bindings(runtime, run)
                paths = {}
                for name in ('metric', 'roi', 'labels'):
                    binding = runtime.input_bindings[name].model_dump() if name in runtime.input_bindings else {}
                    matches = binding.get('sample_matches', [])
                    match = next((m for m in matches if executed and m.get('sample_id') == executed.sample_id), None)
                    paths[name] = match.get('absolute_path') if match else binding.get('absolute_path')
                if not paths.get('metric') or not paths.get('roi'):
                    scientific.append({'passed': False, 'reason': 'ROI resolved inputs unavailable for independent oracle'})
                    continue
                inputs_current = True
                for source in filter(None, paths.values()):
                    with Path(source).open('rb') as stream:
                        current_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
                    if executed.input_fingerprint.get(str(Path(source).resolve())) != current_hash:
                        scientific.append({'passed': False, 'reason': 'scientific input no longer matches recorded run input', 'sample_id': executed.sample_id})
                        inputs_current = False
                        break
                if not inputs_current:
                    continue
                request = {**paths, 'csv': artifact.absolute_path,
                           'min_voxels': step.get('selected_parameters', {}).get('min_voxels', 1)}
                completed = subprocess.run([os.getenv('LOCALFLOW_SKILL_PYTHON', 'python'),
                    str(self.s.settings.project_root/'scripts/hackathon/server-science.py')],
                    input=json.dumps(request), text=True, capture_output=True, timeout=120,
                    env={'PATH': os.defpath, 'PYTHONNOUSERSITE': '1', 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'})
                try:
                    result = json.loads(completed.stdout)
                except ValueError:
                    result = {'passed': False, 'reason': 'independent scientific verifier did not return a result', 'exit_code': completed.returncode}
                result.update(artifact_id=artifact.artifact_id, sample_id=executed.sample_id if executed else None)
                scientific.append(result)
        return {'run_id': identity, 'passed': bool(checks) and all(c['passed'] for c in checks) and all(c['passed'] for c in scientific),
                'scientific_checks': scientific,
                'checks': checks, 'verification_scope': 'Recorded files exist and match their recorded hashes; directory presence and recorded child files are checked. Unlisted files are not verified. '
                'Scientific interpretation additionally requires method-specific quality checks.',
                'scheduler': [s.scheduler for s in run.steps]}


def build_server_app():
    token_file = Path(os.environ['NW_BRIDGE_TOKEN_FILE'])
    info = token_file.stat()
    if token_file.is_symlink() or not token_file.is_file() or info.st_mode & 0o077 or info.st_uid != getattr(os, 'getuid', lambda: info.st_uid)():
        raise RuntimeError('unsafe_bridge_credential_file')
    secret = token_file.read_text().strip()
    if len(secret) < 32:
        raise RuntimeError('invalid_bridge_credential')
    app = create_app()
    business = AGHBusiness(app)
    app.state.agh_business = business

    @app.middleware('http')
    async def private_entry(request: Request, call_next):
        if request.client and request.client.host not in ('127.0.0.1', '::1', 'testclient'):
            return JSONResponse({'detail': 'loopback_only'}, status_code=403)
        if request.url.path.startswith('/api/') and not _internal.get():
            if request.url.path not in ('/api/health', '/api/agh/overview') and not request.url.path.startswith('/api/agh/download/'):
                return JSONResponse({'detail': 'Use the authenticated AGH workbench'}, status_code=403)
        return await call_next(request)

    @app.get('/api/agh/overview')
    def overview():
        return {'mode': 'AGH + NeuroWeave server', 'agh_url': 'http://127.0.0.1:4187',
                'planning_only': business.s.run_service.planning_only,
                'revision': os.getenv('NEUROWEAVE_RELEASE_REVISION', 'unversioned'),
                'execution': business.s.run_service.execution_backends(),
                'approval': 'workbench action button; no additional approval', 'data_mode': 'server datasets'}

    @app.get('/api/agh/download/{identity}')
    def download(identity: str):
        import time
        from urllib.parse import quote
        value = business.downloads.get(identity)
        if not value or value['expires'] <= time.time():
            raise HTTPException(404, 'download_expired')
        headers = {'Content-Security-Policy': "default-src 'none'; sandbox", 'X-Content-Type-Options': 'nosniff'}
        if 'data' in value:
            headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(value['filename'])
            return Response(value['data'], media_type='application/octet-stream', headers=headers)
        business.s.data_operations._artifact_path(value['path'])
        return FileResponse(value['path'], filename=value['filename'], headers=headers)

    @app.post('/bridge/v2/{operation}')
    async def invoke(operation: str, request: Request):
        if request.headers.get('origin') or not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + secret):
            raise HTTPException(401, 'invalid_bridge_authentication')
        session = request.headers.get('x-neuroweave-session', '')
        tool = request.headers.get('x-neuroweave-tool-use', '')
        body = await request.body()
        if len(body) > MAX_BODY:
            raise HTTPException(413, 'request_too_large')
        try:
            args = json.loads(body or '{}')
            if not isinstance(args, dict):
                raise ValueError('arguments_must_be_an_object')
            if operation in ('workspace-preferences', 'workspace-update', 'workspace-remove', 'session-removal-check'):
                handler = {'workspace-preferences': lambda: business.workspace_preferences(),
                    'workspace-update': lambda: business.update_workspace_preferences(args),
                    'workspace-remove': lambda: business.remove_workspace(args),
                    'session-removal-check': lambda: business.session_removal_check(args)}[operation]
                if operation == 'workspace-preferences' and args:
                    raise ValueError('workspace_preferences_takes_no_arguments')
                return {'ok': True, 'result': await run_in_threadpool(handler)}
            business.session(session)
            actor = principal.set(Principal(name=os.getenv('NEUROWEAVE_AGH_OPERATOR', 'local')))
            try:
                if operation == 'context':
                    result = await run_in_threadpool(business.context, session)
                elif operation == 'prompt-context':
                    if args:
                        raise ValueError('prompt_context_takes_no_arguments')
                    result = await run_in_threadpool(business.context, session, for_prompt=True)
                elif operation == 'native-messages':
                    result = await run_in_threadpool(business.native_messages,session,args)
                elif operation == 'task-update':
                    result = await run_in_threadpool(business.update_task,session,args)
                elif operation == 'task-finish':
                    result = await run_in_threadpool(business.finish_task,session,args)
                elif operation == 'select':
                    result = await run_in_threadpool(business.select, session, args)
                elif operation in ('panel-query', 'panel-edit'):
                    if operation == 'panel-query' and args.get('method', 'GET') != 'GET':
                        raise PermissionError('query_cannot_write')
                    result = await business.panel(session, args)
                elif operation == 'tool':
                    result = await run_in_threadpool(business.execute_tool, session, args['name'], args['arguments'])
                elif operation == 'workflow-prepare':
                    proposal = SavedRouteDraft.model_validate(args)
                    result = await business.prepare_workflow(session, proposal.model_dump(exclude_none=True))
                elif operation == 'method-routes':
                    result = await run_in_threadpool(business.method_routes, session, args)
                elif operation == 'shared-facts':
                    result = await run_in_threadpool(business.shared_facts, session, args)
                elif operation == 'artifact-types':
                    if args:
                        raise ValueError('artifact_types_takes_no_arguments')
                    result = await run_in_threadpool(business.artifact_types)
                elif operation == 'action-prepare':
                    result = await run_in_threadpool(business.prepare_action, session, args)
                elif operation == 'action-prepare-user':
                    # Called by the trusted UI effect service, never by a model tool.
                    result = await run_in_threadpool(business.prepare_action, session, args, user_requested=True)
                elif operation == 'action-read':
                    result = await run_in_threadpool(business.action, session, args['action_id'])
                elif operation == 'action-status':
                    result = await run_in_threadpool(business.action_status, session, args['action_id'])
                elif operation == 'action-submit-user':
                    if set(args) != {'action_id', 'action_digest'}:
                        raise ValueError('submit_only_the_frozen_action_identity')
                    result = await business.submit_action(session, args['action_id'], args['action_digest'])
                elif operation == 'action-apply':
                    if not tool or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,200}', tool):
                        raise PermissionError('native_tool_correlation_required')
                    if args.get('reviewed_scope') != business.action(session, args['action_id'])['reviewed_scope']:
                        raise ValueError('reviewed_scope_must_match_the_frozen_action_details')
                    result = await business.apply_action(session, args['action_id'], args['action_digest'], tool)
                elif operation == 'verify-run':
                    result = await run_in_threadpool(business.verify_run, session, args['run_id'])
                elif operation == 'read-workflow':
                    workflow = business.workflow_owned(args['workflow_id'], session)
                    result = {'workflow': workflow.model_dump(mode='json'),
                        'runs': business.s.local_store.list_runs(workflow_id=workflow.workflow_id, limit=5, summary=True)}
                elif operation == 'read-run':
                    run = business.s.run_service.get(args['run_id'])
                    business.workflow_owned(run.workflow_id, session)
                    result = {k: v for k, v in run.model_dump(mode='json').items() if k != 'plan_snapshot'}
                    result['events'] = business.s.local_store.run_event_page(run.run_id, tail=True, limit=30)
                else:
                    raise HTTPException(404, 'unknown_operation')
                return {'ok': True, 'result': result}
            finally:
                principal.reset(actor)
        except (ValueError, KeyError, PermissionError, OSError) as error:
            return JSONResponse({'ok': False, 'error': str(error)[:1500]},
                                status_code=403 if isinstance(error, PermissionError) else 409)

    return app
