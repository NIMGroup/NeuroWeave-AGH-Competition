"""Schedule read-only specialist calls; dependency and mutation boundaries stay explicit."""
import asyncio

from .agent_arguments import tool_arguments


async def dispatch_agents(calls, invoke):
    results, pending, roles, dependencies = {}, set(range(len(calls))), {}, {}

    def reject(index, reason):
        results[index] = ({'error': reason, 'recoverable': True, 'executed': False,
                           'dispatch_error': True}, None)
        pending.discard(index)

    for index, call in enumerate(calls):
        try:
            args = tool_arguments(call['function']['arguments'])
        except (ValueError, TypeError, KeyError):
            # Normal call validation reports malformed JSON without suppressing peers.
            args = {}
        role = args.get('agent')
        roles[index] = role if isinstance(role, str) and role in {'data', 'methods', 'runtime'} else f'invalid-{index}'
        dependencies[index] = args.get('depends_on', [])
    for index in list(pending):
        deps = dependencies[index]
        if not isinstance(deps, list) or any(not isinstance(d, str) for d in deps):
            reject(index, 'depends_on 必须是本批次功能 agent 名称列表')
        elif roles[index] in deps or any(d not in roles.values() for d in deps):
            reject(index, '调查依赖需要本批次其他 agent；不能依赖自身或不存在的分派')
    while pending:
        ready, occupied = [], set()
        for index in sorted(pending):
            required = {i for i, role in roles.items() if role in dependencies[index]}
            if not required <= results.keys():
                continue
            if any(results[i][0].get('error') for i in required):
                reject(index, '前置调查未成功；保留共享事实，主控需修正具体分派缺口')
                continue
            if roles[index] not in occupied:
                ready.append(index)
                occupied.add(roles[index])
            if len(ready) == 3:
                break
        if not ready:
            for index in list(pending):
                reject(index, '调查依赖形成循环，无法开始；请拆分独立问题')
            break
        tasks = [asyncio.create_task(invoke(calls[index])) for index in ready]
        try:
            values = await asyncio.gather(*tasks)
        except BaseException:
            # Stop/error propagation must drain all sibling transports and readers.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        for index, value in zip(ready, values):
            results[index] = value
            pending.remove(index)
    return [results[index] for index in range(len(calls))]
