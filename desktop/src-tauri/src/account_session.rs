//! Central-account process boundary. No email-based ownership or legacy adoption.
use super::RuntimeProfile;
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
use sha2::{Digest, Sha256};
use std::{collections::BTreeMap,path::PathBuf,sync::{Mutex,OnceLock},time::{Instant,SystemTime,UNIX_EPOCH}};
use crate::workspace_grant::{Claims,Store,SignedGrant};


#[derive(Clone)]
pub(crate) struct Session {
    pub profile: RuntimeProfile,
    pub issuer: String,
    pub subject: String,
    pub digest: String,
    pub expires: u64,
    pub parent_lock: PathBuf,
    pub epoch: u64,
    central_digest:String,
    request_token:String,
    started:Instant,
    started_wall:u64,
    online_checked:Option<Instant>,
    offline:bool,
}
#[derive(Default)]
struct State { epoch:u64, session:Option<Session>, clock_high:u64 }
static STATE:OnceLock<Mutex<State>>=OnceLock::new();
pub(crate) static TRANSITION:tokio::sync::Mutex<()>=tokio::sync::Mutex::const_new(());
fn state()-> &'static Mutex<State> { STATE.get_or_init(||Mutex::new(State::default())) }
fn now()->u64 { SystemTime::now().duration_since(UNIX_EPOCH).map(|d|d.as_secs()).unwrap_or(u64::MAX) }
pub(crate) fn current()->Result<Session,String> {
    let mut guard=state().lock().map_err(|_|"계정 상태 오류")?;
    let session=guard.session.clone().ok_or("중앙 계정의 유효한 전용 업무공간 권한이 필요합니다.")?;
    let wall=now();guard.clock_high=guard.clock_high.max(wall).max(session.started_wall.saturating_add(session.started.elapsed().as_secs()));
    if wall.saturating_add(120)<guard.clock_high || guard.clock_high>=session.expires {guard.session=None;return Err("WORKSPACE_CLOCK_OR_EXPIRY_INVALID".into());}
    Ok(session)
}
pub(crate) fn require_epoch(epoch:u64)->Result<(),String>{if current()?.epoch!=epoch {return Err("계정이 바뀌어 이전 응답을 폐기했습니다.".into());}Ok(())}
pub(crate) fn profile()->Result<RuntimeProfile,String>{Ok(current()?.profile)}
pub(crate) fn invalidate(){if let Ok(mut s)=state().lock(){s.epoch=s.epoch.wrapping_add(1);s.session=None;}}
pub(crate) fn owns_process_event(current:Option<u32>, event:u32)->bool {current==Some(event)}

fn canonical_subject(subject:&str)->bool {
    regex::Regex::new(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$").unwrap().is_match(subject)
}
fn claims_after_online_verification(token:&str,issuer:&str,subject:&str,clock:u64)->Result<u64,String>{
    if !canonical_subject(subject){return Err("중앙 계정 식별자가 올바르지 않습니다.".into());}
    let parts:Vec<_>=token.split('.').collect();if parts.len()!=3{return Err("잘못된 중앙 세션".into());}
    let raw=URL_SAFE_NO_PAD.decode(parts[1]).map_err(|_|"잘못된 중앙 세션")?;
    let claims:serde_json::Value=serde_json::from_slice(&raw).map_err(|_|"잘못된 중앙 세션")?;
    let expiry=claims["exp"].as_u64().ok_or("중앙 세션 만료 정보 없음")?;
    if claims["sub"].as_str()!=Some(subject)||claims["iss"].as_str()!=Some(issuer)||claims["role"].as_str()!=Some("authenticated")||expiry<=clock{return Err("중앙 세션의 계정·발급자·만료를 확인하지 못했습니다.".into());}
    Ok(expiry)
}
fn scoped_profile(base:&RuntimeProfile,issuer:&str,subject:&str)->Result<RuntimeProfile,String>{
    if !canonical_subject(subject){return Err("잘못된 계정 식별자".into());}
    // Same canonical JSON identity as account_workspace.py; installation root stays separate.
    let identity=serde_json::to_vec(&vec![issuer,subject]).map_err(|_|"계정 식별 실패")?;
    let hash=format!("{:x}",Sha256::digest(identity));
    let root=base.data_dir.join("accounts").join(hash);
    let mut profile=base.clone();
    profile.data_dir=root.join("data");profile.config_dir=root.join("config");profile.log_dir=root.join("logs");
    let accounts=base.data_dir.join("accounts");
    for path in [&accounts,&root,&profile.data_dir,&profile.config_dir,&profile.log_dir] {
        if let Ok(meta)=std::fs::symlink_metadata(path) {
            if meta.file_type().is_symlink(){return Err("업무공간 링크는 허용하지 않습니다.".into());}
            #[cfg(windows)] {use std::os::windows::fs::MetadataExt;if meta.file_attributes()&0x400!=0{return Err("업무공간 reparse point는 허용하지 않습니다.".into());}}
        }
    }
    Ok(profile)
}
async fn stop_business_processes(app:&tauri::AppHandle,port:u16)->Result<(),String>{
    crate::mobile_sync::stop_for_account_transition(app).await?;
    super::stop_account_engine(app)?;
    if !super::wait_for_local_engine_port_released(port,std::time::Duration::from_secs(5)){return Err("이전 엔진 포트가 남아 계정 전환을 중단했습니다.".into());}
    Ok(())
}
#[derive(serde::Serialize)]
#[serde(rename_all="camelCase")]
pub(crate) struct SessionInfo {subject:String,epoch:u64,expires_at:u64,legacy_migration_required:bool,request_token:String,offline:bool}
fn info(s:&Session)->SessionInfo{SessionInfo{subject:s.subject.clone(),epoch:s.epoch,expires_at:s.expires,legacy_migration_required:false,request_token:s.request_token.clone(),offline:s.offline}}

pub(crate) fn configuration()->Result<(String,String,BTreeMap<String,String>),String>{
    let url=option_env!("VITE_MOADOOM_SUPABASE_URL").unwrap_or("").trim().trim_end_matches('/').to_string();
    let key=option_env!("VITE_MOADOOM_SUPABASE_PUBLISHABLE_KEY").unwrap_or("").trim().to_string();
    let keys:BTreeMap<String,String>=serde_json::from_str(option_env!("VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS").unwrap_or("{}")).map_err(|_|"workspace 공개키 설정 오류")?;
    if !regex::Regex::new(r"^https://[a-z0-9-]+\.supabase\.co$").unwrap().is_match(&url)||!key.starts_with("sb_publishable_")||keys.is_empty(){return Err("중앙 workspace grant 배포 설정이 없습니다. 로컬 계정으로 대체하지 않습니다.".into());}
    Ok((url,key,keys))
}
fn check_transition(epoch:u64)->Result<(),String>{if state().lock().map_err(|_|"계정 상태 오류")?.epoch!=epoch{return Err("이전 계정 전환 요청을 폐기했습니다.".into());}Ok(())}
pub(crate) async fn json_response(mut response:reqwest::Response)->Result<serde_json::Value,String>{
    let mut bytes=Vec::new();while let Some(chunk)=response.chunk().await.map_err(|_|"WORKSPACE_NETWORK_UNAVAILABLE")?{if bytes.len()+chunk.len()>65536{return Err("중앙 응답 크기 오류".into());}bytes.extend_from_slice(&chunk);}
    serde_json::from_slice(&bytes).map_err(|_|"중앙 응답 오류".into())
}
async fn deny(app:&tauri::AppHandle,base:&RuntimeProfile,store:&mut Store)->Result<SessionInfo,String>{
    invalidate();let saved=store.logout(now());let stopped=stop_business_processes(app,base.engine_port).await;saved?;stopped?;
    Err("WORKSPACE_AUTHORIZATION_DENIED".into())
}
fn prepare_business_directories(profile:&RuntimeProfile,maintenance_only:bool)->Result<(),String>{
    if maintenance_only{return Ok(());}
    let account=profile.data_dir.parent().ok_or("업무공간 경로 오류")?;
    let marker=account.parent().ok_or("업무공간 경로 오류")?.join(format!(".transition-{}",account.file_name().and_then(|s|s.to_str()).ok_or("업무공간 경로 오류")?));
    if std::fs::symlink_metadata(marker).is_ok(){return Err("중단된 자료 전환이 있어 업무공간을 새로 만들지 않습니다. 유지보수 전용 로그인에서 검토된 동일 계획 재시도 또는 원복을 진행하세요.".into());}
    if profile.data_dir.join("customers.sqlite3").exists() && !profile.customer_key_path().is_file(){return Err("기존 업무공간의 암호화 키가 없어 연결을 중단했습니다. 새 키를 만들지 않습니다.".into());}
    let runtime_dir=profile.engine_secret_path().parent().ok_or("인증 경로 오류")?.to_path_buf();
    for dir in [&profile.data_dir,&profile.config_dir,&profile.log_dir,&runtime_dir]{std::fs::create_dir_all(dir).map_err(|_|"전용 업무공간 생성 실패")?;}Ok(())
}
async fn install_authority(app:&tauri::AppHandle,base:&RuntimeProfile,claims:Claims,central_digest:Option<String>,issued:u64,maintenance_only:bool)->Result<SessionInfo,String>{
    check_transition(issued)?;
    if let Ok(mut active)=current(){
        if !maintenance_only && active.subject==claims.sub && active.issuer==claims.iss && active.expires<=claims.business_exp && active.expires>now().saturating_add(120){
            // Refresh without interrupting business requests; never extend an immutable engine deadline.
            active.offline=central_digest.is_none();
            if let Some(digest)=central_digest {active.central_digest=digest;active.online_checked=Some(Instant::now());}
            let mut guard=state().lock().map_err(|_|"계정 상태 오류")?;
            if guard.epoch!=issued{return Err("이전 계정 전환 요청을 폐기했습니다.".into());}
            guard.session=Some(active.clone());return Ok(info(&active));
        }
    }
    let issued={let mut guard=state().lock().map_err(|_|"계정 상태 오류")?;if guard.epoch!=issued{return Err("이전 계정 전환 요청을 폐기했습니다.".into());}guard.epoch=guard.epoch.wrapping_add(1);guard.session=None;guard.epoch};
    stop_business_processes(app,base.engine_port).await?;
    let issuer=claims.iss;let subject=claims.sub;let expires=claims.business_exp.min(claims.exp);
    let profile=scoped_profile(base,&issuer,&subject)?;
    prepare_business_directories(&profile,maintenance_only)?;
    let request_token=crate::workspace_grant::random_hex()?;
    let digest=format!("{:x}",Sha256::digest(request_token.as_bytes()));
    let offline=central_digest.is_none();
    let session={let mut guard=state().lock().map_err(|_|"계정 상태 오류")?;if guard.epoch!=issued{return Err("로그아웃 또는 다른 계정 전환으로 인증 응답을 폐기했습니다.".into());}
        let session=Session{profile,issuer,subject,digest,expires,parent_lock:base.lock_path(),epoch:issued,central_digest:central_digest.unwrap_or_default(),request_token,started:Instant::now(),started_wall:now(),online_checked:if offline{None}else{Some(Instant::now())},offline};guard.clock_high=now();guard.session=Some(session.clone());session};
    if maintenance_only {crate::maintenance_coordinator::pause_business();return Ok(info(&session));}
    let start_error=super::spawn_local_engine(app,&session.profile,false).err().map(|e|e.to_string());
    if let Some(error)=start_error{invalidate();let _=stop_business_processes(app,base.engine_port).await;return Err(format!("전용 업무공간 엔진 시작 실패: {error}"));}
    Ok(info(&session))
}
#[tauri::command]
pub(crate) fn account_session_generation()->Result<u64,String>{Ok(state().lock().map_err(|_|"계정 상태 오류")?.epoch)}
#[tauri::command]
pub(crate) async fn account_session_activate(app:tauri::AppHandle,base:tauri::State<'_,RuntimeProfile>,access_token:String,expected_subject:String,expected_epoch:u64,maintenance_only:Option<bool>)->Result<SessionInfo,String>{
    let maintenance_only=maintenance_only.unwrap_or(false);
    let requested_epoch=expected_epoch;
    let _transition=TRANSITION.lock().await;crate::maintenance_coordinator::ensure_business()?;check_transition(requested_epoch)?;
    if access_token.is_empty()||access_token.len()>16384||!canonical_subject(&expected_subject){return Err("중앙 세션이 필요합니다.".into());}
    let digest=format!("{:x}",Sha256::digest(access_token.as_bytes()));
    let (url,key,keys)=configuration()?;let issuer=format!("{url}/auth/v1");
    let mut store=Store::load(&base.data_dir)?;
    if let Ok(active)=current(){if !maintenance_only && active.central_digest==digest && active.subject==expected_subject && active.online_checked.map(|t|t.elapsed().as_secs()<300).unwrap_or(false){
        if store.resume(&issuer,&expected_subject,&keys,now()).is_err(){return deny(&app,&base,&mut store).await;}
        return Ok(info(&active));
    }}
    let mut issued=requested_epoch;
    if store.active_subject().map(|s|s!=expected_subject).unwrap_or(false){
        invalidate();issued=state().lock().map_err(|_|"계정 상태 오류")?.epoch;
        let saved=store.logout(now());let stopped=stop_business_processes(&app,base.engine_port).await;saved?;stopped?;
    }
    let client=reqwest::Client::builder().redirect(reqwest::redirect::Policy::none()).connect_timeout(std::time::Duration::from_secs(5)).timeout(std::time::Duration::from_secs(15)).build().map_err(|_|"중앙 인증 연결 실패")?;
    let response=client.get(format!("{url}/auth/v1/user")).header("apikey",&key).bearer_auth(&access_token).send().await.map_err(|_|"WORKSPACE_NETWORK_UNAVAILABLE")?;
    check_transition(issued)?;
    if response.status().is_server_error(){return Err("WORKSPACE_NETWORK_UNAVAILABLE".into());}
    if response.status()!=reqwest::StatusCode::OK{return deny(&app,&base,&mut store).await;}
    let user=json_response(response).await?;
    let subject=user["id"].as_str().ok_or("중앙 계정ID 없음")?;
    if subject!=expected_subject||claims_after_online_verification(&access_token,&issuer,subject,now()).is_err(){return deny(&app,&base,&mut store).await;}
    let(body,nonce)=store.request(&issuer,subject,now())?;
    let response=client.post(format!("{url}/functions/v1/moadoom-workspace")).header("apikey",&key).bearer_auth(&access_token).header("Content-Type","application/json").body(serde_json::to_vec(&body).map_err(|_|"workspace 요청 직렬화 실패")?).send().await.map_err(|_|"WORKSPACE_NETWORK_UNAVAILABLE")?;
    check_transition(issued)?;
    if response.status().is_server_error(){return Err("WORKSPACE_NETWORK_UNAVAILABLE".into());}
    if response.status()!=reqwest::StatusCode::OK{return deny(&app,&base,&mut store).await;}
    let body=json_response(response).await?;
    let signed:SignedGrant=serde_json::from_value(body.get("grant").cloned().ok_or("workspace grant 없음")?).map_err(|_|"workspace grant 형식 오류")?;
    check_transition(issued)?;
    let claims=match store.accept(signed,&issuer,subject,&nonce,&keys,now()){Ok(c)=>c,Err(_)=>return deny(&app,&base,&mut store).await};
    install_authority(&app,&base,claims,Some(digest),issued,maintenance_only).await
}
#[tauri::command]
pub(crate) async fn account_session_resume(app:tauri::AppHandle,base:tauri::State<'_,RuntimeProfile>,expected_subject:String,expected_epoch:u64)->Result<SessionInfo,String>{
    let issued=expected_epoch;
    let _transition=TRANSITION.lock().await;crate::maintenance_coordinator::ensure_business()?;check_transition(issued)?;
    let(url,_,keys)=configuration()?;
    if !canonical_subject(&expected_subject){return Err("중앙 계정 식별자가 필요합니다.".into());}
    let mut store=Store::load(&base.data_dir)?;
    let claims=store.resume(&format!("{url}/auth/v1"),&expected_subject,&keys,now())?;
    install_authority(&app,&base,claims,None,issued,false).await
}
#[tauri::command]
pub(crate) async fn account_session_close(app:tauri::AppHandle,base:tauri::State<'_,RuntimeProfile>)->Result<(),String>{
    invalidate();let _transition=TRANSITION.lock().await;invalidate();
    let saved=if base.data_dir.join(".workspace-authority/state.dpapi").exists(){Store::load(&base.data_dir).and_then(|mut s|s.logout(now()))}else{Ok(())};
    let stopped=stop_business_processes(&app,base.engine_port).await;saved?;stopped
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn normal_workspace_does_not_require_legacy_approval(){
        let root=std::env::temp_dir().join("account-core-info-only");
        let session=Session{profile:super::super::production_runtime_profile(root.join("config"),root.join("data")),issuer:"https://fixture.supabase.co/auth/v1".into(),subject:"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".into(),digest:String::new(),expires:4102444800,parent_lock:root.join("app.lock"),epoch:1,central_digest:String::new(),request_token:String::new(),started:Instant::now(),started_wall:now(),online_checked:Some(Instant::now()),offline:false};
        assert!(!info(&session).legacy_migration_required,"ordinary verified workspace must not require legacy migration approval");
    }
    #[test] fn maintenance_login_does_not_create_destination_workspace(){
        let root=std::env::temp_dir().join(format!("maintenance-no-bootstrap-{}",crate::workspace_grant::random_hex().unwrap()));
        let base=super::super::production_runtime_profile(root.join("config"),root.join("data"));
        let profile=scoped_profile(&base,"https://fixture.supabase.co/auth/v1","aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").unwrap();
        prepare_business_directories(&profile,true).unwrap();assert!(!root.exists());
        prepare_business_directories(&profile,false).unwrap();assert!(profile.data_dir.is_dir());
        let account=profile.data_dir.parent().unwrap();
        let marker=account.parent().unwrap().join(format!(".transition-{}",account.file_name().unwrap().to_str().unwrap()));
        std::fs::write(marker,b"pending-reviewed-transition").unwrap();
        assert!(prepare_business_directories(&profile,false).is_err(),"normal bootstrap must not recreate a workspace during atomic transition recovery");
        assert!(prepare_business_directories(&profile,true).is_ok());
    }
    #[test] fn delayed_ipc_cannot_capture_a_new_epoch_after_logout(){let old=account_session_generation().unwrap();invalidate();assert!(check_transition(old).is_err());assert!(check_transition(account_session_generation().unwrap()).is_ok());}
    #[test] fn old_child_exit_cannot_detach_new_account_engine(){assert!(!owns_process_event(Some(202),101));assert!(owns_process_event(Some(202),202));assert!(!owns_process_event(None,101));}
    fn token(sub:&str,iss:&str,exp:u64)->String{format!("fixture.{}.fixture",URL_SAFE_NO_PAD.encode(serde_json::to_vec(&serde_json::json!({"sub":sub,"iss":iss,"exp":exp,"role":"authenticated"})).unwrap()))}
    #[test] fn claims_must_match_server_verified_identity(){let a="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";let issuer="https://fixture.supabase.co/auth/v1";let t=token(a,issuer,200);assert_eq!(claims_after_online_verification(&t,issuer,a,100).unwrap(),200);assert!(claims_after_online_verification(&t,issuer,"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",100).is_err());assert!(claims_after_online_verification(&t,issuer,a,200).is_err());assert!(claims_after_online_verification(&t,issuer,"owner@example.invalid",100).is_err());}
    #[test] fn two_accounts_two_installations_never_use_legacy(){for base_path in ["fixture-install-one","fixture-install-two"]{let base=super::super::production_runtime_profile(PathBuf::from("fixture-config"),PathBuf::from(base_path));let a=scoped_profile(&base,"https://fixture.supabase.co/auth/v1","aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa").unwrap();let b=scoped_profile(&base,"https://fixture.supabase.co/auth/v1","bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb").unwrap();assert_ne!(a.data_dir,b.data_dir);assert_ne!(a.customer_key_path(),b.customer_key_path());assert!(a.data_dir.starts_with(base.data_dir.join("accounts")));assert_ne!(a.data_dir,base.data_dir);}}
}
