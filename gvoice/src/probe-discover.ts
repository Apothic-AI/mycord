/**
 * Print the current X-Google-Client-Info inputs by reading the live Voice app bundle.
 *
 *   pnpm probe:discover
 *
 * No credentials required — the bundle is public. Use this when the defaults in
 * client-info.ts go stale, then export the result:
 *
 *   export GV_CLIENT_BUILD="GoogleVoice voice.web-frontend_…"
 *   export GV_ENGINE_VERSION="Birdsong v…"
 *   export GV_CLIENT_BROWSER="Chrome …"
 */

import { discoverClientInfo, encodeClientInfo, resolveClientInfo } from "./client-info.ts";

try {
  const discovered = await discoverClientInfo();
  const parts = resolveClientInfo(discovered);
  console.log("\nresolved parts:");
  console.log(JSON.stringify(parts, null, 1));
  console.log(`\nX-Google-Client-Info: ${encodeClientInfo(parts)}`);
} catch (err) {
  console.error(`discovery failed: ${err instanceof Error ? err.message : String(err)}`);
  console.error("falling back to built-in defaults (override with GV_CLIENT_BUILD / GV_ENGINE_VERSION / GV_CLIENT_BROWSER)");
  process.exitCode = 1;
}
