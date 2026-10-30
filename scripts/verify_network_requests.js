import { chromium } from '../dashboard/frontend/node_modules/playwright/index.mjs';

async function verifyNetwork() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();

  const requests = [];
  page.on('request', (req) => {
    requests.push({
      method: req.method(),
      url: req.url(),
      resourceType: req.resourceType(),
    });
  });

  await page.goto('http://127.0.0.1:8787', { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(2500);
  await browser.close();

  console.log(`Total requests intercepted on page load: ${requests.length}`);
  console.log('-'.repeat(80));
  let externalCount = 0;
  for (const r of requests) {
    const isLocal = r.url.startsWith('http://127.0.0.1:8787') || r.url.startsWith('http://localhost:8787');
    if (!isLocal) externalCount++;
    console.log(`${r.method.padEnd(5)} | ${r.resourceType.padEnd(10)} | ${r.url}`);
  }
  console.log('-'.repeat(80));
  console.log(`External / CDN requests count: ${externalCount} (Must be 0)`);
}

verifyNetwork().catch(console.error);
