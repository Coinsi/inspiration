export const DESKTOP_RELEASES_URL = "https://github.com/Coinsi/inspiration/releases";

type DesktopPlatform = "windows-x64" | "mac-arm64" | "mac-x64";

export interface DesktopRelease {
  version: string;
  releaseUrl: string;
  publishedAt: string | null;
  assets: Record<DesktopPlatform, { url: string; size: number } | null>;
}

const RELEASES_API = "https://api.github.com/repos/Coinsi/inspiration/releases";
const PLATFORMS: Record<DesktopPlatform, string> = {
  "windows-x64": "win-x64.exe",
  "mac-arm64": "mac-arm64.dmg",
  "mac-x64": "mac-x64.dmg",
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function readRelease(value: unknown): DesktopRelease | null {
  if (!record(value) || value.draft !== false || value.prerelease !== false ||
    typeof value.tag_name !== "string" || value.tag_name.length > 80 ||
    typeof value.published_at !== "string" || !value.published_at) return null;
  const match = /^desktop-v((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$/.exec(value.tag_name);
  if (!match) return null;
  const version = match[1];
  const releaseUrl = `${DESKTOP_RELEASES_URL}/tag/${value.tag_name}`;
  if (value.html_url !== releaseUrl) return null;
  const assets: DesktopRelease["assets"] = { "windows-x64": null, "mac-arm64": null, "mac-x64": null };
  for (const platform of Object.keys(PLATFORMS) as DesktopPlatform[]) {
    const name = `Inspiration-${version}-${PLATFORMS[platform]}`;
    const url = `${DESKTOP_RELEASES_URL}/download/${value.tag_name}/${name}`;
    // Never infer availability from a filename: an uploaded asset with the
    // exact official URL, version and architecture must be present in the API.
    const asset = Array.isArray(value.assets) && value.assets.find((item: unknown) => record(item) &&
      item.name === name && item.browser_download_url === url && item.state === "uploaded" &&
      typeof item.size === "number" && Number.isSafeInteger(item.size) && item.size > 0);
    if (asset) assets[platform] = { url, size: asset.size };
  }
  return {
    version, releaseUrl, assets,
    publishedAt: Number.isFinite(Date.parse(value.published_at)) ? value.published_at : null,
  };
}

function newerThan(candidate: DesktopRelease, current: DesktopRelease): boolean {
  const candidateParts = candidate.version.split(".").map(BigInt);
  const currentParts = current.version.split(".").map(BigInt);
  for (let index = 0; index < candidateParts.length; index++) {
    if (candidateParts[index] !== currentParts[index]) return candidateParts[index] > currentParts[index];
  }
  return false;
}

/** Public metadata only; callers show the official releases link on failure. */
export async function getDesktopRelease(signal?: AbortSignal): Promise<DesktopRelease | null> {
  signal?.throwIfAborted();
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  signal?.addEventListener("abort", abort, { once: true });
  const timeout = setTimeout(() => controller.abort(new DOMException("Release lookup timed out", "TimeoutError")), 12_000);
  let latest: DesktopRelease | null = null;
  try {
    // Query all listed versions, rather than GitHub's generic /latest endpoint:
    // this repository also publishes versions unrelated to the desktop app.
    for (let page = 1; page <= 10; page++) {
      const response = await fetch(`${RELEASES_API}?per_page=100&page=${page}`, {
        method: "GET", headers: { Accept: "application/vnd.github+json" },
        credentials: "omit", redirect: "error", referrerPolicy: "no-referrer", cache: "no-cache",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`Desktop release lookup failed (${response.status})`);
      const releases: unknown = await response.json();
      if (!Array.isArray(releases)) throw new Error("Invalid desktop release response");
      for (const item of releases) {
        const release = readRelease(item);
        if (release && (!latest || newerThan(release, latest))) latest = release;
      }
      // Construct pagination on the fixed API origin; never follow a URL from
      // an untrusted Link header or API field.
      const hasNext = /\brel="next"/.test(response.headers.get("Link") ?? "");
      if (!hasNext && releases.length < 100) return latest;
    }
    // A partial scan must not advertise an older version as the latest one.
    throw new Error("Desktop release history exceeds the lookup limit");
  } finally {
    clearTimeout(timeout);
    signal?.removeEventListener("abort", abort);
  }
}
