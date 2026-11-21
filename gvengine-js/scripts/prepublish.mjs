// SPDX-License-Identifier: MIT
//
// Run by `npm publish` before anything is uploaded (prepublishOnly). A version
// on npm is permanent, and `dist/` is the one part of this package that is
// not in git -- it is built (./build.sh, which needs emscripten) and gitignored
// -- so this refuses to publish a tarball whose build is missing, is from a
// different bgsage than `bgsage.version` pins, or was built from a working
// copy that has moved since. The tests run after it (`npm test`), and they
// skip without `dist/`, which is why this has to check for it first.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(root, "dist");
const fail = (message) => { console.error(`prepublish: ${message}`); process.exit(1); };

if (!fs.existsSync(path.join(dist, "manifest.json"))) fail("dist/ is not built: run ./build.sh");
const manifest = JSON.parse(fs.readFileSync(path.join(dist, "manifest.json"), "utf8"));
const pinned = fs.readFileSync(path.join(root, "bgsage.version"), "utf8").trim();
if (manifest.bgsage !== pinned) fail(`dist/ is bgsage ${manifest.bgsage}, bgsage.version says ${pinned}: rebuild`);
for (const name of Object.keys(manifest.sha256 ?? {})) {
    if (!fs.existsSync(path.join(dist, name))) fail(`dist/${name} is missing`);
}
// The build is from bgsage's tag plus entry.cpp; an entry.cpp newer than the
// build is a dist/ that does not contain what the tarball's source says.
const built = fs.statSync(path.join(dist, "manifest.json")).mtimeMs;
if (fs.statSync(path.join(root, "src", "entry.cpp")).mtimeMs > built) fail("src/entry.cpp is newer than dist/: rebuild");
console.log(`prepublish: dist/ is bgsage ${pinned}, ${Object.keys(manifest.sha256 ?? {}).length} files`);
