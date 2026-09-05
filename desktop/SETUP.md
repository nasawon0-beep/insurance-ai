# 작업 C — 첫 실행 가능한 Desktop App 만들기

Mac mini(개발 머신)에서 순서대로 진행하세요.

## 1. Local Engine 먼저 켜기

```bash
cd insurance-ai/local-engine
pip install -r requirements.txt --break-system-packages
python main.py
```

`http://127.0.0.1:8420/health` 를 브라우저로 열어서
`{"local_engine":"ok","ollama":"connected", ...}` 가 뜨면 성공.
(Ollama가 안 켜져 있으면 `ollama serve` 먼저 실행)

## 2. Tauri Desktop 앱 생성 (별도 터미널)

```bash
cd insurance-ai
npm create tauri-app@latest desktop -- --template react-ts
cd desktop
npm install
```

질문이 나오면: 패키지 매니저는 npm, 프론트엔드는 React, 언어는 TypeScript로 선택.

## 3. 제공된 화면 코드 붙여넣기

방금 생성된 `desktop/src/App.tsx` 를 이번에 받은 `App.tsx`(로그인+홈+엔진상태 표시)로
덮어쓰세요.

```bash
cp ../App.tsx src/App.tsx   # 이번에 받은 파일 위치에 맞게 경로 조정
```

## 4. 실행

```bash
npm run tauri dev
```

## 5. 성공 기준 (문서 62번 작업 C 그대로)

- [ ] Insurance AI 창이 뜬다
- [ ] 로그인 화면에서 아무 값이나 입력하면 통과된다 (Mock)
- [ ] 홈 화면에서 5초마다 자동으로 Local Engine / Ollama 연결 상태가 갱신된다
- [ ] Ollama가 꺼져 있으면 "연결 안됨"이 뜨고, 켜면 자동으로 "연결됨"으로 바뀐다

여기까지 되면 작업 C 완료 — 다음은 작업 D(PDF 1개 RAG)로 넘어갑니다.
