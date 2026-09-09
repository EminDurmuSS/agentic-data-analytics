// Usage: node build-report.cjs /path/to/marked
// Generates a self-contained research report from the local Markdown sources.
const fs = require('node:fs');
const path = require('node:path');
const { marked } = require(process.argv[2] || 'marked');
const root = __dirname;
const docs = [
  ['PLAN.md', 'main', 'Ana plan'],
  ['current-system-audit.md', 'audit', 'Mevcut sistem: deneyler ve açıklar'],
  ['competition-audit.md', 'competition', 'Yarışma: sayfa ve zaman atıfları'],
  ['harness-research.md', 'harness', 'Claude, Qwen ve yürütme katmanı'],
  ['codex-patterns.md', 'codex', 'Codex: yarışmaya aktarım'],
  ['similar-systems.md', 'similar', 'WrenAI, Vanna, DB-GPT ve benchmarklar'],
];
const escape = value => value.replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const slug = text => text.toLocaleLowerCase('tr-TR').replace(/<[^>]*>/g, '').replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '');
const toc = [];
function render(filename, prefix) {
  let source = fs.readFileSync(path.join(root, filename), 'utf8');
  const footnotes = [];
  source = source.replace(/^\[\^([^\]]+)\]:[ \t]*(.*)(?:\n(?: {4}|\t).*)*/gm, (all, id, first) => {
    const rest = all.split('\n').slice(1).map(line => line.replace(/^( {4}|\t)/, '')).join('\n');
    footnotes.push([id, first + (rest ? '\n' + rest : '')]);
    return '';
  });
  source = source.replace(/\[\^([^\]]+)\]/g, (_, id) => `<sup><a href="#fn-${prefix}-${escape(id)}" aria-label="Kaynak ${escape(id)}">[${escape(id)}]</a></sup>`);
  const renderer = new marked.Renderer();
  renderer.heading = (text, level) => {
    const id = prefix + '-' + slug(text);
    if (prefix === 'main' && level === 2) toc.push([id, text]);
    return `<h${level} id="${id}">${text}</h${level}>\n`;
  };
  renderer.table = (header, body) => `<div class="table-scroll"><table><thead>${header}</thead><tbody>${body}</tbody></table></div>`;
  let html = marked.parse(source, {renderer, mangle:false, headerIds:false});
  if (footnotes.length) {
    html += '<ol class="footnotes">' + footnotes.map(([id, value]) => `<li id="fn-${prefix}-${escape(id)}" value="${/^\d+$/.test(id) ? id : '1'}">${marked.parse(value,{mangle:false,headerIds:false})}</li>`).join('') + '</ol>';
  }
  for (const [file, target] of docs) {
    html = html.replaceAll(`href="${file}"`, `href="#doc-${target}"`);
  }
  return html;
}
let main = render('PLAN.md', 'main');
const svg = fs.readFileSync(path.join(root, 'architecture.svg'), 'utf8');
main = main.replace(/<img src="architecture.svg"[^>]*>/, `<span class="diagram">${svg}</span>`);
const appendices = docs.slice(1).map(([file, id, label]) => `<details class="appendix" id="doc-${id}"><summary>${escape(label)}</summary><div class="appendix-body">${render(file,id)}</div></details>`).join('\n');
const nav = toc.map(([id,label]) => `<a href="#${id}">${label}</a>`).join('');
const html = `<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KKB Hackathon | Veri, analitik motor ve teslim planı</title>
<style>
:root{color-scheme:light;--ink:#181818;--muted:#555;--line:#d1d1d1}*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}body{margin:0;background:#fff;color:var(--ink);font:17px/1.66 Georgia,'Times New Roman',serif}a{color:inherit;text-underline-offset:3px}a:hover{color:#666}nav{position:fixed;top:0;bottom:0;left:0;width:238px;padding:35px 20px 30px 24px;overflow:auto;border-right:1px solid var(--line);font:13px/1.5 Arial,sans-serif}nav strong{display:block;font-size:15px;margin-bottom:18px}nav a{display:block;text-decoration:none;padding:7px 0;border-bottom:1px solid #eee}nav .appendix-link{margin-top:16px;font-weight:bold}main{max-width:1220px;margin-left:238px;padding:42px 62px 90px}.toolbar{display:flex;justify-content:flex-end;gap:18px;align-items:center;margin-bottom:24px;font:13px Arial,sans-serif}.toolbar button{font:inherit;border:1px solid #888;background:#fff;color:#222;padding:7px 11px;cursor:pointer;border-radius:0}.toolbar button:hover{background:#f0f0f0}h1,h2,h3,h4{font-family:Arial,sans-serif;line-height:1.22;font-weight:650;letter-spacing:-.02em}h1{font-size:38px;margin:0 0 23px;max-width:840px}h2{font-size:26px;margin:48px 0 18px;padding-top:20px;border-top:1px solid #aaa}h3{font-size:20px;margin:30px 0 14px}h4{font-size:17px}p{margin:15px 0}#doc-main>h1+p{font:13px/1.7 Arial,sans-serif;color:var(--muted);margin-bottom:25px}li{margin:7px 0}strong{font-weight:bold}code{font:12.5px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;background:#f1f1f1;padding:1px 3px;overflow-wrap:anywhere}pre{background:#f5f5f5;border:1px solid #ddd;padding:16px;overflow:auto;font-size:13px}pre code{padding:0;background:none}blockquote{border-left:2px solid #888;margin-left:0;padding-left:20px;color:#444}.table-scroll{overflow-x:auto;margin:22px 0}table{width:100%;border-collapse:collapse;font:13px/1.6 Arial,sans-serif}th{background:#f0f0f0;text-align:left;font-weight:bold;border-top:1px solid #333;border-bottom:1px solid #999}td,th{padding:11px 12px;vertical-align:top;border-bottom:1px solid #d9d9d9;min-width:115px}td code,th code{font-size:11px}sup{font:10px Arial,sans-serif;white-space:nowrap;vertical-align:super;margin-left:2px}sup a{text-decoration:none}.footnotes{font:12px/1.65 Arial,sans-serif;padding-left:23px}.footnotes li{padding-left:3px;margin-bottom:13px}.footnotes p{margin:3px 0}.footnotes a{overflow-wrap:anywhere}.diagram{display:block;width:100%;overflow-x:auto;margin:18px 0}.diagram svg{display:block;width:100%;height:auto;min-width:730px}.appendix{border-top:1px solid #bbb;padding:18px 0}.appendix summary{font:bold 18px/1.45 Arial,sans-serif;cursor:pointer}.appendix-body{padding-top:28px}.appendix-body h1{font-size:29px}.appendix-body h2{font-size:23px}.appendix-body{font-size:16px}.report-end{margin-top:40px;border-top:1px solid #333;padding-top:18px;font:12px/1.6 Arial,sans-serif;color:#555}h1,h2,h3,li[id],details{scroll-margin-top:24px}button:focus-visible,a:focus-visible,summary:focus-visible{outline:2px solid #333;outline-offset:4px}
@media(min-width:1500px){main{margin-left:calc(238px + (100vw - 1500px)/2)}}
@media(max-width:1050px){nav{display:none}main{margin:0 auto;padding:30px 28px 70px}h1{font-size:32px}}
@media(max-width:600px){body{font-size:16px}main{padding:25px 18px 60px}h1{font-size:29px}h2{font-size:23px}td,th{min-width:155px;padding:9px}.toolbar{justify-content:flex-start;flex-wrap:wrap}.diagram svg{min-width:760px}}
@media print{nav,.toolbar,.appendices-title,.appendix,.report-end{display:none!important}main{margin:0;padding:0;max-width:none}body{font-size:10.5pt;line-height:1.45}h1{font-size:25pt}h2{font-size:17pt;margin-top:24px}h3{font-size:13pt}table{font-size:8pt}td,th{padding:6px;min-width:0}tr{break-inside:avoid}h1,h2,h3{break-after:avoid}.diagram svg{min-width:0;max-height:650px}.footnotes{font-size:8pt}a{text-decoration:none}pre{white-space:pre-wrap} @page{size:A4;margin:16mm}}
</style></head><body>
<nav aria-label="İçindekiler"><strong>KKB Hackathon<br>Veri ve analitik platform</strong>${nav}<a class="appendix-link" href="#appendices">Kanıt dosyaları ve ayrıntılar</a></nav>
<main><div class="toolbar"><a href="PLAN.md">Markdown kaynağı</a><a href="current-system-audit.json">30 deneyin kaydı</a><button type="button" onclick="window.print()">Yazdır / PDF</button><button type="button" id="expand">Ekleri aç</button></div><article id="doc-main">${main}</article>
<h2 class="appendices-title" id="appendices">Kanıt dosyaları ve ayrıntılı incelemeler</h2>${appendices}
<p class="report-end">10 Eylül 2026. Ana rapor, mimari SVG ve ayrıntılı araştırma ekleri bu HTML dosyasına gömülüdür. Harici ağ bağlantısı olmadan okunabilir. Kaynak bağlantıları ve ham veri/JSON referansları ayrıca açılır.</p></main>
<script>
function reveal(){const target=document.getElementById(decodeURIComponent(location.hash.slice(1)));if(!target)return;let p=target;while(p){if(p.tagName==='DETAILS')p.open=true;p=p.parentElement}target.scrollIntoView({block:'start'})}
window.addEventListener('hashchange',reveal);window.addEventListener('load',reveal);
document.getElementById('expand').addEventListener('click',function(){const items=[...document.querySelectorAll('.appendix')];const shouldOpen=items.some(x=>!x.open);items.forEach(x=>x.open=shouldOpen);this.textContent=shouldOpen?'Ekleri kapat':'Ekleri aç'});
</script></body></html>`;
fs.writeFileSync(path.join(root,'index.html'),html);
console.log(JSON.stringify({file:path.join(root,'index.html'),bytes:Buffer.byteLength(html),sections:toc.length,appendices:docs.length-1}));
