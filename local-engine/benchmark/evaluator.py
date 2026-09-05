#!/usr/bin/env python3
"""
Insurance AI - 모델 후보 벤치마크 (문서 12번 계획 구현)

사용법:
    python evaluator.py --models qwen2.5:14b,qwen2.5:7b,gemma2:9b
    python evaluator.py --models qwen2.5:14b --grade   # 직전 결과를 사람이 채점

전제:
    - Ollama가 로컬에서 실행 중 (기본 http://localhost:11434)
    - 비교하고 싶은 모델은 미리 `ollama pull <model>` 로 받아둘 것
    - test_set.json의 context에 실제 약관 발췌문을 넣어야 의미 있는 비교가 됨
"""
import argparse
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
TEST_SET_PATH = Path(__file__).parent / "test_set.json"
RESULTS_DIR = Path(__file__).parent / "results"

SYSTEM_PROMPT = """당신은 보험 약관 전문 AI입니다. 반드시 아래 규칙을 지키세요.

1. 오직 [약관 발췌문]에 있는 내용만 근거로 답변하세요. 모르면 모른다고 하세요.
2. 답변 형식은 다음과 같습니다:

답변: (핵심 답)

근거:
- 보험사: (있으면)
- 상품명: (있으면)
- 관련 조항: (있으면)
- 페이지: (있으면)

3. [약관 발췌문]에서 확정할 근거를 찾지 못하면 반드시 다음과 같이 답하세요:
"해당 약관에서 지급 여부를 확정할 근거를 찾지 못했습니다."
그리고 추측하지 마세요."""


def call_ollama(model: str, question: str, context: str) -> str:
    prompt = f"{SYSTEM_PROMPT}\n\n[약관 발췌문]\n{context}\n\n[질문]\n{question}"
    payload = {"model": model, "prompt": prompt, "stream": False}
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        elapsed = round(time.time() - start, 1)
        return data.get("response", "").strip(), elapsed
    except Exception as e:
        return f"[호출 실패: {e}]", round(time.time() - start, 1)


def run(models: list[str]):
    test_set = json.loads(TEST_SET_PATH.read_text(encoding="utf-8"))
    RESULTS_DIR.mkdir(exist_ok=True)

    run_log = {"timestamp": datetime.now().isoformat(), "models": models, "cases": []}

    for case in test_set["cases"]:
        print(f"\n=== {case['id']} ({case['category']}) ===")
        print(f"Q: {case['question']}")
        case_result = {"id": case["id"], "question": case["question"], "answers": {}}
        for model in models:
            answer, elapsed = call_ollama(model, case["question"], case["context"])
            print(f"\n--- {model} ({elapsed}s) ---\n{answer}")
            case_result["answers"][model] = {"text": answer, "seconds": elapsed}
        run_log["cases"].append(case_result)

    out_path = RESULTS_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.json"
    out_path.write_text(json.dumps(run_log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {out_path}")
    return out_path


def grade_latest():
    """가장 최근 결과 파일을 사람이 채점 (1~5점 + 환각 여부)"""
    files = sorted(RESULTS_DIR.glob("run_*.json"))
    if not files:
        print("채점할 결과가 없습니다. 먼저 벤치마크를 실행하세요.")
        return
    latest = files[-1]
    run_log = json.loads(latest.read_text(encoding="utf-8"))
    scores = {m: [] for m in run_log["models"]}

    for case in run_log["cases"]:
        print(f"\n=== {case['id']} ===")
        print(f"Q: {case['question']}")
        for model, ans in case["answers"].items():
            print(f"\n--- {model} ---\n{ans['text']}")
            while True:
                s = input(f"[{model}] 점수(1~5, 0=환각/틀림): ").strip()
                if s.isdigit() and 0 <= int(s) <= 5:
                    scores[model].append(int(s))
                    break
                print("0~5 사이 숫자를 입력하세요.")

    print("\n=== 평균 점수 ===")
    for model, s in scores.items():
        avg = sum(s) / len(s) if s else 0
        print(f"{model}: {avg:.2f} ({s})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=str, default="qwen2.5:14b")
    parser.add_argument("--grade", action="store_true", help="최근 결과를 사람이 채점")
    args = parser.parse_args()

    if args.grade:
        grade_latest()
    else:
        run(args.models.split(","))
