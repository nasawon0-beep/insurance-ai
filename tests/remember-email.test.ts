import assert from "node:assert/strict";
import test from "node:test";

import {
  REMEMBER_EMAIL_KEY,
  loadRememberedEmail,
  saveRememberedEmail,
} from "../desktop/src/rememberEmail.ts";

class MemoryStorage {
  values = new Map<string, string>();
  getItem(key: string): string | null {
    return this.values.has(key) ? (this.values.get(key) as string) : null;
  }
  setItem(key: string, value: string): void {
    this.values.set(key, String(value));
  }
  removeItem(key: string): void {
    this.values.delete(key);
  }
}

function freshStorage(): MemoryStorage {
  const store = new MemoryStorage();
  (globalThis as { localStorage?: unknown }).localStorage = store;
  return store;
}

test("no stored value → loads as empty string", () => {
  freshStorage();
  assert.equal(loadRememberedEmail(), "");
});

test("remember=true stores the email and it restores", () => {
  const store = freshStorage();
  saveRememberedEmail("agent@example.com", true);
  assert.equal(store.getItem(REMEMBER_EMAIL_KEY), "agent@example.com");
  assert.equal(loadRememberedEmail(), "agent@example.com");
});

test("remember=false clears a previously stored email", () => {
  const store = freshStorage();
  saveRememberedEmail("agent@example.com", true);
  saveRememberedEmail("agent@example.com", false);
  assert.equal(store.getItem(REMEMBER_EMAIL_KEY), null);
  assert.equal(loadRememberedEmail(), "");
});

test("empty email is never stored even with remember=true", () => {
  const store = freshStorage();
  saveRememberedEmail("", true);
  assert.equal(store.getItem(REMEMBER_EMAIL_KEY), null);
});

test("only the email key is written — nothing password-shaped", () => {
  const store = freshStorage();
  saveRememberedEmail("agent@example.com", true);
  assert.deepEqual([...store.values.keys()], [REMEMBER_EMAIL_KEY]);
});
