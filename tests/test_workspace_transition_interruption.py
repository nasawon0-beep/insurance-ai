import pytest
from test_workspace_transition_safety import review
from test_workspace_transition import A
import account_migration as m

@pytest.mark.parametrize('mode',['empty-bootstrap','same-owner-format'])
@pytest.mark.parametrize('point',['before-candidate','after-candidate','after-original'])
def test_interrupted_rollback_rename_resumes_without_loss(tmp_path,monkeypatch,mode,point):
 installation,w,plan,approval,key=review(tmp_path,mode);before=m._tree_snapshot(w.root)
 m.migrate(plan,A,approval,key)
 rename=m.os.rename
 def interrupted(source,dest):
  if point=='before-candidate' and str(dest).endswith('rolled-back-workspace'):raise OSError('fixture interruption')
  result=rename(source,dest)
  if point=='after-candidate' and str(dest).endswith('rolled-back-workspace'):raise OSError('fixture interruption')
  if point=='after-original' and str(source).endswith('previous-workspace'):raise OSError('fixture interruption')
  return result
 with monkeypatch.context() as patch:
  patch.setattr(m.os,'rename',interrupted)
  with pytest.raises(OSError):m.rollback(plan,A,approval,key)
 m.rollback(plan,A,approval,key)
 assert m._tree_snapshot(w.root)==before
 assert not m._transition_marker(w.root).exists()

@pytest.mark.parametrize('mode',['empty-bootstrap','same-owner-format'])
def test_conflict_during_staging_preserves_live_target_without_pending_marker(tmp_path,mode):
 installation,w,plan,approval,key=review(tmp_path,mode)
 def changed(phase):
  if phase=='staging':(w.root/'new-user-file').write_bytes(b'SYNTHETIC NEW USER CONTENT')
 with pytest.raises(m.MigrationError):m.migrate(plan,A,approval,key,checkpoint=changed)
 assert (w.root/'new-user-file').read_bytes()==b'SYNTHETIC NEW USER CONTENT'
 assert not m._transition_marker(w.root).exists()
