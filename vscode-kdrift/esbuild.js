const esbuild = require("esbuild");
const path = require("path");
const fs = require("fs");

const watch = process.argv.includes("--watch");
const minify = process.argv.includes("--minify");

async function build() {
  const shared = { bundle: true, minify, sourcemap: !minify };

  const extensionBuild = esbuild.build({
    ...shared,
    entryPoints: ["src/extension.ts"],
    outfile: "dist/extension.js",
    format: "cjs",
    platform: "node",
    external: ["vscode"],
  });

  const webviewBuild = esbuild.build({
    ...shared,
    entryPoints: ["src/webview/preview.ts"],
    outfile: "dist/webview/preview.js",
    format: "iife",
    platform: "browser",
  });

  fs.mkdirSync("dist/webview", { recursive: true });
  fs.copyFileSync("src/webview/styles.css", "dist/webview/styles.css");

  await Promise.all([extensionBuild, webviewBuild]);
  console.log("Build complete");
}

if (watch) {
  const ctx = Promise.all([
    esbuild.context({
      entryPoints: ["src/extension.ts"],
      outfile: "dist/extension.js",
      bundle: true,
      format: "cjs",
      platform: "node",
      external: ["vscode"],
      sourcemap: true,
    }),
    esbuild.context({
      entryPoints: ["src/webview/preview.ts"],
      outfile: "dist/webview/preview.js",
      bundle: true,
      format: "iife",
      platform: "browser",
      sourcemap: true,
    }),
  ]).then(async (contexts) => {
    for (const ctx of contexts) {
      await ctx.watch();
    }
    console.log("Watching for changes...");
  });
} else {
  build().catch(() => process.exit(1));
}
