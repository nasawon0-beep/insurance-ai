# 파일럿 온보딩 체크리스트

상담자 PC 1대를 파일럿용으로 세팅하는 절차. macOS / Windows 공통 뼈대 + OS별 주의사항.

---

## 0. 준비물 (PC마다 1회)

- [ ] OS 확인: macOS 13+ 또는 Windows 10/11 (64bit)
- [ ] **Python 3.11 설치** (권장).
      엔진 가상환경은 현재 3.9 로도 동작하지만, 새 PC는 3.11 로 만드는 것을 권장.
- [ ] **Ollama 설치**: <https://ollama.com/download>
- [ ] 모델 내려받기 (합계 약 6GB, 네트워크 상태에 따라 10~40분):
      ```
      ollama pull qwen2.5:7b     # 필드 추출·요약 (약 4.7GB)
      ollama pull bge-m3         # 약관 임베딩 (약 1.2GB)
      ```
      (`qwen2.5:14b` 는 기본 설치하지 않음. 품질 검증 때만 환경변수로 선택.)
- [ ] 프로젝트 폴더를 PC로 복사 (경로에 공백/한글이 있어도 됨 — 스크립트가 따옴표 처리함)
- [ ] Node.js LTS 설치 — **데스크톱 앱을 dev 모드(`npm run tauri dev`)로 띄울 경우에만** 필요.
      빌드된 앱(.app/.msi)을 쓰면 불필요.

## 1. 설치 (PC마다 1회)

- macOS: `scripts/setup-once.command` 더블클릭 (또는 터미널에서 실행)
- Windows: `scripts\setup-once.bat` 더블클릭

하는 일: `local-engine/.venv` 생성 → `pip install -r requirements.txt` →
`ollama pull qwen2.5:7b` / `ollama pull bge-m3` (Ollama 설치돼 있을 때).
`control-server/.venv` 생성과 control-server 의존성 설치도 함께 진행된다(약 1분).

> `requirements.txt` 에 OCR 폴백용 `rapidocr-onnxruntime`, `pypdfium2` 가 포함된다.
> 설치 용량이 조금 크다(opencv 포함, 약 90MB). 오프라인 PC면 wheel 을 미리 받아두자.

## 2. 실행 / 정지 (매번)

- 시작 — macOS: `scripts/start.command` / Windows: `scripts\start.bat`
  - Ollama 확인·기동 → control-server·local-engine 기동 → `/health` 200 대기 → 앱 실행
  - 최초 1회 `control-server/secrets.env` 를 자동 생성하며, 이 파일은 커밋·공유하지 않는다.
  - 로그: `logs/control-server.log`, `logs/local-engine.log`, `logs/ollama.log`, `logs/desktop.log`
  - 포트 8420 이 이미 우리 엔진이면 재사용, 남이 쓰면 메시지 후 중단
- 정지 — macOS: `scripts/stop.command` / Windows: `scripts\stop.bat`
  - **포트 8420 / 8790 를 점유한 PID 만** 종료 (Ollama·앱 창은 유지)
  - local-engine 과 control-server 는 둘 다 `python main.py` 라 이름 기준 kill 금지

> control-server 를 띄우지 않으려면 `START_CONTROL_SERVER=0` 을 환경변수로 두고 start 실행.

## 3. 첫 로그인

- [ ] 앱에서 로그인 → control-server 가 라이선스 블롭을 내려주고 로컬에 서명 저장됨
      (이후 5일간 오프라인 사용 가능)
- [ ] 로그인 후 **설정·진단** 탭 → "엔진 / Ollama / 모델 / 암호화 / OCR" 상태가 정상인지 확인

## 4. macOS 전용

- [ ] **Keychain**: 첫 실행 때 고객 DB 암호화 키 접근 팝업이 뜨면 **"항상 허용"** 클릭
      (매번 물어보지 않게)
- [ ] **Gatekeeper**: 서명 안 된 .app 은 첫 실행 시 우클릭 → "열기" 로 실행
- [ ] STT: Apple Silicon 은 `whisper-cli`(Metal) 경로가 있으면 빠름.
      없으면 자동으로 faster-whisper(CPU)로 폴백.
- [ ] OCR: macOS Vision 바이너리(`local-engine/ocr/pdf-ocr`)가 있으면 그걸 사용.

## 5. Windows 전용 (중요)

- [ ] **STT 는 CPU 전사라 느립니다.** `whisper-cli` 가 PATH 에 없으면 faster-whisper(CPU)로 동작.
      기본 모델은 `small`(약 460MB, 첫 사용 시 HuggingFace 에서 자동 다운로드).
      더 정확히 하려면 환경변수 `WHISPER_FASTER_MODEL=medium` (느려짐, 약 1.5GB).
      설정·진단 화면에 "이 PC: 음성 CPU 전사(느림)" 안내가 표시됨.
- [ ] **OCR 은 RapidOCR(onnxruntime)** 로 동작. 글자 없는 PDF(스캔본/벡터 출력물)를
      `pypdfium2` 로 렌더 → RapidOCR(한국어+영어 내장 모델). **첫 실행 시 모델 준비로 몇 초** 걸림.
- [ ] 실행은 `.bat` 런처 사용 (`start.bat` / `stop.bat` / `setup-once.bat`).
- [ ] AMD GPU/CPU 상태 확인: `python scripts\windows-ollama-check.py` 실행 후
      `ollama ps` 의 PROCESSOR 또는 JSON `size_vram` 확인.
      gfx1103 PC가 CPU로 폴백하면 `docs/WINDOWS-OLLAMA-AMD.md` 순서대로 점검.
- [ ] **Windows 앱 빌드(.msi)는 별도 리드 작업**입니다. Windows 머신에서
      `cd desktop && npm run tauri build` → `src-tauri/target/release/bundle/msi/*.msi`.
      파일럿 동안에는 **Node 설치 + `start.bat` 이 `npm run tauri dev` 로 앱을 띄우는 방식**이면 충분.
      두 경로 모두 문서화해 두었으니 리드가 결정.

## 6. 파일럿 설정 메모

- **주민등록번호 입력 기능: 기본 OFF** (`RRN_INPUT_ENABLED` 미설정 → 기본 false).
  - 설정·진단에서 런타임 토글 가능 (단, 환경변수로 `0`/`1` 을 강제하면 잠김 → `PATCH /settings` 403 `locked`).
  - OFF 여도 이미 저장된 주민번호는 암호화된 채 보존되며 응답에서만 숨겨집니다. 나중에 켜면 그대로 복구.
- **자동 백업**: 앱 시작 시 하루 1회, `local-engine/database/data/backups/` 에 최신 20개 보관.
  `ENGINE_BACKUP=0` 이면 시작 백업 비활성.
- **사용 로그**: 익명 집계만 로컬 저장. **서버로 자동 전송 없음.**
  설정·진단 → "사용 로그" 에서 수동 CSV 내보내기만 가능. 180일 지난 행은 시작 시 자동 정리.
- **내보내기/가져오기**: 설정·진단 → "데이터 관리". 내보내기는 CSV 3개 zip,
  가져오기는 CSV 고객만(v1, 계약 없음). 엑셀은 'CSV UTF-8' 로 저장 후 업로드.

## 7. 리드에게 넘길 것

- [ ] Windows 앱 정식 빌드(.msi) 여부 결정 (dev 모드로 갈지)
- [ ] 각 PC에 Ollama 모델 사전 배포 방법 (인터넷 느린 지점 대비 오프라인 pull)
- [ ] `qwen2.5:14b` 예외 사용 여부 (기본 7B, 필요 시 `RAG_LLM_MODEL=qwen2.5:14b`)
