from test_workspace_transition_safety import review
from test_workspace_transition import A
import account_migration as m
import pytest

@pytest.mark.parametrize('mode',['empty-bootstrap','same-owner-format'])
def test_rollback_journal_commit_before_marker_cleanup_can_resume(tmp_path,monkeypatch,mode):
 installation,w,plan,approval,key=review(tmp_path,mode);before=m._tree_snapshot(w.root)
 m.migrate(plan,A,approval,key)
 with monkeypatch.context() as patch:
  def interrupted(*args):raise OSError('synthetic interruption after rollback journal')
  patch.setattr(m,'_clear_transition_marker',interrupted)
  with pytest.raises(OSError):m.rollback(plan,A,approval,key)
 assert m._transition_marker(w.root).exists()
 m.rollback(plan,A,approval,key)
 assert not m._transition_marker(w.root).exists()
 assert m._tree_snapshot(w.root)==before

@pytest.mark.parametrize('mode',['empty-bootstrap','same-owner-format'])
def test_new_empty_directory_after_promotion_blocks_rollback(tmp_path,mode):
 installation,w,plan,approval,key=review(tmp_path,mode)
 m.migrate(plan,A,approval,key)
 (w.root/'user-created-directory').mkdir()
 before=m._tree_snapshot(w.root)
 with pytest.raises(m.MigrationError):m.rollback(plan,A,approval,key)
 assert m._tree_snapshot(w.root)==before
