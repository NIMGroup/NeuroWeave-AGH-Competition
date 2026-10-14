"""Background sandbox jobs; generated artifacts still use exact commit rules."""
import hashlib
import json
import time
from pathlib import Path

from .data_auth import principal
from .data_sandbox import SandboxCancelled
from .operation_context import current_operation, OperationContext, OperationCancelled, checkpoint


class DataPythonMixin:
    @staticmethod
    def _script_plan_hash(task):
        fields = {key: task[key] for key in ('_python_request', 'scope', 'source_root', 'target_root', 'root_stamps',
                                            'sandbox_image', 'sandbox_environment', 'sandbox_lock_sha256', 'resource_profile',
                                            'sandbox_packages', 'sandbox_resources')}
        return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _python_scope(request):
        return request.model_dump(exclude={'code', 'title', 'execution', 'background', 'environment', 'resource_profile', 'selection', 'result_kind', 'change_actions', 'test_cases', 'manifest_invariants'})

    def environments(self):
        from .script_contract import RESULT_CONTRACTS
        return {**self.sandbox.environments.listing(self.sandbox.image), 'result_contracts': RESULT_CONTRACTS}

    def _script_fields(self, request, selected):
        return {'script': request.code, 'script_result_kind': request.result_kind, 'selection': request.selection.model_dump(), 'script_hash': hashlib.sha256(request.code.encode()).hexdigest(),
                'sandbox_image': selected['image'], 'sandbox_environment': request.environment,
                'sandbox_lock_sha256': selected.get('lock_sha256'),
                'sandbox_packages': selected['packages'], 'sandbox_resources': selected['resources'],
                'resource_profile': request.resource_profile, 'requested_execution': request.execution}

    def python(self, request):
        """Prepare an immutable script proposal; neither request flag starts it."""
        from .script_contract import validate_script
        validate_script(request.code, request.result_kind)
        from .script_verification import checked_cases
        checked_cases(request.code, request.test_cases)
        scope = self._python_scope(request)
        selected = self.sandbox.describe(request.environment, request.resource_profile)
        with self.lock, self.run_lock:
            source, target = self._roots(scope)
            self._guard(source, target)
            task = self._record(scope, source, target, request.title, [], [], ready=False, status='pending')
            task.update(self._script_fields(request, selected), phase='script', _python_request=request.model_dump())
            task['script_plan_hash'] = self._script_plan_hash(task)
            self._save(task)
        return self.get(task['id'], task['conversation_id'])

    def start_python(self, identity, confirmation):
        """HTTP-only, single-use approval of the exact script and its scope."""
        from .task_management import assert_plan_open
        assert_plan_open(self.store,'data',identity)
        from .data_models import PythonRequest
        with self.lock, self.run_lock:
            task = self._raw(identity, 'preview')
            self._owner(task, confirmation.conversation_id)
            if not task.get('script_plan_hash') or task['script_plan_hash'] != confirmation.script_plan_hash:
                raise PermissionError('确认的脚本方案已变化')
            if task['status'] != 'pending' or task.get('phase') != 'script':
                return self.public(task)  # Repeated clicks never restart a script.
            if task.get('quality', {}).get('blocking_issues'):
                raise ValueError('脚本方案尚未通过目标核验，请先修正方案')
            if task['expires_at'] < time.time():
                raise ValueError('脚本方案已过期，请重新准备')
            if self._script_plan_hash(task) != task['script_plan_hash']:
                raise PermissionError('脚本、范围或资源配置已变化，请重新准备')
            request = PythonRequest.model_validate(task['_python_request'])
            selected = self.sandbox.describe(request.environment, request.resource_profile)
            if any(self._script_fields(request, selected)[key] != task[key] for key in
                   ('sandbox_image', 'sandbox_lock_sha256', 'sandbox_packages', 'sandbox_resources')):
                raise PermissionError('脚本环境已变化，请重新准备')
            with self.store.connection() as db:
                count = db.execute("SELECT count(*) FROM data_operations WHERE status IN ('validating','queued','analyzing','running','rolling_back','cancel_requested')").fetchone()[0]
            if count >= 16:
                raise ValueError('数据任务队列已满，请稍后重试')
            source, target = self._roots(task['scope'])
            self._check_roots(task, source, target)
            self._guard(source, target, task_id=identity)
            task.update(status='queued', _script_grant={'actor': principal.get().name,
                        'script_plan_hash': task['script_plan_hash'], 'confirmed_at': time.time()})
            self._save(task)
            event = self._cancel_event(task['id'])
            self.futures[task['id']] = self.script_pool.submit(self._python_as, principal.get(), task['id'], request, event)
        return self.get(task['id'], task['conversation_id'])

    def _python_as(self, actor, identity, request, event):
        token = principal.set(actor)
        context_token = current_operation.set(OperationContext(event, lambda _: None))
        try:
            with self.lock:
                task = self._raw(identity, 'state')
                if task['status'] != 'queued' or event.is_set():
                    return
                source, target = self._roots(task['scope'])
                self._check_roots(task, source, target)
                task['status'] = 'analyzing'
                self._save(task)
            checkpoint()
            self._python_sync(request, identity, event)
        except (SandboxCancelled, OperationCancelled):
            with self.lock:
                task = self._raw(identity, 'state')
                task.update(status='cancelled', error=None)
                self._save(task)
        except Exception as exc:
            with self.lock:
                task = self._raw(identity, 'state')
                task.update(status='failed', error=str(exc))
                log = self.directory / identity / 'execution.log'
                if log.is_file():
                    task['execution_log'] = log.read_text(encoding='utf8', errors='replace')[:65536]
                self._save(task)
        finally:
            current_operation.reset(context_token)
            principal.reset(token)

    def _python_sync(self, request, identity=None, event=None):
        from .data_operations import digest, root_stamp, stamp
        if identity is None:
            raise PermissionError('脚本必须由用户点击运行后执行')
        prepared = self._raw(identity, 'preview')
        grant = prepared.get('_script_grant', {})
        if grant.get('actor') != principal.get().name or grant.get('script_plan_hash') != prepared.get('script_plan_hash'):
            raise PermissionError('脚本缺少准确的手动运行授权')
        from .data_models import PythonRequest
        if self._script_plan_hash(prepared) != grant['script_plan_hash'] or request.model_dump() != PythonRequest.model_validate(prepared['_python_request']).model_dump():
            raise PermissionError('脚本方案与手动运行授权不一致')
        scope = self._python_scope(request)
        source, target = self._roots(scope)
        roots = [root_stamp(source), root_stamp(target)]
        self._check_roots(prepared, source, target)
        self._guard(source, target, task_id=identity)
        self.workspace._validate_tree(source)
        before = self.inventory(scope)
        context = self.sandbox.execution(request.environment, request.resource_profile, event)
        while not self.script_slots.acquire(timeout=.2):
            if event and event.is_set():
                raise SandboxCancelled()
        try:
            with context as selected:
                if any(self._script_fields(request, selected)[key] != prepared[key] for key in
                       ('sandbox_image', 'sandbox_lock_sha256', 'sandbox_packages', 'sandbox_resources')):
                    raise PermissionError('脚本环境已变化，请重新准备')
                from .script_verification import sandbox_program
                artifacts = self.sandbox.run(source, self.directory / identity, sandbox_program(request.code, request.test_cases))
        finally:
            self.script_slots.release()
        self.workspace._validate_tree(artifacts)
        if request.test_cases:
            log_path = artifacts.parent / 'execution.log'
            marker = 'NEUROWEAVE_FUNCTION_CASES_PASSED=' + str(len(request.test_cases))
            if not log_path.is_file() or marker not in log_path.read_text(encoding='utf8', errors='replace').splitlines():
                raise ValueError('脚本结束但未完成声明的边界案例；不接受生成清单')
        verification = {'checks': [], 'function_case_count': len(request.test_cases),
                        'function_cases_status': 'passed' if request.test_cases else 'not_declared',
                        'source': 'sandbox_function_cases',
                        'semantic_notice': '已验证声明的函数案例；不代表所有业务语义已被证明或文件改动已执行。'}
        changes = None
        if request.result_kind == 'changes':
            from .data_script_changes import read_script_changes
            changes = read_script_changes(self, request, scope, artifacts)
            from .script_verification import verify_manifest
            from .data_selection import provenance_paths
            verification = verify_manifest(changes.edits if changes else [], request, before['items'],
                                           provenance_paths(self, scope, request.selection))
        manifest, conflicts = [], []
        for artifact in (sorted(artifacts.rglob('*')) if request.result_kind == 'files' else []):
            if artifact.is_dir():
                continue
            relative = artifact.relative_to(artifacts).as_posix()
            src, dst = self._path(source, relative), self._path(target, relative)
            self._artifact_path(artifact)
            if dst.exists() and not (dst.is_file() and scope['mode'] in {'copy', 'inplace'}):
                conflicts.append('目标已存在：' + relative)
            manifest.append({'action': 'write_text', 'path': relative, 'destination': None, 'content': None,
                             'source': str(src), 'target': str(dst), 'before': stamp(src), 'target_before': stamp(dst),
                             'size': artifact.stat().st_size, 'artifact': str(artifact), 'content_hash': digest(artifact),
                             'diff': '隔离 Python 生成的新版本；请检查输出文件后确认'})
        if len(manifest) > 10000:
            raise ValueError('隔离脚本输出数量超过 10000')
        if request.mode == 'analysis':
            manifest = []
        if request.mode == 'inplace':
            from .data_selection import provenance_paths
            allowed = provenance_paths(self, scope, request.selection)
            if any(not request.selection.matches(row['path']) or (allowed is not None and row['path'] not in allowed) for row in manifest):
                raise ValueError('脚本生成文件超出本轮修改范围；未生成可执行文件清单')
        current_source, current_target = self._roots(scope)
        if [root_stamp(current_source), root_stamp(current_target)] != roots:
            raise PermissionError('脚本执行期间登记目录被替换')
        if self.inventory(scope)['items'] != before['items']:
            raise ValueError('隔离执行期间输入清单变化，请重新执行')
        with self.lock:
            if event and event.is_set():
                raise SandboxCancelled()
            if identity:
                current = self._raw(identity, 'state')
                self._check_roots(current, current_source, current_target)
                created = current['created_at']
            task = (self._propose(changes, identity=identity) if changes else
                    self._record(scope, source, target, request.title, manifest, conflicts, identity=identity,
                                 status='pending' if manifest else 'analyzed'))
            if identity:
                task['created_at'] = created
            task.update(self._script_fields(request, selected), phase='files' if task['file_count'] else 'analysis')
            task.update(script_plan_hash=prepared['script_plan_hash'], _script_grant=grant)
            if request.result_kind == 'changes' or request.test_cases:
                task['verification_report'] = verification
            if prepared.get('quality'):
                task['quality'] = prepared['quality']
                review = task['quality'].get('proposal_review', {})
                if task['status'] == 'pending' and review.get('source') == 'unified_agent':
                    from .assistant_proposal_review import REVIEW_BLOCK
                    task['quality'] = {**task['quality'],
                        'blocking_issues': list(dict.fromkeys([*task['quality'].get('blocking_issues', []), REVIEW_BLOCK])),
                        'proposal_review': {**review, 'decision': 'pending', 'issues': [], 'plan_hash': task['manifest_hash']}}
            log = artifacts.parent / 'execution.log'
            task['execution_log'] = log.read_text(encoding='utf8', errors='replace')[:65536] if log.is_file() else ''
            task['execution_log_bytes'] = log.stat().st_size if log.is_file() else 0
            task['execution_log_truncated'] = len(task['execution_log'].encode('utf8')) < task['execution_log_bytes']
            self._save(task)
            return self._finish_request(task, request.execution)
