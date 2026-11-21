const fs=require('fs'),vm=require('vm'),assert=require('assert');
// Load the studio with the few globals it touches at load time stubbed out.
const context=vm.createContext({});
vm.runInContext(`const state={};const views={};const actions={};const forms={section:async()=>{}};const root={addEventListener(){},querySelectorAll(){return [];}};
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const button=(label,action,extra='')=>\`<button data-action="\${action}" \${extra}>\${label}</button>\`;`+
  fs.readFileSync('frontend/writing-studio.js','utf8'),context);
const run=code=>vm.runInContext(code,context);

// placeholders: bilingual duplicates collapse to one wording
const ph=run(String.raw`wsPlaceholders("[AUTHOR_INPUT_NEEDED: ethics approval] text\n\n[AUTHOR_INPUT_NEEDED: 伦理审批] 中文\n[SKILL_PENDING: keywords-writer] Keywords")`);
assert.deepStrictEqual([...ph.author],['伦理审批']);
assert.deepStrictEqual([...ph.skill],['keywords-writer']);
assert.strictEqual(run(String.raw`wsPlaceholders('plain').author.length`),0);

// generation stages follow the progress text written by session_store
assert.strictEqual(run(String.raw`wsStage({status:'running',progress:'正在准备稿件'}).stage`),0);
assert.strictEqual(run(String.raw`wsStage({status:'running',progress:'正在写结果（3/7）'}).stage`),1);
assert.strictEqual(run(String.raw`wsStage({status:'running',progress:'正在准备审稿与定向修改'}).stage`),2);
assert.strictEqual(run(String.raw`wsStage({status:'running',progress:'正在准备中文译文'}).stage`),3);
assert.strictEqual(run(String.raw`wsStage({status:'ready'}).ratio`),1);

// quality summary parsed from the two pipeline reports
const q=run(String.raw`wsQuality([{name:'storms_checklist.md',markdown:'# x\n\nReported 12 · author input needed 5 · missing 3 (of 20)\n'},
  {name:'peer_review.md',markdown:'## 1. [minor] abstract — redundant\nAction: revised\n\n## 2. [major] introduction — unsupported\nAction: unchanged (no fix)\n'}])`);
assert.deepStrictEqual(JSON.parse(JSON.stringify(q)),{storms:{reported:12,author:5,missing:3,total:20},review:{total:2,major:1,revised:1}});

// figures counted once even when both language halves embed them; citation keys split
assert.strictEqual(run(String.raw`wsFigureCount(['![a](figures/f1.png) ![a](figures/f1.png)','![b](figures/f2.png)'])`),2);
assert.deepStrictEqual([...run(String.raw`wsCiteKeys('x [@Wood2019; @Beghini2021] y [@Wood2019]')`)],['Wood2019','Beghini2021']);

// markdown renderer: escaping, tables with status pills, report fields, quotes
const html=run(String.raw`wsMarkdown('# Title\n\n> quoted <b>\n\nProblem: too long\nAction: revised\n\n| Item | Status |\n|---|---|\n| Ethics | 待作者补充 (author_input) |\n\n- one\n- two')`);
assert(html.includes('<h3>Title</h3>'));
assert(html.includes('<blockquote>quoted &lt;b&gt;</blockquote>'));
assert(html.includes('<b class="ws-field">Problem</b> too long')&&html.includes('<b class="ws-field">Action</b> revised'));
assert(html.includes('<span class="ws-pill author_input">待作者补充</span>'));
assert(html.includes('<ul><li>one</li><li>two</li></ul>'));

// list status labels
assert.strictEqual(run(String.raw`wsStatus({status:'ready',total_sections:7,confirmed_count:7}).label`),'已全部确认');
assert.strictEqual(run(String.raw`wsStatus({status:'ready',total_sections:7,confirmed_count:2}).label`),'草稿 · 已确认 2/7');
assert.strictEqual(run(String.raw`wsStatus({status:'running'}).cls`),'run');

// language halves: edit one, keep the other byte-for-byte
context.labelled=['Results text.','','![f](figures/a.png)','','中文','','结果正文。','','![f](figures/a.png)'].join('\n');
context.marked=['<!-- lang:en -->','EN','','<!-- lang:zh -->','中','','<!-- lang:shared -->','![x](figures/x.png)',''].join('\n');
const lab=run(`(()=>{const p=wsSplitLang(labelled);
  return {mode:p.mode,en:wsLangText(p,'en'),zh:wsLangText(p,'zh'),back:wsJoinLang(p,'zh',wsLangText(p,'zh'))===labelled+'\\n',edited:wsSplitLang(wsJoinLang(p,'zh','新的中文。'))};})()`);
assert.strictEqual(lab.mode,'label');
assert.strictEqual(lab.en,'Results text.\n\n![f](figures/a.png)');
assert.strictEqual(lab.zh,'结果正文。\n\n![f](figures/a.png)');
assert(lab.back);
assert.strictEqual(lab.edited.en,lab.en);assert.strictEqual(lab.edited.zh,'新的中文。');
const mk=run(`(()=>{const p=wsSplitLang(marked);const j=wsSplitLang(wsJoinLang(p,'en','EN2'));return {p,j};})()`);
assert.deepStrictEqual(JSON.parse(JSON.stringify(mk.p)),{mode:'marked',en:'EN',zh:'中',shared:'![x](figures/x.png)'});
assert.deepStrictEqual(JSON.parse(JSON.stringify(mk.j)),{mode:'marked',en:'EN2',zh:'中',shared:'![x](figures/x.png)'});
assert.strictEqual(run(String.raw`wsSplitLang('只有中文的占位段落').mode`),'single');

// formatted chapter view: no markdown marks reach the author, sources kept in data-md
context.id=x=>encodeURIComponent(x);
const rich=run(String.raw`wsRichHtml('**Read quality.** Reads [@Wood2019; @Beghini2021] kept.\n\n[AUTHOR_INPUT_NEEDED: ethics]\n\n![Taxa](figures/fig_a.png)\n\n**Figure 1.** Taxa per sample.\n\nReferences\n\n[@Wood2019]: Wood DE. Kraken 2. 2019.','s1')`);
assert(!/\*\*|!\[|\]\(/.test(rich.replace(/data-md="[^"]*"/g,'')),'markdown marks leaked into the view');
assert(rich.includes('<strong>Read quality.</strong>'));
assert(rich.includes('data-md="[@Wood2019; @Beghini2021]">Wood2019; Beghini2021</span>'));
assert(rich.includes('class="ws-ph" contenteditable="false"')&&rich.includes('待作者填写：ethics'));
assert(rich.includes('src="/services/writing/api/sessions/s1/assets/fig_a.png"'));
assert(rich.includes('<b>Figure 1.</b> Taxa per sample.'));
assert(rich.includes('class="ws-r-ref"')&&rich.includes('>Wood DE. Kraken 2. 2019.</p>'));

// the studio replaces the writing view and the start dialog
assert.strictEqual(run(String.raw`typeof views.writing`),'function');
assert.strictEqual(run(String.raw`typeof actions['new-writing']`),'function');
console.log('writing-studio ok');
