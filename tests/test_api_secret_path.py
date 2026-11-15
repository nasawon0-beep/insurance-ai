"""Exercise only API-secret file initialization, never import/start the engine."""
import ast
import getpass
import marshal
import os
from pathlib import Path
import secrets
import tempfile
import types
import pytest


def load_create_secret():
    archive_path = os.environ.get('API_SECRET_REGRESSION_ARCHIVE')
    if archive_path:
        from PyInstaller.archive.readers import CArchiveReader
        code = marshal.loads(CArchiveReader(archive_path).extract('main'))
        function = next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == '_create_api_secret')
        return types.FunctionType(function, {'os': os, 'getpass': getpass, 'tempfile': tempfile, 'secrets': secrets})
    source = Path(__file__).resolve().parents[1] / 'local-engine/main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_create_api_secret')
    namespace = {'os': os, 'getpass': getpass, 'tempfile': tempfile, 'secrets': secrets}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace['_create_api_secret']


def test_explicit_path_does_not_require_username_environment(monkeypatch, tmp_path):
    for name in ('LOGNAME', 'USER', 'LNAME', 'USERNAME'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delattr(os, 'getuid', raising=False)
    path = tmp_path / 'owned-fixture.secret'
    monkeypatch.setenv('INSURANCE_AI_API_SECRET_FILE', str(path))
    value = load_create_secret()()
    assert path.read_bytes() == value.encode('utf-8')
    assert len(value) >= 32
    previous = value
    value = load_create_secret()()
    assert value != previous
    assert path.read_bytes() == value.encode('utf-8')


def test_unset_path_retains_legacy_username_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv('INSURANCE_AI_API_SECRET_FILE', raising=False)
    monkeypatch.delattr(os, 'getuid', raising=False)
    monkeypatch.setattr(getpass, 'getuser', lambda: 'synthetic-fixture-user')
    monkeypatch.setattr(tempfile, 'gettempdir', lambda: str(tmp_path))
    value = load_create_secret()()
    assert (tmp_path / 'insurance-ai-api-synthetic-fixture-user.secret').read_bytes() == value.encode('utf-8')


def test_empty_explicit_path_fails_closed_without_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv('INSURANCE_AI_API_SECRET_FILE', '')
    monkeypatch.delattr(os, 'getuid', raising=False)
    monkeypatch.setattr(getpass, 'getuser', lambda: 'synthetic-fixture-user')
    monkeypatch.setattr(tempfile, 'gettempdir', lambda: str(tmp_path))
    with pytest.raises(OSError):
        load_create_secret()()
    assert not list(tmp_path.iterdir())
