#!/usr/bin/env node
/**
 * 真打开一次看板：报错、截图、导出 XML。
 *
 * 为什么要有这个：看板引擎跑在浏览器里，`board_check.py` 只能验数据。引擎自己会不会崩、
 * 导出的 XML 里 clip 时间对不对，只有真跑一遍才知道。以前吃过「报告全绿但画面是空」的亏。
 *
 *   node scripts/board_smoke.js <board.html> <输出目录>
 *
 * 产出：<输出目录>/board-shot.png（整页截图）、<输出目录>/export-test.xml（真导出的 XML）
 * 退出码非 0 表示页面有 JS 错误或导出失败。
 */
const fs = require('fs');
const path = require('path');
const puppeteer = require(process.env.CUTSKILLS_PUPPETEER
  || (process.env.CUTSKILLS_HOME || path.join(require('os').homedir(), '.cutskills'))
      + '/npm/node_modules/puppeteer-core');

const CHROME = process.env.CUTSKILLS_CHROME
  || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

(async () => {
  const [html, outDir] = process.argv.slice(2);
  if (!html || !outDir) {
    console.error('用法：node board_smoke.js <board.html> <输出目录>');
    process.exit(2);
  }
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: true,
    args: ['--allow-file-access-from-files', '--font-render-hinting=none'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1680, height: 1500, deviceScaleFactor: 1 });

  const errors = [];
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push('console: ' + m.text());
  });

  // 拦下载：记下要导出的 blob 文本，别真落盘
  await page.evaluateOnNewDocument(() => {
    window.__lastBlob = null;
    window.__lastName = null;
    const objURL = URL.createObjectURL.bind(URL);
    URL.createObjectURL = (b) => {
      try {
        if (b && typeof b.text === 'function') window.__lastBlob = b;
      } catch (e) {}
      return objURL(b);
    };
    const click = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () {
      if (this.download) {
        window.__lastName = this.download;
        return;
      }
      return click.apply(this, arguments);
    };
  });

  await page.goto('file://' + path.resolve(html), { waitUntil: 'networkidle2', timeout: 60000 });
  await new Promise((r) => setTimeout(r, 2500));

  // 引擎有没有把镜头真的画出来
  const stats = await page.evaluate(() => {
    const q = (s) => document.querySelectorAll(s).length;
    const clips = [...document.querySelectorAll('.clip')].map((c) =>
      (c.className.match(/\b(a|rec|br|ov)\b/) || ['?'])[0]);
    const byTrack = clips.reduce((m, k) => ((m[k] = (m[k] || 0) + 1), m), {});
    return {
      shots: (window.M && M.shots && M.shots.length) || null,
      clips: clips.length,
      byTrack,
      rows: q('.tl-row'),
      canvases: q('canvas'),
      svgs: q('svg'),
      title: document.title,
      bodyText: document.body.innerText.slice(0, 400),
    };
  });

  await page.screenshot({ path: path.join(outDir, 'board-shot.png'), fullPage: true });
  const tl = await page.$('.tl-body');
  if (tl) await tl.screenshot({ path: path.join(outDir, 'board-timeline.png') });
  const pv = await page.$('#stage, .stage, .pv');
  if (pv) await pv.screenshot({ path: path.join(outDir, 'board-preview.png') });

  // 导出 XML
  let xmlPath = null;
  try {
    await page.click('#xml');
    await new Promise((r) => setTimeout(r, 1200));
    const res = await page.evaluate(async () => {
      if (!window.__lastBlob) return null;
      return { name: window.__lastName, text: await window.__lastBlob.text() };
    });
    if (res && res.text) {
      xmlPath = path.join(outDir, 'export-test.xml');
      fs.writeFileSync(xmlPath, res.text);
      console.log(`导出 XML：${res.name}（${Math.round(res.text.length / 1024)} KB）→ ${xmlPath}`);
    } else {
      console.log('导出 XML：没拿到 blob');
    }
  } catch (e) {
    errors.push('export: ' + e.message);
  }

  console.log(`页面标题：${stats.title}`);
  console.log(`引擎镜头数：${stats.shots} · 时间线 clip ${stats.clips}（${JSON.stringify(stats.byTrack)}）`
    + ` · 轨道行 ${stats.rows} · svg ${stats.svgs} · canvas ${stats.canvases}`);
  if (errors.length) {
    console.log('JS 错误：');
    for (const e of errors.slice(0, 12)) console.log('  ' + e);
  } else {
    console.log('JS 错误：无');
  }
  await browser.close();
  process.exit(errors.length ? 1 : 0);
})();
