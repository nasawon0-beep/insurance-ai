import assert from "node:assert/strict";
import test from "node:test";

import { computeMergeRows, memoAppend } from "../desktop/src/mergeDiff.ts";

test("computeMergeRows returns only changed, non-empty fields", () => {
  assert.deepEqual(
    computeMergeRows({ phone: " 010-3333-4444 ", email: "", name: "홍길동" }, { phone: "010-1111-2222", email: "old@example.com", name: "홍길동" }),
    [{ key: "phone", label: "전화", before: "010-1111-2222", after: "010-3333-4444" }],
  );
});

test("computeMergeRows handles missing existing fields and multiple changes", () => {
  assert.deepEqual(
    computeMergeRows({ address: "서울", occupation: "설계사" }, { occupation: "교사" }),
    [
      { key: "address", label: "주소", before: "", after: "서울" },
      { key: "occupation", label: "직업", before: "교사", after: "설계사" },
    ],
  );
});

test("computeMergeRows ignores undefined and whitespace-only new values", () => {
  assert.deepEqual(
    computeMergeRows({ name: "   " }, { name: "홍길동", phone: "010-1", email: "a@b.c" }),
    [],
  );
});

test("computeMergeRows stringifies non-string existing values before comparing", () => {
  assert.deepEqual(
    computeMergeRows({ phone: "010", occupation: "true" }, { phone: 10 as unknown as string, occupation: true as unknown as string }),
    [{ key: "phone", label: "전화", before: "10", after: "010" }],
  );
});

test("memoAppend creates a dated memo when no previous memo exists", () => {
  assert.equal(memoAppend(" 내용 ", undefined, "2026-09-04"), "[2026-09-04] 내용");
});

test("memoAppend skips blank and already included content", () => {
  assert.equal(memoAppend("  ", "기존", "2026-09-04"), null);
  assert.equal(memoAppend("내용", "기존 내용 포함", "2026-09-04"), null);
});

test("memoAppend appends to existing memo", () => {
  assert.equal(memoAppend("새", "기존", "2026-09-04"), "기존\n[2026-09-04] 새");
});
