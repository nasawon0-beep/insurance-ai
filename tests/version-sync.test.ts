import assert from "node:assert/strict";
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { test } from "node:test";

const repo = new URL("..", import.meta.url);

function makeFixture(eol: "\n" | "\r\n") {
  const root = mkdtempSync(join(tmpdir(), "insurance-version-sync-"));
  mkdirSync(join(root, "scripts"), { recursive: true });
  mkdirSync(join(root, "desktop", "src-tauri"), { recursive: true });
  cpSync(new URL("scripts/check-version-sync.mjs", repo), join(root, "scripts", "check-version-sync.mjs"));
  cpSync(new URL("scripts/sync-version.mjs", repo), join(root, "scripts", "sync-version.mjs"));
  writeFileSync(join(root, "desktop", "package.json"), '{"version":"2.5.2"}\n');
  writeFileSync(
    join(root, "desktop", "package-lock.json"),
    '{"version":"2.5.2","packages":{"":{"version":"2.5.2"}}}\n',
  );
  writeFileSync(join(root, "desktop", "src-tauri", "tauri.conf.json"), '{"version":"2.5.2"}\n');
  writeFileSync(
    join(root, "desktop", "src-tauri", "Cargo.toml"),
    ["[package]", 'name = "desktop-scaffold"', 'version = "2.5.2"', "", "[dependencies]", ""].join(eol),
  );
  writeFileSync(
    join(root, "desktop", "src-tauri", "Cargo.lock"),
    ['name = "desktop-scaffold"', 'version = "2.5.2"', ""].join(eol),
  );
  return root;
}

for (const [label, eol] of [["LF", "\n"], ["CRLF", "\r\n"]] as const) {
  test(`version checker and sync support ${label} clean checkouts`, () => {
    const root = makeFixture(eol);
    try {
      const checkBefore = spawnSync("node", [join(root, "scripts", "check-version-sync.mjs")], { encoding: "utf8" });
      assert.equal(checkBefore.status, 0, checkBefore.stderr || checkBefore.stdout);

      writeFileSync(join(root, "desktop", "package.json"), '{"version":"2.5.3"}\n');
      const sync = spawnSync("node", [join(root, "scripts", "sync-version.mjs")], { encoding: "utf8" });
      assert.equal(sync.status, 0, sync.stderr || sync.stdout);

      const cargoToml = readFileSync(join(root, "desktop", "src-tauri", "Cargo.toml"), "utf8");
      assert.match(cargoToml, new RegExp(`version = "2\\.5\\.3"${eol === "\r\n" ? "\\r\\n" : "\\n"}`));

      const checkAfter = spawnSync("node", [join(root, "scripts", "check-version-sync.mjs")], { encoding: "utf8" });
      assert.equal(checkAfter.status, 0, checkAfter.stderr || checkAfter.stdout);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
}
