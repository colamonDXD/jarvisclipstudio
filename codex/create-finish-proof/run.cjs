const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium } = require('../JarvisClipStudio/frontend/node_modules/@playwright/test');
const base = 'http://localhost:3107';

async function main() {
  const browser = await chromium.launchPersistentContext(path.join(require('node:os').tmpdir(), 'snipnova-create-verification'), { channel: 'msedge', headless: true, viewport: { width: 1440, height: 1000 } });
  const page = await browser.newPage();
  try {
    if (!(await (await page.request.get(base + '/api/auth/get-session')).json())?.user) {
      const registration = await page.request.post(base + '/api/auth/sign-up/email', {
      headers: { Origin: base },
      data: { name: 'Create render verification', email: `create-proof-${Date.now()}@example.test`,
        password: crypto.randomBytes(24).toString('hex') },
    });
      if (!registration.ok()) throw Error('Local registration: ' + registration.status());
    }
    await page.goto(base);
    await page.locator('#video-upload').setInputFiles(path.resolve(__dirname, '../verified-kai/caption-proof/window_002.mp4'));
    await page.getByRole('radio', { name: /Manual Cutter/ }).click();
    await page.locator('#manual-ranges').fill('0:10-0:40');
    await page.getByRole('radio', {name:'Auto template',exact:true}).click();
    const draw = page.getByRole('button', { name: 'Draw boxes', exact: true });
    const framing = page.getByRole('button', { name: /Framing & format/ });
    if (await framing.getAttribute('aria-expanded') === 'false') await framing.click();
    await draw.click();
    if (await page.getByRole('radio', { name: /Face on top.*Red/i }).getAttribute('aria-checked') !== 'true') throw Error('Layout did not change');
    const canvas = page.getByTestId('box-canvas');
    if (!await canvas.isVisible()) await page.getByRole('button', { name: 'Grab frame' }).click();
    await canvas.scrollIntoViewIfNeeded();
    const box = await canvas.boundingBox();
    for (const [x,y,w,h] of [[.003,.401,.2,.202],[.24,.06,.54,.9]]) {
      await page.mouse.move(box.x+x*box.width,box.y+y*box.height);
      await page.mouse.down();
      await page.mouse.move(box.x+(x+w)*box.width,box.y+(y+h)*box.height,{steps:10});
      await page.mouse.up();
    }
    await page.getByText('These crops will be used for your clips.').waitFor();
    await canvas.screenshot({path:path.join(__dirname,'drawn-boxes.png')});
    const response = page.waitForResponse(r=>r.url().endsWith('/api/tasks/create'),{timeout:120000});
    await page.getByRole('button',{name:'Generate clips',exact:true}).click();
    const result = await response;
    if (!result.ok()) throw Error('Create returned ' + result.status());
    await page.waitForURL('**/tasks/*');
    const task = new URL(page.url()).pathname.split('/').pop();
    fs.writeFileSync(path.join(__dirname,'submitted.json'),JSON.stringify({task,request:result.request().postDataJSON()},null,2));
    console.log('Created job', task);
  } catch (e) {
    console.error(page.url(), (await page.locator('body').innerText()).slice(0,800));
    throw e;
  } finally { await browser.close(); }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
