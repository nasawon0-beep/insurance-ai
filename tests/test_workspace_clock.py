"""Synthetic clock only; never changes the Windows clock."""
import hashlib

def test_business_deadline_cannot_be_extended_by_clock_rollback(monkeypatch):
    import account_boundary as module
    wall=[1000.0]; mono=[100.0]
    monkeypatch.setattr(module.time,'time',lambda:wall[0])
    monkeypatch.setattr(module.time,'monotonic',lambda:mono[0])
    binding=module.AccountBinding('https://fixture.supabase.co/auth/v1','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',hashlib.sha256(b'fixture').hexdigest(),1050)
    assert binding.accepts('Bearer fixture')
    mono[0]=151.0
    assert not binding.accepts('Bearer fixture')
    wall[0]=999.0
    assert not binding.accepts('Bearer fixture')

def test_observed_clock_reversal_latches_denial(monkeypatch):
    import account_boundary as module
    wall=[1000.0]
    monkeypatch.setattr(module.time,'time',lambda:wall[0])
    binding=module.AccountBinding('https://fixture.supabase.co/auth/v1','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',hashlib.sha256(b'fixture').hexdigest(),9999)
    wall[0]=1300.0
    assert binding.accepts('Bearer fixture')
    wall[0]=1000.0
    assert not binding.accepts('Bearer fixture')
    wall[0]=1300.0
    assert not binding.accepts('Bearer fixture')
