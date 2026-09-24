from types import SimpleNamespace

import pytest

from spark_intelligence.chip_create import pipeline

WINDOWS_KEYS = (
    'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP',
    'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA',
)
COMMON_KEYS = ('LANG', 'LC_ALL', 'LC_CTYPE', 'PATH', 'TMPDIR', 'CODEX_HOME')

@pytest.mark.parametrize('platform', ['nt', 'posix'])
def test_codex_environment_is_platform_allowlisted(monkeypatch, platform):
    source = {key: 'synthetic-' + key for key in COMMON_KEYS + WINDOWS_KEYS}
    source.update(OPENAI_API_KEY='synthetic-not-a-key', UNRELATED_VARIABLE='excluded')
    monkeypatch.setattr(pipeline, 'os', SimpleNamespace(name=platform, environ=source))
    expected_keys = COMMON_KEYS + (WINDOWS_KEYS if platform == 'nt' else ())
    assert pipeline._codex_cli_env() == {key: source[key] for key in expected_keys}

@pytest.mark.parametrize('platform', ['nt', 'posix'])
def test_codex_environment_omits_empty_and_missing_values(monkeypatch, platform):
    source = {'CODEX_HOME': 'synthetic-codex-home', 'PATH': 'synthetic-path', 'TEMP': '', 'LANG': ''}
    monkeypatch.setattr(pipeline, 'os', SimpleNamespace(name=platform, environ=source))
    assert pipeline._codex_cli_env() == {'CODEX_HOME': source['CODEX_HOME'], 'PATH': source['PATH']}

def test_codex_environment_defaults_home_without_forwarding_home(monkeypatch):
    monkeypatch.setattr(pipeline, 'os', SimpleNamespace(name='posix', environ={'HOME': 'do-not-forward'}))
    assert pipeline._codex_cli_env() == {'CODEX_HOME': str(pipeline.Path.home() / '.codex')}
