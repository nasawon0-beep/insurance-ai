# Reviewed Windows workspace transitions

This supplements account-isolation-maintenance.md. Nothing here authorizes a hosted deployment, production-data inspection, automatic ownership assignment, or plaintext/portable recovery.

## Two explicit plan modes

The existing migrate/rollback/restore_backup operation approval remains bound to the entire plan SHA256, immutable issuer/subject, installation, nonce and epoch. A new optional `replacement` object is part of that binding, not an unsigned switch. Old plans without replacement still refuse an existing target.

A reviewer can construct an offline candidate with `account_migration.plan_workspace_transition(source, installation, files, principal, mode=...)`. A Python VerifiedPrincipal in this helper is NOT authentication authority. Execution still requires native current verified owner, separate human review, signed server approval, confirmation, worker online identity verification, protected backup-key vault and lock handoff. Review only authorized data in an offline/quiescent profile. Do not use this helper to inspect an operating user's files without approval.

- empty-bootstrap: shared legacy source stays separate. The exact destination file hashes and directory inventory are reviewed. Every current CRM/RAG/backup table must contain no rows; triggers/views, WAL/SHM/journal, unknown files, attachments and mobile credentials reject. A business key, if present, must be valid DPAPI for this owner. Only the exact known engine-secret paths and format are classified as restart-generated runtime files. Nothing is deleted.
- same-owner-format: source must be this verified owner's exact account directory, never another owner/root. The complete supported file inventory must be included, with no remapping or silently omitted business files. The existing key must validate through DPAPI ownership or authenticated CRM ciphertext/current account-bound backup seal. No substitute key is created. CRM ciphertext and existing sync rows retain their key/owner; RAG conversion and DPAPI wrapping occur only in staging.

## Startup backups are not ignored

The engine creates a startup backup on a normally initialized account. Its presence therefore cannot be treated as an unknown file unconditionally. The existing encrypted v1 account-database-backup manifest must authenticate the exact owner, existing key and snapshot SHA256. In empty-bootstrap mode every table in each authenticated backup must also be empty; an empty live DB with a populated old backup is NOT an empty workspace. Unbound, malformed, foreign-owner and digest-mismatched backups reject. Normal named account-bound backups may be copied byte-for-byte during same-owner-format; their seals are rechecked on staging with the unchanged business key.

Unknown/safety backup naming, unbound legacy backup ownership, mobile credential migration, unknown application files, and a base64 key with no authenticated evidence require separate policy review. This implementation does not invent that policy or silently drop those files.

## Transaction and recovery

The existing app.lock/runtime lock/job lock and native owned-process shutdown remain. All snapshots are rechecked under the transaction lock. An encrypted backup contains approved source bytes and the previous target bytes before promotion. Existing source and previous target are retained; `.account-migrations/<binding>/previous-workspace` is an atomic directory rename, not deletion. The final staging→target rename is atomic on the installation volume. The pair of renames is not a single filesystem transaction: a durable journal and per-account transition marker cover the gap.

Normal native bootstrap refuses a pending marker; maintenance-only login remains available for reviewed retry/rollback. Recovery validates the archived original when the original account path has already been converted or is temporarily absent. Rollback moves the candidate into its own archive and restores the original. Changes to active files or directories after promotion block rollback without discarding new work. A pre-promotion conflict is rejected before setting a new startup-blocking marker. Existing journals without replacement retain their former semantics.

Keep source, job, encrypted backup, journal, archives and protected native key custody. Do not remove a marker, delete a target, regenerate a business/backup key, or use a new plan to evade an interrupted transaction. Retry requires the same unchanged plan and still-valid approval, or a renewed explicit approval for that binding/operation. Missing source/authority/key or unexplained collisions remain fail-closed. restore_backup still restores into quarantine, not over active data.

Tests exercise Windows fresh processes terminated by os._exit at backup/staging/archive/promotion/commit, rollback rename exceptions and journal-before-marker interruption. These are process termination/exception tests, not power-loss qualification. DPAPI does not guarantee recovery after Windows-profile protection loss.

## Release CI and residual gates

release.yml builds shipping and separately packaged QA workers before native checks and exports the explicit fixture interpreter/path. It reads VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS and VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS from repository variables, and connects TAURI_SIGNING_PRIVATE_KEY_PASSWORD by secret name only. Missing approved workspace keys still fail build-moadoom-live.mjs. QA workers must never be bundled/uploaded as product resources. Public maps must match the actually deployed custody keys; never create local stand-ins.

Local source/packaged fixture tests are not real Auth, GUI, installed update, CI runner, or release-signature acceptance. Those gates and legacy business-fixture CI wiring must be verified before teamlead push/tag/deployment.
