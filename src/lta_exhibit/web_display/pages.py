"""Self-contained HTML for the seat screens, the staff page and the preview.

No external fonts, scripts or images: the venue runs on our own router with
no internet, and a page that waits on a CDN is a blank 4K monitor.

Sizing is in viewport units so the same page reads well on a 4K panel at any
OS scaling. Every touch target is huge, reacts on pointerdown (not click), and
the usual touch-kiosk hazards -- pinch zoom, long-press menus, text selection,
pull-to-refresh -- are switched off.
"""

from __future__ import annotations

import json

from lta_exhibit.theme import APP_SUBTITLE, APP_TITLE, SHOWCASE_LABEL

_BASE_CSS = r"""
:root{--bg:#0a0c11;--bg2:#121620;--ink:#f3efe6;--soft:#9aa3b2;--line:#252b38;--card:#151a24;
--nova:#f2a33a;--sage:#2ec4b6;--aegis:#5b8def;--accent:var(--sage);color-scheme:dark}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--ink);overflow:hidden;
font-family:"Segoe UI","Noto Sans","DejaVu Sans",system-ui,sans-serif;
-webkit-user-select:none;user-select:none;-webkit-tap-highlight-color:transparent;
touch-action:manipulation;overscroll-behavior:none}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer;touch-action:manipulation}
"""

_SEAT_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>__TITLE__ · __SEATNAME__</title><style>__BASE__
body{background:radial-gradient(120vmax 90vmax at 15% -10%,color-mix(in srgb,var(--accent) 22%,transparent),transparent 60%),
 radial-gradient(90vmax 70vmax at 110% 120%,#1a2233,transparent 60%),var(--bg)}
#app{position:fixed;inset:0;display:flex;flex-direction:column;padding:3.2vmin 4vmin 11vmin}  /* room for #tools */
header{display:flex;justify-content:space-between;align-items:baseline;font-size:1.9vmin;letter-spacing:.32em;
 text-transform:uppercase;color:var(--soft)}
header b{color:var(--ink);font-weight:700}
main{flex:1;position:relative;margin-top:2.4vmin}
.view{position:absolute;inset:0;display:flex;opacity:0;visibility:hidden;pointer-events:none;transform:scale(1.01);
 transition:opacity .6s ease,visibility 0s linear .6s,transform .6s ease}
.view.on{opacity:1;visibility:visible;pointer-events:auto;transform:scale(1);
 transition:opacity .6s ease,visibility 0s linear 0s,transform .6s ease}
/* identity */
.who{display:flex;flex-direction:column;justify-content:center;gap:1.6vmin}
.who .name{font-size:13vmin;font-weight:800;letter-spacing:.04em;line-height:.9;color:var(--accent)}
.who .title{font-size:3.4vmin;font-weight:600}
.who .arch{font-size:2.6vmin;color:var(--soft);letter-spacing:.2em;text-transform:uppercase}
.who .tag{font-size:3.2vmin;font-style:italic;margin-top:1.6vmin;max-width:36ch;line-height:1.3}
.orb{width:22vmin;height:22vmin;border-radius:50%;position:relative;flex:none;
 background:radial-gradient(circle at 35% 30%,color-mix(in srgb,var(--accent) 80%,#fff),var(--accent) 45%,color-mix(in srgb,var(--accent) 40%,#000));
 box-shadow:0 0 6vmin color-mix(in srgb,var(--accent) 55%,transparent);transition:transform .08s linear}
.orb::after{content:"";position:absolute;inset:-2.2vmin;border-radius:50%;border:.5vmin solid color-mix(in srgb,var(--accent) 45%,transparent)}
.orb.small{width:11vmin;height:11vmin}
/* attract */
#attract{gap:5vmin;align-items:stretch}
#attract .left{flex:0 0 40%;display:flex;flex-direction:column;justify-content:center;gap:4vmin;padding-left:3vmin}
#attract .right{flex:1;display:flex;flex-direction:column;justify-content:center;gap:2.2vmin}
.kicker{font-size:2.4vmin;letter-spacing:.3em;text-transform:uppercase;color:var(--accent);font-weight:700}
.h1{font-size:5.4vmin;font-weight:800;line-height:1.1}
.topic{display:flex;align-items:center;gap:3vmin;text-align:left;padding:3vmin 3.4vmin;border-radius:2.4vmin;
 background:linear-gradient(180deg,#1a2030,#131823);border:.25vmin solid var(--line);transition:transform .12s,border-color .2s,background .2s}
.topic .num{font-size:6vmin;font-weight:800;color:var(--accent);width:7vmin;flex:none;text-align:center}
.topic .t{font-size:4vmin;font-weight:750}
.topic .q{font-size:2.7vmin;color:var(--soft);margin-top:.6vmin}
.topic .go{margin-left:auto;font-size:5vmin;color:var(--accent)}
.topic.press{transform:scale(.97);border-color:var(--accent);background:#1d2536}
.hint{font-size:2.4vmin;color:var(--soft)}
.paused{font-size:4vmin;color:var(--soft);padding:4vmin;border:.25vmin dashed var(--line);border-radius:2vmin;text-align:center}
/* talk */
#talk{flex-direction:column;gap:3vmin}
.topicbar{display:flex;align-items:baseline;gap:2vmin}
.topicbar .t{font-size:3.6vmin;font-weight:800}
.topicbar .q{font-size:2.8vmin;color:var(--soft)}
.stage{flex:1;display:flex;align-items:center;gap:5vmin;padding-left:3vmin}
.caption{font-size:5.2vmin;line-height:1.28;font-weight:600;max-width:60ch}
.stage.listening .caption{font-size:3.8vmin;color:#d9d4ca;font-weight:500}
.stage.listening .orb{opacity:.35}
.speakertag{display:inline-flex;align-items:center;gap:1.2vmin;font-size:2.6vmin;letter-spacing:.24em;text-transform:uppercase;
 font-weight:700;margin-bottom:2vmin;padding:.8vmin 1.8vmin;border-radius:99vmin;background:#ffffff10}
.speakertag i{font-style:normal;font-size:3.4vmin}
.dot{width:1.6vmin;height:1.6vmin;border-radius:50%;background:currentColor}
/* vote */
#vote{flex-direction:column;gap:2.4vmin}
.vhead{display:flex;gap:3vmin;align-items:flex-start}
.vhead .d{font-size:3.9vmin;line-height:1.3;font-weight:600;flex:1}
.ring{width:15vmin;height:15vmin;flex:none;position:relative}
.ring svg{width:100%;height:100%;transform:rotate(-90deg)}
.ring .n{position:absolute;inset:0;display:grid;place-items:center;font-size:5.6vmin;font-weight:800}
.opts{flex:1;display:flex;flex-direction:column;gap:2vmin}
.opt{position:relative;flex:1;display:flex;align-items:center;gap:3vmin;padding:0 3.4vmin;border-radius:2.4vmin;overflow:hidden;
 border:.3vmin solid var(--line);background:#141924;text-align:left;transition:transform .12s,border-color .2s}
.opt .fill{position:absolute;inset:0 auto 0 0;width:0;background:color-mix(in srgb,var(--c) 26%,transparent);transition:width .6s cubic-bezier(.2,.8,.2,1)}
.opt .k{position:relative;font-size:7vmin;font-weight:800;color:var(--c);width:7vmin;text-align:center}
.opt .l{position:relative;font-size:3.6vmin;font-weight:650;line-height:1.2;flex:1}
.opt .who2{position:relative;font-size:2vmin;letter-spacing:.2em;text-transform:uppercase;color:var(--c);display:block;margin-top:.6vmin;font-weight:700}
.opt .pct{position:relative;font-size:4.4vmin;font-weight:800;min-width:12vmin;text-align:right;opacity:0;transition:opacity .4s}
.showpct .opt .pct{opacity:1}
.opt.press{transform:scale(.975);border-color:var(--c)}
.opt.mine{border-color:var(--c);box-shadow:0 0 0 .5vmin color-mix(in srgb,var(--c) 40%,transparent)}
.opt.win{border-color:var(--c);box-shadow:0 0 5vmin color-mix(in srgb,var(--c) 45%,transparent)}
.opt.lose{opacity:.45}
.vfoot{display:flex;justify-content:space-between;font-size:2.6vmin;color:var(--soft)}
.toast{position:absolute;left:50%;bottom:9vmin;transform:translate(-50%,3vmin);opacity:0;pointer-events:none;
 background:#0d1018ee;border:.3vmin solid var(--c,var(--accent));border-radius:2vmin;padding:2.4vmin 4vmin;text-align:center;
 transition:opacity .25s,transform .25s;min-width:60%;z-index:5}
.toast.on{opacity:1;transform:translate(-50%,0)}
.toast .a{font-size:4vmin;font-weight:800}
.toast .b{font-size:2.7vmin;color:var(--soft);margin-top:.8vmin}
/* result bars */
.res{display:none;gap:1.6vmin;flex-direction:column;padding:2.2vmin 2.6vmin;border-radius:2vmin;background:#ffffff08;border:.25vmin solid var(--line)}
.res .hd{font-size:2.4vmin;letter-spacing:.24em;text-transform:uppercase;color:var(--soft);font-weight:700}
.res .hd b{color:var(--c);font-size:3.2vmin;letter-spacing:.05em;text-transform:none}
.rb{display:flex;align-items:center;gap:2vmin;font-size:2.8vmin}
.rb .k{width:4vmin;font-weight:800;color:var(--c);font-size:3.4vmin}
.rb .bar{flex:1;height:3.4vmin;border-radius:99vmin;background:#ffffff10;overflow:hidden}
.rb .bar i{display:block;height:100%;width:0;background:var(--c);border-radius:99vmin;transition:width 1.2s cubic-bezier(.2,.8,.2,1)}
.rb .p{width:9vmin;text-align:right;font-weight:800}
.rb .lbl{width:52%;color:#d9d4ca;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rb.lose{opacity:.5}
/* result strip & close */
.strip{display:flex;gap:2vmin;align-items:center;font-size:3vmin;min-height:7vmin;padding:1.6vmin 2.4vmin;border-radius:1.6vmin;background:#ffffff0a}
.strip b{color:var(--c,var(--accent))}
#close{flex-direction:column;justify-content:center;gap:3.4vmin}
.panelq{font-size:5.4vmin;font-weight:700;line-height:1.25;max-width:40ch}
.tonight{display:flex;gap:2.4vmin}
.tonight .c{flex:1;padding:2.4vmin;border-radius:2vmin;background:#ffffff0a;border-top:.6vmin solid var(--c)}
.tonight .c .p{font-size:6vmin;font-weight:800;color:var(--c)}
.tonight .c .l{font-size:2.4vmin;color:var(--soft);margin-top:.6vmin;line-height:1.3}
#start{position:fixed;inset:0;z-index:50;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3vmin;
 background:#07090dee;text-align:center;cursor:pointer}
#start .big{font-size:7vmin;font-weight:800;color:var(--accent)}
#start .sm{font-size:2.6vmin;color:var(--soft);max-width:60ch;line-height:1.4}
#start.off{display:none}
#fs{position:fixed;right:1.4vmin;bottom:1.4vmin;z-index:40;font-size:2.2vmin;color:var(--soft);padding:1vmin 1.6vmin;
 border:.2vmin solid var(--line);border-radius:1vmin;background:#0d1018cc;display:none}
/* Controls, Ethics-player style: a pill at the bottom centre that fades away
   3 s after the last touch while the show runs, and comes back on any touch.
   Hidden means pointer-events:none, so that touch also reaches the screen. */
#tools{position:fixed;left:50%;bottom:2.2vmin;z-index:30;transform:translateX(-50%);display:flex;align-items:center;gap:1.4vmin;
 width:max-content;max-width:96vw;white-space:nowrap;padding:1.1vmin 1.4vmin;border-radius:999px;
 background:#121a28e0;border:.2vmin solid #ffffff1a;box-shadow:0 .6vmin 2.4vmin #0008;opacity:1;transition:opacity .45s ease}
#tools.idle,#fs.idle{opacity:0;pointer-events:none}
#fs{transition:opacity .45s ease}
body.hide-cursor,body.hide-cursor *{cursor:none}
#tools button{height:6.6vmin;padding:0 2.6vmin;border-radius:999px;font-size:2.4vmin;font-weight:800;letter-spacing:.08em;
 background:#ffffff1f;display:flex;align-items:center;gap:1vmin;transition:background .2s,transform .12s}
#tools button.press{transform:scale(.95);background:#ffffff38}
#tools svg{width:3.2vmin;height:3.2vmin;flex:none}
#homeBtn.off{display:none}
#volWrap{display:flex;align-items:center;gap:1.4vmin;padding:0 1.6vmin 0 1.2vmin;height:6.6vmin;transition:opacity .2s}
#volWrap.muted{opacity:.5}
#volWrap button{padding:0 1.4vmin!important;background:transparent!important}
#vol{-webkit-appearance:none;appearance:none;width:24vmin;height:1.1vmin;border-radius:999px;outline:none;
 background:linear-gradient(90deg,var(--accent) var(--p,100%),#ffffff33 var(--p,100%));touch-action:none}
#vol::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:4.4vmin;height:4.4vmin;border-radius:50%;background:#fff;border:0;box-shadow:0 0 .8vmin #0006}
#vol::-moz-range-thumb{width:4.4vmin;height:4.4vmin;border-radius:50%;background:#fff;border:0}
#volN{min-width:7vmin;font-size:2.2vmin;font-weight:800;color:var(--soft);text-align:right}
#sheet{position:fixed;inset:0;z-index:45;display:none;align-items:center;justify-content:center;background:#05070bcc}
#sheet.on{display:flex}
#sheet .box{background:#121620;border:.3vmin solid var(--accent);border-radius:2.4vmin;padding:4vmin 5vmin;text-align:center;max-width:70vw}
#sheet .q{font-size:4.2vmin;font-weight:750;line-height:1.25}
#sheet .s{font-size:2.6vmin;color:var(--soft);margin-top:1.4vmin}
#sheet .row{display:flex;gap:3vmin;justify-content:center;margin-top:4vmin}
#sheet .row button{font-size:3.4vmin;font-weight:750;padding:2.4vmin 4vmin;border-radius:1.8vmin;border:.3vmin solid var(--line);background:#1a2030}
#sheet .row button.yes{background:color-mix(in srgb,var(--accent) 30%,#1a2030);border-color:var(--accent)}
#conn{position:fixed;left:50%;top:1vmin;transform:translateX(-50%);font-size:1.6vmin;color:#ff8a80;display:none}
@media (orientation:portrait){#attract{flex-direction:column}#attract .left{flex:none}.who .name{font-size:10vmin}}
</style></head><body>
<div id="app">
 <header><span><b>__TITLE__</b> · __SUBTITLE__</span><span>__SHOWCASE__</span></header>
 <main>
  <section id="attract" class="view">
   <div class="left who">
    <div class="orb" id="orbA"></div>
    <div><div class="name" id="myName"></div><div class="title" id="myTitle"></div><div class="arch" id="myArch"></div>
    <div class="tag" id="myTag"></div></div>
   </div>
   <div class="right">
    <div class="kicker">Convene the robot C-suite</div>
    <div class="h1">Tap a leadership dilemma.<br>Then make the call.</div>
    <div id="topics" style="display:flex;flex-direction:column;gap:2vmin"></div>
    <div class="hint" id="attractHint"></div>
   </div>
  </section>
  <section id="talk" class="view">
   <div class="topicbar"><span class="t" id="tTitle"></span><span class="q" id="tQ"></span></div>
   <div class="stage" id="stage">
    <div class="orb" id="orbT"></div>
    <div style="flex:1"><div class="speakertag" id="spk"></div><div class="caption" id="cap"></div></div>
   </div>
   <div class="res" id="res"></div>
  </section>
  <section id="vote" class="view">
   <div class="vhead"><div style="flex:1"><div class="kicker" id="vKick">Your call</div><div class="d" id="dilemma"></div></div>
    <div class="ring" id="ring"><svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="44" fill="none" stroke="#ffffff18" stroke-width="8"/>
     <circle id="ringArc" cx="50" cy="50" r="44" fill="none" stroke="var(--accent)" stroke-width="8" stroke-linecap="round" stroke-dasharray="276.5" stroke-dashoffset="0"/></svg>
     <div class="n" id="ringN"></div></div></div>
   <div class="opts" id="opts"></div>
   <div class="vfoot"><span id="vTotal"></span><span id="vHint">Tap A, B or C. Everyone gets a vote: tap, then pass it on.</span></div>
   <div class="toast" id="toast"><div class="a" id="toastA"></div><div class="b" id="toastB"></div></div>
  </section>
  <section id="close" class="view">
   <div class="kicker">Bring this to tonight's panel</div>
   <div class="panelq" id="panelQ"></div>
   <div class="kicker" style="margin-top:2vmin;color:var(--soft)" id="tonightLbl"></div>
   <div class="tonight" id="tonight"></div>
   <div class="hint">Tap any screen in a moment to try another dilemma.</div>
  </section>
 </main>
</div>
<div id="tools"><button id="homeBtn" class="off"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg>TOPICS</button>
 <div id="volWrap"><button id="volIcon" aria-label="Mute"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M4 9v6h4l5 4V5L8 9H4z"/>
  <path id="w1" d="M15.5 8.5a4.5 4.5 0 010 7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
  <path id="w2" d="M18.5 5.5a8.5 8.5 0 010 13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg></button>
  <input id="vol" type="range" min="10" max="100" step="5" value="100" aria-label="Volume"><span id="volN"></span></div></div>
<div id="sheet"><div class="box"><div class="q">Leave this conversation and pick another dilemma?</div>
 <div class="s">The robots will stop and the topic cards will come back.</div>
 <div class="row"><button id="sheetNo">Keep listening</button><button id="sheetYes" class="yes">Yes, show the topics</button></div></div></div>
<div id="conn">reconnecting…</div>
<div id="start"><div class="big">Tap to start</div><div class="sm" id="startSm"></div></div>
<button id="fs">⛶ Full screen</button>
<script>
const SEAT="__SEAT__", ROLE="__ROLE__", ID=__IDENTITY__, TOPICS=__TOPICS__, IS_AUDIO=__IS_AUDIO__;
// Served either at / or under a prefix (https://rm2.aiccore-uno.ai/lta/ via the gateway).
const BASE=location.pathname.replace(/\/seat\/[^/]*\/?$/,'');
const ROLES=Object.keys(ID).sort((a,b)=>ID[a].position-ID[b].position);
const $=s=>document.getElementById(s);
document.documentElement.style.setProperty('--accent',ID[ROLE].accent);
$('myName').textContent=ID[ROLE].name;$('myTitle').textContent=ID[ROLE].title;
$('myArch').textContent=ID[ROLE].archetype;$('myTag').textContent='“'+ID[ROLE].tagline+'”';
document.addEventListener('contextmenu',e=>e.preventDefault());
document.addEventListener('gesturestart',e=>e.preventDefault());
let ws,S=null,boot=null,lastVoteEpoch=-1,lastLine=-1,lineT0=0,voteEnd=0,voteDur=20,attractEnd=0,lastAttract=-1,myVote=null;
function send(o){if(ws&&ws.readyState===1)ws.send(JSON.stringify(o));}
function press(el,fn){el.addEventListener('pointerdown',e=>{e.preventDefault();el.classList.add('press');fn();
 setTimeout(()=>el.classList.remove('press'),160);});}
function topicById(id){return TOPICS.find(t=>t.id===id);}
// attract topic cards
TOPICS.forEach((t,i)=>{const b=document.createElement('button');b.className='topic';
 b.innerHTML=`<span class="num">${i+1}</span><span><div class="t"></div><div class="q"></div></span><span class="go">›</span>`;
 b.querySelector('.t').textContent=t.title;b.querySelector('.q').textContent=t.question;
 press(b,()=>send({type:'tap_topic',topicId:t.id}));$('topics').appendChild(b);});
// toolbar: back to the topic cards (with a confirm sheet), volume
let sheetTimer=0;function sheet(on){$('sheet').classList.toggle('on',on);clearTimeout(sheetTimer);if(on)sheetTimer=setTimeout(()=>sheet(false),8000);}
press($('homeBtn'),()=>sheet(true));
press($('sheetNo'),()=>sheet(false));
press($('sheetYes'),()=>{sheet(false);send({type:'home'});});
$('sheet').addEventListener('pointerdown',e=>{if(e.target.id==='sheet')sheet(false);});
// Volume: a slider like the Ethics player (10-100 % from a seat), throttled
// while dragging; the speaker icon mutes, and SPACE does too (staff keyboard).
let volBusy=false,volSendT=0;
function volShow(v,muted){const pc=Math.round(v*100);$('volN').textContent=muted?'MUTE':pc+'%';
 $('vol').style.setProperty('--p',((pc-10)/90*100)+'%');$('volWrap').classList.toggle('muted',!!muted||v===0);
 $('w1').style.display=(muted||v<.05)?'none':'';$('w2').style.display=(muted||v<.55)?'none':'';}
$('vol').addEventListener('input',e=>{volBusy=true;const v=e.target.value/100;volShow(v,false);
 const now=performance.now();if(now-volSendT>120){volSendT=now;send({type:'volume_set',value:v});}});
$('vol').addEventListener('change',e=>{send({type:'volume_set',value:e.target.value/100});setTimeout(()=>{volBusy=false;},400);});
press($('volIcon'),()=>send({type:'mute'}));
window.addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();send({type:'mute'});}});
// Controls fade away 3 s after the last touch while the show is live (always
// shown while paused, so staff can find them), like the Ethics player.
let hideT=0;
function nudge(){$('tools').classList.remove('idle');$('fs').classList.remove('idle');document.body.classList.remove('hide-cursor');
 clearTimeout(hideT);if(S&&S.transport!=='playing')return;
 hideT=setTimeout(()=>{if($('sheet').classList.contains('on')||volBusy)return nudge();
  $('tools').classList.add('idle');$('fs').classList.add('idle');document.body.classList.add('hide-cursor');},3000);}
['pointerdown','pointermove','keydown'].forEach(ev=>document.addEventListener(ev,nudge,{passive:true}));
nudge();
// options
function buildOpts(topic){const box=$('opts');box.innerHTML='';
 ['A','B','C'].forEach(k=>{const o=topic.options[k],c=ID[o.role].accent;const b=document.createElement('button');
  b.className='opt';b.dataset.k=k;b.style.setProperty('--c',c);
  b.innerHTML=`<span class="fill"></span><span class="k">${k}</span><span class="l"><span class="lt"></span><span class="who2">${ID[o.role].name} · ${ID[o.role].archetype}</span></span><span class="pct"></span>`;
  b.querySelector('.lt').textContent=o.label;press(b,()=>{if(S&&S.voteOpen)send({type:'vote',option:k});});box.appendChild(b);});}
function show(id){document.querySelectorAll('.view').forEach(v=>v.classList.toggle('on',v.id===id));}
function pct(n,t){return t?Math.round(100*n/t):0;}
function renderBars(counts,total,reveal){document.querySelectorAll('.opt').forEach(b=>{const n=counts[b.dataset.k]||0;
 b.querySelector('.fill').style.width=(total?100*n/total:0)+'%';b.querySelector('.pct').textContent=pct(n,total)+'%';});
 $('opts').classList.toggle('showpct',reveal||total>0);
 $('vTotal').textContent=total?`${total} vote${total>1?'s':''} so far`:'Be the first to vote';}
function arrowTo(role){const d=ID[role].position-ID[ROLE].position;return d<0?'←':d>0?'→':'●';}
function renderTalk(){const t=topicById(S.topicId);if(!t)return;
 $('tTitle').textContent=t.title;$('tQ').textContent=t.question;
 const sp=S.speaking,mine=sp===ROLE,stage=$('stage');
 stage.classList.toggle('listening',!mine);
 const tag=$('spk');
 if(sp){tag.style.color=ID[sp].accent;tag.innerHTML=mine?`<span class="dot"></span>${ID[sp].name} is speaking`:`<i>${arrowTo(sp)}</i>${ID[sp].name}`;
  $('cap').textContent=S.text;}
 else{$('cap').textContent=S.text||'';}
 if(!sp&&S.next){tag.style.color=ID[S.next].accent;tag.innerHTML=`<i>${arrowTo(S.next)}</i>Next: ${ID[S.next].name}`;}
 else if(!sp){tag.innerHTML='';}
 renderRes(t);}
function renderRes(t){const box=$('res');if(S.phase!=='result'){box.style.display='none';box.dataset.k='';return;}
 box.style.display='flex';const key=S.topicId+':'+S.voteEpoch;const w=S.winner;
 if(box.dataset.k!==key){box.dataset.k=key;
  const head=w?`The room chose <b></b>`:'No votes this round, so SAGE made the call';
  box.innerHTML=`<div class="hd">${head}</div>`;if(w){box.style.setProperty('--c',ID[t.options[w].role].accent);box.querySelector('b').textContent=w+' · '+t.options[w].label;}
  ['A','B','C'].forEach(k=>{const o=t.options[k],r=document.createElement('div');r.className='rb'+(w&&k!==w?' lose':'');
   r.style.setProperty('--c',ID[o.role].accent);r.innerHTML=`<span class="k">${k}</span><span class="lbl"></span><span class="bar"><i></i></span><span class="p">${pct(S.counts[k]||0,S.voteTotal)}%</span>`;
   r.querySelector('.lbl').textContent=o.label;box.appendChild(r);
   requestAnimationFrame(()=>requestAnimationFrame(()=>{r.querySelector('i').style.width=pct(S.counts[k]||0,S.voteTotal)+'%';}));});}}
function renderVote(){const t=topicById(S.topicId);if(!t)return;
 if(S.voteEpoch!==lastVoteEpoch){if($('opts').dataset.topic!==t.id||S.voteRemaining===null){buildOpts(t);$('opts').dataset.topic=t.id;myVote=null;}
  lastVoteEpoch=S.voteEpoch;if(S.voteRemaining!==null){voteDur=S.voteSeconds||20;voteEnd=performance.now()+S.voteRemaining*1000;}else voteEnd=0;}
 $('dilemma').textContent=t.dilemma;$('vKick').textContent='Your call · '+t.title;
 renderBars(S.counts,S.voteTotal,false);
 document.querySelectorAll('.opt').forEach(b=>{b.classList.remove('win','lose');b.classList.toggle('mine',b.dataset.k===myVote);});}
function renderClose(){const t=topicById(S.topicId);if(!t)return;$('panelQ').textContent='“'+t.panelQuestion+'”';
 const box=$('tonight');box.innerHTML='';const T=S.tonight||{counts:{},total:0};
 $('tonightLbl').textContent=T.total?`Tonight's room · ${T.total} votes on “${t.title}”`:'';
 ['A','B','C'].forEach(k=>{const o=t.options[k],c=document.createElement('div');c.className='c';c.style.setProperty('--c',ID[o.role].accent);
  c.innerHTML=`<div class="p">${pct(T.counts[k]||0,T.total)}%</div><div class="l"></div>`;c.querySelector('.l').textContent=k+' · '+o.label;box.appendChild(c);});}
let lastTransport=null;
function render(){if(!S)return;
 const inTopic=S.phase!=='attract';$('homeBtn').classList.toggle('off',!inTopic);if(!inTopic)sheet(false);
 if(!volBusy){$('vol').value=Math.round(Math.max(.1,S.volume??1)*100);volShow(S.volume??1,S.muted);}
 if(S.transport!==lastTransport){lastTransport=S.transport;nudge();}
 if(S.phase==='attract'){show('attract');
  const stopped=S.transport!=='playing',warming=!S.ready;
  $('topics').style.display=(stopped||warming)?'none':'flex';
  if(S.attractEpoch!==lastAttract){lastAttract=S.attractEpoch;attractEnd=S.attractRemaining?performance.now()+S.attractRemaining*1000:0;}
  if(stopped)$('attractHint').innerHTML='<div class="paused">The robot C-suite is on a short break.</div>';
  else if(warming)$('attractHint').innerHTML='<div class="paused">The robots are warming up. One moment…</div>';
 }else if(S.phase==='vote'){show('vote');renderVote();}
 else if(S.phase==='close'){show('close');renderClose();}
 else{show('talk');renderTalk();}
 if(S.phase==='talk'||S.phase==='result'){if(S.lineEpoch!==lastLine){lastLine=S.lineEpoch;lineT0=performance.now()-(S.lineElapsed||0)*1000;}}
 audioRender();}
// animation: orbs, countdowns
function tick(){const now=performance.now();
 let lv=0;if(S&&S.speaking&&S.levels&&S.levels.length){const i=Math.floor((now-lineT0)/50);lv=S.levels[Math.min(i,S.levels.length-1)]||0;if(i>=S.levels.length)lv=0;}
 const speakingMine=S&&S.speaking===ROLE;
 $('orbT').style.transform=`scale(${speakingMine?1+lv*.28:1})`;
 $('orbA').style.transform=`scale(${1+0.03*Math.sin(now/900)})`;
 if(S&&S.phase==='vote'){const ring=$('ring');
  if(voteEnd){const rem=Math.max(0,(voteEnd-now)/1000);$('ringN').textContent=Math.ceil(rem);
   $('ringArc').setAttribute('stroke-dashoffset',String(276.5*(1-rem/voteDur)));ring.style.visibility='visible';}
  else{$('ringN').textContent='';ring.style.visibility='hidden';}}
 if(S&&S.phase==='attract'&&S.transport==='playing'&&S.ready){
  const h=(attractEnd&&S.autoStart)?`Or wait ${Math.max(0,Math.ceil((attractEnd-now)/1000))}s and the robots will pick one.`
   :'Tap any card to start the conversation.';
  if($('attractHint').textContent!==h)$('attractHint').textContent=h;}  // no relayout every frame
 requestAnimationFrame(tick);}
requestAnimationFrame(tick);
// vote feedback
let toastTimer=0;
function onVoted(m){const t=topicById(S&&S.topicId);if(!t)return;
 if(!m.accepted){if(m.reason==='wait'){$('toast').style.setProperty('--c','var(--soft)');$('toastA').textContent='One moment…';
  $('toastB').textContent='Then tap your call again.';$('toast').classList.add('on');
  clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$('toast').classList.remove('on');},1800);}return;}
 myVote=m.option;
 const o=t.options[m.option],r=ID[o.role];$('toast').style.setProperty('--c',r.accent);
 $('toastA').textContent=`You chose ${m.option}. Your call aligns with ${r.name}, ${r.archetype}.`;
 $('toastB').textContent='Thanks! Next person: tap your call.';
 $('toast').classList.add('on');clearTimeout(toastTimer);toastTimer=setTimeout(()=>{$('toast').classList.remove('on');myVote=null;renderVote();},3200);renderVote();}
// ---- audio (center seat only) ----
let AC=null,master=null,bgmGain=null,speechEl=null,bgmEl=null,curSpeech=-1,curBgm=-1;
function unlockAudio(){if(!IS_AUDIO)return;ensureAudio();if(AC&&AC.state!=='running')AC.resume();
 if(bgmEl&&bgmEl.paused&&bgmEl.src)bgmEl.play().catch(()=>{});}
function ensureAudio(){if(!IS_AUDIO||AC)return;try{AC=new (window.AudioContext||window.webkitAudioContext)();}catch(e){return;}
 master=AC.createGain();master.connect(AC.destination);
 speechEl=new Audio();speechEl.crossOrigin='anonymous';const sSrc=AC.createMediaElementSource(speechEl);
 const pan=AC.createStereoPanner?AC.createStereoPanner():AC.createGain();sSrc.connect(pan);pan.connect(master);speechEl._pan=pan;
 bgmEl=new Audio();bgmEl.loop=true;const bSrc=AC.createMediaElementSource(bgmEl);bgmGain=AC.createGain();bgmGain.gain.value=0;bSrc.connect(bgmGain);bgmGain.connect(master);}
function audioRender(){if(!IS_AUDIO||!S)return;ensureAudio();if(!AC)return;
 if(AC.state!=='running')AC.resume().catch(()=>{});
 master.gain.setTargetAtTime(S.muted?0:S.volume,AC.currentTime,.05);
 if(S.speechAudioEpoch!==curSpeech){
  // Only a line that is being spoken right now: a page opened (or reconnected)
  // between lines must not replay the last fallback clip.
  // The clip must belong to THIS line (not an earlier fallback line), and a
  // page joining mid-line starts it where the line has got to.
  if(S.speaking&&S.speechAudioSrc&&S.transport==='playing'&&S.speechLine===S.lineEpoch){speechEl.src=BASE+S.speechAudioSrc;
   const t0=performance.now()-(S.lineElapsed||0)*1000;
   if((S.lineElapsed||0)>0.5)speechEl.addEventListener('loadedmetadata',()=>{try{speechEl.currentTime=(performance.now()-t0)/1000;}catch(e){}},{once:true});
   if(speechEl._pan.pan)speechEl._pan.pan.value=S.speechPan||0;speechEl.play().catch(e=>console.log('speech blocked',e));}
  curSpeech=S.speechAudioEpoch;}
 if(S.transport!=='playing'&&!speechEl.paused)speechEl.pause();
 if(S.bgmSrc&&S.bgmEpoch!==curBgm){curBgm=S.bgmEpoch;bgmEl.src=BASE+S.bgmSrc;bgmEl.play().catch(e=>console.log('bgm blocked',e));}
 if(!S.bgmSrc&&bgmEl&&!bgmEl.paused)bgmEl.pause();
 const target=S.transport!=='playing'?0:(S.ducked?S.bgmVolume*0.3:S.bgmVolume);
 bgmGain.gain.setTargetAtTime(target,AC.currentTime,S.ducked?.12:.6);}
// ---- start overlay, full screen, audio unlock ----
// Not a kiosk: the browser needs one real tap per screen to go full screen
// and (center screen) to be allowed to make sound. pointerup/click carry user
// activation on touch screens; touch pointerdown does not.
$('startSm').textContent=ID[ROLE].name+' screen'+(IS_AUDIO?' · this screen also plays the music':'')+
 '. Staff: tap once after opening the page. Visitors never see this.';
function goFull(){const el=document.documentElement;if(!document.fullscreenElement&&el.requestFullscreen)el.requestFullscreen({navigationUI:'hide'}).catch(()=>{});}
function activate(){goFull();unlockAudio();$('start').classList.add('off');}
['pointerup','click'].forEach(ev=>document.addEventListener(ev,activate,{passive:true}));
document.addEventListener('fullscreenchange',()=>{$('fs').style.display=document.fullscreenElement?'none':'block';});
$('fs').addEventListener('click',e=>{e.stopPropagation();goFull();});
if(new URLSearchParams(location.search).has('nostart'))$('start').classList.add('off');
// ?snapshot (scripts/snapshot_pages.py): no transitions, so a headless shot never lands mid-fade.
if(new URLSearchParams(location.search).has('snapshot'))document.head.insertAdjacentHTML('beforeend','<style>*{transition:none!important}</style>');
// keep the panel awake: Wake Lock API on https/localhost, else a tiny looping
// muted video (browsers hold a wake lock for a visible playing video).
(function(){let lock=null;async function req(){if(lock||document.visibilityState!=='visible')return;
 try{lock=await navigator.wakeLock.request('screen');lock.addEventListener('release',()=>{lock=null;});}catch(e){lock=null;}}
 if('wakeLock' in navigator&&window.isSecureContext){req();document.addEventListener('visibilitychange',req);document.addEventListener('pointerup',req);return;}
 const c=document.createElement('canvas');c.width=c.height=2;const x=c.getContext('2d');let t=0;
 setInterval(()=>{x.fillStyle=(t++&1)?'#000':'#010101';x.fillRect(0,0,2,2);},1000);
 const v=document.createElement('video');v.muted=true;v.loop=true;v.playsInline=true;v.setAttribute('playsinline','');
 v.srcObject=c.captureStream(1);v.style.cssText='position:fixed;left:0;bottom:0;width:2px;height:2px;opacity:.01;pointer-events:none';
 const keep=()=>{if(v.paused)v.play().catch(()=>{});};document.body.appendChild(v);keep();
 document.addEventListener('visibilitychange',keep);document.addEventListener('pointerup',keep);setInterval(keep,15000);})();
// ---- socket ----
function connect(){ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+BASE+'/ws/'+SEAT);
 ws.onopen=()=>{$('conn').style.display='none';};
 ws.onmessage=e=>{const m=JSON.parse(e.data);
  // A restarted server counts its epochs from zero again: forget ours rather than reload.
  if(boot&&m.bootId&&m.bootId!==boot){lastVoteEpoch=-1;lastLine=-1;lastAttract=-1;curSpeech=-1;curBgm=-1;}boot=m.bootId||boot;
  if(m.type==='state'){S=m;render();}else if(m.type==='voted')onVoted(m);};
 ws.onclose=()=>{$('conn').style.display='block';setTimeout(connect,1000);};}
connect();setInterval(()=>send({type:'ping'}),20000);
</script></body></html>"""


_CONTROL_HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LTA · Staff control</title><style>__BASE__
html,body{overflow:auto;-webkit-user-select:auto;user-select:auto}
body{padding:16px;max-width:900px;margin:0 auto;font-size:16px}
h1{font-size:20px;margin-bottom:4px}.sub{color:var(--soft);margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin-bottom:12px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.btn{padding:12px 18px;border-radius:10px;background:#1e2533;border:1px solid var(--line);font-weight:650}
.btn.primary{background:#1f6f5f;border-color:#2ec4b6}.btn.danger{background:#5a1f24;border-color:#a8434c}
.k{color:var(--soft);font-size:13px;text-transform:uppercase;letter-spacing:.12em;margin-bottom:8px}
table{width:100%;border-collapse:collapse}td,th{padding:6px 4px;text-align:left;border-bottom:1px solid var(--line);font-size:14px}
input[type=range]{width:220px}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;background:#ffffff14;font-size:13px}
</style></head><body>
<h1>Leading Through AI · Staff control</h1><div class="sub">Not for visitors. Robots, transport and tonight's tally.</div>
<div class="card"><div class="k">Show</div><div class="row">
 <span>Status: <b id="tr"></b></span><span class="pill" id="ph"></span>
 <button class="btn primary" id="play">Start</button><button class="btn danger" id="stop">Pause (fade out)</button><button class="btn" id="home">Back to topics</button>
 <label><input type="checkbox" id="auto"> Auto-start when idle</label></div></div>
<div class="card"><div class="k">Start a topic now</div><div class="row" id="starts"></div></div>
<div class="card"><div class="k">Volume · robots + center screen (also the slider on each seat screen)</div><div class="row">
 <input type="range" id="vol" min="0" max="1" step="0.01"><span id="volv"></span><button class="btn" id="mute">Mute / unmute</button></div></div>
<div class="card"><div class="k">Robots</div><div id="robots"></div></div>
<div class="card"><div class="k">Tonight's tally</div><table id="tally"></table>
 <div class="row" style="margin-top:10px"><button class="btn danger" id="reset">Reset tally (after rehearsal)</button></div></div>
<script>
const ID=__IDENTITY__,TOPICS=__TOPICS__;const $=s=>document.getElementById(s);let ws,S=null,boot=null;
const BASE=location.pathname.replace(/\/control\/?$/,'');
function send(o){if(ws&&ws.readyState===1)ws.send(JSON.stringify(o));}
$('play').onclick=()=>send({type:'play'});$('home').onclick=()=>send({type:'home'});$('stop').onclick=()=>send({type:'stop'});
$('auto').onchange=e=>send({type:'auto',value:e.target.checked});$('mute').onclick=()=>send({type:'mute'});
$('vol').oninput=e=>{$('volv').textContent=Math.round(e.target.value*100)+'%';send({type:'volume',value:+e.target.value});};
$('reset').onclick=()=>{if(confirm('Reset tonight\'s tally? (The vote log file keeps everything.)'))send({type:'reset_tally'});};
TOPICS.forEach(t=>{const b=document.createElement('button');b.className='btn';b.textContent=t.title;b.onclick=()=>send({type:'start_topic',topicId:t.id});$('starts').appendChild(b);});
function render(){$('tr').textContent=S.transport;$('ph').textContent=S.phase+(S.topicId?' · '+S.topicId:'');
 $('auto').checked=S.autoStart;if(document.activeElement!==$('vol')){$('vol').value=S.volume;$('volv').textContent=Math.round(S.volume*100)+'%'+(S.muted?' (muted)':'');}
 $('robots').innerHTML=Object.keys(ID).map(r=>`<div>${ID[r].name} (${ID[r].seat}): <b>${(S.robots||{})[r]||'?'}</b></div>`).join('');
 const T=S.tonightAll||{};let h='<tr><th>Topic</th><th>A</th><th>B</th><th>C</th><th>Votes</th><th>Runs</th></tr>';
 TOPICS.forEach(t=>{const x=T[t.id]||{counts:{},total:0,runs:0};h+=`<tr><td>${t.title}</td>${['A','B','C'].map(k=>`<td>${x.counts[k]||0}</td>`).join('')}<td>${x.total}</td><td>${x.runs}</td></tr>`;});
 $('tally').innerHTML=h;}
function connect(){ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+BASE+'/ws/control');
 ws.onmessage=e=>{const m=JSON.parse(e.data);if(boot&&m.bootId!==boot){location.reload();return;}boot=m.bootId;if(m.type==='state'){S=m;render();}};
 ws.onclose=()=>setTimeout(connect,1000);}
connect();
</script></body></html>"""


_ALL_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>LTA · all seats</title><style>__BASE__
html,body{overflow:auto}body{padding:10px;display:grid;gap:10px;grid-template-columns:repeat(3,1fr)}
.f{aspect-ratio:16/9;width:100%;border:1px solid #333;border-radius:6px}
.c{grid-column:1/4;height:720px;width:100%;border:1px solid #333;border-radius:6px}
.l{color:#9aa3b2;font-size:12px}
</style></head><body>
<div><div class="l">/seat/left · NOVA</div><iframe class="f" src="seat/left?nostart" allow="autoplay; fullscreen"></iframe></div>
<div><div class="l">/seat/center · SAGE (plays music &amp; fallback voice)</div><iframe class="f" src="seat/center?nostart" allow="autoplay; fullscreen"></iframe></div>
<div><div class="l">/seat/right · AEGIS</div><iframe class="f" src="seat/right?nostart" allow="autoplay; fullscreen"></iframe></div>
<iframe class="c" src="control"></iframe>
</body></html>"""


_INDEX_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><title>LTA exhibit</title><style>__BASE__
html,body{overflow:auto}body{padding:24px;font-size:18px;line-height:1.8}a{color:#2ec4b6}</style></head><body>
<h2>Leading Through AI · Robot C-Suite</h2>
<ul><li><a href="seat/left">/seat/left</a> (NOVA)</li><li><a href="seat/center">/seat/center</a> (SAGE · plays audio)</li>
<li><a href="seat/right">/seat/right</a> (AEGIS)</li><li><a href="control">/control</a> (staff)</li><li><a href="all">/all</a> (preview)</li></ul>
<p style="color:#9aa3b2">Port __PORT__</p></body></html>"""


def _fill(template: str, **values: str) -> str:
    out = template.replace("__BASE__", _BASE_CSS)
    for key, value in values.items():
        out = out.replace(f"__{key}__", value)
    return out


def page_seat(seat: str, role: str, identity: dict, topics: list[dict]) -> str:
    return _fill(
        _SEAT_HTML,
        TITLE=APP_TITLE,
        SUBTITLE=APP_SUBTITLE,
        SHOWCASE=SHOWCASE_LABEL,
        SEATNAME=identity[role]["name"],
        SEAT=seat,
        ROLE=role,
        IDENTITY=json.dumps(identity),
        TOPICS=json.dumps(topics),
        IS_AUDIO="true" if seat == "center" else "false",
    )


def page_control(identity: dict, topics: list[dict]) -> str:
    return _fill(_CONTROL_HTML, IDENTITY=json.dumps(identity), TOPICS=json.dumps(topics))


def page_all() -> str:
    return _fill(_ALL_HTML)


def page_index(port: int) -> str:
    return _fill(_INDEX_HTML, PORT=str(port))
