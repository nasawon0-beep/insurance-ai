import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { responseFromNativeResult } from "../desktop/src/nativeResponse.ts";
import { withoutCustomerId } from "../desktop/src/customerListState.ts";

const root = new URL("../", import.meta.url);
const read = (path: string) => readFileSync(new URL(path, root), "utf8");

test("native bridge uses null bodies for Fetch null-body statuses", async () => {
  for (const status of [204, 205, 304]) {
    const response = responseFromNativeResult({ status, body: [], headers: [] });
    assert.equal(response.status, status);
    assert.equal(await response.text(), "");
  }
  const ok = responseFromNativeResult({
    status: 200,
    body: Array.from(new TextEncoder().encode('{"ok":true}')),
    headers: [["content-type", "application/json"]],
  });
  assert.deepEqual(await ok.json(), { ok: true });
  const error = responseFromNativeResult({
    status: 409,
    body: Array.from(new TextEncoder().encode("error")),
    headers: [],
  });
  assert.equal(await error.text(), "error");
});

test("successful deletion removes only the deleted customer from list state", () => {
  const rows = [{ id: "deleted", name: "삭제대상" }, { id: "next", name: "다른고객" }];
  assert.deepEqual(withoutCustomerId(rows, "deleted"), [{ id: "next", name: "다른고객" }]);
  assert.deepEqual(withoutCustomerId(withoutCustomerId(rows, "deleted"), "deleted"), [{ id: "next", name: "다른고객" }]);
});

test("customer delete invalidates stale list/detail work and preserves a newer selection", () => {
  const customers = read("desktop/src/Customers.tsx");
  const deletion = customers.slice(customers.indexOf("const deleteCustomer"), customers.indexOf("const wrap"));
  assert.match(deletion, /const deletedId = selectedId/);
  assert.match(deletion, /if \(deletingCustomerIdRef\.current === deletedId\) return/);
  assert.match(deletion, /deletedCustomerIdsRef\.current\.add\(deletedId\)/);
  assert.match(deletion, /listAbortRef\.current\?\.abort\(\)/);
  assert.match(deletion, /listGenRef\.current\+\+/);
  assert.match(deletion, /setList\(\(current\) => withoutCustomerId\(current, deletedId\)\)/);
  assert.match(deletion, /if \(selectedIdRef\.current === deletedId\)/);
  assert.doesNotMatch(deletion, /if \(selectedIdRef\.current !== deletedId\) return/);
  assert.match(customers, /customers\.customers\.filter\(\(customer: Customer\) => !deletedCustomerIdsRef\.current\.has\(customer\.id\)\)/);
  assert.match(customers, /expiring_policies:[\s\S]*deletedCustomerIdsRef\.current\.has\(row\.customer_id\)/);
  assert.match(customers, /birthdays:[\s\S]*deletedCustomerIdsRef\.current\.has\(row\.id\)/);
});

test("customer detail header is non-sticky and ledger has balanced fixed columns", () => {
  const customers = read("desktop/src/Customers.tsx");
  const css = read("desktop/src/styles/navy.css");
  const header = customers.slice(customers.indexOf('className="customer-detail-header"'), customers.indexOf('role="tabpanel"'));
  assert.doesNotMatch(header, /position:\s*"sticky"|top:\s*40/);
  assert.match(css, /\.customer-detail-header \{ position: relative/);
  assert.match(customers, /className="coverage-ledger-table"[\s\S]*<colgroup>/);
  assert.match(css, /\.coverage-ledger-table \{ min-width: 1380px; table-layout: fixed; \}/);
  assert.match(css, /\.coverage-ledger-status,.coverage-ledger-period \{ white-space: nowrap; \}/);
  assert.match(css, /\.coverage-ledger-amount \{ text-align: right;/);
});
