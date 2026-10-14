"""Large console output cannot hide function validation or the actual failure."""
import contextlib
import io
from app.bounded_log import HeadTailLog
from app.script_verification import ScriptCase, sandbox_program


def test_large_script_output_retains_validation_completion_in_bounded_capture():
    code='def transform(row):\n    return row.get("value", 0)\nprint("x" * 200000)\n'
    cases=[ScriptCase(name='ordinary',function='transform',args=[{'value':2}],expected=2),
           ScriptCase(name='missing',function='transform',args=[{}],expected=0)]
    output=io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(sandbox_program(code,cases),{})
    log=io.BytesIO();capture=HeadTailLog(log)
    raw=output.getvalue().encode()
    for offset in range(0,len(raw),4096):capture.write(raw[offset:offset+4096])
    capture.finish();value=log.getvalue()
    assert len(value)<75000 and b'middle bytes omitted' in value
    assert b'NEUROWEAVE_FUNCTION_CASES_PASSED=2' in value.splitlines()


def test_small_log_is_exact_and_large_error_tail_survives():
    log=io.BytesIO();capture=HeadTailLog(log);capture.write(b'plain\n');capture.finish()
    assert log.getvalue()==b'plain\n'
    log=io.BytesIO();capture=HeadTailLog(log)
    capture.write(b'input details\n'+b'x'*100000+b'\nValueError: conflicting records\n');capture.finish()
    assert log.getvalue().startswith(b'input details\n')
    assert log.getvalue().endswith(b'ValueError: conflicting records\n')


def test_numeric_cases_allow_equivalent_numbers_but_reject_boolean_confusion():
    import pytest
    case=ScriptCase(name='mean',function='mean',args=[[1,3]],expected=2)
    exec(sandbox_program('def mean(xs):\n    return sum(xs)/len(xs)\n',[case]),{})
    invalid=ScriptCase(name='boolean is not count',function='mean',args=[[]],expected=1)
    with pytest.raises(ValueError):
        exec(sandbox_program('def mean(xs):\n    return True\n',[invalid]),{})


def test_tool_argument_envelopes_preserve_code_and_cannot_add_grants():
    import json
    import pytest
    from app.agent_arguments import tool_arguments
    raw={'code':'print("value")\n', 'source_scope':'output', 'mode':'copy'}
    assert tool_arguments(json.dumps(raw)) == raw
    assert tool_arguments(json.dumps({'arguments':json.dumps(raw)})) == raw
    assert tool_arguments(json.dumps(json.dumps(raw))) == raw
    assert 'confirmed' not in tool_arguments(json.dumps({'arguments':raw}))
    with pytest.raises(ValueError,match='完整 JSON'):
        tool_arguments('{"code":"unterminated')
