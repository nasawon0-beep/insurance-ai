import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import {
  canonicalJson,
  type SignedLicenseBlob,
  verifyLicenseForUser,
  verifyLicenseSignature,
} from "../desktop/src/licenseSignature.ts";

const secret = "dev-license-secret-change-me";
const body = {
  license: {
    status: "active",
    plan: "BASIC",
    nested: { z: true, a: "한글" },
    features: [true, false, null, "😀"],
    expiry: null,
    device_limit: 1,
    user_id: "user-1",
  },
  signed_at: 1_725_000_000,
};

test("canonical JSON matches Python json.dumps output", () => {
  const serverRaw =
    '{"license":{"device_limit":1,"expiry":null,"features":[true,false,null,"\\ud83d\\ude00"],"nested":{"a":"\\ud55c\\uae00","z":true},"plan":"BASIC","status":"active","user_id":"user-1"},"signed_at":1725000000}';
  assert.equal(canonicalJson(body), serverRaw);
});

function signedBlob(): SignedLicenseBlob {
  // control-server/security.py sign_license fixture for the body above.
  const signature = "12bff416d59da959fcd6260723d6eb61d4fdeff6e89932e4a7dd90507a5d44a1";
  return { ...body, signature };
}

function sign(blobBody: Omit<SignedLicenseBlob, "signature">): SignedLicenseBlob {
  return {
    ...blobBody,
    signature: createHmac("sha256", secret).update(canonicalJson(blobBody)).digest("hex"),
  };
}

test("valid server-style license signature passes", async () => {
  assert.equal(await verifyLicenseSignature(signedBlob(), secret), true);
});

test("tampering with the plan invalidates the signature", async () => {
  const blob = signedBlob();
  blob.license = { ...(blob.license as object), plan: "PRO" };
  assert.equal(await verifyLicenseSignature(blob, secret), false);
});

test("signature truncation, omission, null, and numeric types are rejected", async () => {
  const valid = signedBlob();
  const invalidSignatures: unknown[] = [valid.signature.slice(0, -1), undefined, null, 123];
  for (const signature of invalidSignatures) {
    assert.equal(
      await verifyLicenseSignature({ ...valid, signature } as SignedLicenseBlob, secret),
      false,
    );
  }
});

test("tampering with signed_at or device_limit invalidates the signature", async () => {
  const changedTime = signedBlob();
  changedTime.signed_at += 1;
  assert.equal(await verifyLicenseSignature(changedTime, secret), false);

  const changedLimit = signedBlob();
  changedLimit.license = { ...(changedLimit.license as object), device_limit: 2 };
  assert.equal(await verifyLicenseSignature(changedLimit, secret), false);
});

test("WebCrypto failure is fail-closed", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: { subtle: { importKey: async () => { throw new Error("unavailable"); } } },
  });
  try {
    assert.equal(await verifyLicenseSignature(signedBlob(), secret), false);
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "crypto", descriptor);
    else delete (globalThis as { crypto?: unknown }).crypto;
  }
});

test("offline license for another user is rejected", async () => {
  assert.equal(await verifyLicenseForUser(signedBlob(), secret, "user-2"), false);
});

test("missing or invalid user IDs are rejected", async () => {
  const missingLicense = { ...(body.license as Record<string, unknown>) };
  delete missingLicense.user_id;
  const missing = sign({ license: missingLicense, signed_at: body.signed_at });
  const numeric = sign({
    license: { ...(body.license as object), user_id: 1 },
    signed_at: body.signed_at,
  });
  assert.equal(await verifyLicenseForUser(missing, secret, "user-1"), false);
  assert.equal(await verifyLicenseForUser(numeric, secret, "user-1"), false);
  assert.equal(await verifyLicenseForUser(signedBlob(), secret, null), false);
  assert.equal(await verifyLicenseForUser(signedBlob(), secret, 1), false);
});
