import {useEffect,useState} from 'react';
import {invoke} from '@tauri-apps/api/core';
import {centralClient} from './centralClient';
import {MAINTENANCE_LIMIT,maintenanceAllowed} from './maintenancePolicy';
type Status={available:boolean;message:string;paused:boolean};
type Prepared={replacement?:{mode:string;snapshot:{files:Record<string,string>;dirs:string[]}}|null;ticket:string;subject:string;issuer:string;epoch:number;installation:string;durableEpoch:number;binding:string;operation:string;files:Record<string,{source:string;sha256:string}>};
export default function MaintenancePanel({readOnly=false}:{readOnly?:boolean}){
 const [status,setStatus]=useState<Status>({available:false,message:'유지보수 배포 설정 확인 전에는 실행할 수 없습니다.',paused:false});
 const [plan,setPlan]=useState<unknown>(null),[operation,setOperation]=useState('migrate');
 const [prepared,setPrepared]=useState<Prepared|null>(null),[confirmed,setConfirmed]=useState(false),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const refresh=()=>invoke<Status>('maintenance_status').then(setStatus).catch(()=>setStatus({available:false,message:'네이티브 승인 서비스 설정을 확인할 수 없어 차단했습니다.',paused:false}));
 useEffect(()=>{void refresh();},[]);
 const prepare=async()=>{setBusy(true);setPrepared(null);setConfirmed(false);try{
  const epoch=await invoke<number>('account_session_generation');
  setPrepared(await invoke<Prepared>('maintenance_prepare',{plan,operation,expectedEpoch:epoch}));setMessage('계정·설치·계획과 작업을 확인해 주세요. 별도 검토 기록 없이는 승인이 발급되지 않습니다.');
 }catch(e){setMessage(String(e));}finally{setBusy(false);}};
 const execute=async()=>{if(!prepared||!maintenanceAllowed(status.available&&!readOnly,true,confirmed,busy))return;setBusy(true);setMessage('온라인 승인 확인 중입니다. 승인 후 업무·모바일을 중지하고 종료가 확인된 경우에만 실행합니다.');try{
  const {data,error}=await centralClient().auth.getSession();if(error||!data.session)throw new Error('온라인 모아둠 로그인이 필요합니다.');
  await invoke('maintenance_execute',{ticket:prepared.ticket,expectedEpoch:prepared.epoch,confirmed:true,accessToken:data.session.access_token});
  setMessage('요청 작업이 완료되었습니다. 업무는 중지 상태입니다. 앱을 닫고 다시 시작해 자료를 확인하세요.');setPrepared(null);
 }catch(e){setMessage(`${String(e)} 기존 원본과 복구 journal을 보존합니다. 같은 유효 승인은 재시도할 수 있고, 만료·다른 작업은 새 검토가 필요합니다.`);}finally{setConfirmed(false);setBusy(false);void refresh();}};
 return <details><summary>이전 자료 소유권 검토·유지보수</summary>
  <p>{MAINTENANCE_LIMIT}</p><p role="status">{status.message}</p>
  <p>기존 계정 업무공간의 구형 키·RAG 파일은 자동 변환하지 않습니다. 기존 폴더를 삭제하거나 키를 새로 만들지 마세요. 빈 초기 업무공간도 삭제하지 않고 원본 보관 후 이전하며, 동일 소유자 형식 전환도 검증된 기존 키·정확한 전체 자료 계획·별도 승인이 필요합니다. 실자료 충돌 시 양쪽 보존, 자동 병합·덮어쓰기 금지입니다. 알 수 없는 파일·모바일 인증정보·검증 불가능한 키는 별도 검토가 필요합니다. 유효한 승인과 원본이 그대로인 중단된 동일 계획만 재시도할 수 있습니다.</p>
  <p>검토자가 만든 정확한 계획 JSON만 선택하세요. 로그인만으로 이전 자료가 귀속되지 않습니다. 이전·원복·격리 복구는 각각 별도 승인이 필요합니다.</p>
  <label>검토 계획 파일 <input type="file" accept="application/json,.json" disabled={!status.available||readOnly||busy} onChange={async e=>{setPrepared(null);setConfirmed(false);setPlan(null);const f=e.target.files?.[0];if(!f)return;try{if(f.size>512000)throw new Error('계획 파일이 너무 큽니다.');setPlan(JSON.parse(await f.text()));setMessage('계획 파일을 선택했습니다.');}catch{setMessage('올바른 계획 JSON 파일이 아닙니다.');}}}/></label>
  <label>작업 <select disabled={busy} value={operation} onChange={e=>{setOperation(e.target.value);setPrepared(null);setConfirmed(false);}}><option value="migrate">이전 또는 중단된 이전 재시도</option><option value="rollback">이전 대상 원복</option><option value="restore_backup">백업 격리 복구</option></select></label>
  <button disabled={!status.available||readOnly||!plan||busy} onClick={()=>void prepare()}>계획·계정 확인</button>
  {prepared&&<section aria-label="유지보수 확인"><p>계정: {prepared.subject}<br/>발급자: {prepared.issuer}<br/>설치: {prepared.installation}<br/>계정 세대: {prepared.durableEpoch}<br/>계획: {prepared.binding}<br/>작업: {prepared.operation}</p>
   <ul>{Object.entries(prepared.files).map(([dest,v])=><li key={dest}>{v.source} → {dest}<br/>SHA256 {v.sha256}</li>)}</ul>
   {prepared.replacement&&<section aria-label="기존 대상 보관 검토"><p>대상 처리: {prepared.replacement.mode==='empty-bootstrap'?'빈 초기 업무공간 보관 후 이전':'동일 소유자 형식 전환'}</p><p>아래 기존 대상은 삭제하지 않고 보관합니다. 실행 직전 파일·DB·잠금을 다시 검사하며 차이가 있으면 중단합니다.</p><ul>{Object.entries(prepared.replacement.snapshot.files).map(([name,hash])=><li key={name}>{name}<br/>SHA256 {hash}</li>)}</ul><p>보존 폴더: {prepared.replacement.snapshot.dirs.join(', ')}</p></section>}
   <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={e=>setConfirmed(e.target.checked)}/>이 계정과 정확한 계획·작업을 확인했습니다. 업무·모바일 중지와 완료 후 앱 재시작, DPAPI 복구 한계를 이해하고 실행에 동의합니다.</label>
   <button disabled={!maintenanceAllowed(status.available&&!readOnly,!!prepared,confirmed,busy)} onClick={()=>void execute()}>온라인 승인 확인 후 실행</button></section>}
  {status.paused&&<p role="alert">유지보수 때문에 업무 실행과 계정 전환이 중지됐습니다. 결과 확인 후 앱을 다시 시작하세요. 로그아웃은 진행 중 작업을 취소합니다.</p>}
  <p role="status" aria-live="polite">{message}</p>
 </details>;
}
