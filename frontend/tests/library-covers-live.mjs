import { chromium } from 'file:///C:/Users/ASUS/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';
import { createServer } from '../node_modules/vite/dist/node/index.js';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const out = new URL('../../test-results/library-covers/', import.meta.url);
await mkdir(out, {recursive:true});
const server = await createServer({root:new URL('../',import.meta.url).pathname.replace(/^\/(\w:)/,'$1'), server:{host:'127.0.0.1',port:55173}}); await server.listen();
const base='http://127.0.0.1:'+server.httpServer.address().port;
const browser=await chromium.launch({headless:true,channel:'msedge'});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[]; page.on('pageerror',e=>errors.push(e.message));
const project={id:'cover-test',name:'封面展示验收',code:'TEST',settings:{}};
const cover=base+'/cover-fixtures/landscape';
const portrait = {
  id: 'p2', code: 'P-2', name: '真人写实人像摄影', tags: [],
  positive: '树荫下的人物回过头，发饰和衣袖都留在画面里。\n\n用浅景深分开人物和背景，让阳光在树叶间形成柔和的光斑。保留衣料的纹理，让人物的表情成为画面的焦点。',
  negative: '过曝、过度锐化', cover_image: base+'/cover-fixtures/portrait',
};
const landscape = {id:'p1',code:'P-1',name:'清晨山景',positive:'晨光落在海面上，远处的山脊留在薄雾中。',negative:'',tags:[],cover_image:cover};
const prompts = [portrait, landscape];
const fragments = [
  {id:'f1',code:'F-1',category:'lighting',name:'雨夜霓虹',text:'用街面的倒影带出青蓝色的光。',cover_image:base+'/cover-fixtures/square'},
  {id:'f2',code:'F-2',category:'camera',name:'宽银幕构图',text:'铺开场景，让环境参与叙事。',cover_image:base+'/images/studio-scenes.webp'},
  {id:'f3',code:'F-3',category:'style',name:'雨巷的纵深',text:'借两侧的灯光勾出街巷的纵深。',cover_image:base+'/cover-fixtures/tall'},
  {id:'f4',code:'F-4',category:'quality',name:'细节质感',text:'保留皮肤与衣料的自然纹理。',cover_image:''},
];
const scenes = await readFile(new URL('../public/images/studio-scenes.webp', import.meta.url));
const portraitPhoto = process.env.PROMPT_COVER_PORTRAIT_JPEG && await readFile(process.env.PROMPT_COVER_PORTRAIT_JPEG);
const samples = {
  landscape: [960,540,'1448 158 724 407'],
  square: [600,600,'724 0 724 724'],
  tall: [240,960,'260 0 181 724'],
  portrait: [544,681,'80 0 578 724'],
};
await page.route('**/cover-fixtures/*', route => {
  const name = new URL(route.request().url()).pathname.split('/').pop();
  if (name === 'portrait' && portraitPhoto) return route.fulfill({contentType:'image/jpeg', body:portraitPhoto});
  const [width, height, viewBox] = samples[name];
  return route.fulfill({contentType:'image/svg+xml', body:
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${viewBox}"><image width="2172" height="724" href="data:image/webp;base64,${scenes.toString('base64')}"/></svg>`});
});
const skills=[{id:'s1',revision:1,name:'场景氛围与镜头设计',description:'把故事中的地点、光线和情绪整理成可使用的镜头语言。',instructions:'读取故事，提炼画面。',category:'shot',source:'项目方法',required_tools:[],file_count:1,project_id:project.id,cover_image:cover},{id:'s2',revision:1,name:'角色设定精修',description:'整理角色的服装与识别特征，保持人物设定一致。',instructions:'整理角色。',category:'shot',source:'项目方法',required_tools:[],file_count:1,project_id:project.id,cover_image:''}];
let failSave=false;
await page.addInitScript(()=>{localStorage.setItem('inspiration_token','isolated-preview');localStorage.setItem('inspiration_sidebar_collapsed','1');});
await page.route('**/api/v1/**',async route=>{
 const path=new URL(route.request().url()).pathname; let data=[],status=200;
 if(path.endsWith('/me'))data={user:{id:'u1',username:'review',display_name:'创作者'},memberships:[{project_id:project.id,role:'admin'}]};
 else if(path.endsWith('/projects'))data=[project];
 else if(path.endsWith('/projects/'+project.id))data=project;
 else if(path.endsWith('/prompts/p1')) {if(failSave){status=500;data={error:{message:'测试保存失败'}};}else{data=Object.assign(landscape,route.request().postDataJSON());}}
 else if(path.endsWith('/prompts'))data=prompts;
 else if(path.endsWith('/prompt-fragments'))data=fragments;
 else if(path.endsWith('/capabilities'))data={modalities:['image'],features:[],param_schema:{}};
 else if(path.endsWith('/quota'))data={used_cost:0,limit_cost:200};
 else if(path.endsWith('/skills/catalog'))data={items:skills,total:2};
 else if(path.endsWith('/skills/s1'))data=skills[0];
 await route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
});
async function assertWholeImage(image) {
  await image.scrollIntoViewIfNeeded();
  const metrics = await image.evaluate(async img => {
    await img.decode();
    const {width, height} = img.getBoundingClientRect();
    return {width, height, ratio:img.naturalWidth / img.naturalHeight, fit:getComputedStyle(img).objectFit};
  });
  assert.ok(metrics.width > 0 && metrics.height > 0);
  assert.ok(Math.abs(metrics.width - metrics.height * metrics.ratio) < 2, JSON.stringify(metrics));
  assert.equal(metrics.fit, 'contain');
}
async function checkPromptLayouts() {
  await page.goto(`${base}/projects/${project.id}/prompts`);
  const card = name => page.getByRole('button', {name:`查看提示词 ${name}`, exact:true});
  const back = page.getByRole('button', {name:'返回提示词列表'});
  const detailImage = page.locator('.prompt-detail-content img');
  const text = page.locator('.prompt-detail-text');
  for (const entry of [...prompts, ...fragments].filter(entry => entry.cover_image)) {
    await assertWholeImage(card(entry.name).locator('img'));
  }
  const tallCard = card('雨巷的纵深');
  const tallPhoto = await tallCard.locator('img').boundingBox();
  assert.ok((await tallCard.locator('h3').boundingBox()).x > tallPhoto.x + tallPhoto.width, 'text should use the space next to a very narrow cover');
  await card('细节质感').getByText('尚未添加效果图').waitFor();
  await card(portrait.name).click();
  await assertWholeImage(detailImage);
  const picture = await detailImage.boundingBox();
  const frame = await page.locator('.prompt-detail-content .effect-cover').boundingBox();
  const copy = await text.boundingBox();
  assert.ok(Math.abs(frame.width - picture.width) < 2, 'the frame should hug the photograph');
  assert.ok(copy.x >= picture.x + picture.width && Math.abs(copy.y - picture.y) < 2, 'desktop portrait and text should sit side by side');
  await page.screenshot({path:fileURLToPath(new URL('portrait-detail.png', out))});
  const zoom = page.getByRole('button', {name:`放大查看${portrait.name}的效果图`});
  await zoom.focus();
  await page.keyboard.press('Enter');
  const lightbox = page.getByRole('dialog', {name:`${portrait.name}的效果图`});
  await assertWholeImage(lightbox.getByRole('img', {name:`${portrait.name}的效果图`, exact:true}));
  await page.route(portrait.cover_image, route => route.fulfill({status:403}), {times:1});
  await lightbox.getByRole('button', {name:'下载', exact:true}).click();
  await lightbox.getByRole('status').filter({hasText:'下载失败'}).waitFor();
  await page.keyboard.press('Escape');
  await lightbox.waitFor({state:'hidden'});
  assert.ok(await zoom.evaluate(button => button === document.activeElement));
  await page.setViewportSize({width:390,height:844});
  await assertWholeImage(detailImage);
  const mobilePhoto = await detailImage.boundingBox();
  const mobileCopy = await text.boundingBox();
  assert.ok(mobileCopy.y >= mobilePhoto.y + mobilePhoto.height, 'mobile text should follow the photograph');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({path:fileURLToPath(new URL('portrait-detail-mobile.png', out))});
  await zoom.click();
  const enlarged = lightbox.getByRole('img', {name:`${portrait.name}的效果图`, exact:true});
  await assertWholeImage(enlarged);
  assert.ok((await enlarged.boundingBox()).width > mobilePhoto.width + 12);
  await page.keyboard.press('Escape');
  await lightbox.waitFor({state:'hidden'});
  await page.setViewportSize({width:1440,height:1000});
  await back.click();
  assert.ok(await card(portrait.name).evaluate(button => button === document.activeElement));
  await card('宽银幕构图').click();
  await assertWholeImage(detailImage);
  const wide = await detailImage.boundingBox();
  assert.ok((await text.boundingBox()).y >= wide.y + wide.height, 'panoramas should use their own row');
  await back.click();
  await card('细节质感').click();
  assert.equal(await detailImage.count(), 0);
  assert.equal(await text.innerText(), fragments[3].text);

  await page.goto(base+'/tests/fixtures/generation-panel.html');
  for (const width of [390, 1440]) {
    await page.setViewportSize({width,height:width === 390 ? 844 : 1000});
    const instruction = page.getByLabel('本次生成指令', {exact:true});
    await instruction.fill('');
    await page.getByRole('button', {name:'从提示词库添加', exact:true}).click();
    const library = page.getByRole('dialog', {name:'从提示词库添加', exact:true});
    await library.getByRole('button', {name:`查看提示词 ${portrait.name}`}).click();
    await library.getByRole('button', {name:`放大查看${portrait.name}的效果图`}).click();
    await lightbox.waitFor();
    await page.keyboard.press('Escape');
    await lightbox.waitFor({state:'hidden'});
    await library.getByRole('button', {name:'追加到创作指令'}).click();
    assert.equal(await instruction.inputValue(), portrait.positive);
  }
}
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
 await checkPromptLayouts();
 await page.goto(`${base}/projects/${project.id}/prompts`);
 const landscapeCard = page.getByRole('button',{name:'查看提示词 清晨山景',exact:true});
 await landscapeCard.click();
 await page.getByRole('button',{name:'编辑提示词',exact:true}).click();
 await page.locator('input[type=file]').setInputFiles(new URL('../public/images/studio-scenes.webp',import.meta.url).pathname.replace(/^\/(\w:)/,'$1'));
 await page.waitForFunction(()=>document.querySelector('.effect-cover-editor img')?.src.startsWith('data:image/jpeg;base64,'));
 failSave=true; await page.getByRole('button',{name:'保存提示词',exact:true}).click();
 await page.getByRole('alert').filter({hasText:'保存失败'}).waitFor();
 assert.ok(await page.locator('.effect-cover-editor img').count());
 failSave=false; await page.getByRole('button',{name:'保存提示词',exact:true}).click();
 await page.getByRole('dialog').waitFor({state:'hidden'});
 assert.ok(landscape.cover_image.startsWith('data:image/jpeg;base64,'));
 await page.reload();
 await landscapeCard.locator('img').waitFor();
 assert.equal(await landscapeCard.locator('img').getAttribute('src'),landscape.cover_image);
 await page.route('**/broken.jpg',r=>r.abort());
 landscape.cover_image=base+'/broken.jpg'; await page.reload();
 await page.getByText('效果图暂时无法显示').waitFor();
 await landscapeCard.click();
 await page.getByText('效果图暂时无法显示').waitFor();
 assert.equal(await page.getByRole('button',{name:/放大查看/}).count(),0);
 assert.deepEqual(errors,[]);
 await writeFile(new URL('checks.json',out),JSON.stringify({passed:['desktop light/dark','390px no overflow','landscape, portrait, square, tall and panoramic covers','detail layout on desktop and mobile','keyboard image preview','append from the generation panel','local image import','save failure retains image','save and reload','empty and broken image fallback'],errors},null,2));
 console.log('Cover UI checks passed');
} finally {await browser.close();await server.close();}
