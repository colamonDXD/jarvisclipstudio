const {chromium} = require('../JarvisClipStudio/frontend/node_modules/@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const task = process.argv[2];
const base = 'http://localhost:3107/api/tasks/' + task;
async function main() {
  const browser = await chromium.launchPersistentContext(path.join(require('node:os').tmpdir(), 'snipnova-create-verification'), {channel:'msedge',headless:true});
  try {
    if (process.argv.includes('--retry')) {
      const retry = await browser.request.post(base+'/resume');
      if (!retry.ok()) throw Error('Retry failed: '+retry.status()+' '+await retry.text());
    }
    let previous;
    for (;;) {
      const response = await browser.request.get(base);
      if (response.status() >= 500) {
        await new Promise(resolve=>setTimeout(resolve,10000));
        continue;
      }
      if (!response.ok()) throw Error('Task status ' + response.status());
      const state = await response.json();
      const status = [state.status,state.progress,state.progress_message].join(' ');
      if (previous !== status) console.log(status);
      previous = status;
      if (state.status === 'completed') break;
      if (['error','failed','cancelled'].includes(state.status)) throw Error(state.error || status);
      await new Promise(resolve=>setTimeout(resolve,10000));
    }
    const clips = (await (await browser.request.get(base+'/clips')).json()).clips;
    if (!clips?.length) throw Error('Completed without a clip');
    for (const [index,clip] of clips.entries()) {
      const media = await browser.request.get(base+'/clips/'+clip.id+'/file');
      if (!media.ok()) throw Error('Clip download '+media.status());
      fs.writeFileSync(path.join(__dirname,'kai-'+(index+1)+'.mp4'),await media.body());
      console.log(JSON.stringify({id:clip.id,duration:clip.duration,filename:clip.filename}));
    }
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
