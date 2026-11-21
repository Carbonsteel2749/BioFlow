const fs=require('fs'),vm=require('vm'),assert=require('assert');
const lib={default_article_type:'research_article',
  article_types:[{id:'research_article',title_zh:'研究论文',title_en:'Research Article',examples:'Microbiome',sections:['abstract','methods','results']},
                 {id:'data_descriptor',title_zh:'数据描述论文',title_en:'Data Descriptor',examples:'Scientific Data',sections:['abstract','data_records','funding']}],
  sections:[{id:'abstract',title_zh:'摘要',title_en:'Abstract',status:'ready'},{id:'methods',title_zh:'方法',title_en:'Methods',status:'ready'},
            {id:'results',title_zh:'结果',title_en:'Results',status:'ready'},{id:'data_records',title_zh:'数据记录',title_en:'Data Records',status:'skill_missing',purpose:'files <x>'},
            {id:'funding',title_zh:'资助',title_en:'Funding',status:'author_input'}]};
const context=vm.createContext({});
vm.runInContext(`const state={};const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));`+
  fs.readFileSync('frontend/writing-sections.js','utf8'),context);
context.lib=lib;
const r=vm.runInContext(`({
  def:writingSectionDefaults(lib,'data_descriptor'),fallback:writingSectionDefaults(lib,'nope'),
  box:writingSectionsBox(lib),
  rows:writingSectionRows(lib,['results','abstract','ghost']),
  saved:(state.writingSectionDraft={article_type:'data_descriptor',sections:['funding','abstract']},writingSectionsBox(lib))
})`,context);
assert.deepStrictEqual([...r.def],['abstract','data_records','funding']);
assert.deepStrictEqual([...r.fallback],['abstract','methods','results']);
const order=html=>[...html.matchAll(/data-section="([^"]+)"/g)].map(m=>m[1]);
const checked=html=>[...html.matchAll(/value="([^"]+)" checked/g)].map(m=>m[1]);
assert.deepStrictEqual(order(r.box),['abstract','methods','results','data_records','funding']);
assert.deepStrictEqual(checked(r.box),['abstract','methods','results']);
assert(r.box.includes('<option value="research_article" selected>'));
// chosen order first (unknown ids dropped), unchosen after, statuses labelled and escaped
assert.deepStrictEqual(order(r.rows),['results','abstract','methods','data_records','funding']);
assert(r.rows.includes('此章节尚未接通')&&r.rows.includes('需要作者提供事实')&&r.rows.includes('files &lt;x&gt;'));
assert(r.box.includes('name="author-funding"'));
// a saved draft restores type and order
assert(r.saved.includes('<option value="data_descriptor" selected>'));
assert.deepStrictEqual(checked(r.saved),['funding','abstract']);
console.log('writing-sections ok');
