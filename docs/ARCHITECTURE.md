# Insurance AI Desktop — 아키텍처 확정 (작업 A)

## 0. 폴더 구조

```text
insurance-ai/
├── desktop/          # Tauri2 + React/TS. 사용자가 보는 유일한 화면.
├── local-engine/      # AI/RAG/Whisper/DB. 고객 PC에서 도는 두뇌.
├── control-server/    # 로그인/라이선스/업데이트/결제. 최소 권한.
├── tests/             # 통합 테스트 (Local Engine ↔ Desktop 계약 검증)
├── installer/         # Tauri bundler 설정, 서명/공증, 자동 업데이트 매니페스트
└── docs/              # 이 문서, ADR(설계 결정 기록) 등
```

## 1. desktop/ — UI만 담당, 판단 로직 없음

- Tauri2 + React + TypeScript
- 역할: 화면 렌더링, 사용자 입력 수집, `local-engine`을 로컬 HTTP(127.0.0.1)로 호출
- **절대 하지 않는 것**: PDF 파싱, DB 직접 접근, AI 모델 호출 — 전부 local-engine API를 통해서만
- 사이드바 구조(위노트 참고): 상담자 홈 / 고객 / 약관 / 상담(V0.2~) / 설정·진단

## 2. local-engine/ — 실제 두뇌, UI와 완전 분리

```text
local-engine/
├── ai/          # 모델 실행 래퍼 (Ollama dev → llama.cpp 판매판)
│   └── tiers.py   # LIGHT / STANDARD / HIGH 3단 티어 라우팅 (위노트 AI/AI Pro/클라우드 참고)
├── rag/         # embedding(BGE-M3) + vector DB + rerank + 근거 인용
├── parser/      # doc-parser: PDF 텍스트/표 추출, 페이지 보존, 메타데이터
├── whisper/     # 로컬 STT
├── database/    # SQLite, AES-256 암호화, OS 키체인 연동
├── diagnostics/ # 자가진단, 오류코드, 진단파일 생성
└── benchmark/   # ← 이번에 먼저 만든 부분 (evaluator.py)
```

- 인터페이스: `127.0.0.1:<port>` 로컬 REST API만 노출. `0.0.0.0` 바인딩 금지.
- 모델 계층 (위노트 벤치마킹):
  - **LIGHT**: 저사양 PC 자동 감지 시 폴백. Gemma 4 E4B / Qwen 7~8B 후보.
  - **STANDARD**: 기본. Qwen2.5 14B 또는 Qwen3.8-27B(고사양).
  - **CLOUD (선택)**: 익명화 후 전송하는 옵트인 옵션. "완전 로컬"이 어려운 저사양 PC 구제용. 기본값은 OFF.
- 사용량 정책: 모든 로컬 기능 무제한 (Whisper 상담 전사 포함). 크레딧/차감 없음.

## 3. control-server/ — 최소 권한, 고객 데이터 없음

```text
control-server/
├── auth/          # Supabase Auth 연동
├── license/       # ACTIVE/PRO, device_limit, expiry
├── device/        # 기기 등록 (라이선스당 최대 N대, 위노트는 2대)
├── update/        # 버전 체크, 서명된 업데이트 매니페스트
└── error-report/  # PII-safe 오류 수집만
```

- 저장 항목은 문서 24번 원칙 그대로: user_id, email, plan, status, device_id, app_version, license_expiry
- 오프라인 유예 3~7일 + 서명된 로컬 인증 상태. 시계 되돌리기 방지용 단조 타임스탬프 별도 로컬 저장 필요(이전 검토에서 지적한 부분).

## 4. installer/ — 배포

- Tauri bundler, 코드 서명, 자동 업데이트 채널(BETA/DEV → STABLE)
- 모델 파일은 설치파일에 안 넣고 첫 실행 시 다운로드(문서 28번 그대로)

## 5. tests/ — 계약 테스트

- desktop이 local-engine에 기대하는 API 스펙이 깨지지 않는지 확인
- RAG 응답에 항상 `{answer, company, product, clause, page}` 구조가 오는지 검증 (문서 9번 "근거 없는 답변 금지" 원칙을 코드 레벨에서 강제)

---

## 다음 우선순위 (변경 없음, 문서 59번 기준)

1. Tauri+React 빈 셸
2. local-engine 최소 API(헬스체크) + desktop에서 연결 상태 표시
3. 로그인 Mock
4. PDF 1개 업로드 → 텍스트 추출 → 페이지 보존
5. RAG 최소 파이프라인
6. **benchmark/evaluator.py로 모델 후보 비교 (지금 여기)**
