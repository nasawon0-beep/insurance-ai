export type WorkspaceSession = {subject:string;requestToken:string;expiresAt:number;epoch:number;offline:boolean};
type Options={
 session:{access_token:string;user:{id:string}}|null;
 sessionError:{name:string}|null;
 cachedSubject:string|null;
 offlineBackoff?:boolean;
 activate:(token:string,subject:string)=>Promise<WorkspaceSession>;
 resume:(subject:string)=>Promise<WorkspaceSession>;
};
/** Selectors never authorize access: only native verification of the protected signed grant does. */
export async function obtainWorkspaceSession(o:Options):Promise<WorkspaceSession>{
 if(o.sessionError && o.sessionError.name!=='AuthRetryableFetchError')throw new Error('중앙 인증이 거부되었습니다. 다시 로그인하세요.');
 const subject=o.session?.user.id || o.cachedSubject;
 if(!subject)throw new Error('중앙 계정 로그인이 필요합니다.');
 let result:WorkspaceSession;
 if(o.session?.access_token && !o.sessionError && !o.offlineBackoff){
  try{result=await o.activate(o.session.access_token,subject);}
  catch(error){
   if((error instanceof Error?error.message:String(error))!=='WORKSPACE_NETWORK_UNAVAILABLE')throw error;
   result=await o.resume(subject);
  }
 }else result=await o.resume(subject);
 if(result.subject!==subject || !/^[a-f0-9]{64}$/.test(result.requestToken) || !Number.isSafeInteger(result.expiresAt) || result.expiresAt<=0)throw new Error('중앙 계정과 업무공간이 일치하지 않습니다.');
 return result;
}
