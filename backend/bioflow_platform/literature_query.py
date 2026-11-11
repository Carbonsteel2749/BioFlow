"""One bilingual query representation for paper search and evidence retrieval."""
import re
import time
import httpx

# Concept expressions are shared by both languages; longer phrases take priority.
CONCEPTS = {
    'autism': ['autism spectrum disorder', 'autism spectrum disorders', 'autistic', 'autism', 'asd', '孤独症谱系障碍', '自闭症谱系障碍', '孤独症', '自闭症'],
    'gut microbiota': ['intestinal microbiota', 'intestinal flora', 'gut microbiome', 'gut microbiota', '肠道微生物群', '肠道微生物', '肠道菌群', '肠道菌', '肠道微生态'],
    'fecal microbiota transplantation': ['fecal microbiota transplantation', 'faecal microbiota transplantation', 'fecal transplantation', 'fmt', '粪菌移植', '粪便微生物移植', '菌群移植'],
    'short chain fatty acid': ['short-chain fatty acids', 'short chain fatty acids', 'short-chain fatty acid', 'short chain fatty acid', 'scfas', 'scfa', '短链脂肪酸'],
    'probiotic': ['probiotics', 'probiotic', '益生菌'],
    'prebiotic': ['prebiotics', 'prebiotic', '益生元'],
    'children': ['children', 'child', 'pediatric', '儿童', '患儿'],
    'symptom': ['symptoms', 'symptom', '症状', '表现'],
    'intervention': ['intervention', 'treatment', '干预', '治疗'],
    'effect': ['effects', 'effect', '影响', '效果', '作用'],
    'butyrate': ['butyrate', 'butyric acid', '丁酸'],
    'propionate': ['propionate', 'propionic acid', '丙酸'],
    'acetate': ['acetate', 'acetic acid', '乙酸'],
    'gut brain axis': ['gut-brain axis', 'gut brain axis', '肠脑轴'],
    'animal': ['animal', 'mice', 'mouse', '小鼠', '动物'],
    'randomized trial': ['randomized controlled trial', 'randomised controlled trial', '随机对照试验'],
    'systematic review': ['systematic review', '系统综述'],
    'meta analysis': ['meta-analysis', 'meta analysis', '荟萃分析', '元分析'],
    'metabolite': ['metabolites', 'metabolite', '代谢物'],
    'diversity': ['diversity', '多样性'],
    'inflammation': ['inflammation', '炎症'],
    'evidence': ['evidence', '证据'],
    'mechanism': ['mechanisms', 'mechanism', '机制'],
    'social behavior': ['social behaviour', 'social behavior', '社交行为', '社会行为'],
    'repetitive behavior': ['repetitive behavior', '重复刻板行为', '刻板行为'],
    'human': ['humans', 'human', '人群', '人类'],
    'antibiotic': ['antibiotics', 'antibiotic', '抗生素'],
    'tryptophan': ['tryptophan', '色氨酸'],
    'serotonin': ['serotonin', '5-羟色胺', '五羟色胺'],
    'bifidobacterium': ['bifidobacteria', 'bifidobacterium', '双歧杆菌'],
    'lactobacillus': ['lactobacilli', 'lactobacillus', '乳杆菌'],
    'limosilactobacillus reuteri': ['limosilactobacillus reuteri', 'lactobacillus reuteri', '罗伊氏乳杆菌'],
    'intestinal permeability': ['intestinal permeability', 'gut permeability', '肠道通透性'],
}
STOP = set('the a an and or of in on to with for is are does do what how effects effect symptom intervention'.split())
CACHE = {}


def normalize(text):
    value=text.lower()
    replacements=sorted(((alias,concept) for concept,aliases in CONCEPTS.items() for alias in aliases),key=lambda p:len(p[0]),reverse=True)
    pattern='|'.join(re.escape(alias) if re.search('[\u3400-\u9fff]',alias) else r'(?<![a-z])'+re.escape(alias)+r'(?![a-z])' for alias,_ in replacements)
    mapping=dict(replacements)
    return re.sub(pattern,lambda m:' '+mapping[m.group()]+' ',value)


def tokens(text):
    return [t for t in re.findall(r'[a-z0-9]+',normalize(text)) if len(t)>1 and t not in STOP]


async def prepare(query,mt_url='http://127.0.0.1:8090'):
    query=query.strip(); has_chinese=bool(re.search('[\u3400-\u9fff]',query))
    key=(query,mt_url)
    if key in CACHE and time.monotonic()-CACHE[key][0]<300:
        result=CACHE[key][1];return {**result,'issues':list(result['issues'])}
    normalized=normalize(query); issues=[]; method='original'
    filler=r'今天|请问|请|帮我|查找|检索|相关|关于|有没有|有什么|是否|如何|为什么|的|与|和|对|中|有|吗|是|能|可以|研究|文献|论文|分析|哪些|什么|了|及|在|想|了解|补充|改善|进行|一个|一下|能够|之间|关联'
    unknown=''.join(re.findall('[\u3400-\u9fff]+',re.sub(filler,'',normalized)))
    # Known medical expressions are deterministic; translation handles remaining qualifiers.
    dictionary_complete=has_chinese and not unknown and bool(tokens(normalized))
    if dictionary_complete:method='domain_dictionary'
    if has_chinese and not dictionary_complete:
        try:
            async with httpx.AsyncClient(timeout=8,trust_env=False) as client:
                # Translate remaining Chinese qualifiers, never retranslate recognized medical terms.
                response=await client.post(mt_url.rstrip('/')+'/translate',json={'text':unknown})
                response.raise_for_status();data=response.json()
                translated=data.get('translation') or data.get('translated_text') or data.get('translatedText') or data.get('text')
                if not translated or re.search('[\u3400-\u9fff]',translated):raise ValueError('No English translation')
                anchored=' '.join(re.findall(r'[a-zA-Z][a-zA-Z -]*',normalized))
                normalized=normalize(translated)+' '+anchored;method='translation'
        except (httpx.HTTPError,ValueError,TypeError):
            # Strip only common question phrasing; retain unknown Chinese as a visible warning.
            remainder=re.sub(filler,'',normalized)
            unknown=''.join(re.findall('[\u3400-\u9fff]+',remainder))
            normalized=re.sub('[\u3400-\u9fff]+',' ',normalized);method='domain_dictionary'
            if unknown:issues.append('翻译服务暂不可用，未识别的中文限定：'+unknown+'；可补充英文关键词。')
    english=' '.join(dict.fromkeys(tokens(normalized)))
    if not english:issues.append('未识别到可用于英文文献检索的术语，请补充关键词。')
    result={'original':query,'english':english,'method':method,'issues':issues}
    if len(CACHE)>=512:CACHE.pop(next(iter(CACHE)))
    CACHE[key]=(time.monotonic(),result)
    return {**result,'issues':list(issues)}
