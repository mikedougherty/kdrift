const esbuild = require("esbuild");
const fs = require("fs");

const watch = process.argv.includes("--watch");
const minify = process.argv.includes("--minify");

const extensionConfig = {
  entryPoints: ["src/extension.ts"],
  outfile: "dist/extension.js",
  bundle: true,
  format: "cjs",
  platform: "node",
  external: ["vscode"],
};

const webviewConfig = {
  entryPoints: ["src/webview/preview.ts"],
  outfile: "dist/webview/preview.js",
  bundle: true,
  format: "iife",
  platform: "browser",
};

function copyStyles() {
  fs.mkdirSync("dist/webview", { recursive: true });
  fs.copyFileSync("src/webview/styles.css", "dist/webview/styles.css");
}

async function build() {
  const shared = { minify, sourcemap: !minify };
  await Promise.all([
    esbuild.build({ ...extensionConfig, ...shared }),
    esbuild.build({ ...webviewConfig, ...shared }),
  ]);
  copyStyles();
  console.log("Build complete");
}

async function startWatch() {
  const shared = { sourcemap: true };
  const contexts = await Promise.all([
    esbuild.context({ ...extensionConfig, ...shared }),
    esbuild.context({ ...webviewConfig, ...shared }),
  ]);
  for (const ctx of contexts) {
    await ctx.watch();
  }
  copyStyles();
  fs.watchFile("src/webview/styles.css", () => copyStyles());
  console.log("Watching for changes...");
}

if (watch) {
  startWatch();
} else {
  build().catch(() => process.exit(1));
}
