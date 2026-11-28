import {
  readFile,
  stat,
  mkdir,
  mkdtemp,
  rename,
  rm,
  readdir,
} from "node:fs/promises";
import { createReadStream } from "node:fs";
import { createServer } from "node:http";
import { createHash } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { bundle } from "@remotion/bundler";
import {
  RenderInternals,
  openBrowser,
  selectComposition,
  renderMedia,
  makeCancelSignal,
} from "@remotion/renderer";
import { jobSchema, isAllowedRequest } from "./src/peakcut/schema.mjs";

const root = path.dirname(fileURLToPath(import.meta.url));
const emit = (value) => process.stdout.write(JSON.stringify(value) + "\n");
let lastProgress = -1;
const progress = (value) => {
  const rounded = Math.max(0, Math.min(1, Math.floor(value * 1000) / 1000));
  if (rounded > lastProgress) {
    lastProgress = rounded;
    emit({ type: "progress", progress: rounded });
  }
};
const { cancel, cancelSignal } = makeCancelSignal();
let browser,
  server,
  cancelled = false;
const stop = () => {
  cancelled = true;
  cancel();
  void browser?.close({ silent: true }).catch(() => {});
};
process.once("SIGINT", stop);
process.once("SIGTERM", stop);

async function bundleForJob(cacheDir) {
  const hash = createHash("sha256");
  async function sourceFiles(directory) {
    const entries = await readdir(directory, { withFileTypes: true });
    for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name))) {
      const full = path.join(directory, entry.name);
      if (entry.isDirectory()) await sourceFiles(full);
      else {
        hash.update(path.relative(root, full));
        hash.update(await readFile(full));
      }
    }
  }
  await sourceFiles(path.join(root, "src"));
  hash.update(await readFile(path.join(root, "package-lock.json")));
  const target = path.join(
    cacheDir,
    "bundle-" + hash.digest("hex").slice(0, 20),
  );
  if (await stat(path.join(target, "index.html")).catch(() => false)) {
    progress(0.1);
    return target;
  }
  await mkdir(cacheDir, { recursive: true });
  const scratch = await mkdtemp(path.join(cacheDir, "building-"));
  try {
    await bundle({
      entryPoint: path.join(root, "src", "index.ts"),
      rootDir: root,
      outDir: scratch,
      rspack: true,
      enableCaching: true,
      gitSource: null,
      publicDir: null,
      onProgress: (value) => progress(value * 0.1),
    });
    if (cancelled) throw new Error("Render cancelled");
    try {
      await rename(scratch, target);
    } catch (error) {
      if (!(await stat(path.join(target, "index.html")).catch(() => false)))
        throw error;
    }
    return target;
  } finally {
    await rm(scratch, { recursive: true, force: true });
  }
}

async function serveBundle(directory, assetOrigin) {
  const contentTypes = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".map": "application/json",
    ".wasm": "application/wasm",
  };
  const instance = createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(
        new URL(request.url, "http://127.0.0.1").pathname,
      );
      const resolved = path.resolve(
        directory,
        "." + (pathname === "/" ? "/index.html" : pathname),
      );
      if (!resolved.startsWith(path.resolve(directory) + path.sep))
        throw new Error("Invalid bundle path");
      const info = await stat(resolved);
      if (!info.isFile()) throw new Error("Missing bundle file");
      response.writeHead(200, {
        "Content-Type":
          contentTypes[path.extname(resolved)] || "application/octet-stream",
        "Content-Length": info.size,
        "Content-Security-Policy": `default-src 'self' ${assetOrigin} data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src 'self' ${assetOrigin}; font-src 'self' ${assetOrigin} data:; media-src 'self' ${assetOrigin} blob:; img-src 'self' ${assetOrigin} data: blob:; object-src 'none'; base-uri 'none'`,
      });
      createReadStream(resolved)
        .on("error", () => response.destroy())
        .pipe(response);
    } catch {
      response.writeHead(404);
      response.end();
    }
  });
  await new Promise((resolve, reject) => {
    instance.once("error", reject);
    instance.listen(0, "127.0.0.1", resolve);
  });
  server = instance;
  return `http://127.0.0.1:${instance.address().port}`;
}

function restrictBrowser(instance, origins) {
  // Remotion 4.0.547 exposes CDP through its browser page. Install interception
  // before Remotion navigates any page, including composition selection.
  const original = instance.newPage.bind(instance);
  instance.newPage = async (options) => {
    const page = await original(options),
      client = page._client();
    client.on("Fetch.requestPaused", (event) => {
      const allowed = isAllowedRequest(event.request.url, origins);
      void client
        .send(
          allowed ? "Fetch.continueRequest" : "Fetch.failRequest",
          allowed
            ? { requestId: event.requestId }
            : { requestId: event.requestId, errorReason: "BlockedByClient" },
        )
        .catch(() => {});
    });
    await client.send("Fetch.enable", {
      patterns: [{ urlPattern: "*", requestStage: "Request" }],
    });
    return page;
  };
}

async function main() {
  if (process.argv.length !== 3)
    throw new Error("Usage: node my-video/worker.mjs JOB.json");
  const jobFile = path.resolve(process.argv[2]);
  if ((await stat(jobFile)).size > 4 * 1024 * 1024)
    throw new Error("Worker job is too large");
  const job = jobSchema.parse(JSON.parse(await readFile(jobFile, "utf8")));
  process.chdir(root);
  progress(0);
  // Rendering never downloads software. Install Chrome once with `npm exec remotion browser ensure`.
  // The public ensureBrowser callback runs even for an installed browser. The
  // pinned internal API can skip version negotiation and refuse downloads.
  const installed = await RenderInternals.internalEnsureBrowser({
    browserExecutable: null,
    indent: false,
    chromeMode: "headless-shell",
    preferRequestedVersion: false,
    logLevel: "error",
    onBrowserDownload: () => {
      throw new Error(
        "Install the Remotion browser first: npm exec remotion browser ensure",
      );
    },
  });
  if (!("path" in installed))
    throw new Error("The Remotion browser is not installed");
  const built = await bundleForJob(job.bundleDir);
  const assetOrigin = new URL(job.fontUrl).origin;
  const serveUrl = await serveBundle(built, assetOrigin);
  browser = await openBrowser("chrome", {
    browserExecutable: installed.path,
    logLevel: "error",
    chromiumOptions: {
      headless: true,
      disableWebSecurity: false,
      flags: {
        "disable-background-networking": true,
        "disable-component-update": true,
        "disable-sync": true,
      },
    },
  });
  restrictBrowser(browser, new Set([assetOrigin, serveUrl]));
  const inputProps = {
    timeline: job.timeline,
    media: job.media,
    fontUrl: job.fontUrl,
    height: job.height,
  };
  const options = {
    serveUrl,
    inputProps,
    puppeteerInstance: browser,
    envVariables: {},
    logLevel: "error",
    timeoutInMilliseconds: 30000,
  };
  const composition = await selectComposition({
    ...options,
    id: "PeakCutVertical",
  });
  if (cancelled) throw new Error("Render cancelled");
  await renderMedia({
    ...options,
    composition,
    codec: "h264",
    outputLocation: job.output,
    overwrite: true,
    muted: true,
    enforceAudioTrack: false,
    pixelFormat: "yuv420p",
    colorSpace: "bt709",
    imageFormat: "jpeg",
    jpegQuality: 90,
    crf: 18,
    x264Preset: "fast",
    concurrency: 2,
    cancelSignal,
    onProgress: (event) => progress(0.1 + 0.9 * event.progress),
  });
  if (cancelled) throw new Error("Render cancelled");
  progress(1);
  emit({ type: "done", output: job.output });
}

try {
  await main();
} catch (error) {
  emit({
    type: "error",
    code: cancelled
      ? "cancelled"
      : error?.issues
        ? "validation"
        : "render_failed",
    message: error?.issues
      ? "Invalid render job"
      : String(error?.message || error),
    ...(error?.issues
      ? {
          issues: error.issues.map((issue) => ({
            code: issue.code,
            path: "/" + issue.path.join("/"),
            message: issue.message,
          })),
        }
      : {}),
  });
  process.exitCode = cancelled ? 130 : 1;
} finally {
  if (browser) await browser.close({ silent: true }).catch(() => {});
  if (server) await new Promise((resolve) => server.close(resolve));
}
