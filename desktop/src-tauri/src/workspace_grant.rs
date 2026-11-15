//! Server-signed authority with installation-bound Windows protected persistence.
use base64::{engine::general_purpose::{URL_SAFE_NO_PAD,STANDARD},Engine};
use ring::signature::{Ed25519KeyPair,KeyPair,UnparsedPublicKey,ED25519};
use serde::{Serialize,Deserialize};
use sha2::{Sha256,Digest};
use std::{collections::BTreeMap,path::{Path,PathBuf},io::Write};

#[derive(Clone,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SignedGrant {pub kid:String,pub payload:String,pub sig:String}
#[derive(Clone,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Claims {
 pub v:u32,pub kid:String,pub iss:String,pub sub:String,pub aud:String,pub purpose:String,
 pub installation_id:String,pub client_epoch:u64,pub nonce:String,pub jti:String,
 pub iat:u64,pub nbf:u64,pub exp:u64,pub business_exp:u64,pub revocation_generation:u64,pub sequence:u64,
}
#[derive(Clone,Default,Serialize,Deserialize)]
struct Floor {revocation:u64,sequence:u64,iat:u64}
#[derive(Clone,Serialize,Deserialize)]
struct Cached {subject:String,grant:SignedGrant}
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct ProtectedState {v:u32,seed:Vec<u8>,epoch:u64,last_seen:u64,floors:BTreeMap<String,Floor>,active:Option<Cached>,#[serde(default)] migration_keys:BTreeMap<String,String>}
pub(crate) struct Store {path:PathBuf,entropy:Vec<u8>,state:ProtectedState}
fn error()->String {"WORKSPACE_AUTHORITY_INVALID".into()}
pub(crate) fn random_hex()->Result<String,String>{let mut b=[0u8;32];getrandom::getrandom(&mut b).map_err(|_|error())?;Ok(hex(&b))}
fn hex(b:&[u8])->String{b.iter().map(|v|format!("{v:02x}")).collect()}
fn decode(s:&str)->Result<Vec<u8>,String>{URL_SAFE_NO_PAD.decode(s).or_else(|_|STANDARD.decode(s)).map_err(|_|error())}
fn safe(path:&Path)->Result<(),String>{
 for p in path.ancestors(){match std::fs::symlink_metadata(p){
  Ok(m)=>{if m.file_type().is_symlink(){return Err(error());}#[cfg(windows)]{use std::os::windows::fs::MetadataExt;if m.file_attributes()&0x400!=0{return Err(error());}}},
  Err(e) if e.kind()==std::io::ErrorKind::NotFound=>{},Err(_)=>return Err(error()),
 }}Ok(())
}
#[cfg(windows)]
fn protect(data:&[u8],entropy:&[u8],encrypt:bool)->Result<Vec<u8>,String>{
 use windows_sys::Win32::{Security::Cryptography::{CryptProtectData,CryptUnprotectData,CRYPT_INTEGER_BLOB,CRYPTPROTECT_UI_FORBIDDEN},Foundation::LocalFree};
 if data.len()>1_048_576{return Err(error());}
 let input=CRYPT_INTEGER_BLOB{cbData:data.len() as u32,pbData:data.as_ptr() as *mut u8};
 let extra=CRYPT_INTEGER_BLOB{cbData:entropy.len() as u32,pbData:entropy.as_ptr() as *mut u8};
 let mut output=CRYPT_INTEGER_BLOB{cbData:0,pbData:std::ptr::null_mut()};
 unsafe{
  let ok=if encrypt{CryptProtectData(&input,std::ptr::null(),&extra,std::ptr::null(),std::ptr::null(),CRYPTPROTECT_UI_FORBIDDEN,&mut output)}else{CryptUnprotectData(&input,std::ptr::null_mut(),&extra,std::ptr::null(),std::ptr::null(),CRYPTPROTECT_UI_FORBIDDEN,&mut output)};
  if ok==0{return Err(error());}
  let bytes=std::slice::from_raw_parts(output.pbData,output.cbData as usize).to_vec();
  LocalFree(output.pbData as *mut std::ffi::c_void);Ok(bytes)
 }
}
#[cfg(not(windows))]
fn protect(_: &[u8],_: &[u8],_:bool)->Result<Vec<u8>,String>{Err("OS_PROTECTED_WORKSPACE_STORE_NOT_IMPLEMENTED".into())}
impl Store {
 pub fn active_subject(&self)->Option<&str>{self.state.active.as_ref().map(|a|a.subject.as_str())}
 pub fn load(root:&Path)->Result<Self,String>{
  safe(root)?;let canonical=root.canonicalize().map_err(|_|error())?;
  let entropy=format!("moadoom-workspace-v1:{}",canonical.to_string_lossy()).into_bytes();
  let path=root.join(".workspace-authority").join("state.dpapi");safe(&path)?;
  let state=if path.exists(){
   let bytes=std::fs::read(&path).map_err(|_|error())?;
   serde_json::from_slice::<ProtectedState>(&protect(&bytes,&entropy,false)?).map_err(|_|error())?
  }else{
   let mut seed=vec![0u8;32];getrandom::getrandom(&mut seed).map_err(|_|error())?;
   ProtectedState{v:1,seed,epoch:0,last_seen:0,floors:BTreeMap::new(),active:None,migration_keys:BTreeMap::new()}
  };
  if state.v!=1||state.seed.len()!=32||state.epoch>9_007_199_254_740_991{return Err(error());}
  let s=Self{path,entropy,state};if !s.path.exists(){s.save()?;}Ok(s)
 }
 fn save(&self)->Result<(),String>{
  safe(&self.path)?;let parent=self.path.parent().ok_or_else(error)?;
  std::fs::create_dir_all(parent).map_err(|_|error())?;
  let data=protect(&serde_json::to_vec(&self.state).map_err(|_|error())?,&self.entropy,true)?;
  let temp=parent.join(format!("{}.tmp",random_hex()?));
  let mut f=std::fs::OpenOptions::new().write(true).create_new(true).open(&temp).map_err(|_|error())?;
  f.write_all(&data).and_then(|_|f.sync_all()).map_err(|_|error())?;drop(f);
  #[cfg(windows)] {
   use std::os::windows::ffi::OsStrExt;
   use windows_sys::Win32::Storage::FileSystem::{MoveFileExW,MOVEFILE_REPLACE_EXISTING,MOVEFILE_WRITE_THROUGH};
   let from:Vec<u16>=temp.as_os_str().encode_wide().chain(Some(0)).collect();
   let to:Vec<u16>=self.path.as_os_str().encode_wide().chain(Some(0)).collect();
   if unsafe{MoveFileExW(from.as_ptr(),to.as_ptr(),MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)}==0{return Err(error());}
  }
  #[cfg(not(windows))] std::fs::rename(&temp,&self.path).map_err(|_|error())?;
  Ok(())
 }
 fn pair(&self)->Result<Ed25519KeyPair,String>{Ed25519KeyPair::from_seed_unchecked(&self.state.seed).map_err(|_|error())}
 pub fn installation_id(&self)->Result<String,String>{Ok(hex(&Sha256::digest(self.pair()?.public_key().as_ref())))}
 pub fn epoch(&self)->u64 {self.state.epoch}
 pub fn maintenance_request(&self,issuer:&str,subject:&str,binding:&str,operation:&str,confirmed:bool,now:u64)->Result<(serde_json::Value,String),String>{
  if !confirmed||!regex::Regex::new(r"^[0-9a-f]{64}$").unwrap().is_match(binding)||!["migrate","rollback","restore_backup"].contains(&operation){return Err(error());}
  let nonce=random_hex()?;let pair=self.pair()?;
  let mut message=serde_json::json!({"v":2,"aud":"moadoom-maintenance-request","iss":issuer,"sub":subject,"installation_id":self.installation_id()?,"client_epoch":self.state.epoch,"nonce":nonce,"iat":now,"binding":binding,"operation":operation,"confirmed":true});
  let sorted:BTreeMap<_,_>=message.as_object().ok_or_else(error)?.iter().collect();
  let raw=serde_json::to_vec(&sorted).map_err(|_|error())?;
  message["installation_key"]=hex(pair.public_key().as_ref()).into();message["proof"]=URL_SAFE_NO_PAD.encode(pair.sign(&raw).as_ref()).into();
  Ok((message,nonce))
 }
 pub fn verify_maintenance(&self,signed:&SignedGrant,issuer:&str,subject:&str,binding:&str,operation:&str,nonce:&str,epoch:u64,keys:&BTreeMap<String,String>,now:u64)->Result<u64,String>{
  if epoch!=self.state.epoch||signed.payload.len()>8192||signed.sig.len()>128{return Err(error());}
  let raw=decode(&signed.payload)?;let key=decode(keys.get(&signed.kid).ok_or_else(error)?)?;
  UnparsedPublicKey::new(&ED25519,key).verify(&raw,&decode(&signed.sig)?).map_err(|_|error())?;
  let value:serde_json::Value=serde_json::from_slice(&raw).map_err(|_|error())?;
  let expected=serde_json::json!({"v":2,"kid":signed.kid,"iss":issuer,"sub":subject,"aud":"moadoom-maintenance","purpose":"legacy-owner-approval","binding":binding,"operation":operation,"installation_id":self.installation_id()?,"client_epoch":epoch,"nonce":nonce});
  let object=value.as_object().ok_or_else(error)?;let expect=expected.as_object().ok_or_else(error)?;
  if object.len()!=expect.len()+3||expect.iter().any(|(k,v)|object.get(k)!=Some(v)){return Err(error());}
  let iat=value["iat"].as_u64().ok_or_else(error)?;let exp=value["exp"].as_u64().ok_or_else(error)?;
  if iat>now||exp<=now||exp.saturating_sub(iat)>300||!regex::Regex::new(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$").unwrap().is_match(value["approval_id"].as_str().unwrap_or("")){return Err(error());}Ok(exp)
 }
 pub fn request(&self,issuer:&str,subject:&str,now:u64)->Result<(serde_json::Value,String),String>{
  let nonce=random_hex()?;let pair=self.pair()?;
  let message=serde_json::json!({"v":1,"aud":"moadoom-workspace-issue","iss":issuer,"sub":subject,"installation_id":self.installation_id()?,"client_epoch":self.state.epoch,"nonce":nonce,"iat":now});
  let sorted:BTreeMap<_,_>=message.as_object().ok_or_else(error)?.iter().collect();
  let raw=serde_json::to_vec(&sorted).map_err(|_|error())?;
  Ok((serde_json::json!({"installation_key":hex(pair.public_key().as_ref()),"client_epoch":self.state.epoch,"nonce":nonce,"requested_at":now,"proof":URL_SAFE_NO_PAD.encode(pair.sign(&raw).as_ref())}),nonce))
 }
 fn verify(&self,signed:&SignedGrant,issuer:&str,subject:&str,nonce:Option<&str>,keys:&BTreeMap<String,String>,now:u64)->Result<Claims,String>{
  if signed.payload.len()>16384||signed.sig.len()>128||(nonce.is_none() && now.saturating_add(120)<self.state.last_seen){return Err(error());}
  let key=decode(keys.get(&signed.kid).ok_or_else(error)?)?;
  let payload=decode(&signed.payload)?;let sig=decode(&signed.sig)?;
  UnparsedPublicKey::new(&ED25519,key).verify(&payload,&sig).map_err(|_|error())?;
  let c:Claims=serde_json::from_slice(&payload).map_err(|_|error())?;
  // Fresh nonce-bound ONLINE proof can recover from a corrected local clock.
  if nonce.is_some() && now.abs_diff(c.iat)>300{return Err(error());}
  let uuid=regex::Regex::new(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$").map_err(|_|error())?;
  if c.v!=1||c.kid!=signed.kid||c.iss!=issuer||c.sub!=subject||!uuid.is_match(&c.sub)||!uuid.is_match(&c.jti)||c.aud!="moadoom-desktop"||c.purpose!="account-workspace"||c.installation_id!=self.installation_id()?||c.client_epoch!=self.state.epoch||nonce.map(|n|n!=c.nonce).unwrap_or(false)||!regex::Regex::new(r"^[0-9a-f]{64}$").unwrap().is_match(&c.nonce)||c.nbf!=c.iat||c.iat>now.saturating_add(300)||c.exp<=c.iat||c.exp-c.iat>432000||c.business_exp<=c.iat||c.business_exp>c.exp||now>=c.exp||now>=c.business_exp||c.sequence==0||c.sequence>9_007_199_254_740_991||c.revocation_generation>9_007_199_254_740_991{return Err(error());}
  let floor=self.state.floors.get(&format!("{issuer}|{subject}")).cloned().unwrap_or_default();
  if c.revocation_generation<floor.revocation||c.sequence<floor.sequence||c.iat<floor.iat{return Err(error());}Ok(c)
 }
 pub fn accept(&mut self,grant:SignedGrant,issuer:&str,subject:&str,nonce:&str,keys:&BTreeMap<String,String>,now:u64)->Result<Claims,String>{
  let c=self.verify(&grant,issuer,subject,Some(nonce),keys,now)?;
  self.state.floors.insert(format!("{issuer}|{subject}"),Floor{revocation:c.revocation_generation,sequence:c.sequence,iat:c.iat});
  self.state.last_seen=now;self.state.active=Some(Cached{subject:subject.into(),grant});self.save()?;Ok(c)
 }
 pub fn resume(&mut self,issuer:&str,subject:&str,keys:&BTreeMap<String,String>,now:u64)->Result<Claims,String>{
  let active=self.state.active.clone().ok_or_else(error)?;if active.subject!=subject{return Err(error());}
  let c=self.verify(&active.grant,issuer,subject,None,keys,now)?;
  self.state.last_seen=self.state.last_seen.max(now);self.save()?;Ok(c)
 }
 pub fn logout(&mut self,now:u64)->Result<(),String>{
  self.state.active=None;self.state.epoch=self.state.epoch.checked_add(1).ok_or_else(error)?;
  self.state.last_seen=self.state.last_seen.max(now);self.save()
 }
 /// Internal maintenance key vault only, NOT migration approval or a WebView command.
 /// An independently reviewed human approval/online maintenance adapter is still required.
 pub fn migration_backup_key(&mut self,issuer:&str,subject:&str,binding:&str,keys:&BTreeMap<String,String>,now:u64,new_key:Option<&[u8]>)->Result<Vec<u8>,String>{
  if !regex::Regex::new(r"^[0-9a-f]{64}$").unwrap().is_match(binding){return Err(error());}
  self.resume(issuer,subject,keys,now)?;
  let tag=format!("{issuer}|{subject}|{binding}");
  if let Some(saved)=self.state.migration_keys.get(&tag){
   let key=decode(saved)?;if new_key.map(|n|n!=key).unwrap_or(false){return Err("기존 이전 백업 키를 덮어쓰지 않습니다.".into());}return Ok(key);
  }
  let key=new_key.ok_or("이전 백업 키가 없습니다. 새 키로 복구하지 않습니다.")?;
  if key.len()!=44 || base64::engine::general_purpose::URL_SAFE.decode(key).map_err(|_|error())?.len()!=32{return Err(error());}
  self.state.migration_keys.insert(tag,URL_SAFE_NO_PAD.encode(key));self.save()?;Ok(key.to_vec())
 }
}

#[cfg(test)]
mod tests {
    use super::*;
    const ISSUER:&str="https://fixture.supabase.co/auth/v1";
    const A:&str="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    const B:&str="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    fn root()->std::path::PathBuf {let p=std::env::var_os("TMPDIR").map(std::path::PathBuf::from).unwrap_or_else(std::env::temp_dir).join(format!("workspace-grant-{}",random_hex().unwrap()));std::fs::create_dir_all(&p).unwrap();p}
    fn grant(store:&Store,subject:&str,seq:u64)->(SignedGrant,std::collections::BTreeMap<String,String>){
        let signer=ring::signature::Ed25519KeyPair::from_seed_unchecked(&[7u8;32]).unwrap();
        use ring::signature::KeyPair;
        let claims=serde_json::json!({"v":1,"kid":"synthetic","iss":ISSUER,"sub":subject,"aud":"moadoom-desktop","purpose":"account-workspace","installation_id":store.installation_id().unwrap(),"client_epoch":store.state.epoch,"nonce":"a".repeat(64),"jti":"cccccccc-cccc-4ccc-8ccc-cccccccccccc","iat":1000,"nbf":1000,"exp":433000,"business_exp":5000,"revocation_generation":0,"sequence":seq});
        let raw=serde_json::to_vec(&claims).unwrap();
        (SignedGrant{kid:"synthetic".into(),payload:URL_SAFE_NO_PAD.encode(&raw),sig:URL_SAFE_NO_PAD.encode(signer.sign(&raw).as_ref())},std::collections::BTreeMap::from([("synthetic".into(),URL_SAFE_NO_PAD.encode(signer.public_key().as_ref()))]))
    }
    #[test]
    fn maintenance_proof_requires_explicit_confirmation_and_binds_plan_epoch(){
        let p=root();let s=Store::load(&p).unwrap();
        assert!(s.maintenance_request(ISSUER,A,&"b".repeat(64),"migrate",false,1000).is_err());
        let (request,nonce)=s.maintenance_request(ISSUER,A,&"b".repeat(64),"migrate",true,1000).unwrap();
        assert_eq!(request["installation_id"],s.installation_id().unwrap());
        assert_eq!(request["client_epoch"],0);assert_eq!(request["nonce"],nonce);
        assert_eq!(request["binding"],"b".repeat(64));
    }
    #[test]
    fn signed_online_offline_switch_replay_clock_and_logout(){
        let p=root();let mut s=Store::load(&p).unwrap();let(g,keys)=grant(&s,A,2);
        assert!(s.accept(g.clone(),ISSUER,B,&"a".repeat(64),&keys,1000).is_err());
        assert!(s.accept(g.clone(),ISSUER,A,&"b".repeat(64),&keys,1000).is_err());
        let claims=s.accept(g.clone(),ISSUER,A,&"a".repeat(64),&keys,1000).unwrap();
        assert_eq!(claims.business_exp,5000);
        let mut restarted=Store::load(&p).unwrap();
        assert!(restarted.resume(ISSUER,A,&keys,1200).is_ok());
        assert!(restarted.resume(ISSUER,B,&keys,1200).is_err());
        assert!(restarted.resume(ISSUER,A,&keys,1000).is_err());
        assert!(restarted.resume(ISSUER,A,&keys,5000).is_err());
        let(old,_)=grant(&s,A,1);assert!(s.accept(old,ISSUER,A,&"a".repeat(64),&keys,1200).is_err());
        restarted.logout(1200).unwrap();
        assert!(Store::load(&p).unwrap().resume(ISSUER,A,&keys,1200).is_err());
        assert!(restarted.accept(g,ISSUER,A,&"a".repeat(64),&keys,1200).is_err());
    }
    #[test]
    fn migration_key_is_protected_retained_and_bound_to_owner_and_plan(){
        let p=root();let mut store=Store::load(&p).unwrap();let(g,keys)=grant(&store,A,1);
        store.accept(g,ISSUER,A,&"a".repeat(64),&keys,1000).unwrap();
        let key=base64::engine::general_purpose::URL_SAFE.encode([9u8;32]).into_bytes();
        let binding="c".repeat(64);
        assert_eq!(store.migration_backup_key(ISSUER,A,&binding,&keys,1000,Some(&key)).unwrap(),key);
        assert!(!std::fs::read(&store.path).unwrap().windows(key.len()).any(|w|w==key));
        store.logout(1100).unwrap();
        assert!(store.migration_backup_key(ISSUER,A,&binding,&keys,1100,None).is_err());
        let(g,_) = grant(&store,B,1);store.accept(g,ISSUER,B,&"a".repeat(64),&keys,1100).unwrap();
        assert!(store.migration_backup_key(ISSUER,B,&binding,&keys,1100,None).is_err());
        store.logout(1100).unwrap();
        let(g,_) = grant(&store,A,2);store.accept(g,ISSUER,A,&"a".repeat(64),&keys,1100).unwrap();
        let mut restored=Store::load(&p).unwrap();
        assert_eq!(restored.migration_backup_key(ISSUER,A,&binding,&keys,1100,None).unwrap(),key);
        assert!(restored.migration_backup_key(ISSUER,A,&"d".repeat(64),&keys,1100,None).is_err());
        let different=base64::engine::general_purpose::URL_SAFE.encode([8u8;32]).into_bytes();
        assert!(restored.migration_backup_key(ISSUER,A,&binding,&keys,1100,Some(&different)).is_err());
    }
    #[test]
    fn failed_durable_logout_is_reported_not_silently_successful(){
        let p=root();let mut store=Store::load(&p).unwrap();let(g,keys)=grant(&store,A,1);
        store.accept(g,ISSUER,A,&"a".repeat(64),&keys,1000).unwrap();
        let original=std::fs::metadata(&store.path).unwrap().permissions();let mut readonly=original.clone();readonly.set_readonly(true);
        std::fs::set_permissions(&store.path,readonly).unwrap();
        let result=store.logout(1100);
        std::fs::set_permissions(&store.path,original).unwrap();
        assert!(result.is_err(),"must not acknowledge a logout that could not be persisted");
        store.logout(1100).unwrap();assert!(Store::load(&p).unwrap().resume(ISSUER,A,&keys,1100).is_err());
    }
    #[test]
    fn native_protected_key_to_real_python_migration_and_epoch_denial(){
        run_native_maintenance(false);
    }
    #[test]
    fn packaged_worker_with_real_lock_handoff_and_dpapi(){
        run_native_maintenance(true);
    }
    #[test] fn packaged_empty_bootstrap_transition(){run_native_maintenance_mode(true,"empty-bootstrap");}
    #[test] fn packaged_same_owner_format_transition(){run_native_maintenance_mode(true,"same-owner-format");}
    fn run_native_maintenance(packaged:bool){run_native_maintenance_mode(packaged,"legacy");}
    fn run_native_maintenance_mode(packaged:bool,mode:&str){
        use crate::maintenance_bridge::{PinnedWorker,execute_reviewed_worker};
        use std::process::{Command,Stdio};
        let p=root();let mut store=Store::load(&p).unwrap();
        let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs();
        let(pair_grant,keys)=grant(&store,A,1);let signer=Ed25519KeyPair::from_seed_unchecked(&[7u8;32]).unwrap();
        let sign=|value:&serde_json::Value|{let raw=serde_json::to_vec(value).unwrap();SignedGrant{kid:"synthetic".into(),payload:URL_SAFE_NO_PAD.encode(&raw),sig:URL_SAFE_NO_PAD.encode(signer.sign(&raw).as_ref())}};
        let mut claims:serde_json::Value=serde_json::from_slice(&decode(&pair_grant.payload).unwrap()).unwrap();
        claims["iat"]=now.into();claims["nbf"]=now.into();claims["exp"]=(now+432000).into();claims["business_exp"]=(now+432000).into();
        store.accept(sign(&claims),ISSUER,A,&"a".repeat(64),&keys,now).unwrap();
        let python=PathBuf::from(std::env::var_os("MAINTENANCE_TEST_PYTHON").unwrap());
        let fixture=Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/native_maintenance_fixture.py").canonicalize().unwrap();
        let mut child=Command::new(&python).arg(&fixture).arg("plan").stdin(Stdio::piped()).stdout(Stdio::piped()).spawn().unwrap();
        child.stdin.take().unwrap().write_all(&serde_json::to_vec(&serde_json::json!({"root":p,"mode":mode})).unwrap()).unwrap();
        let output=child.wait_with_output().unwrap();assert!(output.status.success());let plan:serde_json::Value=serde_json::from_slice(&output.stdout).unwrap();
        let binding=plan["binding"].as_str().unwrap();let nonce="a".repeat(64);
        let approval=sign(&serde_json::json!({"v":2,"kid":"synthetic","iss":ISSUER,"sub":A,"aud":"moadoom-maintenance","purpose":"legacy-owner-approval","binding":binding,"operation":"migrate","installation_id":store.installation_id().unwrap(),"client_epoch":store.epoch(),"nonce":nonce,"approval_id":"cccccccc-cccc-4ccc-8ccc-cccccccccccc","iat":now,"exp":now+300}));
        assert!(store.verify_maintenance(&approval,ISSUER,A,binding,"migrate",&nonce,store.epoch()+1,&keys,now).is_err());
        assert!(store.verify_maintenance(&approval,ISSUER,B,binding,"migrate",&nonce,store.epoch(),&keys,now).is_err());
        assert!(store.verify_maintenance(&approval,ISSUER,A,binding,"rollback",&nonce,store.epoch(),&keys,now).is_err());
        let frame=serde_json::json!({"plan":plan,"operation":"migrate","access_token":"synthetic-session","approval_token":approval,"config":{"central_url":"https://fixture.supabase.co","publishable_key":"sb_publishable_fixture","approval_keys":keys},"installation_id":store.installation_id().unwrap(),"client_epoch":store.epoch(),"nonce":nonce,"confirmed":true});
        let hash=|path:&Path|format!("{:x}",Sha256::digest(std::fs::read(path).unwrap()));
        let worker=if packaged {let executable=PathBuf::from(std::env::var_os("MAINTENANCE_TEST_PACKAGED_WORKER").expect("explicit separately packaged synthetic Auth fixture"));PinnedWorker{executable_sha256:hash(&executable),executable,script:None}} else {PinnedWorker{executable_sha256:hash(&python),executable:python.clone(),script:Some((fixture.clone(),hash(&fixture)))}};
        let mut lock=crate::maintenance_lock::ApplicationLease::acquire(&p.join("app.lock")).unwrap();let token=lock.token().to_owned();
        assert!(execute_reviewed_worker(&p,&worker,&frame,&keys,&keys,||Ok(())).is_err()); // worker may never bypass the live application's lock
        let mut business=Command::new(python).args(["-c","import time;time.sleep(60)"]).spawn().unwrap();
        let job=crate::SidecarJob::new().unwrap();job.assign(business.id()).unwrap();
        lock.handoff(&token,||{job.stop_confirmed()?;assert!(!business.wait().unwrap().success());Ok(())},||{
            assert!(crate::maintenance_lock::ApplicationLease::acquire(&p.join("app.lock")).is_err());
            execute_reviewed_worker(&p,&worker,&frame,&keys,&keys,||Ok(()))
        }).unwrap();
        let identity=serde_json::to_vec(&vec![ISSUER,A]).unwrap();let target=p.join("accounts").join(format!("{:x}",Sha256::digest(identity))).join("pdfs/fixture.bin");
        assert_eq!(std::fs::read(target).unwrap(),b"SYNTHETIC NATIVE PIPE DATA");
        let mut current=Store::load(&p).unwrap();assert!(current.migration_backup_key(ISSUER,A,binding,&keys,now,None).is_ok());current.logout(now).unwrap();
        assert!(execute_reviewed_worker(&p,&worker,&frame,&keys,&keys,||Ok(())).is_err());
    }
    #[test]
    fn native_proof_to_typescript_signer_to_native_verifier(){
        use std::process::{Command,Stdio};
        let p=root();let mut store=Store::load(&p).unwrap();let(body,nonce)=store.request(ISSUER,A,1000).unwrap();
        let fixture=Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/workspace_grant_server_fixture.mjs");
        let mut child=Command::new("node").arg("--experimental-strip-types").arg(fixture).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
        child.stdin.take().unwrap().write_all(&serde_json::to_vec(&serde_json::json!({"request":body,"issuer":ISSUER,"subject":A,"now":1000})).unwrap()).unwrap();
        let output=child.wait_with_output().unwrap();assert!(output.status.success(),"fixture failed: {}",String::from_utf8_lossy(&output.stderr));
        let response:serde_json::Value=serde_json::from_slice(&output.stdout).unwrap();
        let keys=BTreeMap::from([("synthetic-cross-language".to_string(),response["public_key"].as_str().unwrap().to_string())]);
        let signed=serde_json::from_value(response["grant"].clone()).unwrap();
        let claims=store.accept(signed,ISSUER,A,&nonce,&keys,1000).unwrap();
        assert_eq!(claims.sub,A);assert_eq!(claims.exp-claims.iat,432000);assert_eq!(claims.business_exp-claims.iat,432000);
        // Subscription end in the fixture is one day; ownership still lasts five.
        assert!(Store::load(&p).unwrap().resume(ISSUER,A,&keys,1000+86401).is_ok());
        assert!(Store::load(&p).unwrap().resume(ISSUER,A,&keys,1100).is_err());
    }
    #[test]
    fn signature_claim_mutations_revocation_floor_and_clock_recovery(){
        let p=root();let mut store=Store::load(&p).unwrap();let(g,keys)=grant(&store,A,1);
        let mut tampered=g.clone();tampered.sig=URL_SAFE_NO_PAD.encode([0u8;64]);
        assert!(store.accept(tampered,ISSUER,A,&"a".repeat(64),&keys,1000).is_err());
        for (field,value) in [("iss",serde_json::json!("https://foreign.supabase.co/auth/v1")),("aud",serde_json::json!("other-client")),("purpose",serde_json::json!("legacy-migration")),("exp",serde_json::json!(433001)),("business_exp",serde_json::json!(433001)),("iat",serde_json::json!(1400))]{
            let mut c:serde_json::Value=serde_json::from_slice(&decode(&g.payload).unwrap()).unwrap();c[field]=value;
            let pair=Ed25519KeyPair::from_seed_unchecked(&[7u8;32]).unwrap();let raw=serde_json::to_vec(&c).unwrap();
            let bad=SignedGrant{kid:g.kid.clone(),payload:URL_SAFE_NO_PAD.encode(&raw),sig:URL_SAFE_NO_PAD.encode(pair.sign(&raw).as_ref())};
            assert!(store.accept(bad,ISSUER,A,&"a".repeat(64),&keys,1000).is_err(),"accepted {field}");
        }
        store.accept(g.clone(),ISSUER,A,&"a".repeat(64),&keys,1000).unwrap();
        store.state.last_seen=9000;
        assert!(store.resume(ISSUER,A,&keys,1000).is_err());
        let(fresh,_)=grant(&store,A,2);store.accept(fresh,ISSUER,A,&"a".repeat(64),&keys,1000).unwrap();
        store.state.floors.get_mut(&format!("{ISSUER}|{A}")).unwrap().revocation=2;
        let(old_generation,_)=grant(&store,A,3);
        assert!(store.accept(old_generation,ISSUER,A,&"a".repeat(64),&keys,1000).is_err());
    }
    #[test]
    fn copied_grant_and_dpapi_state_do_not_open_another_installation(){
        let p=root();let q=root();let mut a=Store::load(&p).unwrap();let mut b=Store::load(&q).unwrap();
        let(g,keys)=grant(&a,A,1);
        assert!(b.accept(g.clone(),ISSUER,A,&"a".repeat(64),&keys,1000).is_err());
        a.accept(g,ISSUER,A,&"a".repeat(64),&keys,1000).unwrap();
        std::fs::copy(&a.path,&b.path).unwrap();
        assert!(Store::load(&q).is_err());
    }
}
