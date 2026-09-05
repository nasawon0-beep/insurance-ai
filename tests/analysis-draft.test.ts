import assert from "node:assert/strict";
import test from "node:test";

import { hasPendingDraft } from "../desktop/src/analysisDraft.ts";

test("returns false when every analysis draft is empty", () => {
  assert.equal(hasPendingDraft([], [], null), false);
});

test("returns true when any analysis draft exists", () => {
  assert.equal(hasPendingDraft([{}], [], null), true);
  assert.equal(hasPendingDraft([], [{}], null), true);
  assert.equal(hasPendingDraft([], [], {}), true);
});
