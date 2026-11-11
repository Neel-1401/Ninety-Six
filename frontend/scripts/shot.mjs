// Screenshot helper (lead-owned). Uses the system Microsoft Edge via Playwright (no browser download).
// Usage: node scripts/shot.mjs <url> <out.png> [--mobile] [--login staff:<username>|patient-demo:<user_id>] [--wait <ms>]
//   --login staff:nurse.asha      signs in through /api/auth/staff/login (password demo1234, demo mode)
//   --login demo:<user_id>        signs in through /api/demo/login-as
// The URL's origin must be the Vite dev server (it proxies /api).
import { chromium } from "@playwright/test";

const args = process.argv.slice(2);
const [url, out] = args;
if (!url || !out) {
  console.error("usage: node scripts/shot.mjs <url> <out.png> [--mobile] [--login staff:user|demo:id] [--wait ms] [--full]");
  process.exit(2);
}
const flag = (n) => args.includes(n);
const opt = (n) => {
  const i = args.indexOf(n);
  return i >= 0 ? args[i + 1] : undefined;
};
const mobile = flag("--mobile");
const browser = await chromium.launch({ channel: "msedge", headless: true });
const ctx = await browser.newContext(
  mobile
    ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }
    : { viewport: { width: 1440, height: 900 } },
);
const page = await ctx.newPage();
const errors = [];
page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
page.on("pageerror", (e) => errors.push(String(e)));
const origin = new URL(url).origin;
const login = opt("--login");
if (login) {
  const [kind, who] = login.split(":");
  const headers = { "X-Requested-With": "ninety-six", "Content-Type": "application/json" };
  const r =
    kind === "staff"
      ? await page.request.post(`${origin}/api/auth/staff/login`, { headers, data: { username: who, password: "demo1234" } })
      : await page.request.post(`${origin}/api/demo/login-as`, { headers, data: { user_id: Number(who) } });
  if (!r.ok()) console.error("login failed", r.status(), await r.text());
}
await page.goto(url, { waitUntil: "networkidle" });
await page.waitForTimeout(Number(opt("--wait") ?? 600));
await page.screenshot({ path: out, fullPage: flag("--full") });
console.log(JSON.stringify({ saved: out, title: await page.title(), consoleErrors: errors.slice(0, 10) }));
await browser.close();
