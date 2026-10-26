from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
BUILD = ROOT / ".github" / "workflows" / "build.yml"


def test_release_workflow_builds_verified_onedir_candidate_as_draft():
    source = RELEASE.read_text(encoding="utf-8")
    assert "scripts/build-local-engine-onedir.ps1" in source
    assert "src-tauri/tauri.onedir.conf.json" in source
    assert "local-engine-onedir.spec" not in source
    assert "local-engine.spec" not in source
    assert "Create or update draft GitHub Release" in source
    assert "gh release create \"$TAG\" --draft" in source
    assert "actions/upload-artifact@v4" in source


def test_release_workflow_verifies_runtime_modules_and_resources_before_bundle():
    source = RELEASE.read_text(encoding="utf-8")
    for required in (
        "runtime_paths",
        "database.coverage",
        "database.coverage_analysis_parser",
        "database.coverage_mapping",
        "local-engine._internal",
        "python311.dll",
    ):
        assert required in source


def test_release_workflow_uses_existing_signing_secret_names_only():
    source = RELEASE.read_text(encoding="utf-8")
    assert "${{ secrets.TAURI_SIGNING_PRIVATE_KEY }}" in source
    assert "TAURI_SIGNING_PRIVATE_KEY:" in source
    assert "TAURI_SIGNING_PRIVATE_KEY_PASSWORD:" not in source


def test_general_build_is_draft_and_public_mirror_requires_explicit_input():
    source = BUILD.read_text(encoding="utf-8")
    assert "publish_public:" in source
    assert "default: false" in source
    assert "releaseDraft: true" in source
    assert "if: ${{ inputs.publish_public == true }}" in source


def test_general_windows_build_uses_onedir_overlay():
    source = BUILD.read_text(encoding="utf-8")
    assert "scripts/build-local-engine-onedir.ps1" in source
    assert "src-tauri/tauri.onedir.conf.json" in source
