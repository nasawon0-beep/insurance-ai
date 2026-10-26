import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ONEDIR_SPEC = ROOT / "local-engine" / "local-engine-onedir.spec"
ONEDIR_SCRIPT = ROOT / "scripts" / "build-local-engine-onedir.ps1"
ONEDIR_CONFIG = ROOT / "desktop" / "src-tauri" / "tauri.onedir.conf.json"


def test_onedir_spec_keeps_existing_analysis_and_uses_collect_layout():
    source = ONEDIR_SPEC.read_text(encoding="utf-8")
    assert '"main.py"' in source
    assert '"lan_sync"' in source
    assert 'exclude_binaries=True' in source
    assert 'contents_directory="local-engine._internal"' in source
    assert "COLLECT(" in source
    assert 'name="local-engine-onedir"' in source


def test_onedir_build_is_isolated_and_stages_executable_plus_internal_tree():
    source = ONEDIR_SCRIPT.read_text(encoding="utf-8")
    assert "local-engine-onedir.spec" in source
    assert 'desktop\\src-tauri\\binaries\\onedir' in source
    assert 'local-engine-$TargetTriple.exe' in source
    assert 'local-engine._internal' in source
    assert "Copy-Item" in source
    assert "-Recurse" in source
    assert "$Python311" in source
    assert "if (Test-Path $LocalEngineVenvPython)" in source
    assert "-m venv" not in source
    assert "pip install" not in source
    assert "local-engine.spec" not in source.replace("local-engine-onedir.spec", "")
    assert "local-engine\\dist" not in source


def test_tauri_overlay_keeps_sidecar_name_and_maps_onedir_runtime_resources():
    config = json.loads(ONEDIR_CONFIG.read_text(encoding="utf-8"))
    bundle = config["bundle"]
    assert bundle["externalBin"] == [
        "binaries/control-server",
        "binaries/onedir/local-engine",
    ]
    assert bundle["resources"] == {
        "binaries/onedir/local-engine._internal/": "local-engine._internal/"
    }


def test_all_engine_modes_continue_to_use_the_same_sidecar_entrypoint():
    main = (ROOT / "local-engine" / "main.py").read_text(encoding="utf-8")
    lib = (ROOT / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
    mobile = (ROOT / "desktop" / "src-tauri" / "src" / "mobile_sync.rs").read_text(encoding="utf-8")
    assert '{"--lan-sync-server", "--lan-sync-admin"}' in main
    assert '.sidecar("local-engine")' in lib
    assert mobile.count('.sidecar("local-engine")') == 2
