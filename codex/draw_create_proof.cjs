const path = require('node:path');
const fs = require('node:fs');
const dir = path.join(__dirname, 'JarvisClipStudio/frontend');
const out = path.join(__dirname, 'verified-kai/caption-proof/title-off');
const {build} = require(path.join(dir, 'node_modules/.pnpm/esbuild@0.28.1/node_modules/esbuild'));
const {chromium} = require(path.join(dir, 'node_modules/@playwright/test'));
(async () => {
  const result = await build({absWorkingDir:dir, stdin:{contents:`
    import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
    import {BoxDraw} from './src/app/live/box-draw';
    function Form(){const [file,setFile]=useState(null);const [boxes,change]=useState(null);
      return <><input type="file" onChange={e=>setFile(e.target.files[0])}/><BoxDraw create url="" file={file} layout="facecam_red" onChange={change}/><output>{JSON.stringify(boxes)}</output></>}
    createRoot(document.getElementById('root')).render(<Form/>);
  `,resolveDir:dir,loader:'tsx'},alias:{'@':path.join(dir,'src')},bundle:true,jsx:'automatic',write:false,define:{'process.env.NODE_ENV':'"production"'}});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1250,height:960}});
    await page.setContent('<style>canvas[data-testid]{width:640px;height:auto;display:block}</style><div id="root"></div>');
    await page.addScriptTag({content:result.outputFiles[0].text});
    await page.locator('input[type=file]').setInputFiles(path.join(out,'../window_002.mp4'));
    const canvas=page.getByTestId('box-canvas'); await canvas.waitFor();
    const box=await canvas.boundingBox();
    for(const [x,y,w,h] of [[.003,.401,.200,.202],[.24,.06,.54,.90]]){
      await page.mouse.move(box.x+x*box.width,box.y+y*box.height);await page.mouse.down();
      await page.mouse.move(box.x+(x+w)*box.width,box.y+(y+h)*box.height,{steps:8});await page.mouse.up();
    }
    const arrangements=page.getByRole('combobox',{name:'Layout arrangement'});
    if(await arrangements.locator('option').count()!==11)throw Error('Missing layout');
    await arrangements.selectOption('bottom');
    await page.waitForFunction(()=>document.querySelector('output').textContent.includes('bottom'));
    fs.writeFileSync(path.join(out,'drawn-boxes.json'),await page.locator('output').textContent());
    await canvas.screenshot({path:path.join(out,'drawn-boxes.png')});
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
