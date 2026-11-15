use std::{path::Path,sync::{Mutex,OnceLock,atomic::{AtomicBool,Ordering}},collections::BTreeMap};
use tauri::Manager;
use serde_json::{Value,json};
use crate::{account_session,workspace_grant::{Store,SignedGrant},maintenance_bridge::{PinnedWorker,execute_reviewed_worker},RuntimeProfile};
static PAUSED:AtomicBool=AtomicBool::new(false);
pub(crate) fn pause_business(){PAUSED.store(true,Ordering::SeqCst);}
pub(crate) static BUSINESS:Mutex<()>=Mutex::new(());
pub(crate) static AUXILIARY:tokio::sync::RwLock<()>=tokio::sync::RwLock::const_new(());
pub(crate) fn ensure_business()->Result<(),String>{if PAUSED.load(Ordering::SeqCst){Err("유지보수로 업무가 중지됐습니다. 결과 확인 후 앱을 다시 시작하세요.".into())}else{Ok(())}}
fn now()->u64{std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|v|v.as_secs()).unwrap_or(u64::MAX)}
#[derive(Clone)]
struct Pending {ticket:String,epoch:u64,subject:String,issuer:String,plan:Value,operation:String,frame:Option<Value>}
static PENDING:OnceLock<Mutex<Option<Pending>>>=OnceLock::new();
fn pending()->&'static Mutex<Option<Pending>>{PENDING.get_or_init(||Mutex::new(None))}
fn denied()->String{"유지보수 요청이 유효하지 않습니다. 계정과 검토된 계획을 다시 확인하세요.".into()}
fn approval_keys()->Result<BTreeMap<String,String>,String>{
 let keys:BTreeMap<String,String>=serde_json::from_str(option_env!("VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS").unwrap_or("{}")).map_err(|_|denied())?;
 if keys.is_empty(){return Err("승인 서비스 공개키가 준비되지 않아 유지보수를 차단했습니다. 우회 실행하지 않습니다.".into());}Ok(keys)
}
fn worker(app:&tauri::AppHandle)->Result<PinnedWorker,String>{
 if !cfg!(windows){return Err("현재 유지보수는 Windows에서만 지원합니다.".into());}
 let executable=app.path().resource_dir().map_err(|_|denied())?.join("maintenance-worker.exe");
 let w=PinnedWorker{executable,executable_sha256:option_env!("MOADOOM_PACKAGED_WORKER_SHA256").unwrap_or("").into(),script:None};
 crate::maintenance_bridge::validate_worker(&w)?;Ok(w)
}
fn validate_plan(root:&Path,plan:&Value,issuer:&str,subject:&str)->Result<(),String>{
 if serde_json::to_vec(plan).map_err(|_|denied())?.len()>512000{return Err(denied());}
 let canonical=root.canonicalize().map_err(|_|denied())?;
 if Path::new(plan["installation"].as_str().ok_or_else(denied)?).canonicalize().map_err(|_|denied())?!=canonical{return Err(denied());}
 let replacement=plan.get("replacement");
 let upgrade=replacement.map(|r|r["mode"]=="same-owner-format").unwrap_or(false);
 if let Some(r)=replacement {
  if ![Some("empty-bootstrap"),Some("same-owner-format")].contains(&r["mode"].as_str())||r["issuer"]!=issuer||r["subject"]!=subject{return Err(denied());}
  if !r["snapshot"]["files"].is_object()||!r["snapshot"]["dirs"].as_array().map(|a|a.iter().all(Value::is_string)).unwrap_or(false)||!r["snapshot"]["files"].as_object().unwrap().values().all(Value::is_string){return Err(denied());}
 }
 let source=Path::new(plan["source"].as_str().ok_or_else(denied)?);
 if upgrade {
  use sha2::Digest;
  let digest=format!("{:x}",sha2::Sha256::digest(serde_json::to_vec(&vec![issuer,subject]).map_err(|_|denied())?));
  // The leaf may be absent between archive and promotion; canonicalize its existing parent.
  let scoped=source.parent().ok_or_else(denied)?.canonicalize().map_err(|_|denied())?.join(source.file_name().ok_or_else(denied)?);
  if scoped!=canonical.join("accounts").join(digest){return Err(denied());}
 }else if source.canonicalize().map_err(|_|denied())?!=canonical{return Err(denied());}
 if !regex::Regex::new(r"^[0-9a-f]{64}$").unwrap().is_match(plan["binding"].as_str().unwrap_or("")){return Err(denied());}
 let files=plan["files"].as_object().ok_or_else(denied)?;if files.is_empty(){return Err(denied());}
 for(dest,item)in files {
  let source=item["source"].as_str().ok_or_else(denied)?;
  let safe=source.split('/').all(|v|!v.is_empty()&&v!="."&&v!=".."&&!v.contains(['\\',':'])&&!v.ends_with(['.',' ']));
  let allowed=if upgrade {dest==source&&(matches!(source,"data/customers.sqlite3"|"data/rag-index.sqlite3"|"config/customer-db.key")||source.starts_with("pdfs/")||source.starts_with("data/backups/"))}else{match source{"customers.sqlite3"|"rag-index.sqlite3"=>dest==&format!("data/{source}"),"customer-db.key"=>dest=="config/customer-db.key",_=>source.starts_with("pdfs/")&&dest==source}};
  if !safe||!allowed{return Err("검토 계획에 지원하지 않는 자료 경로가 있습니다. 운영 인증정보·계정 vault는 이전할 수 없습니다.".into());}
 }Ok(())
}
#[tauri::command]
pub(crate) fn maintenance_status(app:tauri::AppHandle)->Value{
 let ready=(||{account_session::configuration()?;approval_keys()?;worker(&app)?;Ok::<(),String>(())})();
 json!({"available":ready.is_ok(),"workerPinned":worker(&app).is_ok(),"message":ready.err().unwrap_or_else(||"별도 소유권 검토와 온라인 승인이 필요합니다.".into()),"paused":PAUSED.load(Ordering::SeqCst)})
}
#[tauri::command]
pub(crate) fn maintenance_prepare(app:tauri::AppHandle,base:tauri::State<'_,RuntimeProfile>,plan:Value,operation:String,expected_epoch:u64)->Result<Value,String>{
 let _transition=account_session::TRANSITION.try_lock().map_err(|_|"다른 계정/유지보수 작업이 진행 중입니다.")?;
 account_session::require_epoch(expected_epoch)?;account_session::configuration()?;approval_keys()?;worker(&app)?;
 if !["migrate","rollback","restore_backup"].contains(&operation.as_str()){return Err(denied());}
 let session=account_session::current()?;validate_plan(&base.data_dir,&plan,&session.issuer,&session.subject)?;
 let target=session.profile.data_dir.parent().ok_or_else(denied)?;
 let job=base.data_dir.join(".account-migrations").join(plan["binding"].as_str().ok_or_else(denied)?);
 if operation=="migrate"&&target.exists()&&plan.get("replacement").is_none()&&!job.join("journal.enc").exists(){return Err("기존 업무공간이 있어 일반 이전 계획으로 덮어쓰지 않습니다. 빈 초기 업무공간 보관 또는 동일 소유자 형식 전환을 포함한 정확한 snapshot 계획과 별도 승인이 필요합니다. 실자료 충돌 시 양쪽을 보존하며 자동 병합하지 않습니다.".into());}
 let store=Store::load(&base.data_dir)?;let ticket=crate::workspace_grant::random_hex()?;
 let summary=json!({"ticket":ticket,"subject":session.subject,"issuer":session.issuer,"epoch":expected_epoch,"installation":store.installation_id()?,"durableEpoch":store.epoch(),"binding":plan["binding"],"files":plan["files"],"replacement":plan.get("replacement"),"operation":operation});
 *pending().lock().map_err(|_|denied())?=Some(Pending{ticket,epoch:expected_epoch,subject:session.subject,issuer:session.issuer,plan,operation,frame:None});Ok(summary)
}
#[tauri::command]
pub(crate) async fn maintenance_execute(app:tauri::AppHandle,base:tauri::State<'_,RuntimeProfile>,ticket:String,expected_epoch:u64,confirmed:bool,access_token:String)->Result<(),String>{
 if !confirmed||access_token.is_empty()||access_token.len()>16384{return Err(denied());}
 let _transition=account_session::TRANSITION.try_lock().map_err(|_|"다른 계정/유지보수 작업이 진행 중입니다.")?;
 account_session::require_epoch(expected_epoch)?;
 let mut p=pending().lock().map_err(|_|denied())?.clone().ok_or_else(denied)?;
 let active=account_session::current()?;
 if p.ticket!=ticket||p.epoch!=expected_epoch||p.subject!=active.subject||p.issuer!=active.issuer{return Err(denied());}
 validate_plan(&base.data_dir,&p.plan,&active.issuer,&active.subject)?;
 let(url,pubkey,workspace_keys)=account_session::configuration()?;let keys=approval_keys()?;let worker=worker(&app)?;
 let store=Store::load(&base.data_dir)?;
 if p.frame.is_none(){
  let(body,nonce)=store.maintenance_request(&p.issuer,&p.subject,p.plan["binding"].as_str().ok_or_else(denied)?,&p.operation,true,now())?;
  let client=reqwest::Client::builder().redirect(reqwest::redirect::Policy::none()).timeout(std::time::Duration::from_secs(15)).build().map_err(|_|denied())?;
  let response=client.post(format!("{url}/functions/v1/moadoom-maintenance")).header("apikey",&pubkey).bearer_auth(&access_token).header("Content-Type","application/json").body(serde_json::to_vec(&body).map_err(|_|denied())?).send().await.map_err(|_|"승인 서비스에 연결할 수 없어 중단했습니다. 업무 잠금은 해제하지 않았습니다.")?;
  account_session::require_epoch(expected_epoch)?;
  if response.status()!=reqwest::StatusCode::OK{return Err("승인이 발급되지 않았습니다. 독립 소유권 검토·서비스 설정·로그인 상태를 확인하세요. 우회하지 않습니다.".into());}
  let response=account_session::json_response(response).await?;
  p.frame=Some(json!({"plan":p.plan,"operation":p.operation,"access_token":"","approval_token":response["approval"],"config":{"central_url":url,"publishable_key":pubkey,"approval_keys":keys},"installation_id":store.installation_id()?,"client_epoch":store.epoch(),"nonce":nonce,"confirmed":true}));
 }
 let mut frame=p.frame.clone().ok_or_else(denied)?;
 let signed:SignedGrant=serde_json::from_value(frame["approval_token"].clone()).map_err(|_|denied())?;
 store.verify_maintenance(&signed,&p.issuer,&p.subject,p.plan["binding"].as_str().ok_or_else(denied)?,&p.operation,frame["nonce"].as_str().ok_or_else(denied)?,store.epoch(),&keys,now())?;
 account_session::require_epoch(expected_epoch)?;
 *pending().lock().map_err(|_|denied())?=Some(p); // No token/key persisted; retries reuse still-valid approval only.
 frame["access_token"]=access_token.into();
 PAUSED.store(true,Ordering::SeqCst);
 crate::mobile_sync::stop_for_maintenance(&app).await?;
 let _auxiliary=tokio::time::timeout(std::time::Duration::from_secs(15),AUXILIARY.write()).await.map_err(|_|"진행 중인 모바일 관리 명령 종료를 확인하지 못해 잠금 인계를 차단했습니다.")?;
 let base=base.inner().clone();
 tauri::async_runtime::spawn_blocking(move||{
  let _business=BUSINESS.lock().map_err(|_|denied())?;
  account_session::require_epoch(expected_epoch)?;
  crate::stop_all_for_maintenance(&app,&base)?;
  let state=app.state::<crate::AppLock>();let mut lock=state.0.lock().map_err(|_|denied())?;let owner=lock.token().to_owned();
  lock.handoff(&owner,||account_session::require_epoch(expected_epoch),||execute_reviewed_worker(&base.data_dir,&worker,&frame,&workspace_keys,&keys,||account_session::require_epoch(expected_epoch)))
 }).await.map_err(|_|"유지보수 작업 오류: 앱 재시작 후 검토가 필요합니다.".to_string())?
}

#[cfg(test)]
mod tests {
 use super::*;
 #[test] fn same_owner_transition_plan_is_scoped_even_while_target_is_archived(){
  use sha2::Digest;
  let root=std::env::temp_dir().join(format!("transition-scope-{}",crate::workspace_grant::random_hex().unwrap()));
  let issuer="https://fixture.supabase.co/auth/v1";let subject="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  let digest=format!("{:x}",sha2::Sha256::digest(serde_json::to_vec(&vec![issuer,subject]).unwrap()));
  std::fs::create_dir_all(root.join("accounts")).unwrap();
  let plan=json!({"installation":root,"source":root.join("accounts").join(digest),"binding":"a".repeat(64),"files":{"config/customer-db.key":{"source":"config/customer-db.key","sha256":"b".repeat(64)}},"replacement":{"mode":"same-owner-format","issuer":issuer,"subject":subject,"snapshot":{"files":{},"dirs":[]}}});
  assert!(validate_plan(&root,&plan,"https://fixture.supabase.co/auth/v1","aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").is_ok());
 }
 #[test] fn plan_scope_rejects_other_roots_and_authority_files(){
  let root=std::env::temp_dir().join(format!("scope-{}",crate::workspace_grant::random_hex().unwrap()));std::fs::create_dir_all(&root).unwrap();
  let mut plan=serde_json::json!({"installation":root,"source":root,"binding":"a".repeat(64),"files":{"data/customers.sqlite3":{"source":"customers.sqlite3","sha256":"b".repeat(64)}}});
  assert!(validate_plan(&root,&plan,"https://fixture.supabase.co/auth/v1","aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").is_ok());
  plan["files"]["data/customers.sqlite3"]["source"]=".workspace-authority/state.dpapi".into();assert!(validate_plan(&root,&plan,"https://fixture.supabase.co/auth/v1","aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").is_err());
  plan["source"]="C:/forbidden-other-root".into();assert!(validate_plan(&root,&plan,"https://fixture.supabase.co/auth/v1","aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").is_err());
 }
}
