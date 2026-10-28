import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  buildFamilyPayload,
  eligibleSharedChildren,
  saveRegistrationFamilyDrafts,
  saveFamilyRelationship,
  type FamilyRegistrationDraft,
  type FamilyRelationship,
} from "../desktop/src/familyRelationshipModel.ts";

const spouse: FamilyRelationship = {
  id: "edge-ab",
  relationship: "SPOUSE",
  relationship_label: "배우자",
  other_description: null,
  customer: { id: "B", name: "B고객", phone: "010-0000-0202", birth_date: "1986-01-01" },
  created_at: "synthetic",
  updated_at: "synthetic",
};
const child: FamilyRelationship = {
  id: "edge-ac",
  relationship: "CHILD",
  relationship_label: "자녀",
  other_description: null,
  customer: { id: "C", name: "C고객", phone: "010-0000-0303", birth_date: "2014-01-01" },
  created_at: "synthetic",
  updated_at: "synthetic",
};

test("child payload includes only an explicitly selected spouse as co-parent", () => {
  assert.deepEqual(buildFamilyPayload({
    relatedCustomerId: "C",
    relationship: "CHILD",
    otherDescription: "",
    coParentCustomerId: "B",
    sharedChildIds: [],
  }), {
    related_customer_id: "C",
    relationship: "CHILD",
    co_parent_customer_id: "B",
  });
  assert.deepEqual(buildFamilyPayload({
    relatedCustomerId: "C",
    relationship: "CHILD",
    otherDescription: "",
    coParentCustomerId: "",
    sharedChildIds: [],
  }), {
    related_customer_id: "C",
    relationship: "CHILD",
  });
});

test("late spouse payload shares only checked existing children", () => {
  assert.deepEqual(buildFamilyPayload({
    relatedCustomerId: "B",
    relationship: "SPOUSE",
    otherDescription: "",
    coParentCustomerId: "",
    sharedChildIds: ["C"],
  }), {
    related_customer_id: "B",
    relationship: "SPOUSE",
    shared_child_ids: ["C"],
  });
  assert.deepEqual(eligibleSharedChildren([spouse, child]).map((row) => row.customer.id), ["C"]);
});

test("other relation keeps trimmed safe description", () => {
  assert.deepEqual(buildFamilyPayload({
    relatedCustomerId: "D",
    relationship: "OTHER",
    otherDescription: "  법정대리인  ",
    coParentCustomerId: "",
    sharedChildIds: [],
  }), {
    related_customer_id: "D",
    relationship: "OTHER",
    other_description: "법정대리인",
  });
});

test("network failure preserves the reopened relationship list", async () => {
  const existing = [spouse];
  const api = async () => { throw new Error("합성 네트워크 실패"); };
  await assert.rejects(
    saveFamilyRelationship(api, "A", {
      relatedCustomerId: "C", relationship: "CHILD", otherDescription: "",
      coParentCustomerId: "", sharedChildIds: [],
    }, existing),
    /합성 네트워크 실패/,
  );
  assert.deepEqual(existing, [spouse]);
});

test("new-customer family drafts save once as an atomic retry-safe batch", async () => {
  const calls: Array<{ path: string; init?: RequestInit }> = [];
  const api = async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    return { family_relationships: [spouse, child], idempotent_replay: false };
  };
  const drafts: FamilyRegistrationDraft[] = [
    {
      customer: spouse.customer,
      form: { relatedCustomerId: "B", relationship: "SPOUSE", otherDescription: "", coParentCustomerId: "", sharedChildIds: [] },
    },
    {
      customer: child.customer,
      form: { relatedCustomerId: "C", relationship: "CHILD", otherDescription: "", coParentCustomerId: "B", sharedChildIds: [] },
    },
  ];
  const result = await saveRegistrationFamilyDrafts(
    api,
    "new-A",
    drafts,
    "family-registration:fixed-test-key",
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, "/customers/new-A/family-relationships/batch");
  assert.equal(calls[0].init?.method, "POST");
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
    idempotency_key: "family-registration:fixed-test-key",
    relationships: [
      { related_customer_id: "B", relationship: "SPOUSE" },
      { related_customer_id: "C", relationship: "CHILD", co_parent_customer_id: "B" },
    ],
  });
  assert.equal(result.length, 2);
});

test("new customer screen exposes optional family selection and relation-only retry", () => {
  const customers = fs.readFileSync(new URL("../desktop/src/Customers.tsx", import.meta.url), "utf8");
  const registration = fs.readFileSync(new URL("../desktop/src/NewCustomerFamilyRelationships.tsx", import.meta.url), "utf8");
  assert.match(customers, /mode === "new"[\s\S]*NewCustomerFamilyRelationships/);
  assert.match(customers, /고객은 등록됐지만 가족 관계 저장에 실패했습니다/);
  assert.match(customers, /가족 관계만 다시 저장/);
  assert.match(customers, /saveCustomerInFlightRef\.current/);
  assert.match(customers, /disabled=\{savingCustomer\}/);
  assert.match(customers, /registrationFamilyBatchKey/);
  assert.match(customers, /createFamilyBatchIdempotencyKey/);
  assert.match(registration, /기존 고객 검색/);
  assert.match(registration, /현재 등록할 고객 기준 관계/);
  assert.match(registration, /공동 자녀 연결/);
  assert.match(registration, /선택 항목이며 입력하지 않아도 고객을 등록할 수 있습니다/);
});

test("PC component follows B layout and real API paths", () => {
  const source = fs.readFileSync(new URL("../desktop/src/FamilyRelationships.tsx", import.meta.url), "utf8");
  assert.match(source, /가족 연결/);
  assert.match(source, /선택/);
  assert.match(source, /family-relationships/);
  assert.match(source, /관계만 해제/);
  assert.match(source, /고객과 계약·상담·분석 자료는 유지/);
  assert.match(source, /배우자.*자녀로도 연결/s);
  assert.match(source, /기존 자녀.*함께 연결/s);
  assert.match(source, /onOpenCustomer/);
});

test("customer detail integrates family section and back navigation without a new menu", () => {
  const customers = fs.readFileSync(new URL("../desktop/src/Customers.tsx", import.meta.url), "utf8");
  const app = fs.readFileSync(new URL("../desktop/src/App.tsx", import.meta.url), "utf8");
  assert.match(customers, /FamilyRelationships/);
  assert.match(customers, /familyBackTarget/);
  assert.match(customers, /원래 고객으로 돌아가기/);
  assert.doesNotMatch(app, /tab\([^\n]*family/i);
});
