'use strict';

const RELEASES_API = 'https://api.github.com/repos/Coinsi/inspiration/releases';
const RELEASES_PAGE = 'https://github.com/Coinsi/inspiration/releases';
const DEFAULT_TIMEOUT_MS = 8_000;
const PAGE_SIZE = 100;
const MAX_PAGES = 5;
const SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/;

function parseVersion(value) {
  if (typeof value !== 'string' || value.length > 128) return null;
  const match = SEMVER.exec(value);
  if (!match) return null;
  const prerelease = match[4]?.split('.') ?? [];
  if (prerelease.some(part => /^\d+$/.test(part) && part.length > 1 && part.startsWith('0'))) return null;
  return { numbers: match.slice(1, 4).map(BigInt), prerelease };
}

function compareVersions(left, right) {
  for (let i = 0; i < 3; i++) {
    if (left.numbers[i] !== right.numbers[i]) return left.numbers[i] > right.numbers[i] ? 1 : -1;
  }
  if (!left.prerelease.length || !right.prerelease.length)
    return Number(!left.prerelease.length) - Number(!right.prerelease.length);
  for (let i = 0; i < Math.max(left.prerelease.length, right.prerelease.length); i++) {
    const a = left.prerelease[i], b = right.prerelease[i];
    if (a === b) continue;
    if (a === undefined) return -1;
    if (b === undefined) return 1;
    const numericA = /^\d+$/.test(a), numericB = /^\d+$/.test(b);
    if (numericA && numericB) return BigInt(a) > BigInt(b) ? 1 : -1;
    if (numericA !== numericB) return numericA ? -1 : 1;
    return a > b ? 1 : -1;
  }
  return 0;
}

function targetFor(platform, arch) {
  if (platform === 'win32' && arch === 'x64') return { os: 'win', arch, ext: 'exe' };
  if (platform === 'darwin' && ['x64', 'arm64'].includes(arch)) return { os: 'mac', arch, ext: 'dmg' };
  return null;
}

/** Only validated URLs from this fixed repository leave this module. No files are downloaded. */
function selectUpdate(releases, { currentVersion, platform = process.platform, arch = process.arch } = {}) {
  const current = parseVersion(currentVersion);
  if (!current) return { status: 'invalid', reason: 'current-version' };
  const target = targetFor(platform, arch);
  if (!target) return { status: 'invalid', reason: 'unsupported-platform' };
  if (!Array.isArray(releases)) return { status: 'invalid', reason: 'release-data' };
  let selected;
  let invalidReason;
  for (const release of releases) {
    if (!release || typeof release !== 'object' || typeof release.tag_name !== 'string') {
      invalidReason = 'release-data';
      continue;
    }
    if (!release.tag_name.startsWith('desktop-v') || release.draft === true || release.prerelease === true) continue;
    const version = release.tag_name.slice('desktop-v'.length);
    const parsed = parseVersion(version);
    if (!parsed) {
      invalidReason = 'release-data';
      continue;
    }
    if (parsed.prerelease.length) continue;
    if (release.draft !== false || release.prerelease !== false) {
      invalidReason = 'release-data';
      continue;
    }
    if (compareVersions(parsed, current) <= 0) continue;
    if (!Array.isArray(release.assets)) {
      invalidReason = 'release-data';
      continue;
    }
    const assetName = `Inspiration-${version}-${target.os}-${target.arch}.${target.ext}`;
    const expectedURL = `${RELEASES_PAGE}/download/${encodeURIComponent(release.tag_name)}/${encodeURIComponent(assetName)}`;
    const matches = release.assets.filter(asset => asset?.name === assetName);
    // A missing architecture must never silently select another installer or a source archive.
    if (matches.length !== 1) {
      invalidReason = 'no-compatible-installer';
      continue;
    }
    const asset = matches[0];
    if (asset.browser_download_url !== expectedURL || asset.state !== 'uploaded' || !Number.isSafeInteger(asset.size) || asset.size <= 0) {
      invalidReason = 'invalid-installer';
      continue;
    }
    if (!selected || compareVersions(parsed, selected.parsed) > 0) {
      selected = {
        parsed,
        result: {
          status: 'available', version, assetName, downloadUrl: expectedURL,
          releaseUrl: `${RELEASES_PAGE}/tag/${encodeURIComponent(release.tag_name)}`,
        },
      };
    }
  }
  return selected?.result ?? (invalidReason ? { status: 'invalid', reason: invalidReason } : { status: 'up-to-date' });
}

/** Deadline covers the response body and pagination too; fetchImpl exists only for deterministic tests. */
async function checkForUpdates({ currentVersion, platform = process.platform, arch = process.arch, fetchImpl = globalThis.fetch, timeoutMs = DEFAULT_TIMEOUT_MS } = {}) {
  const options = { currentVersion, platform, arch };
  const initial = selectUpdate([], options);
  if (initial.status === 'invalid') return initial;
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0 || timeoutMs > 30_000)
    return { status: 'invalid', reason: 'timeout-config' };
  if (typeof fetchImpl !== 'function') return { status: 'offline', reason: 'network' };
  const controller = new AbortController();
  let timer;
  const timeout = new Promise(resolve => {
    timer = setTimeout(() => {
      controller.abort();
      resolve({ status: 'offline', reason: 'timeout' });
    }, timeoutMs);
  });
  const request = async () => {
    const releases = [];
    for (let page = 1; page <= MAX_PAGES; page++) {
      const url = `${RELEASES_API}?per_page=${PAGE_SIZE}&page=${page}`;
      const response = await fetchImpl(url, {
        headers: {
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2026-03-10',
          'User-Agent': 'Inspiration-Desktop',
        },
        credentials: 'omit', cache: 'no-store', redirect: 'error', signal: controller.signal,
      });
      if (controller.signal.aborted) return { status: 'offline', reason: 'timeout' };
      if (!response || response.redirected || (response.url && response.url !== url))
        return { status: 'invalid', reason: 'response-origin' };
      if (!response.ok) return { status: 'offline', reason: 'http-error' };
      if (typeof response.json !== 'function') return { status: 'invalid', reason: 'release-data' };
      let entries;
      try { entries = await response.json(); }
      catch (error) {
        if (controller.signal.aborted) return { status: 'offline', reason: 'timeout' };
        return error instanceof SyntaxError
          ? { status: 'invalid', reason: 'release-data' }
          : { status: 'offline', reason: 'network' };
      }
      if (controller.signal.aborted) return { status: 'offline', reason: 'timeout' };
      if (!Array.isArray(entries) || entries.length > PAGE_SIZE)
        return { status: 'invalid', reason: 'release-data' };
      releases.push(...entries);
      if (entries.length < PAGE_SIZE) return selectUpdate(releases, options);
      // Never follow a URL supplied by a Link header or stop early on unrelated web releases.
    }
    return { status: 'invalid', reason: 'release-page-limit' };
  };
  try {
    return await Promise.race([
      request().catch(() => ({ status: 'offline', reason: controller.signal.aborted ? 'timeout' : 'network' })),
      timeout,
    ]);
  } finally {
    clearTimeout(timer);
    controller.abort();
  }
}

module.exports = { checkForUpdates, selectUpdate, RELEASES_API, RELEASES_PAGE, DEFAULT_TIMEOUT_MS };
