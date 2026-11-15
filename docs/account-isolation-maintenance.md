# Account-isolation maintenance and QA handoff

Status: implementation candidate, not approved for real-data migration or release.

## Migration API (offline maintenance, not a login endpoint)

`local-engine/account_migration.py` exposes:

- `plan_migration(legacy_root, installation_root, files)` — explicit source/destination manifest, hashes and installation/source binding. No discovery or automatic first-login ownership.
- `migrate(plan, verified_principal, owner_approval, backup_key)` — encrypted backup/readback, encrypted journal, staging, SQLite integrity check, removal of copied sync credentials/state, same-volume directory promotion. Retry is idempotent across recorded phases and promotion-before-journal interruption.
- `rollback(...)` — archive the unmodified promoted workspace, retaining the original legacy files and backup. Refuse rollback if promoted data has since changed; never silently discard subsequent work.
- `restore_backup(...)` — authenticated recovery into `recovered-original` quarantine, including when original source files are missing. Never overwrite a source or publish recovered data automatically.

Preconditions:

1. A trusted maintenance caller obtains the immutable UUID/issuer from real online verification. `VerifiedPrincipal` is a Python value object, not a signed credential.
2. Separately obtain human owner confirmation for the exact plan binding. `OwnerApproval` is an operator contract, not an HTTP authorization mechanism. Never deserialize an end-user request into trusted approval. This module is deliberately not exposed by any login/API route.
3. Stop the app and all business/mobile children; checkpoint source SQLite cleanly. The tool takes both normal and QA app-lock locations and its own operation lock. WAL/journal source files, existing destination workspaces, reparse points and path aliases cause refusal.
4. Supply and securely retain a separate backup encryption key. The migration module does not persist that key. Loss of both original data and backup key is not recoverable. Production key escrow/UI/verified-identity maintenance adapter still requires review.
5. Map only explicitly approved files to account `data/`, `config/`, `pdfs/`, `cache/`, `logs/`. Source customer key material must already be available in a reviewed file; keyring-only legacy extraction is not implemented. Do not invent a new key for old encrypted records.

Example mapping names (not execution authorization):
`data/customers.sqlite3 <- customers.sqlite3`, `config/customer-db.key <- customer-db.key`, `data/rag-index.sqlite3 <- rag-index.sqlite3`.

Original files are preserved. Normal operation does not read the unassigned legacy root. Existing account workspaces are never merged/overwritten. The source and destination snapshots are bound to one installation root and owner. SQLite connections are explicitly closed before Windows directory renames. Intermediate files are kept outside the promotable staging tree. Rollback/recovery quarantine is not a live account workspace.

Tests cover exceptions and actual child-process exit at backup/staging/promotion/committed checkpoints, a staging file-write interruption, tampered backups, key mismatch, missing originals, wrong owner, two installation paths, B preservation, Windows junction/alias rejection, app lock refusal, and preservation/decryption of a real synthetic encrypted customer DB/key.

Limits: process-crash recovery is tested, including bounded 64MiB synthetic payloads at four checkpoints; hardware power-loss/storage-controller guarantees and production maximum-size qualification are not. Backup encryption currently materializes the snapshot in memory. No real files/accounts/keys should be passed to this API in the present work phase.

## Independent maintenance gates (candidate, still NO-GO)

- `moadoom-maintenance/index.ts` verifies a fresh central Auth user and a native Ed25519 proof over explicit confirmation, immutable subject, installation, durable epoch, exact plan, operation, nonce and timestamp. The signer is server-only `MOADOOM_MAINTENANCE_KID` / `MOADOOM_MAINTENANCE_SIGNING_KEY`; no client signing key or public bootstrap exists.
- Candidate `202611120001_maintenance_reviews.sql` requires a separately privileged, independently reviewed record. Ordinary users cannot select/insert/update/delete the review table. No review-creation/self-promotion endpoint was added. The consume RPC checks caller ownership, active installation, epoch/plan/operation and single-use status under row locks. Human review provisioning is an authorized operator process, not a user assertion. No SQL/service deployment was performed.
- Approval v2 adds `installation_id/client_epoch/nonce/approval_id` to the previous scope and rejects v1. Python requires native context and explicit confirmation before online identity/approval checks, and only then requests a key.
- `maintenance_bridge.rs` implements pinned absolute executable/script checks, an owned child with kill-and-wait cleanup, cleared child environment, anonymous pipe frames, generic stdout status, discarded release stderr, and account-fence checks during writes/execution. Secret/key are never placed in argv/env/files or returned to WebView. `account_maintenance_worker.py` uses strict UTF-8 input/output; CRLF and LF status lines are supported. Windows tests actually exercise native protected key → pipe → real Python migration. Only central Auth transport in that cross-process fixture is synthetic.
- Integrated candidate: `maintenance_coordinator.rs` registers only status/prepare/execute commands, not raw frame/config/key helpers. It obtains the transition lease, binds a private preparation ticket to subject/issuer/live epoch, requests the separate server approval, verifies it, drains business/mobile work, and hands the app lock to the pinned packaged worker. `MaintenancePanel` requires explicit confirmation and shows unavailable-service and DPAPI-loss boundaries. This is implemented and locally exercised, not deployed Auth success.
- Normal login creates a destination workspace, so first migration must use the explicit **maintenance-only login** on `LoginScreen`. It still requires fresh Auth and signed workspace authority, but does not create account directories or start business engines. It does not call the normal App bootstrap. A pre-existing destination without this plan's recovery journal is refused before consuming an approval. Existing-data merge/replacement is NOT authorized or implemented.
- The selected handoff keeps the native UI process alive only as supervisor: business/control/mobile jobs are terminated and Windows Job Object active-process counts must reach zero, auxiliary mobile commands must drain, and no new starts/account switches are permitted. Only then is `app.lock` released. A separately held owner lock prevents another app from opening the workspace during handoff. The worker takes both production/QA app locks itself; after it exits, the native owner reacquires its lock. The user must restart after completion/failure; no automatic business resume.
- Logout invalidates the live epoch immediately and cancels the worker before durable logout proceeds through the transition lease. Closing/crashing the supervising app closes its kill-on-close worker job. The worker is created suspended, assigned to the job before its first instruction, then resumed, covering one-file bootloader children. A dead-owner independent-process test verifies that a replacement owner cannot acquire until the worker's app lock is released. This is not a detached worker continuing after full UI-process exit.
- `scripts/build_maintenance_worker.py` stages only four maintenance modules and the spec to avoid engine-directory module shadowing. It produces the shipping one-file EXE and source digest. `build.rs` rejects stale packaged source, embeds the worker SHA256, and Tauri bundles the exact resource. Runtime holds a deny-write/delete file handle over hash validation and execution. `--qa-fixture` builds a separately named, separately stored synthetic Auth wrapper; it is never a shipping resource. Shipping EXE denial and archive checks and a separately packaged synthetic Auth migration are distinct results.
- Process-kill regression uses 64MiB synthetic payloads at backup/staging/promotion/committed phases, then retries, rolls back and restores into quarantine while preserving source/other-account data. It is NOT a power-loss test or a production maximum-size qualification. Backup encryption still materializes data in memory.
- Portable recovery custody/retention is undecided. Current native protection is Windows CurrentUser DPAPI bound to installation root. No plaintext key export, new escrow policy, keyring extraction or real migration was introduced.

## Central E2E prerequisites

Need two pre-existing, email-confirmed ordinary central accounts (A/B), distinct immutable UUIDs, each with an active valid signed license, in an approved isolated test project. Neither app account should be owner/admin: privileged-owner entitlement behavior must not hide normal-member failures. A separate authorized test-project operator may prepare license states; no privileged credential belongs in the desktop or chat. No project/account creation is authorized here.

Public build configuration, identical for Vite and Rust builds:

- `VITE_MOADOOM_SUPABASE_URL`: approved `https://<project-ref>.supabase.co`.
- `VITE_MOADOOM_SUPABASE_PUBLISHABLE_KEY`: `sb_publishable_...`; never secret/service_role.
- `VITE_MOADOOM_LICENSE_PUBLIC_KEYS`: JSON `kid` to raw Ed25519 public-key base64, matching the test project's license signer.
- `VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS`: JSON `kid` to raw Ed25519 public-key base64 for the separate workspace signer; required by native verification.
- `VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS`: separate reviewed maintenance signer public keys; missing configuration keeps the UI blocked. No signing key belongs in this build.
- `VITE_MOADOOM_SITE_URL`: approved test account/reset site.
- `VITE_MOADOOM_SIGNUP_OPEN`: keep false for this test (existing accounts only).

The project's `moadoom-license` function and account/license schema must already be deployed by its owner. Do not copy operating access/refresh tokens. Login is performed by the authorized tester or an approved credential-vault UI flow; credentials must not enter arguments, reports or messages.

Safe run preparation:

1. Rebuild both Python sidecars from these modified sources and the debug Tauri app in an isolated build directory; old packaged sidecars do not contain new guards. No installation/update/publish step.
2. Use `scripts/create_qa_profile.py --root <new scratch root> --engine-port <checked-free nonproduction port> --control-port <different checked-free nonproduction port>` for each of two roots. The helper `--help` has been exercised. Use its emitted `launch_args` exactly; do not fabricate marker fields. APPDATA override alone is not isolation.
3. Retain QA TLS pin and instance/binding checks. CDP is debug-only through `INSURANCE_AI_QA_CDP_PORT` on official profiles. No firewall mutation or real-phone registration.
4. Verify process/profile identity before synthetic input. Test A→logout→B→logout→A and application restarts, late responses, native-child exit races, wrong/expired tokens, direct foreign IDs and backup/reset/restore. Compare operating paths only through configuration guards, not by opening operating data.
5. Record the actual binary/source revisions, QA marker identities, HTTP status assertions, state/cache behavior and child lifetimes without secrets. Frontend mock harnesses are not actual central-auth evidence.

## Signed workspace grant candidate (A selected; release NO-GO)

A has been selected and implemented in source. This is not a deployed service or actual central-login E2E result. The v2 license verifier and its five-day cache age policy have not been rewritten.

### Protocol and storage

- `POST /functions/v1/moadoom-workspace` uses the caller's JWT/publishable key, verifies Auth user/email confirmation and checks the entitlement subject. No service-role credential is used by the client or this handler.
- Installation identity is SHA-256 of a native-generated Ed25519 public key, not a PC name/email/hardware identifier. A proof binds issuer, immutable subject, installation, local epoch, request nonce, timestamp and issuance audience. Proof timestamps have a 300-second window.
- Candidate SQL `202611110001_workspace_grants.sql` stores only account/installation/authorization metadata. Caller scope comes from `auth.uid()`. Table access is revoked from public/anon/authenticated, including under Supabase-style default grants. RPC issuance serializes sequence allocation, rejects duplicate nonces and revoked installations, and bounds metadata growth. `issued_at` comes from the DB authorization decision, not a later signing time.
- Signed fields: `v/kid/iss/sub/aud/purpose/installation_id/client_epoch/nonce/jti/iat/nbf/exp/business_exp/revocation_generation/sequence`. Native verifies raw Ed25519 signature and every scope/time constraint. It does not trust decoded JWTs, localStorage, copied v2 licenses or UI subject selectors as workspace authority.
- Windows CurrentUser DPAPI protects the installation private key, active signed grant, epoch, observed-clock/sequence floors and internal migration backup keys. Entropy includes the canonical installation data root. Copying a grant or the protected state to another root is rejected. Reparse paths are refused. Writes flush before a same-volume write-through replacement.
- Logout first invalidates native requests and durably increments the local epoch, then stops business/mobile children. Persistence/termination failure is returned to the UI, not reported as successful logout. Keys/customer data are retained. A separate IPC generation prevents an old queued activation from adopting a newer session generation after logout.
- Engine requests use a fresh process-local random bearer, not an expired cloud access token. That bearer is memory-only in the renderer; Python receives its hash and a fixed deadline. Refreshing the same account need not interrupt the engine or extend its existing deadline in place. Mutation requests are not automatically replayed after 401.
- Python business/mobile deadline guards use monotonic elapsed time and a latched observed-time high-water check. Native persists observed time when verifying/resuming cached authority. Fresh nonce-bound online verification can recover from a corrected local clock.

### Confirmed billing/ownership separation

The latest approval preserves the existing banner-only subscription policy. The Edge handler no longer rejects ownership solely because subscription status is inactive/expired. `buildWorkspaceGrant` requires the authenticated immutable subject and installation proof, but does not use subscription status or end time as ownership authority. Both `exp` and the legacy-named `business_exp` now carry the same five-day OWNERSHIP deadline. Invalid Auth users, mismatched subjects, invalid proofs and revoked installations are still rejected. The v2 license verifier retains its independent exact-expiry/banner semantics. The grant and Edge fixtures exercise expired subscriptions, missing subscription end and billing suspension separately from Auth rejection/install revocation. Actual expired-user Tauri view/export E2E is still pending; test fixtures do not establish deployed behavior.

### Limits and maintenance authorization

- A disconnected machine cannot learn immediate server revocation. Offline use is bounded by the accepted grant/deadline; reconnect attempts normally revalidate within five minutes while making requests. Network failure can prevent that check. An in-flight grant uses the timestamp of its atomic issuance decision.
- This is app/account isolation, not protection from the same Windows user's arbitrary code execution, OS administrators, or restoration of an entire protected-state/clock snapshot. DPAPI is not a hardware monotonic counter. Moving a whole OS profile/state back in time is outside the claimed guarantee.
- Only Windows protected storage is implemented. Other platforms fail closed until an OS-protected backend is reviewed.
- `Store::migration_backup_key` is an internal native key vault, bound to issuer/subject/plan and guarded by signed workspace authority. It preserves keys through logout, refuses replacement by a different key and is not a WebView/HTTP command. It does not approve ownership or execute migration. `account_maintenance.py` now provides an UNEXPOSED adapter requiring fresh online identity plus a separately Ed25519-signed human ownership approval for the exact plan/operation, valid at most 300 seconds. No signer/service/endpoint was deployed. Approval provisioning and actual central E2E remain external gates; native coordinator/private key transport and packaged worker are implemented as described above. Portable recovery-key retention and keyring-only legacy extraction are intentionally not implemented. Do not expose raw `VerifiedPrincipal`/`OwnerApproval` as user input or automatically assign legacy files to the first login.
- Existing Supabase SDK session persistence is still WebView storage; the new DPAPI guarantee concerns native workspace authority and migration keys, not a claim that all pre-existing cloud credentials were migrated.

### Deployment and review order (not authorization to deploy)

1. Review the implemented banner-only billing/ownership separation and grant/clock/logout threat model; exercise expired-member view/export in actual Tauri before approval.
2. Review the candidate SQL/Edge function in an approved isolated project, including nonce/rate limits and revocation recovery. No live migration or deployment was performed here.
3. The authorized backend operator provisions a dedicated `MOADOOM_WORKSPACE_KID` and `MOADOOM_WORKSPACE_SIGNING_KEY` (PKCS8 Ed25519). Never obtain/print/copy operating signing keys for this work. Publish only raw public keys as `VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS`, alongside the existing public build configuration above. Keep valid retired public keys for the intended bounded validity interval.
4. Rebuild sidecars from the changed sources; review actual bundled paths and guard inclusion. Preserve all old data/keys. The protected backup-key helper alone does not make real-data migration approved.
5. Run two ordinary approved test accounts through two official QA roots: initial login, restart online/offline, A→logout→B→A, late IPC/responses, expiry/revocation, foreign bearer/IDs, PDF/RAG, backup/reset/restore and mobile boundaries. No credentials in command arguments/chat. The authorized tester logs in locally, or uses an approved vault UI flow.
6. Only after actual central Tauri E2E and team-lead review consider a separate release decision. No version bump, commit, push, installation or public-site change is part of this work.

### Candidate command templates

From `C:/build/moadoom-central-auth`, with approved PUBLIC configuration exported into the same shell for Vite and Rust:

```text
npm --prefix desktop run build
CARGO_TARGET_DIR=C:/build/central-tauri-target cargo build --offline --manifest-path desktop/src-tauri/Cargo.toml
python scripts/create_qa_profile.py --root <new-profile-scratch-root> --engine-port <checked-free-port> --control-port <different-checked-free-port>
npm --prefix desktop run tauri -- dev --no-watch -- -- <exact emitted launch_args>
```

Use the approved Python 3.11 executable for the profile helper. The `tauri dev --help` argument contract was checked; the final launch command is a preparation template, not a record of a successful app launch. Do not run the app without the official QA arguments. `APPDATA` override is insufficient. Do not use old packaged sidecars as evidence for new guards. Rust target directories must avoid Korean paths on this linker setup; test scratch roots should remain under the current profile scratch and may include Korean characters.

Set `TMP`, `TEMP`, and `TMPDIR` explicitly to the Windows-form profile scratch path for tests; git-bash may otherwise supply `/tmp`. For the isolated SQL test use the existing `C:/build/central-pg-venv/Scripts/python.exe`, `PYTHONUTF8=0`, and `supabase/tests/test_workspace_grants_pg.py`. Its scoped diagnostic decoder tolerates pgserver's mixed encoding; SQL execution, role checks and result decoding are real, not mocked. It creates a fresh loopback-only scratch cluster and stops it after testing.

