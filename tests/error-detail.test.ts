import assert from "node:assert/strict";
import test from "node:test";

import { formatErrorDetail } from "../desktop/src/errorDetail.ts";

test("array detail is formatted with Korean field names and messages", () => {
  assert.equal(
    formatErrorDetail([
      { loc: ["body", "name"], msg: "Field required", type: "missing" },
      { loc: ["body", "unknown_field"], msg: "Invalid value", type: "value_error" },
    ]),
    "이름: Field required / unknown_field: Invalid value",
  );
});

test("string and null details retain fallback behavior", () => {
  assert.equal(formatErrorDetail("요청 오류"), "요청 오류");
  assert.equal(formatErrorDetail(null), null);
});

test("object detail uses string message fields without object coercion", () => {
  assert.equal(formatErrorDetail({ message: "메시지 오류" }), "메시지 오류");
  assert.equal(formatErrorDetail({ msg: "검증 오류" }), "검증 오류");
  assert.equal(formatErrorDetail({ code: "bad_request" }), null);
});

test("undefined and empty arrays use the HTTP fallback signal", () => {
  assert.equal(formatErrorDetail(undefined), null);
  assert.equal(formatErrorDetail([]), null);
});

test("query locations are formatted like body locations", () => {
  assert.equal(
    formatErrorDetail([{ loc: ["query", "phone"], msg: "Invalid value" }]),
    "전화번호: Invalid value",
  );
});
