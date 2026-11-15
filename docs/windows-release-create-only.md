# Windows release gate: create-only, isolated regression selection

This policy applies to `.github/workflows/release.yml`, not the separate general build workflow.

## Release identity and collision policy

The release tag remains `v<VERSION>-windows`; a source trigger tag is `v<VERSION>`.
The workflow serializes the same version across dispatch/tag triggers with `cancel-in-progress: false`.
`create_release_only.py` requires the committed desktop version and exact `GITHUB_SHA`.
Only a well-formed explicit HTTP 404 from GitHub establishes release/tag absence.
The authenticated, paginated release inventory additionally rejects existing drafts (the tag endpoint alone is insufficient).
Any existing release or tag, including a draft, wrong target, or tag-only partial attempt, stops the run.
401/403/5xx, network/timeout, malformed replies and incomplete pagination stop before creation.

The script atomically creates a new tag reference, creates a new draft, then POSTs assets to that newly returned release ID.
API conflicts stop immediately. No release edit, asset clobber, DELETE, adoption, retry or automatic cleanup exists.
A partial attempt may leave its newly created tag/draft/assets; a retry intentionally fails closed.
Do not delete or overwrite it automatically: escalate for a separately authorized operator decision or a new version.
Offline tests intercept GH transport; they do not authorize or perform remote publication.
Real signed installation/business/update acceptance remains a separate public-release gate.

## Public build input policy

`desktop/moadoom-live.public.json` is canonical and reproducible. Do not change actual keys to clear a build.
For `github-env`, both repository variables `VITE_MOADOOM_WORKSPACE_PUBLIC_KEYS` and
`VITE_MOADOOM_MAINTENANCE_PUBLIC_KEYS` must be nonempty JSON maps matching the corresponding committed maps.
Comparison ignores JSON whitespace/property order, not key IDs or values.
Missing, invalid, empty or different maps stop BEFORE any `GITHUB_ENV` write.
Variables never override the file and are never silently ignored. Values are not printed.
Actual repository settings were not read/changed by offline verification; the CI gate will check them.

## Regression execution

`run_release_regressions.py --group control` runs in the control-server dependency environment.
`--group engine` runs in the local-engine venv and uses Node 24 for the explicitly selected mobile/map tests.
The required-file inventory is `tests/release-regression-required.json`; removal from selection and missing files both fail closed.
Every Python file has its own process, generated fixture directory, storage/key paths and test-ID evidence.
Only `test_customers_api.py` receives the opt-in `account_bound_ci` positive fixture.
Engine auth/STT/audit/settings use their own auth fixtures with legacy shared test utilities, NOT that opt-in identity plugin.
All other boundary/migration/RAG/control suites use `--noconftest`; no broad fixture merging is used.
The inherited Python sandbox blocks operating-profile file reads and non-fixture network connections.
The large migration test only kills its own freshly created 64 MiB synthetic child fixtures.
Collected IDs must equal passed IDs; zero tests, skips, failures and missing evidence fail the gate.
Evidence remains under the requested output directory, never in installer assets.

## Publisher edge policy (ASCII v1)

Before any remote write, inventory all `bundle/msi/*` and `bundle/nsis/*` files.
Only `.exe`, `.msi` and their corresponding `.sig` are accepted. Each installer
must have its actual local basename plus `.sig`; orphan/missing pairs are rejected.
No source file is renamed or rewritten. Original bytes, signature contents, size
and SHA256 are checked before and after upload.

Remote name algorithm (case is preserved):
1. Trim leading/trailing ASCII periods and spaces.
2. For a signature, normalize its installer basename first and append exactly `.sig`.
3. Encode each non-ASCII codepoint as `u` followed by lowercase hexadecimal, minimum
   four digits. This is deterministic escaping, NOT transliteration or Unicode normalization.
4. Replace each run of ASCII characters outside `[A-Za-z0-9._-]` with `_`.
5. Require a nonempty ASCII name without leading/trailing periods, at most240 bytes
   INCLUDING `.sig`. Preserve internal periods and multiple extensions.
6. Reject duplicate remote names and case-insensitive collisions across BOTH directories,
   including collisions between escaped Unicode and literal ASCII spellings.

Examples:
- `Insurance AI_2.5.18_x64-setup.exe` -> `Insurance_AI_2.5.18_x64-setup.exe`
- Its signature -> `Insurance_AI_2.5.18_x64-setup.exe.sig`
- `모아둠.exe` -> `ubaa8uc544ub460.exe`
- `.Insurance.AI.setup.exe` -> `Insurance.AI.setup.exe`
- `.a.b.exe...` -> `a.b.exe`; its signature normalizes to `a.b.exe.sig`.
  Windows normally cannot store a literal trailing-period basename; this last rule
  is covered as a pure naming-contract test, not a claim about Windows file creation.

`release-asset-manifest.json` is created exclusively in the workflow working directory
BEFORE API requests; an existing manifest is not overwritten on restart. It records
schema/naming_policy/repository/tag/commit/verified and assets with local_path,
local_name, remote_name, paired_remote_name, role, size and sha256. It is local CI
output, not an additional release asset or automatic site-publication input.
Only after GET readback of the new release ID confirms all asset IDs/names/size/state
and available SHA256 digests does it become verified:true with release_id, asset_id
and digest_verified fields. Missing API digest is explicitly false, not a claim that
a remote cryptographic digest was verified. Unexpected server renaming is rejected.
No clobber/delete/retry is introduced; partial failures leave verified:false.

Site metadata compatibility:
The existing site asset verifier looks up `asset.name === windows.fileName` and the
signature as `windows.fileName + '.sig'` (tools/site-2517.mjs). For the chosen Windows
installer, set api/windows.json fileName to the manifest's remote_name, checksum.sha256
to its unchanged local sha256 and fileSize from size. AFTER separately authorized
publication, fetch/verify the public asset and use its actual browser_download_url
for both api/windows.json.url and api/windows-latest.json.platforms['windows-x86_64'].url.
The updater signature is the original paired `.sig` text (existing trimming convention),
NOT the sig file's SHA256 and NOT a regenerated signature. Preserve the original
`.sig` file bytes. Do not use a draft/untagged URL, the local filename, or an assumed
GitHub normalization. Metadata preparation must still enforce the approved version,
public-download hash/signature checks and real installation/business acceptance.
This publisher neither reads nor changes live site metadata and does not publish.

Pagination policy:
Preserve status and all header values, including duplicate Link header lines.
Accept only canonical quoted first/prev/next/last Link relations with HTTPS host
api.github.com, exact `/repos/<owner>/<repo>/releases` path and exactly page/per_page
query fields (per_page=100). No userinfo, port, fragment, alternate path/host, duplicate
relations, cycle, page gaps or pages above100. Invalid/ambiguous responses stop before
POST. A short page with next is NOT terminal. A last-page declaration must remain
consistent across pages; without a next link, any declared last must equal the current
page. A validated response without Link is terminal per the API contract, regardless
of body count. Noncanonical pagination is rejected, not guessed or followed.

