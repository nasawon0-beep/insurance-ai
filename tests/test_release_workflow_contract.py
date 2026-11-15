from pathlib import Path
import re
import pytest


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
BUILD = ROOT / ".github" / "workflows" / "build.yml"


def test_release_workflow_builds_verified_onedir_candidate_as_draft():
    source = RELEASE.read_text(encoding="utf-8")
    assert "scripts/build-local-engine-onedir.ps1" in source
    assert "src-tauri/tauri.onedir.conf.json" in source
    assert "local-engine-onedir.spec" not in source
    assert "local-engine.spec" not in source
    assert "Create new draft GitHub Release only" in source
    assert 'python scripts/create_release_only.py --version "$RELEASE_VERSION"' in source
    publisher = (ROOT / 'scripts/create_release_only.py').read_text(encoding='utf-8')
    assert '"draft": True' in publisher
    assert '--clobber' not in source and 'gh release edit' not in source
    assert "actions/upload-artifact@v4" in source


def test_release_workflow_verifies_runtime_modules_and_resources_before_bundle():
    source = RELEASE.read_text(encoding="utf-8")
    for required in (
        "runtime_paths",
        "database.coverage",
        "database.coverage_analysis_parser",
        "database.coverage_mapping",
        "database.family",
        "local-engine._internal",
        "python311.dll",
    ):
        assert required in source


def assert_signing_secret_contract(source):
    # Preserve the successful existing-key workflow: no optional password input.
    # Never replace the signing key or disable updater signatures to clear a build.
    pattern = r'^\s*(TAURI_SIGNING_PRIVATE_KEY(?:_PASSWORD)?):\s*(.+)$'
    bindings = re.findall(pattern, source, re.M)
    expected = {'TAURI_SIGNING_PRIVATE_KEY': '${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}'}
    assert len(bindings) == 1 and dict(bindings) == expected, 'only the existing signing key reference is approved'
    step = source.split('- name: Build Windows signed bundles from onedir candidate', 1)[1].split('- name:', 1)[0]
    assert dict(re.findall(pattern, step, re.M)) == expected, 'signing secrets must be build-scoped'
    remainder = re.sub(pattern, '', source, flags=re.M)
    assert 'TAURI_SIGNING_PRIVATE_KEY' not in remainder, 'signing secret must not be printed/passed in run commands'
    assert not re.search(r'-----BEGIN .*PRIVATE KEY-----', source), 'private key literal forbidden'


def test_release_workflow_uses_existing_signing_secret_names_only():
    assert_signing_secret_contract(RELEASE.read_text(encoding='utf-8'))


@pytest.mark.parametrize('fault', ['literal-key', 'literal-password', 'wrong-secret', 'missing-key', 'echo-key', 'duplicate', 'injected-password'])
def test_signing_contract_rejects_leaks_and_wrong_references(fault):
    source = RELEASE.read_text(encoding='utf-8')
    key = '${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}'
    password = '${{ secrets.TAURI_SIGNING_PRIVATE_KEY_PASSWORD }}'
    if fault == 'literal-key':
        source = source.replace(key, 'SYNTHETIC-NOT-A-SECRET')
    elif fault == 'literal-password':
        source += '\n          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: SYNTHETIC-NOT-A-SECRET\n'
    elif fault == 'wrong-secret':
        source = source.replace(key, '${{ secrets.UNAPPROVED_SIGNER }}')
    elif fault == 'missing-key':
        source = re.sub(r'^.*TAURI_SIGNING_PRIVATE_KEY:.*\n', '', source, flags=re.M)
    elif fault == 'injected-password':
        source += '\n          TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ' + password + '\n'
    elif fault == 'echo-key':
        source += '\n          echo "$TAURI_SIGNING_PRIVATE_KEY"\n'
    else:
        source += '\n          TAURI_SIGNING_PRIVATE_KEY: ' + key + '\n'
    with pytest.raises(AssertionError):
        assert_signing_secret_contract(source)


def test_updater_signing_remains_enabled_without_password_input():
    import json
    source=RELEASE.read_text(encoding='utf-8')
    assert 'TAURI_SIGNING_PRIVATE_KEY_PASSWORD' not in source
    step=source.split('- name: Build Windows signed bundles from onedir candidate',1)[1].split('- name:',1)[0]
    assert 'npm run tauri -- build --ci -v --config src-tauri/tauri.onedir.conf.json' in step
    assert '--no-sign' not in step
    base=json.loads((ROOT/'desktop/src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
    overlay=json.loads((ROOT/'desktop/src-tauri/tauri.onedir.conf.json').read_text(encoding='utf-8'))
    assert base['bundle']['createUpdaterArtifacts'] is True
    assert overlay.get('bundle',{}).get('createUpdaterArtifacts',True) is True


def test_release_validation_uses_the_local_engine_venv_python():
    source = RELEASE.read_text(encoding="utf-8")
    validate_step = source.split("- name: Validate release candidate", 1)[1].split(
        "- name: Build Windows signed bundles", 1
    )[0]

    assert "local-engine/.venv/Scripts/python.exe -m pytest -q" in validate_step
    assert "\n          python -m pytest -q" not in validate_step
    install_command = "local-engine/.venv/Scripts/python.exe -m pip install"
    assert install_command in source
    assert source.index(install_command) < source.index("- name: Validate release candidate")


def test_release_cargo_checks_the_staged_onedir_sidecar_config():
    source = RELEASE.read_text(encoding="utf-8")
    validate_step = source.split("- name: Validate release candidate", 1)[1].split(
        "- name: Build Windows signed bundles", 1
    )[0]
    cargo_check_lines = [
        line.strip() for line in source.splitlines() if "cargo check" in line
    ]

    assert 'TAURI_CONFIG="$(cat tauri.onedir.conf.json)" cargo check' in validate_step
    assert cargo_check_lines == [
        'TAURI_CONFIG="$(cat tauri.onedir.conf.json)" cargo check'
    ]
    assert (
        "npm run tauri -- build --ci -v --config src-tauri/tauri.onedir.conf.json"
        in source
    )


def test_general_build_is_draft_and_public_mirror_requires_explicit_input():
    source = BUILD.read_text(encoding="utf-8")
    assert "publish_public:" in source
    assert "default: false" in source
    assert "releaseDraft: true" in source
    assert "if: ${{ inputs.publish_public == true }}" in source


def assert_api_secret_ci_contract(source):
    step = source.split('- name: Validate release candidate', 1)[1].split('- name:', 1)[0]
    guard = re.search(r'for Input in ([^\n]+); do\n(.*?)\n\s*done', step, re.S)
    assert guard is not None, 'required-input guard missing'
    assert 'tests/test_api_secret_path.py' in guard.group(1).split(), 'API-secret fixture must be guarded'
    assert 'if [ ! -f "$Input" ]; then' in guard.group(2)
    assert 'exit 2' in guard.group(2)
    command = 'local-engine/.venv/Scripts/python.exe -m pytest --noconftest -q tests/test_api_secret_path.py'
    assert command in [line.strip() for line in step.splitlines()], 'isolated API-secret CI command missing'
    assert step.index(command) > guard.end(), 'guard must precede execution'


def test_release_runs_guarded_api_secret_regression():
    assert_api_secret_ci_contract(RELEASE.read_text(encoding='utf-8'))


@pytest.mark.parametrize('fault', ['missing-guard', 'missing-command', 'ignored-failure'])
def test_api_secret_ci_contract_rejects_omission(fault):
    source = RELEASE.read_text(encoding='utf-8')
    command = 'local-engine/.venv/Scripts/python.exe -m pytest --noconftest -q tests/test_api_secret_path.py'
    if fault == 'missing-guard':
        source = re.sub(r'(for Input in )tests/test_api_secret_path.py ', r'\1', source)
    elif fault == 'missing-command':
        source = source.replace(command, '')
    else:
        source = source.replace(command, command + ' || true')
    with pytest.raises(AssertionError):
        assert_api_secret_ci_contract(source)


def test_general_windows_build_uses_onedir_overlay():
    source = BUILD.read_text(encoding="utf-8")
    assert "scripts/build-local-engine-onedir.ps1" in source
    assert "src-tauri/tauri.onedir.conf.json" in source
