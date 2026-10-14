"""Idempotent, observable chat turns. Disconnected HTTP clients do not cancel planning."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from threading import Event
from uuid import uuid4
from fastapi import HTTPException
from .operation_context import OperationContext, current_operation, OperationCancelled, cancellable, checkpoint
from .token_usage import usage_scope


class ChatTurns:
    def __init__(self,store,conversations,handler):
        from .managed_tasks import ensure_schema
        ensure_schema(store)
        self.store,self.conversations,self.handler=store,conversations,handler
        self.tasks={};self.events={}
        with store._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS chat_turns(id TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,conversation_id TEXT NOT NULL,status TEXT NOT NULL,message TEXT NOT NULL,request_json TEXT NOT NULL,result_json TEXT,error_json TEXT,updated_at TEXT NOT NULL)')
            db.execute("UPDATE chat_turns SET status='interrupted',message='服务已重启；请检查已返回的内容，再明确重试。' WHERE status IN ('queued','running')")

    async def shutdown(self):
        # Only planning tasks belong here; Runner jobs have their own lifetime.
        pending=list(self.tasks.values())
        for task in pending:task.cancel()
        if pending:await asyncio.gather(*pending,return_exceptions=True)

    def get(self,identity,*,include_result=True):
        with self.store._connect() as db:row=db.execute('SELECT * FROM chat_turns WHERE id=?',(identity,)).fetchone()
        if row is None:raise KeyError(identity)
        self.store.get_conversation(row['conversation_id'])
        usage = self.conversations.token_usage.summary(row['conversation_id'], identity).model_dump()
        result = json.loads(row['result_json']) if include_result and row['result_json'] else None
        if result is not None:
            result['token_usage'] = usage
        task = self.tasks.get(identity)
        event = self.events.get(identity)
        finishing = row['status'] == 'succeeded' and task is not None and not task.done()
        return dict(turn_id=identity,conversation_id=row['conversation_id'],
                    updated_at=row['updated_at'],
                    request_kind='result_verification' if json.loads(row['request_json']).get('_server_feedback') else 'user_message',
                    cancel_requested=bool(event and event.is_set()),
                    status='running' if finishing else row['status'],
                    message='回复已保存，正在整理会话记忆' if finishing else row['message'],
                    token_usage=usage,result=result,error=json.loads(row['error_json']) if row['error_json'] else None)

    def active(self):
        """Discover server-created turns, including replies still draining/compacting."""
        with self.store._connect() as db:
            ids = {row[0] for row in db.execute("SELECT id FROM chat_turns WHERE status IN ('queued','running')")}
        ids.update(identity for identity, task in list(self.tasks.items()) if not task.done())
        turns = []
        for identity in sorted(ids):
            try:
                turn = self.get(identity, include_result=False)
            except (KeyError, PermissionError):
                continue
            except HTTPException as exc:
                if exc.status_code in {403, 404}:
                    continue
                raise
            if turn['status'] in {'queued', 'running'}:
                turns.append(turn)
        return {'turns': turns}

    def submit(self,request):
        identity=request.client_turn_id or 'turn-'+uuid4().hex
        payload=request.model_dump(mode='json');payload['client_turn_id']=identity
        signature=hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.store._lock,self.store._connect() as db:
            row=db.execute('SELECT fingerprint FROM chat_turns WHERE id=?',(identity,)).fetchone()
            if row:
                if row['fingerprint']!=signature:raise HTTPException(409,'同一请求 ID 不能用于不同的消息或上下文')
                return self.get(identity)
            if db.execute("SELECT COUNT(*) FROM chat_turns WHERE status IN ('queued','running')").fetchone()[0]>=8:raise HTTPException(429,'对话处理队列已满，请稍后重试')
            conversation_id=request.conversation_id
            if conversation_id:
                if not self.store.get_conversation(conversation_id):raise HTTPException(404,'Conversation not found')
                for active_id, task in list(self.tasks.items()):
                    if not task.done():
                        active = db.execute('SELECT conversation_id FROM chat_turns WHERE id=?',(active_id,)).fetchone()
                        if active and active[0] == conversation_id:raise HTTPException(409,'当前会话仍在处理上一条消息')
                if db.execute("SELECT 1 FROM chat_turns WHERE conversation_id=? AND status IN ('queued','running')",(conversation_id,)).fetchone():raise HTTPException(409,'当前会话仍在处理上一条消息')
            else:
                conversation_id=self.conversations.create(request.message[:80]).conversation_id
            payload['conversation_id']=conversation_id
            db.execute('INSERT INTO chat_turns VALUES(?,?,?,?,?,?,NULL,NULL,?)',(identity,signature,conversation_id,'queued','等待处理',json.dumps(payload,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
        self._start(identity,request.model_copy(update={'client_turn_id':identity,'conversation_id':conversation_id}))
        return self.get(identity)

    def _start(self,identity,request):
        event=Event();self.events[identity]=event
        task=asyncio.create_task(self._run(identity,request,event));self.tasks[identity]=task
        def completed(done):
            if self.tasks.get(identity) is done:
                self.tasks.pop(identity,None)
        task.add_done_callback(completed)

    def submit_feedback(self, request, source_sequence):
        """Server-owned result event; no user message, no execution grant."""
        identity = request.client_turn_id
        payload = request.model_dump(mode='json')
        payload['_server_feedback'] = request._execution_feedback
        signature = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.store._lock, self.store._connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_tasks'").fetchone():
                from .agent_identity import task_closure
                from .data_auth import principal
                goal=db.execute('SELECT payload FROM assistant_tasks WHERE conversation_id=? AND actor=?',
                    (request.conversation_id,principal.get().name)).fetchone()
                state=json.loads(goal[0]) if goal else {}
                source=request._execution_feedback.get('source_id') or request._execution_feedback.get('id')
                if state.get('protocol_version') == 2 and (task_closure(state) or source not in state.get('awaiting_results',[])):
                    return False
            if db.execute('SELECT 1 FROM chat_turns WHERE id=?', (identity,)).fetchone():
                return False
            for active_id, active_task in self.tasks.items():
                if not active_task.done():
                    active_row = db.execute("SELECT conversation_id FROM chat_turns WHERE id=?", (active_id,)).fetchone()
                    if active_row and active_row[0] == request.conversation_id:
                        return False  # Includes committed replies still compacting memory.
            if db.execute("SELECT COUNT(*) FROM chat_turns WHERE status IN ('queued','running')").fetchone()[0] >= 8:
                return False
            if db.execute("SELECT 1 FROM chat_turns WHERE conversation_id=? AND status IN ('queued','running')",
                          (request.conversation_id,)).fetchone():
                return False
            latest = db.execute("SELECT MAX(sequence) FROM conversation_messages WHERE conversation_id=? AND role='user'",
                                (request.conversation_id,)).fetchone()[0]
            if latest != source_sequence:
                return False
            db.execute('INSERT INTO chat_turns VALUES(?,?,?,?,?,?,NULL,NULL,?)',
                       (identity, signature, request.conversation_id, 'queued', '核验执行结果',
                        json.dumps(payload, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))
        self._start(identity, request)
        return True

    async def _run(self,identity,request,event):
        def report(message):
            with self.store._lock,self.store._connect() as db:
                if not event.is_set():
                    db.execute("UPDATE chat_turns SET message=?,updated_at=? WHERE id=? AND status='running'",(message,datetime.now(timezone.utc).isoformat(),identity))
        token=current_operation.set(OperationContext(event,report))
        try:
            checkpoint()
            with self.store._connect() as db:db.execute("UPDATE chat_turns SET status='running',message='解析需求',updated_at=? WHERE id=?",(datetime.now(timezone.utc).isoformat(),identity))
            with usage_scope(self.conversations.token_usage, request.conversation_id, identity):
                result=await cancellable(self.handler(request))
            checkpoint()
            if not result.content.strip() and not result.workflow:
                raise RuntimeError('本轮未返回有效答复；目标与查询事实已保留，可继续处理')
            phase = (result.assistant_task or {}).get('phase')
            message = {'blocked': '本轮已结束，任务尚未完成', 'needs_input': '等待补充关键信息',
                       'awaiting_run': '方案已准备，等待手动运行'}.get(phase, '处理完成')
            with self.store._connect() as db:db.execute("UPDATE chat_turns SET status='succeeded',message=?,result_json=?,updated_at=? WHERE id=?",(message,result.model_dump_json(),datetime.now(timezone.utc).isoformat(),identity))
        except (Exception,asyncio.CancelledError) as exc:
            status='cancelled' if isinstance(exc,OperationCancelled) else 'interrupted' if isinstance(exc,asyncio.CancelledError) else 'failed'
            message=str(getattr(exc,'detail',exc)) or '处理已中断'
            error=dict(message=message,status_code=499 if status=='cancelled' else getattr(exc,'status_code',500))
            with self.store._connect() as db:db.execute("UPDATE chat_turns SET status=?,message=?,error_json=?,updated_at=? WHERE id=? AND status!='succeeded'",(status,message,json.dumps(error,ensure_ascii=False),datetime.now(timezone.utc).isoformat(),identity))
            from .data_auth import principal
            with self.store._connect() as db:
                stored = db.execute("SELECT status FROM chat_turns WHERE id=?", (identity,)).fetchone()
                if stored and stored[0] != 'succeeded' and db.execute("SELECT 1 FROM sqlite_master WHERE name='assistant_tasks'").fetchone():
                    row = db.execute("SELECT payload FROM assistant_tasks WHERE conversation_id=? AND actor=?",
                                     (request.conversation_id, principal.get().name)).fetchone()
                    state = json.loads(row[0]) if row else {}
                    if state.get('protocol_version') == 2 and state.get('last_request', {}).get('client_turn_id') == identity:
                        state.update(phase='blocked', awaiting_results=[], next_step='本轮请求尚未完成：'+message+'。目标和查询事实已保留，可以重试核验或准备方案。')
                        changed=db.execute("UPDATE assistant_tasks SET payload=? WHERE conversation_id=? AND actor=? AND payload=?",
                                   (json.dumps(state, ensure_ascii=False), request.conversation_id, principal.get().name, row[0]))
                        if changed.rowcount:
                            from .managed_tasks import sync_state
                            sync_state(db,state,request.conversation_id,principal.get().name)
        finally:
            current_operation.reset(token)
            if self.events.get(identity) is event:self.events.pop(identity,None)

    def retry(self,identity):
        from .models import ChatRequest
        with self.store._lock,self.store._connect() as db:
            row=db.execute('SELECT * FROM chat_turns WHERE id=?',(identity,)).fetchone()
            if row is None:raise KeyError(identity)
            self.store.get_conversation(row['conversation_id'])
            if row['status'] not in {'failed','interrupted','cancelled'}:return self.get(identity)
            if identity in self.tasks and not self.tasks[identity].done():raise ValueError('请求仍在停止和清理中，请稍后重试')
            if db.execute("SELECT 1 FROM chat_turns WHERE conversation_id=? AND status IN ('queued','running')",(row['conversation_id'],)).fetchone():raise ValueError('当前会话仍有运行中的请求')
            db.execute("UPDATE chat_turns SET status='queued',error_json=NULL,message='等待重试',updated_at=? WHERE id=?",(datetime.now(timezone.utc).isoformat(),identity))
        payload = json.loads(row['request_json'])
        request = ChatRequest.model_validate(payload)
        request._execution_feedback = payload.get('_server_feedback', {})
        self._start(identity,request)
        return self.get(identity)

    def cancel(self,identity):
        result=self.get(identity)
        with self.store._lock,self.store._connect() as db:
            event=self.events.get(identity)
            if result['status'] in {'queued','running'} and event is not None and not event.is_set():
                event.set()
                db.execute("UPDATE chat_turns SET message='正在停止请求并清理当前步骤',updated_at=? WHERE id=? AND status IN ('queued','running')",(datetime.now(timezone.utc).isoformat(),identity))
        return self.get(identity)

    async def wait(self,identity):
        task=self.tasks.get(identity)
        if task:await asyncio.shield(task)
        result=self.get(identity)
        if result['status']!='succeeded':raise HTTPException((result['error'] or {}).get('status_code',503),(result['error'] or {}).get('message',result['message']))
        from .models import ChatResponse
        return ChatResponse.model_validate(result['result'])
