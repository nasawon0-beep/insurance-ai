# benchmark/ 사용법

## 1. 모델 준비 (Mac mini에서)

```bash
ollama pull qwen2.5:14b
ollama pull qwen2.5:7b
# Gemma를 비교하고 싶으면
ollama pull gemma2:9b     # 또는 실제로 pull 가능한 최신 gemma 태그 확인 후
```

## 2. test_set.json 채우기

`context` 필드에 실제 약관 PDF에서 복사한 텍스트를 붙여넣으세요.
doc-parser가 아직 없으니 지금은 수작업입니다 — 이게 정상입니다.
`abstention_001` 케이스는 일부러 관련 없는 내용을 넣어서, 모델이
"모른다"고 정직하게 답하는지(환각하지 않는지)를 테스트합니다.

## 3. 실행

```bash
cd insurance-ai/local-engine/benchmark
python evaluator.py --models qwen2.5:14b,qwen2.5:7b
```

결과는 `results/run_<시각>.json`에 저장됩니다.

## 4. 채점

```bash
python evaluator.py --grade
```

각 모델의 답변을 하나씩 보여주고 1~5점(0=환각/오답)을 입력받아
모델별 평균 점수를 냅니다. 이 점수가 V0.1의 기본 모델을 정하는
근거가 됩니다 — "감이 아니라 데이터로" (문서 12번 원칙).

## 5. 다음 단계

- 문항을 최소 10~20개로 늘리기 (실제 자주 나오는 질문 위주)
- 근거 페이지/조항까지 일치하는지 자동 채점하는 로직 추가 (지금은 텍스트 답변만 비교)
- doc-parser가 생기면 context를 수작업이 아니라 실제 RAG 검색 결과로 자동 주입
