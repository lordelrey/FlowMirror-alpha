const $ = id => document.getElementById(id);
let offset=0, total=0, token=0;
const text = (parent,tag,value,cls) => {const n=document.createElement(tag);n.textContent=value;if(cls)n.className=cls;parent.appendChild(n);return n;};
async function get(url){const r=await fetch(url);if(!r.ok)throw new Error('数据未配置或请求失败');return r.json();}
function picture(parent,ref){const img=document.createElement('img');img.loading='lazy';img.alt='本地原始素材';img.src='/api/community/asset/'+encodeURIComponent(ref);img.addEventListener('error',()=>img.replaceWith('图片未加载'));parent.appendChild(img);}
async function load(){
  const t=++token; $('status').textContent='读取本地素材…';
  const q=new URLSearchParams({offset,limit:24});
  for(const id of ['channel','timing','start','end','query'])q.set(id,$(id).value);
  try{
    const data=await get('/api/community/corpus/posts?'+q);if(t!==token)return;
    total=data.total;$('posts').replaceChildren();
    for(const post of data.items){
      const article=document.createElement('article');$('posts').appendChild(article);
      if(post.image_refs?.length)picture(article,post.image_refs[0]);
      const body=document.createElement('div');body.className='body';article.appendChild(body);
      text(body,'p',post.org||'机构未记录','muted');text(body,'h2',post.title||'原始素材（标题缺失）');
      text(body,'p',post.published_at||'缺少发布时间，不纳入历史排期','muted');
      text(body,'p',(post.caption||'').slice(0,180));
      const details=document.createElement('details');text(details,'summary',`展开全文与图片（${post.image_refs?.length||0}张）`);body.appendChild(details);
      text(details,'p',post.caption||'原始正文缺失','text');
      if(post.ocr_text){text(details,'h3','既有 OCR');text(details,'p',post.ocr_text,'text');}
      if(post.image_caption_frozen){text(details,'h3','既有图像描述');text(details,'p',post.image_caption_frozen,'text');}
      let expanded=false;details.addEventListener('toggle',()=>{if(details.open&&!expanded){for(const ref of (post.image_refs||[]).slice(1))picture(details,ref);expanded=true;}});
    }
    $('page').textContent=`${total?offset+1:0}–${Math.min(offset+24,total)} / ${total} 条`;
    $('prev').disabled=offset===0;$('next').disabled=offset+24>=total;
    $('status').textContent=data.notice;
  }catch(e){if(t===token)$('status').textContent=e.message;}
}
$('filters').addEventListener('submit',e=>{e.preventDefault();offset=0;load();});
$('channel').addEventListener('change',()=>{const assets=$('channel').value==='assets';for(const id of ['timing','start','end'])$(id).disabled=assets;});
$('prev').addEventListener('click',()=>{offset=Math.max(0,offset-24);load();});
$('next').addEventListener('click',()=>{offset+=24;load();});
get('/api/community/corpus/summary').then(data=>{
 for(const [name,key] of [['小红书记录','xhs_note'],['全部素材','asset'],['历史净值','fund_nav'],['季度面板','fund_flow_panel']]){
  const box=document.createElement('div');box.className='metric';$('inventory').appendChild(box);text(box,'span',name);text(box,'strong',data.tables[key]);
 }
}).catch(e=>$('status').textContent=e.message);
load();
