import { chromium } from 'file:///C:/Users/ASUS/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';
import { createServer } from '../node_modules/vite/dist/node/index.js';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const out = new URL('../../test-results/library-covers/', import.meta.url);
await mkdir(out, {recursive:true});
const server = await createServer({root:new URL('../',import.meta.url).pathname.replace(/^\/(\w:)/,'$1'), server:{host:'127.0.0.1',port:55173}}); await server.listen();
const base='http://127.0.0.1:'+server.httpServer.address().port;
const browser=await chromium.launch({headless:true,channel:'msedge'});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[]; page.on('pageerror',e=>errors.push(e.message));
const project={id:'cover-test',name:'封面展示验收',code:'TEST',settings:{}};
const cover=base+'/images/studio-scenes.webp';
let prompts=[{id:'p1',code:'P-1',name:'电影感山野场景',positive:'自然光照亮山野，远景构图，柔和的色彩与电影感光影。',negative:'',tags:[],cover_image:cover},{id:'p2',code:'P-2',name:'柔和人物肖像',positive:'柔和侧光，浅景深，保留自然的人物细节。',negative:'',tags:[],cover_image:''}];
const skills=[{id:'s1',revision:1,name:'场景氛围与镜头设计',description:'把故事中的地点、光线和情绪整理成可使用的镜头语言。',instructions:'读取故事，提炼画面。',category:'shot',source:'项目方法',required_tools:[],file_count:1,project_id:project.id,cover_image:cover},{id:'s2',revision:1,name:'角色设定精修',description:'整理角色的服装与识别特征，保持人物设定一致。',instructions:'整理角色。',category:'shot',source:'项目方法',required_tools:[],file_count:1,project_id:project.id,cover_image:''}];
let failSave=false;
await page.addInitScript(()=>{localStorage.setItem('inspiration_token','isolated-preview');localStorage.setItem('inspiration_sidebar_collapsed','1');});
await page.route('**/api/v1/**',async route=>{
 const path=new URL(route.request().url()).pathname; let data=[],status=200;
 if(path.endsWith('/me'))data={user:{id:'u1',username:'review',display_name:'创作者'},memberships:[{project_id:project.id,role:'admin'}]};
 else if(path.endsWith('/projects'))data=[project];
 else if(path.endsWith('/projects/'+project.id))data=project;
 else if(path.endsWith('/prompts/p1')) {if(failSave){status=500;data={error:{message:'测试保存失败'}};}else{prompts[0]={...prompts[0],...route.request().postDataJSON()};data=prompts[0];}}
 else if(path.endsWith('/prompts'))data=prompts;
 else if(path.endsWith('/skills/catalog'))data={items:skills,total:2};
 else if(path.endsWith('/skills/s1'))data=skills[0];
 await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
});
try {
 for(const route of ['prompts','skills']){
  await page.goto(`${base}/projects/${project.id}/${route}`);
  await page.locator('.effect-cover img').first().waitFor();
  await page.locator('.effect-cover img').first().evaluate(img=>img.decode());
  for(const theme of ['light','dark']){
   await page.evaluate(t=>{document.documentElement.classList.remove('light','dark');document.documentElement.classList.add(t)},theme);
   await page.waitForTimeout(300);
   await page.screenshot({path:new URL(`${route}-${theme}.png`,out).pathname.replace(/^\/(\w:)/,'$1'),fullPage:true});
  }
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:new URL(`${route}-mobile.png`,out).pathname.replace(/^\/(\w:)/,'$1'),fullPage:true});
  if(route === "skills") {
   await page.locator(".knowledge-cover-card").first().scrollIntoViewIfNeeded();
   await page.screenshot({path:new URL("skills-mobile-card.png",out).pathname.replace(/^\/(\w:)/,"$1")});
  }
  await page.setViewportSize({width:1440,height:1000});
 }
 await page.goto(`${base}/projects/${project.id}/prompts`);
 await page.getByRole('button',{name:'查看提示词 电影感山野场景',exact:true}).click();
 await page.getByRole('button',{name:'编辑提示词',exact:true}).click();
 await page.locator('input[type=file]').setInputFiles(new URL('../public/images/studio-scenes.webp',import.meta.url).pathname.replace(/^\/(\w:)/,'$1'));
 await page.waitForFunction(()=>document.querySelector('.effect-cover-editor img')?.src.startsWith('data:image/jpeg;base64,'));
 failSave=true; await page.getByRole('button',{name:'保存提示词',exact:true}).click();
 await page.getByRole('alert').filter({hasText:'保存失败'}).waitFor();
 assert.ok(await page.locator('.effect-cover-editor img').count());
 failSave=false; await page.getByRole('button',{name:'保存提示词',exact:true}).click();
 await page.getByRole('dialog').waitFor({state:'hidden'});
 assert.ok(prompts[0].cover_image.startsWith('data:image/jpeg;base64,'));
 await page.reload();
 await page.locator('.effect-cover img').first().waitFor();
 assert.equal(await page.locator('.effect-cover img').first().getAttribute('src'),prompts[0].cover_image);
 await page.route('**/broken.jpg',r=>r.abort());
 prompts[0].cover_image=base+'/broken.jpg'; await page.reload();
 await page.getByText('效果图暂时无法显示').waitFor();
 assert.deepEqual(errors,[]);
 await writeFile(new URL('checks.json',out),JSON.stringify({passed:['desktop light/dark','390px no overflow','local image import','save failure retains image','save and reload','broken image fallback'],errors},null,2));
 console.log('Cover UI checks passed');
} finally {await browser.close();await server.close();}
