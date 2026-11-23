// Builds an offline single-file page that mounts the REAL production App with synthetic fixtures.
// Usage: node tests/blue-ui/build-harness.mjs [outDir]  (default: ../handoff-out/blue-ui-harness under the repo)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(process.argv[2] ?? path.join(here, '../../../handoff/pc-qa/dist'));
const adapter = path.join(here, 'adapter.ts');
const result = await build({
  absWorkingDir: path.join(here, '../..'),
  entryPoints: [path.join(here, 'entry.tsx')],
  bundle: true, write: false, outdir: 'dist', format: 'iife', platform: 'browser', target: ['chrome110'],
  jsx: 'automatic', minify: false, sourcemap: false, metafile: true,
  define: { 'process.env.NODE_ENV': '"production"', 'import.meta.env': '{}', 'import.meta.env.VITE_INITIAL_VIEW': 'undefined' },
  loader: { '.svg': 'dataurl', '.png': 'dataurl', '.woff2': 'dataurl', '.woff': 'dataurl' },
  plugins: [{
    name: 'offline-adapter',
    setup(b) {
      b.onResolve({ filter: /(^|\/)(engine|usage|auth|centralConfig|centralClient|config|rememberEmail|updater)(\.ts)?$/ }, (a) => (a.path.startsWith('.') ? { path: adapter } : undefined));
      b.onResolve({ filter: /^@tauri-apps\// }, () => ({ path: adapter }));
      b.onResolve({ filter: /^@supabase\// }, () => ({ path: adapter }));
    },
  }],
});
const js = result.outputFiles.find((f) => f.path.endsWith('.js')).text;
const css = result.outputFiles.filter((f) => f.path.endsWith('.css')).map((f) => f.text).join('\n');
fs.mkdirSync(out, { recursive: true });
const html = `<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; form-action 'none'; base-uri 'none'"><title>모아둠 PC · blue UI fixture</title><style>${css}</style><body><div id="root"></div><script>${js.replaceAll('</script', '<\\/script')}</script></body></html>`;
fs.writeFileSync(path.join(out, 'index.html'), html);
fs.writeFileSync(path.join(out, 'inputs.json'), JSON.stringify(Object.keys(result.metafile.inputs).filter((p) => p.includes('src')), null, 1));
console.log(JSON.stringify({ built: true, out, bytes: Buffer.byteLength(html), srcInputs: Object.keys(result.metafile.inputs).filter((p) => /(^|[/\\])src[/\\]/.test(p)).length }));
