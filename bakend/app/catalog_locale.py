"""Display-only translations; executable contracts and user content stay canonical.

Translations never touch IDs, CLI arguments, parameter values, conditions, paths,
or stored workflows. No model is called from a listing or a language switch.
"""
from __future__ import annotations
import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'resources' / 'catalog-locales'
TEXT_FIELDS = {'label','description','usage','software_default','condition_help','display_name',
               'title','role','group','placeholder','environment','default_reason','recommendation_reason','reason','instructions',
               'decision','request','summary_en','summary_zh'}
TEXT_LISTS = {'applicability','limitations','selection_rules','runtime_reasons','blocked_reasons',
              'warnings','category','missing_features','input_warnings','scope_warnings'}
SKIP_FIELDS = {'editor','execution','input_bindings','output_bindings','parameter_bindings',
               'fixed_parameters','selected_parameters','parameters_snapshot','messages','history',
               'original_request','content','text','visible_when','required_when','enabled_when',
               'generated_when','required_when_any','default','value'}


@lru_cache(maxsize=4)
def translations(language, signature):
    path=ROOT/(language+'.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


@lru_cache(maxsize=4)
def templates(language, signature):
    result = []
    for source, target in translations(language, signature).items():
        tokens = re.findall(r'\{\d+\}', source)
        literal = re.sub(r'\{\d+\}', '', source)
        if not tokens or len(source) > 500 or not re.search(r'[A-Za-z\u3400-\u9fff]', literal):
            continue
        parts = re.split(r'(\{\d+\})', source)
        pattern = ''.join('(.+?)' if part in tokens else re.escape(part) for part in parts)
        result.append((re.compile(pattern), tokens, target))
    return sorted(result, key=lambda item: len(item[0].pattern), reverse=True)


def translate(text, language):
    path=ROOT/(language+'.json')
    signature = path.stat().st_mtime_ns if path.exists() else 0
    table=translations(language, signature)
    if text in table:
        return table[text]
    for pattern, tokens, target in templates(language, signature):
        match = pattern.fullmatch(text)
        if match:
            values = dict(zip(tokens, match.groups()))
            return re.sub(r'\{\d+\}', lambda m: values.get(m.group(), m.group()), target)
    # Runtime lists may prefix a reason with a stable Runner identifier.
    if '；' in text:
        return ('; ' if language == 'en' else '；').join(translate(part, language) for part in text.split('；'))
    compound = re.fullmatch(r'([A-Za-z0-9_.@-]+)：(.+)', text)
    if compound:
        return compound[1] + (': ' if language == 'en' else '：') + translate(compound[2], language)
    # Pipeline public ports prepend an immutable step ID to a display label.
    if ' · ' in text:
        return ' · '.join(translate(part, language) for part in text.split(' · '))
    return text


def localize(value, language, key='', *, root=True):
    if language not in {'en','zh'}:
        return value
    if isinstance(value, dict):
        custom=value.get('user_defined') or str(value.get('id','')).startswith('user-') or value.get('kind') == 'pipeline' and 'editor' in value
        return {k: (v if k in SKIP_FIELDS or custom and k in {'label','description','title'} else
                    localize(v, language, k, root=False)) for k,v in value.items()}
    if isinstance(value, list):
        return [translate(v, language) if isinstance(v,str) and key in TEXT_LISTS else
                localize(v,language,key,root=False) for v in value]
    return translate(value, language) if isinstance(value,str) and key in TEXT_FIELDS else value


def localize_workflow(value, language):
    """Display only the registered contracts embedded in a saved workflow."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if isinstance(value.get('steps'), list) and 'workflow_id' in value:
        result['steps'] = [{**step, 'method_options': localize(step.get('method_options', []), language)}
                           for step in value['steps']]
    # Conflict responses and operation wrappers may contain a workflow snapshot.
    for key in ('workflow', 'current', 'result', 'detail'):
        if isinstance(value.get(key), dict):
            result[key] = localize_workflow(value[key], language)
    return result


class CatalogLocaleMiddleware:
    """Translate metadata JSON responses, never conversation streaming.

    Workflow responses project registered method metadata only; user titles,
    narratives, file bindings and selected values remain byte-for-byte intact.

    ASGI middleware avoids BaseHTTPMiddleware's streaming/disconnect lifecycle.
    It is intentionally opt-in via a language query or Accept-Language header.
    """
    def __init__(self, app):
        self.app=app

    async def __call__(self, scope, receive, send):
        from urllib.parse import parse_qs
        path=scope.get('path','')
        prefixes=('/api/library','/api/skills','/api/methods','/api/pipelines','/api/capabilities','/api/artifacts/types')
        workflow_path = path.startswith('/api/workflows') or path.endswith('/workflows')
        catalog_path = path.startswith(prefixes) and not path.endswith('/reference')
        if scope['type']!='http' or not (workflow_path or catalog_path):
            return await self.app(scope,receive,send)
        headers=dict(scope.get('headers',[]))
        query=parse_qs(scope.get('query_string',b'').decode())
        language=query.get('language',[headers.get(b'accept-language',b'').decode().split(',')[0].split('-')[0]])[0]
        if language not in {'en','zh'}:
            return await self.app(scope,receive,send)
        start=None
        passthrough=False
        chunks=[]
        async def localized_send(message):
            nonlocal start, passthrough
            if message['type']=='http.response.start':
                content_type = dict(message.get('headers', [])).get(b'content-type', b'')
                passthrough = b'application/json' not in content_type
                if passthrough:
                    await send(message)
                else:
                    start=message
            elif message['type']=='http.response.body':
                if passthrough:
                    return await send(message)
                chunks.append(message.get('body',b''))
                if message.get('more_body'):
                    return
                body=b''.join(chunks)
                if start and (200 <= start['status'] < 300 or start['status'] == 409):
                    try:
                        value = json.loads(body)
                        projected = localize_workflow(value, language) if workflow_path else localize(value, language)
                        body=json.dumps(projected,ensure_ascii=False).encode()
                    except (ValueError,UnicodeError):
                        pass
                if start:
                    start['headers']=[(k,v) for k,v in start['headers'] if k.lower() not in {b'content-length',b'vary'}]
                    start['headers'] += [(b'content-length',str(len(body)).encode()),(b'vary',b'Accept-Language')]
                    await send(start)
                await send({**message,'body':body})
            else:
                await send(message)
        await self.app(scope,receive,localized_send)
