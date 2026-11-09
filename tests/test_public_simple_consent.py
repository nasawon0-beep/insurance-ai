"""Static contract only. Windows behavioral/OS verification is separate."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_simple_public_warning_and_persistent_permission_are_explicit():
    ui=(ROOT/'desktop/src/components/MobileConnectionPanel.tsx').read_text(encoding='utf-8')
    for text in ['공용 네트워크에서 연결할까요?','공용 네트워크에서는 자료 동기화 시 보안 위험이 커질 수 있습니다. 개인 Wi-Fi 또는 휴대폰 핫스팟 사용을 권장합니다.','무시하고 연결','연결하지 않기','공용 네트워크 허용 해제','앱을 종료해도 유지']:
        assert text in ui,text
    assert '이번 연결에만' not in ui
    assert 'mobile_sync_public_connect' not in ui

def test_public_permission_reuses_scoped_policy_without_guardian_or_tasks():
    policy=(ROOT/'desktop/src-tauri/src/mobile_firewall.ps1').read_text(encoding='utf-8')
    assert "[ValidateSet('Auto','Private','Public')]" in policy
    assert "Profile=$profile" in policy
    assert "Protocol='TCP'; LocalPort=18443" in policy
    assert "RemoteAddress='LocalSubnet'" in policy
    assert 'PersistentStore' in policy and '-Enabled False' in policy
    assert "Public.Allow.v1." in policy
    for forbidden in ['Register-ScheduledTask','Set-NetConnectionProfile','Set-NetFirewallProfile','Remove-NetFirewallRule']:
        assert forbidden not in policy
    rust=(ROOT/'desktop/src-tauri/src/mobile_firewall.rs').read_text(encoding='utf-8')
    assert 'mod public_session' not in rust
