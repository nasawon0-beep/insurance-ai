import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createServer} from 'vite';
test('real maintenance UI starts blocked and discloses DPAPI loss boundaries',async()=>{
 const vite=await createServer({server:{middlewareMode:true},appType:'custom',logLevel:'silent'});
 try{const {default:Panel}=await vite.ssrLoadModule('/src/MaintenancePanel.tsx');
 const html=renderToStaticMarkup(React.createElement(Panel));
 assert.match(html,/Windows 프로필 또는 설치 보호정보 손실 시 복구를 보장하지 않습니다/);
 assert.match(html,/기존 계정 업무공간의 구형 키·RAG 파일은 자동 변환하지 않습니다/);
 assert.match(html,/기존 폴더를 삭제하거나 키를 새로 만들지 마세요/);
 assert.match(html,/중단된 동일 계획만 재시도/);
 assert.match(html,/빈 초기 업무공간도 삭제하지 않고 원본 보관/);
 assert.match(html,/실자료 충돌 시 양쪽 보존/);
 assert.match(html,/동일 소유자 형식 전환/);
 assert.match(html,/type="file"[^>]*disabled/);assert.match(html,/<button disabled="">계획·계정 확인/);
 assert.doesNotMatch(html,/온라인 승인 확인 후 실행/);assert.match(html,/별도 승인이 필요/);
 }finally{await vite.close();}
});
