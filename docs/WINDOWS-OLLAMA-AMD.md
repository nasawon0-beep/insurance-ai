# Windows Ollama AMD GPU/CPU 최적화 가이드

## 현재 정책

- 기본 LLM: `qwen2.5:7b`
- 임베딩: `bge-m3`
- `qwen2.5:14b`는 기본 설치/기본 실행 대상이 아니다.
- 14B가 꼭 필요하면 사용자가 직접 설치하고 환경변수로 명시한 경우에만 쓴다.

```bat
ollama pull qwen2.5:14b
set RAG_LLM_MODEL=qwen2.5:14b
```

파일럿 기본값은 속도 우선이므로 7B를 유지한다.

## 상태 확인

프로젝트 루트에서 실행한다.

```bat
python scripts\windows-ollama-check.py
ollama ps
```

판정 기준:

- `ollama ps`의 `PROCESSOR`가 `100% CPU`이거나 스크립트 JSON의 `size_vram`이 `0`이면 CPU 전용이다.
- `PROCESSOR`에 GPU가 표시되거나 `size_vram > 0`이면 GPU 또는 mixed 모드다.

## AMD gfx1103 주의

상원님 PC의 AMD `gfx1103` 환경은 공식 Ollama Windows ROCm 대상에서 CPU로 폴백할 수 있다. 이때 증상은 다음과 같다.

- `ollama ps`에 `100% CPU` 표시
- `/api/ps`의 `size_vram: 0`
- 응답은 되지만 7B도 느림

`HSA_OVERRIDE_GFX_VERSION`은 진단용으로만 적용한다. override만으로 GPU가 활성화되지 않을 수 있다.

권장 시도 순서:

1. Ollama 최신 Windows 설치본으로 업데이트
2. AMD Adrenalin/ROCm/HIP7 가능 드라이버 최신화
3. Ollama 재시작 후 `ollama ps` 확인
4. 여전히 CPU면 세션 한정으로만 테스트

```bat
set HSA_OVERRIDE_GFX_VERSION=11.0.0
ollama serve
```

또는 gfx1103 식별을 그대로 시도할 때:

```bat
set HSA_OVERRIDE_GFX_VERSION=11.0.3
ollama serve
```

적용 후 반드시 `ollama ps`에서 GPU 사용 여부를 다시 확인한다. `size_vram`이 계속 0이면 override 효과 없음으로 판단한다.

## CPU 전용 최적화

GPU가 안 잡히면 CPU 속도 우선으로 다음 기준을 쓴다.

- 기본: `OLLAMA_NUM_THREAD` 미설정 → 앱이 논리 코어 수를 `num_thread`로 전달
- UI 버벅임/발열 있음: `OLLAMA_NUM_THREAD=6` 또는 `8`
- Ollama 자체 자동값 사용: `OLLAMA_NUM_THREAD=auto`
- 컨텍스트는 기본 `2048` 유지. 긴 약관 질문에서만 `OLLAMA_RAG_NUM_CTX`를 올린다.

예:

```bat
set OLLAMA_NUM_THREAD=8
set OLLAMA_NUM_CTX=2048
```

## Quantization 정책

현재 `qwen2.5:7b`는 Ollama 기본 `Q4_K_M` 양자화 모델이다. CPU 전용에서는 이 설정을 유지한다.

더 빠른 응답이 필요하면 기능별 환경변수로 작은 모델을 선택한다.

```bat
set ASSISTANT_SUMMARY_LLM_MODEL=qwen2.5:1.5b
set ASSISTANT_COMPLEX_LLM_MODEL=qwen2.5:7b
set RAG_LLM_MODEL=qwen2.5:7b
```

품질보다 속도가 더 중요하면 RAG도 임시로 3B를 시험할 수 있다.

```bat
ollama pull qwen2.5:3b
set RAG_LLM_MODEL=qwen2.5:3b
```

## 앱 로그

local-engine 시작 시 Ollama 런타임 상태를 로그에 남긴다.

- 버전
- GPU 활성 여부
- `num_thread`
- `HSA_OVERRIDE_GFX_VERSION`
- 로드된 모델별 processor/VRAM/quantization

CPU 폴백이면 다음 경고가 출력된다.

```text
ollama runtime: CPU-only fallback detected; see docs/WINDOWS-OLLAMA-AMD.md
```
