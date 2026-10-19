import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import fs from "node:fs";

function visibleText(html) {
  return html
    .replace(/<style[\s\S]*?<\/style>/g, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&[^;]+;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

test("shared coverage UI hides internal IDs and preserves three-level details", async () => {
  const vite = await createServer({ server: { middlewareMode: true }, appType: "custom", logLevel: "silent" });
  try {
    const { default: CoverageSourceSnapshot } = await vite.ssrLoadModule("/src/CoverageSourceSnapshot.tsx");
    const analysis = {
      run_id: "run-p0-same",
      categories: [{
        category_group: "암",
        items: [{
          id: "cov-p0-item",
          coverage_name: "특정암 진단비",
          status: "부족",
          current_amount: 60_000_000,
          amount_status: "known",
          amount_source_kind: "observed_summary",
          observed_source_page: 5,
          calculated_current_amount: 50_000_000,
          calculated_amount_status: "known",
          source_difference: "원본 60000000원 / 자체계산 50000000원",
          matched_policy_coverage_ids: ["detail-a", "detail-b"],
        }, {
          id: "cov-unknown",
          coverage_name: "금액 미확인 담보",
          status: "미가입",
          current_amount: 0,
          amount_status: "unknown_dash",
          matched_policy_coverage_ids: [],
        }],
      }],
      unlinked_policy_coverages: [{
        id: "detail-unclassified",
        rider_name: "정규화 미분류",
        rider_name_raw: "원문 미분류 회사담보명 전체",
        standard_name: "기타",
        standard_name_raw: "기타",
        amount: null,
        amount_text: "-",
        amount_status: "unknown_dash",
        source_page: 2,
      }],
    };
    const ledger = [
      { id: "detail-a", insurer: "테스트보험", product_name: "상품 A", rider_name: "정규화 특정암 A", rider_name_raw: "원문 특정암 A 회사담보명", standard_name: "특정암 진단비", standard_name_raw: "특정암진단", amount: 50_000_000, amount_status: "known" },
      { id: "detail-b", insurer: "테스트보험", product_name: "상품 B", rider_name: "특정암 B", amount: 10_000_000, amount_status: "known" },
    ];
    const customerHtml = renderToStaticMarkup(React.createElement(CoverageSourceSnapshot, { analysis, policyCoverages: ledger }));
    const independentHtml = renderToStaticMarkup(React.createElement(CoverageSourceSnapshot, { analysis, policyCoverages: ledger }));

    for (const html of [customerHtml, independentHtml]) {
      assert.match(html, /data-run-id="run-p0-same"/);
      assert.match(html, /data-item-id="cov-p0-item"/);
      assert.match(html, /data-detail-id="detail-a"/);
      assert.match(html, /data-detail-id="detail-b"/);
      assert.match(html, /data-detail-id="detail-unclassified"/);
      assert.match(html, /<h3>암<\/h3>/);
      assert.match(html, /특정암 진단비/);
      assert.match(html, /상품 A/);
      assert.match(html, /상품 B/);
      assert.match(html, /5,000만원/);
      assert.match(html, /1,000만원/);
      assert.match(html, /금액 확인필요/);
      assert.match(html, /원본 요약 · 5쪽/);
      assert.match(html, /계약담보 자체계산: 5,000만원 · 원본과 다름/);
      assert.match(html, /원문 특정암 A 회사담보명/);
      assert.match(html, /표준분류: 특정암 진단비 \(검증 원문: 특정암진단\)/);
      assert.match(html, /표준담보 미연결 원장 \(1\)/);
      assert.match(html, /원문 미분류 회사담보명 전체/);
      assert.match(html, />가입</);

      const text = visibleText(html);
      assert.doesNotMatch(text, /run-p0-same|cov-p0-item|detail-a|detail-b|detail-unclassified/);
      assert.doesNotMatch(text, /전용 결과|분석 run ID|item ID|상세 담보 ID|부족|충분/);
    }
    assert.equal(customerHtml, independentHtml);
  } finally {
    await vite.close();
  }
});

test("shared coverage UI exposes page31 review provenance and unresolved occurrences without internal IDs", async () => {
  const vite = await createServer({ server: { middlewareMode: true }, appType: "custom", logLevel: "silent" });
  try {
    const { default: CoverageSourceSnapshot } = await vite.ssrLoadModule("/src/CoverageSourceSnapshot.tsx");
    const reviewRows = [20, 24, 25, 27, 31].map((row) => ({
      id: `internal-ledger-${row}`,
      rider_name_raw: `회사담보 ${row}`,
      standard_name_extracted_raw: `섞인 셀 ${row}`,
      standard_name_raw: row === 20 ? "검증원문" : null,
      standard_name_verification: "needs_review",
      review_status: "needs_review",
      source_link_status: row === 20 ? "source_exact" : "none",
      source_page: 31,
      amount: row * 10_000,
      amount_status: "known",
    }));
    const analysis = {
      run_id: "internal-run-review",
      categories: [],
      unlinked_policy_coverages: reviewRows.slice(1),
      review_required_policy_coverages: reviewRows,
      unresolved_source_link_occurrences: [{
        id: "internal-occurrence-id",
        source_page: 22,
        source_table: 1,
        source_row: 7,
        source_standard_name_raw: "질병수술",
        insurer: "테스트보험",
        product_name: "테스트상품",
        rider_name: "원본 상세담보",
        amount: 1_000_000,
        amount_text: "100만",
        resolution_status: "unresolved",
        resolution_reason: "candidate_multiple",
        candidate_policy_coverage_ids: ["internal-ledger-20"],
      }],
    };
    const html = renderToStaticMarkup(React.createElement(CoverageSourceSnapshot, {
      analysis,
      policyCoverages: reviewRows,
      onReviewOccurrence: async () => {},
    }));
    assert.match(html, /검수 필요 원장 \(5\)/);
    assert.match(html, /미연결 여부와 독립적으로/);
    assert.match(html, /신정원 원본분류 geometry 검증 불확실 · 원본 31쪽/);
    assert.match(html, /셀 추출: 섞인 셀 20 · 검증 원문: 검증원문/);
    assert.match(html, /원본 상세연결 검수 \(1\)/);
    assert.match(html, /원본 22쪽 · 표 2 · 행 8 · 사유: candidate_multiple · 후보 1건/);
    assert.match(html, /선택 연결/);
    assert.match(html, /보류/);
    assert.doesNotMatch(visibleText(html), /internal-run-review|internal-ledger-20|internal-occurrence-id/);
  } finally {
    await vite.close();
  }
});

test("restored category order and filtering match the historical tabs", async () => {
  const vite = await createServer({ server: { middlewareMode: true }, appType: "custom", logLevel: "silent" });
  try {
    const { COVERAGE_CATEGORY_TABS, filterCoverageCategories } = await vite.ssrLoadModule("/src/CoverageSourceSnapshot.tsx");
    assert.deepEqual([...COVERAGE_CATEGORY_TABS], [
      "전체",
      "사망·후유장해",
      "암",
      "뇌혈관질환",
      "심장질환",
      "치매",
      "실손의료비",
      "수술비",
      "입원비(일당)·치료비",
      "운전자·법률배상·기타",
      "기타",
    ]);
    const categories = [
      { category_group: "사망", items: [{ id: "death" }] },
      { category_group: "후유장해", items: [{ id: "disability" }] },
      { category_group: "암", items: [{ id: "cancer" }] },
      { category_group: "뇌혈관질환", items: [{ id: "brain" }] },
      { category_group: "심장질환", items: [{ id: "heart" }] },
      { category_group: "분류 밖 원문", items: [{ id: "other" }] },
    ];
    assert.deepEqual(filterCoverageCategories(categories, "전체"), categories);
    assert.deepEqual(filterCoverageCategories(categories, "사망·후유장해").map((row) => row.category_group), ["사망", "후유장해"]);
    assert.deepEqual(filterCoverageCategories(categories, "암").map((row) => row.category_group), ["암"]);
    assert.deepEqual(filterCoverageCategories(categories, "뇌혈관질환").map((row) => row.category_group), ["뇌혈관질환"]);
    assert.deepEqual(filterCoverageCategories(categories, "심장질환").map((row) => row.category_group), ["심장질환"]);
    assert.deepEqual(filterCoverageCategories(categories, "기타").map((row) => row.category_group), ["분류 밖 원문"]);
  } finally {
    await vite.close();
  }
});

test("coverage screens render one shared user view without diagnostic headings", () => {
  const independent = fs.readFileSync(new URL("../src/CoverageAnalysis.tsx", import.meta.url), "utf8");
  const customer = fs.readFileSync(new URL("../src/Customers.tsx", import.meta.url), "utf8");
  assert.equal((independent.match(/<CoverageSourceSnapshot\b/g) ?? []).length, 1);
  assert.equal((customer.match(/<CoverageSourceSnapshot\b/g) ?? []).length, 1);
  assert.doesNotMatch(independent, /독립 보장분석 전용 결과|coverage-analysis-panel/);
  assert.doesNotMatch(customer, /고객 상세 전용 보장분석 결과|· run \$\{/);
  assert.match(customer, /보장분석 연결·갱신/);
  assert.match(customer, /refreshed\.run_id !== result\?\.run_id/);
  assert.match(independent, /key=\{data\.run_id\}/);
  assert.match(customer, /key=\{analysis\.run_id\}/);
});

test("coverage UI CSS keeps status and Korean amounts readable at desktop and narrow widths", () => {
  const css = fs.readFileSync(new URL("../src/styles/coverage.css", import.meta.url), "utf8");
  assert.match(css, /\.coverage-user-item\s*>\s*summary[\s\S]*grid-template-columns/);
  assert.match(css, /\.coverage-user-item-amount,[\s\S]*white-space:\s*nowrap/);
  assert.match(css, /\.coverage-badge[\s\S]*white-space:\s*nowrap/);
  assert.match(css, /\.coverage-category-tabs[\s\S]*flex-wrap:\s*wrap/);
  assert.match(css, /@media \(max-width:\s*640px\)[\s\S]*\.coverage-category-tabs[\s\S]*flex-wrap:\s*nowrap/);
  assert.match(css, /@media \(max-width:\s*640px\)[\s\S]*\.coverage-user-item\s*>\s*summary/);
});
