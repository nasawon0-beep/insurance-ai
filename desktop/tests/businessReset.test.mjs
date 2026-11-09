import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {test} from 'node:test';
test('business reset requires an exact phrase and explicit button, with preserved data and sync hold disclosed',()=>{
 const source=readFileSync(new URL('../src/components/BusinessResetPanel.tsx',import.meta.url),'utf8');
 for(const text of ['전체 데이터 초기화','원본 PDF','백업','등록 휴대폰','암호화 키','동기화','입력','autoFocus','/business/reset','confirmed: true','window.location.reload'])assert.ok(source.includes(text),text);
 assert.match(source,/confirmation !== "초기화"/);
 assert.match(source,/pending.current/);
});
test('backup UI offers explicit PC-only or validated reset recovery, never a naked sync unlock',()=>{
 const path=new URL('../src/components/BusinessRestorePanel.tsx',import.meta.url);
 assert.ok(existsSync(path),'Reset recovery UI missing');
 const source=readFileSync(path,'utf8');
 for(const text of ['PC만 계속 사용','초기화 전 업무 복원','/business/reset/restore','mobile_sync_stop','confirmation !== "복원"','pending.current','safety_copy','자동 병합하지 않습니다']) assert.ok(source.includes(text),text);
 const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8');
 assert.ok(app.slice(app.indexOf('function BackupBox()'),app.indexOf('function ExportBox()')).includes('<BusinessRestorePanel'));
});
