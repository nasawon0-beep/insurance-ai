# Local Korean LLM Phase 1 Report

Date: 2026-10-08
Project: Insurance AI CRM
Branch/commit checked: main / bb0b331d
Runtime used: Ollama 0.35.1 on Windows 11

## Summary

Phase 1 result: do not integrate EXAONE-3.5-2.4B-Instruct for production.

Reasons:
1. EXAONE-3.5-2.4B-Instruct license is non-commercial. The Hugging Face license text states research-only use and prohibits commercial application/external distribution without a separate commercial license.
2. Local BF16 artifact is 5.3 GB in Ollama, above the requested 2 GB packaging preference.
3. In the sample coverage-analysis prompt, it took 76.5 s total and made a status error on a simple amount comparison.

Recommended implementation direction for Phase 2:
- Keep current deterministic amount/status calculation in `local-engine/database/coverage.py` as the source of truth.
- Use local LLM only for Korean explanation/summary text if needed.
- Use a commercial-allowed 2-3B GGUF model as optional local summarizer, not as the calculator.
- Candidate tested for this role: `hf.co/QuantFactory/llama-3.2-Korean-Bllossom-3B-GGUF:Q4_K_M` (2.0 GB, commercial use claimed on model card/search result). It met the 30 s runtime only when JSON mode was used, but arithmetic/status quality was not good enough for source-of-truth analysis.

## Tested models

### 1. EXAONE-3.5-2.4B-Instruct GGUF BF16

Ollama model:
`hf.co/LGAI-EXAONE/EXAONE-3.5-2.4B-Instruct-GGUF:BF16`

Download/run result:
- Download: success
- Installed size: 5.3 GB
- Command: `ollama run hf.co/LGAI-EXAONE/EXAONE-3.5-2.4B-Instruct-GGUF:BF16 --format json --verbose < local-engine/benchmark/coverage_llm_prompt.txt`
- Total duration: 1m16.4994832s
- Load duration: 4.9069302s
- Prompt eval: 546 tokens / 2.592549s / 210.60 tokens/s
- Eval: 588 tokens / 1m8.970216s / 8.53 tokens/s

Quality check:
- Correct: 일반암 부족, 뇌혈관 부족, 허혈성심장질환 부족, 질병입원일당 부족, 상해입원일당 부족, 운전자 벌금 충분.
- Incorrect: 유사암 5,000,000원 vs 권장 10,000,000원인데 `충분`으로 출력.
- Top recommendations were plausible.

License:
- Not production-safe for this project without separate LG commercial license.
- HF search/license text: EXAONE AI Model License Agreement 1.1 - NC, research-only, commercial use prohibited.

Decision:
- Reject for commercial production integration now.

### 2. Llama-3-Open-Ko-8B-Instruct-preview GGUF

Attempted Ollama model:
`hf.co/LiteLLMs/Llama-3-Open-Ko-8B-Instruct-preview-GGUF:Q4_K_M`

Result:
- Download failed in Ollama because the repository only contains sharded GGUF files.
- Ollama error: `This repository only contains sharded GGUF files. Ollama does not yet support pulling sharded GGUF via the registry; please download the shards and merge them locally with ollama create...`

License:
- Llama 3 license permits commercial use subject to Meta Llama 3 license / acceptable use terms.

Decision:
- Candidate remains legally viable, but not validated in this run due Ollama sharded-GGUF limitation.
- Expected package size around 4.7-5 GB for Q4, likely above 2 GB preference and slower than 30 s on CPU-only systems.

### 3. KoAlpaca-Polyglot-5.8B

License check:
- Polyglot/KoAlpaca search results identify Apache-2.0, commercial-friendly.

Execution:
- Not downloaded in this run. It is older, larger than 3B candidates, and expected to be slower/lower quality than newer Korean Llama/Qwen-family models.

Decision:
- Fallback candidate only if license simplicity matters more than speed/quality.

### 4. Additional practical candidate: Korean-Bllossom-3B Q4_K_M

Ollama model:
`hf.co/QuantFactory/llama-3.2-Korean-Bllossom-3B-GGUF:Q4_K_M`

Download/run result:
- Download: success
- Installed size: 2.0 GB
- Command: `ollama run hf.co/QuantFactory/llama-3.2-Korean-Bllossom-3B-GGUF:Q4_K_M --format json --verbose < local-engine/benchmark/coverage_llm_prompt.txt`
- Total duration: 26.4821184s
- Load duration: 8.0892ms
- Prompt eval: 543 tokens / 84.195ms
- Eval: 485 tokens / 26.383301s / 18.38 tokens/s

Quality check:
- Correct: 일반암 부족, 유사암 부족, 뇌혈관 부족, 허혈성심장질환 부족, 질병입원일당 부족, 상해입원일당 부족.
- Incorrect: 운전자 벌금 20,000,000원 vs 권장 20,000,000원인데 `부족`으로 출력.
- Top recommendations hallucinated generic items (`정기적 검진`, `건강 관리`) instead of coverage items.

License:
- Search result/model card states commercial use is allowed; base Llama 3.2 license obligations still need final review.

Decision:
- Speed/package target is promising, but raw LLM should not be trusted for numeric status calculation.

## Baseline comparison: existing code

`local-engine/database/coverage.py` at bb0b331d does not call OpenAI in the inspected file. It already computes current amount, recommended amount, status, priority, and summaries deterministically from local DB/RAG data.

Implication:
- If production coverage analysis still takes 5+ minutes, the likely bottleneck is not GPT-4o in this file. It is more likely PDF/OCR/RAG extraction, repeated RAG searches, or another route not found by `OPENAI|gpt-4o|openai` repo search.
- Before Phase 2, benchmark the actual slow customer/PDF path with timestamps around parse, OCR, RAG indexing/search, and `coverage.analyze()`.

## Recommendation

For Phase 2 approval, I recommend this architecture instead of replacing analysis math with an LLM:

1. Keep deterministic local analyzer as authoritative:
   - amount matching
   - recommended amount
   - status
   - gap
   - priority

2. Add optional local LLM only for:
   - Korean natural-language explanation
   - customer-facing summary wording
   - ambiguous rider-name normalization suggestions with confidence, never final numbers

3. Local model default:
   - Do not use EXAONE unless a commercial license is obtained.
   - If 2 GB package limit is strict: test Korean-Bllossom-3B Q4_K_M further as summarizer only.
   - If quality is more important than size: validate Llama-3-Open-Ko-8B after resolving sharded GGUF import.

4. Add guardrail:
   - Always recompute `status` from numeric `current_amount` and `recommended_amount` in Python after any LLM response.
   - Reject or repair LLM JSON when amount/status disagree.

## Artifacts

Prompt:
`local-engine/benchmark/coverage_llm_prompt.txt`

Outputs:
`local-engine/benchmark/exaone35_24b_bf16_output_json_verbose.txt`
`local-engine/benchmark/bllossom3b_output_json_verbose.txt`
`local-engine/benchmark/bllossom3b_output.txt`
`local-engine/benchmark/qwen25_7b_output_json_verbose.txt`
`local-engine/benchmark/qwen25_3b_output.txt`
