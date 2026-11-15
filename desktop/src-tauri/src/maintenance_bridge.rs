//! Internal maintenance bridge, deliberately NOT a WebView command.
//! Caller must hold the account transition/maintenance lease; deployment wiring is separately gated.
use std::{path::{Path,PathBuf},process::{Command,Stdio,Child},io::{Write,BufRead,BufReader,Read},collections::BTreeMap,sync::mpsc,time::{Duration,SystemTime,UNIX_EPOCH}};
use sha2::{Digest,Sha256};
use base64::{Engine,engine::general_purpose::URL_SAFE};
use crate::workspace_grant::{Store,SignedGrant};
fn denied()->String{"MAINTENANCE_DENIED".into()}
fn now()->u64{SystemTime::now().duration_since(UNIX_EPOCH).map(|v|v.as_secs()).unwrap_or(u64::MAX)}
pub(crate) struct PinnedWorker {pub executable:PathBuf,pub executable_sha256:String,pub script:Option<(PathBuf,String)>}
pub(crate) fn validate_worker(worker:&PinnedWorker)->Result<(),String>{pinned(&worker.executable,&worker.executable_sha256)?;if let Some((p,h))=&worker.script{pinned(p,h)?;}Ok(())}
fn pinned(path:&Path,hash:&str)->Result<std::fs::File,String>{
 if !path.is_absolute()||hash.len()!=64{return Err(denied());}
 for p in path.ancestors(){let m=std::fs::symlink_metadata(p).map_err(|_|denied())?;
  if m.file_type().is_symlink(){return Err(denied());}
  #[cfg(windows)] {use std::os::windows::fs::MetadataExt;if m.file_attributes()&0x400!=0{return Err(denied());}}
 }
 let mut options=std::fs::OpenOptions::new();options.read(true);
 #[cfg(windows)] {use std::os::windows::fs::OpenOptionsExt;options.share_mode(1);}
 let mut f=options.open(path).map_err(|_|denied())?;let mut digest=Sha256::new();let mut buf=[0u8;65536];
 loop{let n=f.read(&mut buf).map_err(|_|denied())?;if n==0{break;}digest.update(&buf[..n]);}
 if format!("{:x}",digest.finalize())!=hash{return Err(denied());}Ok(f)
}
struct OwnedChild(Child,#[cfg(windows)] crate::SidecarJob);
impl Drop for OwnedChild{fn drop(&mut self){#[cfg(windows)] let _=self.1.stop_confirmed();let _=self.0.kill();let _=self.0.wait();}}
fn spawn_owned(command:&mut Command)->Result<OwnedChild,String>{
 #[cfg(windows)] {
  use std::os::windows::process::CommandExt;
  use windows_sys::Win32::{Foundation::{CloseHandle,INVALID_HANDLE_VALUE},System::{Diagnostics::ToolHelp::*,Threading::*}};
  command.creation_flags(CREATE_SUSPENDED|CREATE_NO_WINDOW);
  let job=crate::SidecarJob::new().map_err(|_|denied())?;
  let child=command.spawn().map_err(|_|denied())?;let owned=OwnedChild(child,job);
  owned.1.assign(owned.0.id()).map_err(|_|denied())?;
  // Assign before any Python/one-file bootloader instruction can fork descendants.
  unsafe {
   let snapshot=CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD,0);if snapshot==INVALID_HANDLE_VALUE{return Err(denied());}
   let mut entry:THREADENTRY32=std::mem::zeroed();entry.dwSize=std::mem::size_of_val(&entry) as u32;
   let mut ok=Thread32First(snapshot,&mut entry);let mut resumed=false;
   while ok!=0 {if entry.th32OwnerProcessID==owned.0.id(){let thread=OpenThread(THREAD_SUSPEND_RESUME,0,entry.th32ThreadID);if !thread.is_null(){resumed=ResumeThread(thread)!=u32::MAX;CloseHandle(thread);}break;}ok=Thread32Next(snapshot,&mut entry);}
   CloseHandle(snapshot);if !resumed{return Err(denied());}
  }Ok(owned)
 }
 #[cfg(not(windows))] {Ok(OwnedChild(command.spawn().map_err(|_|denied())?))}
}
fn pipe_run(worker:&PinnedWorker,frame:&serde_json::Value,fence:impl FnMut()->Result<(),String>,key:impl FnMut()->Result<Vec<u8>,String>)->Result<(),String>{
 pipe_run_bounded(worker,frame,fence,key,Duration::from_secs(300))
}
fn pipe_run_bounded(worker:&PinnedWorker,frame:&serde_json::Value,mut fence:impl FnMut()->Result<(),String>,mut key:impl FnMut()->Result<Vec<u8>,String>,timeout:Duration)->Result<(),String>{
 let _binary_pin=pinned(&worker.executable,&worker.executable_sha256)?;
 let mut command=Command::new(&worker.executable);
 let _script_pin=if let Some((script,hash))=&worker.script{let file=pinned(script,hash)?;command.arg(script);Some(file)}else{None};
 command.env_clear();if let Some(root)=std::env::var_os("SystemRoot"){command.env("SystemRoot",root);}
 command.env("TEMP",std::env::temp_dir()).env("TMP",std::env::temp_dir());
 // stdout is untrusted diagnostics; stderr is an exclusively captured status pipe.
 // Never inherit either channel: no worker output may reach terminal/logs.
 command.stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::piped());
 let mut payload=serde_json::to_vec(frame).map_err(|_|denied())?;if payload.len()>1048575{return Err(denied());}payload.push(b'\n');
 fence()?;let mut child=spawn_owned(&mut command)?;
 let mut input=child.0.stdin.take().ok_or_else(denied)?;
 // A hung worker must not trap the account transition in a blocking stdin write.
 let(write_tx,write_rx)=mpsc::sync_channel(1);
 std::thread::spawn(move||{let result=input.write_all(&payload);payload.fill(0);let _=write_tx.send(if result.is_ok(){Some(input)}else{None});});
 let write_started=std::time::Instant::now();
 let mut input=loop{fence()?;if write_started.elapsed()>Duration::from_secs(15){return Err(denied());}match write_rx.recv_timeout(Duration::from_millis(50)){Ok(Some(handle))=>break handle,Ok(None)|Err(mpsc::RecvTimeoutError::Disconnected)=>return Err(denied()),Err(_)=>{}}};
 let stdout=child.0.stderr.take().ok_or_else(denied)?;let(tx,rx)=mpsc::sync_channel(2);
 std::thread::spawn(move||{let mut reader=BufReader::new(stdout);loop{let mut line=Vec::new();let read=reader.by_ref().take(33).read_until(b'\n',&mut line);if read.is_err()||line.len()>32{let _=tx.send(0);break;}if line.is_empty(){let _=tx.send(3);break;}let event=match line.as_slice(){b"KEY_REQUIRED\n"|b"KEY_REQUIRED\r\n"=>1,b"DONE\n"|b"DONE\r\n"=>2,_=>0};if tx.send(event).is_err()||event==0{break;}}});
 let started=std::time::Instant::now();let mut sent=false;let mut done=false;let mut eof=false;
 loop{
  fence()?;if started.elapsed()>timeout{return Err(denied());}
  match rx.recv_timeout(Duration::from_millis(50)){
   Ok(1) if !sent=>{fence()?;let mut secret=key()?;let mut packet=serde_json::to_vec(&serde_json::json!({"key":std::str::from_utf8(&secret).map_err(|_|denied())?})).map_err(|_|denied())?;secret.fill(0);packet.push(b'\n');fence()?;input.write_all(&packet).map_err(|_|denied())?;packet.fill(0);input.flush().map_err(|_|denied())?;sent=true;},
   Ok(2) if sent&&!done=>{done=true;},
   Ok(3) if done&&!eof=>{eof=true;},
   Ok(_)=>return Err(denied()),
   Err(mpsc::RecvTimeoutError::Timeout)=>{},
   Err(mpsc::RecvTimeoutError::Disconnected) if !eof=>return Err(denied()),
   Err(_)=>{std::thread::sleep(Duration::from_millis(10));},
  }
  // DONE alone is not success: validate the whole stream and process exit.
  if done&&eof {if let Some(status)=child.0.try_wait().map_err(|_|denied())?{return if status.success(){Ok(())}else{Err(denied())};}}
 }
}
#[cfg(test)]
mod tests {
 use super::*;
 fn fixture(mode:&str)->(PathBuf,PinnedWorker){
  let root=std::env::var_os("TMPDIR").map(PathBuf::from).unwrap_or_else(std::env::temp_dir).join(format!("maintenance-pipe-{}",crate::workspace_grant::random_hex().unwrap()));std::fs::create_dir_all(&root).unwrap();
  let executable=PathBuf::from(std::env::var_os("MAINTENANCE_TEST_PYTHON").expect("explicit fixture Python required"));
  let script=root.join("worker.py");
  let code=if mode=="no-read" {"import time; time.sleep(30)".to_string()} else {format!("import sys,json,time,os\nf=json.loads(sys.stdin.readline())\nassert 'synthetic-session' not in str(sys.argv)\nassert 'MAINTENANCE_TEST_PYTHON' not in os.environ\nprint('KEY_REQUIRED',flush=True,file=sys.stderr)\nk=json.loads(sys.stdin.readline())\nassert k['key']=='synthetic-pipe-key'\n{}\nprint('DONE',flush=True,file=sys.stderr)\n",if mode=="hang"{"time.sleep(30)"}else{"pass"})};
  std::fs::write(&script,code).unwrap();let hash=|p:&Path|format!("{:x}",Sha256::digest(std::fs::read(p).unwrap()));
  let worker=PinnedWorker{executable_sha256:hash(&executable),executable,script:Some((script.clone(),hash(&script)))};(root,worker)
 }
 fn rewrite_fixture(mode:&str,transform:impl FnOnce(String)->String)->PinnedWorker {
  let(_,mut worker)=fixture(mode);let script=worker.script.as_ref().unwrap().0.clone();
  let code=transform(std::fs::read_to_string(&script).unwrap());std::fs::write(&script,code).unwrap();
  worker.script=Some((script.clone(),format!("{:x}",Sha256::digest(std::fs::read(script).unwrap()))));worker
 }
 #[test] fn diagnostic_stdout_is_not_a_status_channel(){
  let worker=rewrite_fixture("ok",|code|format!("print('UNTRUSTED STDOUT',flush=True)\n{}\nprint('DONE',file=sys.stdout,flush=True)\n",code));
  pipe_run(&worker,&serde_json::json!({}),||Ok(()),||Ok(b"synthetic-pipe-key".to_vec())).unwrap();
 }
 #[test] fn trailing_noise_and_multiple_done_are_not_success(){
  for extra in ["print('UNTRUSTED OUTPUT',flush=True,file=sys.stderr)","print('DONE',flush=True,file=sys.stderr)","print('KEY_REQUIRED',flush=True,file=sys.stderr)","sys.stderr.write('partial');sys.stderr.flush()"] {
   let worker=rewrite_fixture("ok",|code|format!("{}\n{}\n",code,extra));
   assert!(pipe_run(&worker,&serde_json::json!({}),||Ok(()),||Ok(b"synthetic-pipe-key".to_vec())).is_err(),"trailing output accepted: {}",extra);
  }
 }
 #[test] fn malformed_or_early_eof_never_opens_vault(){
  for response in ["NOISE","DONE","KEY_REQUIRED_EXTRA","KEY_REQUIRED\\nKEY_REQUIRED"] {
   let worker=rewrite_fixture("ok",|_|format!("import sys\nsys.stdin.readline()\nprint({:?},flush=True,file=sys.stderr)\n",response));
   let mut accessed=false;
   assert!(pipe_run(&worker,&serde_json::json!({}),||Ok(()),||{accessed=true;Err(denied())}).is_err());
   if response!="KEY_REQUIRED\\nKEY_REQUIRED" {assert!(!accessed);}
  }
  let worker=rewrite_fixture("ok",|_|"import sys\nsys.stdin.readline()\n".into());
  assert!(pipe_run(&worker,&serde_json::json!({}),||Ok(()),||panic!("EOF accessed vault")).is_err());
 }
 #[test] fn anonymous_pipe_transfers_key_without_arguments_or_environment(){
  let(_,worker)=fixture("ok");let mut accesses=0;
  pipe_run(&worker,&serde_json::json!({"access_token":"synthetic-session"}),||Ok(()),||{accesses+=1;Ok(b"synthetic-pipe-key".to_vec())}).unwrap();assert_eq!(accesses,1);
 }
 #[test] fn status_order_eof_exit_and_old_channel_are_fail_closed(){
  for fault in ["duplicate-key","missing-done","exit-error","oversized","old-channel"] {
   let worker=rewrite_fixture("ok",|code|match fault {
    "duplicate-key"=>code.replace("k=json.loads", "print('KEY_REQUIRED',file=sys.stderr,flush=True)\nk=json.loads"),
    "missing-done"=>code.replace("print('DONE',flush=True,file=sys.stderr)","pass"),
    "exit-error"=>format!("{}\nsys.exit(1)\n",code),
    "oversized"=>format!("{}\nprint('X'*128,file=sys.stderr,flush=True)\n",code),
    _=>code.replace("file=sys.stderr","file=sys.stdout"),
   });
   let mut accesses=0;
   assert!(pipe_run_bounded(&worker,&serde_json::json!({}),||Ok(()),||{accesses+=1;Ok(b"synthetic-pipe-key".to_vec())},Duration::from_secs(2)).is_err(),"{}",fault);
   assert!(accesses<=1);if fault=="old-channel"{assert_eq!(accesses,0);}
  }
 }
 #[test] fn timeout_does_not_accept_done_without_eof(){
  let worker=rewrite_fixture("ok",|code|format!("{}\ntime.sleep(30)\n",code));let start=std::time::Instant::now();
  assert!(pipe_run_bounded(&worker,&serde_json::json!({}),||Ok(()),||Ok(b"synthetic-pipe-key".to_vec()),Duration::from_millis(500)).is_err());
  assert!(start.elapsed()<Duration::from_secs(5));
 }
 #[test] fn changed_epoch_kills_and_reaps_owned_worker(){
  let(_,worker)=fixture("hang");let calls=std::cell::Cell::new(0);let start=std::time::Instant::now();
  assert!(pipe_run(&worker,&serde_json::json!({}),||{calls.set(calls.get()+1);if calls.get()>8{Err(denied())}else{Ok(())}},||Ok(b"synthetic-pipe-key".to_vec())).is_err());assert!(calls.get()>8);assert!(start.elapsed()<Duration::from_secs(5));
 }
 #[test] fn blocked_stdin_is_cancelled_without_vault_access(){
  let(_,worker)=fixture("no-read");let count=std::cell::Cell::new(0);let start=std::time::Instant::now();
  assert!(pipe_run(&worker,&serde_json::json!({"synthetic_padding":"x".repeat(65536)}),||{count.set(count.get()+1);if count.get()>5{Err(denied())}else{Ok(())}},||panic!("key must remain protected")).is_err());
  assert!(count.get()>5);assert!(start.elapsed()<Duration::from_secs(5));
 }
 #[cfg(windows)]
 #[test] fn cancellation_reaps_worker_descendants(){
  let(root,worker)=fixture("hang");let script=&worker.script.as_ref().unwrap().0;
  let code=std::fs::read_to_string(script).unwrap().replace("f=json.loads", "import subprocess\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\nopen(__file__+'.pid','w').write(str(p.pid))\nf=json.loads");std::fs::write(script,code).unwrap();
  let worker=PinnedWorker{executable:worker.executable,executable_sha256:worker.executable_sha256,script:Some((script.clone(),format!("{:x}",Sha256::digest(std::fs::read(script).unwrap()))))};
  let marker=root.join("worker.py.pid");let start=std::time::Instant::now();
  assert!(pipe_run(&worker,&serde_json::json!({}),||if marker.exists()&&start.elapsed()>Duration::from_millis(300){Err(denied())}else{Ok(())},||Ok(b"synthetic-pipe-key".to_vec())).is_err());
  let pid:u32=std::fs::read_to_string(marker).unwrap().parse().unwrap();
  use windows_sys::Win32::System::Threading::*;
  let alive=unsafe{let p=OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|PROCESS_TERMINATE,0,pid);if p.is_null(){false}else{let mut code=0;GetExitCodeProcess(p,&mut code);if code==259{TerminateProcess(p,1);}windows_sys::Win32::Foundation::CloseHandle(p);code==259}};
  assert!(!alive,"worker descendant survived cancellation (fixture cleaned it)");
 }
 #[test]
 #[ignore = "subprocess fixture entry; exercised by dead_owner_releases_only_after_worker_exit"]
 fn owner_process_fixture(){
  let root=PathBuf::from(std::env::var_os("MAINTENANCE_OWNER_FIXTURE_ROOT").unwrap());
  let mut lock=crate::maintenance_lock::ApplicationLease::acquire(&root.join("app.lock")).unwrap();let token=lock.token().to_owned();
  let(_,mut worker)=fixture("hang");let script=worker.script.as_ref().unwrap().0.clone();
  let prelude=format!("import msvcrt\nh=open({},'a+b');h.write(b'0');h.flush();h.seek(0);msvcrt.locking(h.fileno(),msvcrt.LK_NBLCK,1)\nopen(__file__+'.pid','w').write(str(os.getpid()))\n",serde_json::to_string(&root.join("app.lock").to_string_lossy()).unwrap());
  let code=std::fs::read_to_string(&script).unwrap().replace("f=json.loads",&(prelude+"f=json.loads"));std::fs::write(&script,code).unwrap();
  worker.script=Some((script.clone(),format!("{:x}",Sha256::digest(std::fs::read(&script).unwrap()))));
  std::fs::write(root.join("marker-path"),format!("{}.pid",script.display())).unwrap();
  lock.handoff(&token,||Ok(()),||pipe_run(&worker,&serde_json::json!({}),||Ok(()),||Ok(b"synthetic-pipe-key".to_vec()))).unwrap();
 }
 #[test] fn dead_owner_releases_only_after_worker_exit(){
  let root=std::env::temp_dir().join(format!("dead-maintenance-owner-{}",crate::workspace_grant::random_hex().unwrap()));std::fs::create_dir_all(&root).unwrap();
  let mut child=Command::new(std::env::current_exe().unwrap()).args(["--ignored","--exact","maintenance_bridge::tests::owner_process_fixture"]).env("MAINTENANCE_OWNER_FIXTURE_ROOT",&root).stdout(Stdio::null()).spawn().unwrap();
  let start=std::time::Instant::now();let mut pid=None;
  while start.elapsed()<Duration::from_secs(15){if let Ok(path)=std::fs::read_to_string(root.join("marker-path")){if let Ok(value)=std::fs::read_to_string(path){pid=value.parse::<u32>().ok();if pid.is_some(){break;}}}std::thread::sleep(Duration::from_millis(30));}
  if pid.is_none(){let _=child.kill();let _=child.wait();panic!("fixture worker did not start");}
  assert!(crate::maintenance_lock::ApplicationLease::acquire(&root.join("app.lock")).is_err());child.kill().unwrap();child.wait().unwrap();
  let start=std::time::Instant::now();loop{if let Ok(_next)=crate::maintenance_lock::ApplicationLease::acquire(&root.join("app.lock")){break;}assert!(start.elapsed()<Duration::from_secs(5));std::thread::sleep(Duration::from_millis(30));}
 }
 #[test] fn key_delivery_failure_kills_worker_without_success(){let(_,worker)=fixture("hang");assert!(pipe_run(&worker,&serde_json::json!({}),||Ok(()),||Err("SYNTHETIC_VAULT_DENIED".into())).is_err());}
 #[test] fn changed_worker_hash_never_receives_key(){let(_,mut worker)=fixture("ok");worker.executable_sha256="0".repeat(64);assert!(pipe_run(&worker,&serde_json::json!({}),||Ok(()),||panic!("must not access vault")).is_err());}
}

/// frame/config/worker hashes originate from trusted native configuration, never IPC payloads.
/// The fence must reject a changed in-memory account epoch as well as the durable vault epoch.
pub(crate) fn execute_reviewed_worker(root:&Path,worker:&PinnedWorker,frame:&serde_json::Value,workspace_keys:&BTreeMap<String,String>,approval_keys:&BTreeMap<String,String>,session_fence:impl Fn()->Result<(),String>)->Result<(),String>{
 let string=|key:&str|frame[key].as_str().ok_or_else(denied);
 let operation=string("operation")?;let nonce=string("nonce")?;
 let issuer=format!("{}/auth/v1",frame["config"]["central_url"].as_str().ok_or_else(denied)?);
 let signed:SignedGrant=serde_json::from_value(frame["approval_token"].clone()).map_err(|_|denied())?;
 let binding=frame["plan"]["binding"].as_str().ok_or_else(denied)?;
 let epoch=frame["client_epoch"].as_u64().ok_or_else(denied)?;
 let initial=Store::load(root)?;let subject=initial.active_subject().ok_or_else(denied)?.to_owned();
 if frame["confirmed"]!=true||frame["installation_id"]!=initial.installation_id()?||Path::new(frame["plan"]["installation"].as_str().ok_or_else(denied)?).canonicalize().map_err(|_|denied())?!=root.canonicalize().map_err(|_|denied())?{return Err(denied());}
 let verify=||->Result<(),String>{session_fence()?;let mut store=Store::load(root)?;if store.epoch()!=epoch{return Err(denied());}store.resume(&issuer,&subject,workspace_keys,now())?;store.verify_maintenance(&signed,&issuer,&subject,binding,operation,nonce,epoch,approval_keys,now())?;Ok(())};
 let vault=||->Result<Vec<u8>,String>{verify()?;let mut store=Store::load(root)?;
  // Read first; only migration may create. Recovery never substitutes a new key.
  if let Ok(key)=store.migration_backup_key(&issuer,&subject,binding,workspace_keys,now(),None){return Ok(key);}
  if operation!="migrate"{return Err(denied());}
  let mut random=[0u8;32];getrandom::getrandom(&mut random).map_err(|_|denied())?;let fresh=URL_SAFE.encode(random);random.fill(0);
  store.migration_backup_key(&issuer,&subject,binding,workspace_keys,now(),Some(fresh.as_bytes()))
 };
 pipe_run(worker,frame,verify,vault)
}
