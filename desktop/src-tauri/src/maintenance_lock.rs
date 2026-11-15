//! Exclusive application ownership retained during the worker handoff.
use std::{fs::{File,OpenOptions},path::Path};
use fs2::FileExt;
pub(crate) struct ApplicationLease { app:File, _guardian:File, token:String, held:bool }
impl ApplicationLease {
 pub fn acquire(path:&Path)->Result<Self,String>{
  let open=|p:&Path|OpenOptions::new().create(true).truncate(false).read(true).write(true).open(p).map_err(|_|"APP_LOCK_OPEN_FAILED".to_string());
  let guardian=open(&path.with_extension("owner"))?;
  guardian.try_lock_exclusive().map_err(|_|"APP_ALREADY_RUNNING")?;
  let app=open(path)?;app.try_lock_exclusive().map_err(|_|"APP_OR_MAINTENANCE_RUNNING")?;
  Ok(Self{app,_guardian:guardian,token:crate::workspace_grant::random_hex()?,held:true})
 }
 pub fn token(&self)->&str{&self.token}
 pub fn handoff(&mut self,token:&str,stop:impl FnOnce()->Result<(),String>,run:impl FnOnce()->Result<(),String>)->Result<(),String>{
  if token!=self.token||!self.held{return Err("STALE_LOCK_OWNER".into());}
  stop()?; // Never release until the trusted coordinator proves all owned jobs empty.
  FileExt::unlock(&self.app).map_err(|_|"APP_UNLOCK_FAILED")?;self.held=false;
  let result=std::panic::catch_unwind(std::panic::AssertUnwindSafe(run));
  self.app.try_lock_exclusive().map_err(|_|"APP_LOCK_REACQUIRE_FAILED_RESTART_REQUIRED")?;self.held=true;
  match result {Ok(v)=>v,Err(_)=>Err("MAINTENANCE_PANIC_RESTART_REQUIRED".into())}
 }
}
#[cfg(test)]
mod tests {
 use super::*;
 #[test] fn failed_stop_and_stale_owner_never_release_app_lock(){
  let root=std::env::temp_dir().join(format!("handoff-{}",crate::workspace_grant::random_hex().unwrap()));std::fs::create_dir_all(&root).unwrap();
  let mut owner=ApplicationLease::acquire(&root.join("app.lock")).unwrap();
  assert!(ApplicationLease::acquire(&root.join("app.lock")).is_err());
  let token=owner.token().to_owned();
  assert!(owner.handoff("stale",||Ok(()),||panic!("stale worker")).is_err());
  assert!(owner.handoff(&token,||Err("not stopped".into()),||panic!("running business")).is_err());
  let f=std::fs::OpenOptions::new().write(true).open(root.join("app.lock")).unwrap();assert!(fs2::FileExt::try_lock_exclusive(&f).is_err());
  owner.handoff(&token,||Ok(()),||{fs2::FileExt::try_lock_exclusive(&f).unwrap();fs2::FileExt::unlock(&f).unwrap();assert!(ApplicationLease::acquire(&root.join("app.lock")).is_err());Ok(())}).unwrap();
  assert!(fs2::FileExt::try_lock_exclusive(&f).is_err());
 }
}
