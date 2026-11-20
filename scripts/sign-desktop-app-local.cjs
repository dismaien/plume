#!/usr/bin/env node
// Local Plume builds must never discover or use a keychain signing identity.
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const machoMagic = new Set([
  "feedface", "cefaedfe", "feedfacf", "cffaedfe", "cafebabe", "bebafeca",
  "cafebabf", "bfbafeca",
]);

function run(command, args) {
  return execFileSync(command, args, { encoding: "utf8", timeout: 120_000 });
}

function signLocalApp(appPath) {
  if (process.platform !== "darwin") throw new Error("Local app signing requires macOS");
  const app = fs.realpathSync(appPath);
  if (app.startsWith("/Applications/") || app.startsWith(path.join(require("node:os").homedir(), "Applications") + path.sep)) {
    throw new Error("Sign a staged copy before replacing an installed app");
  }
  if (!app.endsWith(".app")) throw new Error("Expected a staged .app bundle");
  const info = JSON.parse(run("/usr/bin/plutil", ["-convert", "json", "-o", "-", path.join(app, "Contents/Info.plist")]));
  if (info.CFBundleIdentifier !== "app.plume.desktop") throw new Error("This signer is only for Plume");

  const binaries = [];
  const bundles = [app];
  function collect(directory) {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const item = path.join(directory, entry.name);
      // Framework links point to their real version, which is visited separately.
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        collect(item);
        if (/\.(app|framework|xpc)$/.test(entry.name)) bundles.push(item);
      } else if (entry.isFile()) {
        const fd = fs.openSync(item, "r");
        const magic = Buffer.alloc(4);
        try {
          if (fs.readSync(fd, magic, 0, 4, 0) === 4 && machoMagic.has(magic.toString("hex"))) binaries.push(item);
        } finally {
          fs.closeSync(fd);
        }
      }
    }
  }
  collect(app);
  bundles.sort((a, b) => b.split(path.sep).length - a.split(path.sep).length);
  const targets = [...binaries.sort(), ...bundles];
  for (const target of targets) {
    // Regenerate requirements: inherited certificate/team requirements must not survive.
    run("/usr/bin/codesign", ["--force", "--sign", "-", "--timestamp=none",
      "--preserve-metadata=identifier,entitlements,flags,runtime", target]);
  }
  run("/usr/bin/codesign", ["--verify", "--deep", "--strict", app]);
  for (const target of targets) {
    const result = require("node:child_process").spawnSync("/usr/bin/codesign", ["-dv", "--verbose=4", target], { encoding: "utf8", timeout: 30_000 });
    const details = result.stderr || "";
    if (result.status !== 0 || !details.includes("Signature=adhoc") || !details.includes("TeamIdentifier=not set") || /^Authority=/m.test(details)) {
      throw new Error(`Expected an ad-hoc signature without an Apple identity: ${target}`);
    }
  }
  console.log(`Verified local ad-hoc signatures on ${targets.length} Plume code objects: ${app}`);
}

// electron-builder skips its certificate signer (mac.identity=null) after this hook.
module.exports = async function afterPack(context) {
  if (context.electronPlatformName === "darwin") {
    signLocalApp(path.join(context.appOutDir, context.packager.appInfo.productFilename + ".app"));
  }
};

if (require.main === module) {
  if (process.argv.length !== 3) throw new Error("Usage: node scripts/sign-desktop-app-local.cjs /path/to/staged/Plume.app");
  signLocalApp(process.argv[2]);
}
