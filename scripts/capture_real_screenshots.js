import { chromium } from '../dashboard/frontend/node_modules/playwright/index.mjs';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const outDir = path.resolve(__dirname, '../docs/screenshots');

if (!fs.existsSync(outDir)) {
  fs.mkdirSync(outDir, { recursive: true });
}

async function capture() {
  console.log('Launching Chrome with Playwright at 1366x768...');
  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
  });

  const context = await browser.newContext({
    viewport: { width: 1366, height: 768 },
    deviceScaleFactor: 1,
  });

  const page = await context.newPage();

  console.log('Navigating to http://127.0.0.1:8787...');
  await page.goto('http://127.0.0.1:8787', { waitUntil: 'networkidle', timeout: 15000 });

  // 1. Compare View (default)
  console.log('Capturing 00_compare.png...');
  await page.waitForTimeout(1000);
  await page.screenshot({ path: path.join(outDir, '00_compare.png'), fullPage: false });

  // Views mapping to shortcuts 1-8
  const views = [
    { key: '2', file: '01_overview.png', name: 'Live Overview' },
    { key: '3', file: '02_timeline.png', name: 'Prediction Timeline' },
    { key: '4', file: '03_distributions.png', name: 'Reserved vs Holdout' },
    { key: '5', file: '04_benefit.png', name: 'Benefit Estimator' },
    { key: '6', file: '05_safety.png', name: 'Safety Governor' },
    { key: '7', file: '06_experiments.png', name: 'Experiments' },
    { key: '8', file: '07_decisions.png', name: 'Decision Log' },
  ];

  for (const v of views) {
    console.log(`Navigating to ${v.name} (Key: ${v.key})...`);
    await page.keyboard.press(v.key);
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(outDir, v.file), fullPage: false });
  }

  // Presentation Mode
  console.log('Capturing Presentation Mode (Key: P)...');
  await page.keyboard.press('1'); // back to compare
  await page.waitForTimeout(400);
  await page.keyboard.press('p'); // enter presentation mode
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(outDir, '08_presentation_mode.png'), fullPage: false });

  await browser.close();
  console.log('All real PNG screenshots captured successfully in docs/screenshots/');
}

capture().catch(err => {
  console.error('Capture error:', err);
  process.exit(1);
});
