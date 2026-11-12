/* 보유: 실제 보유 종목의 수량·평단을 이 폰 브라우저에 저장하고, 전 거래일 종가로 평가손익·팩터·신호를 보여준다.
   종목코드 목록(수량·평단은 빼고)만 ntfy 등록 주제로 보냄 → hold-reg 작업이 config.json holdings에 반영(주간 공시·뉴스 점검 대상). */
(function(){
"use strict";
var X=window.HQ,el=X.el,num=X.num,cls=X.cls,$=X.$,F=X.F;
var KEY="hq_hold_v1",SKEY="hq_hold_sent",SET=null,sortK="val",pending=null,noteEl=null;
var SORTS=[["val","평가금액순"],["ret","수익률순"],["chg","전일 등락순"],["total","종합 점수순"],["name","이름순"]];

// ---------------------------------------------------------------- 저장
function load(){try{var v=JSON.parse(localStorage.getItem(KEY)||"null");return Array.isArray(v)?v:null}catch(e){return null}}
function save(L){try{localStorage.setItem(KEY,JSON.stringify(L));SET=null;return true}catch(e){alert("저장 실패: 브라우저 저장공간을 쓸 수 없습니다.");return false}}
function seed(){var B=X.BY(),out=[];Object.keys(B).forEach(function(c){if(B[c].hold)out.push({c:c,n:B[c].name,q:null,a:null})});return out}   // 처음엔 서버 목록(config.json)
function list(){return load()||seed()}
function has(c){
  if(!SET){var L=load();if(!L)return !!(X.BY()[c]||{}).hold;SET={};L.forEach(function(h){SET[h.c]=1})}
  return !!SET[c];
}
function nameOf(h){var r=X.BY()[h.c];return r?r.name:(h.n||h.c)}
function codesKey(L){return L.map(function(h){return h.c}).sort().join(",")}
function won(v,sign){return (sign&&v>0?"+":"")+num(Math.round(v))+"원"}
function fq(v){return num(v,v%1?2:0)}
function met(m,k,v,c){var s=el("span","met",k);s.appendChild(el("b",c||"",v));m.appendChild(s)}

// ---------------------------------------------------------------- 서버 동기화 (종목코드·이름만)
function sent(){try{return JSON.parse(localStorage.getItem(SKEY)||"null")}catch(e){return null}}
function send(L,bg){
  var M=X.meta()||{},t=M.hold_topic;if(!t||M.sample)return;
  var m={},k=codesKey(L);L.forEach(function(h){m[h.c]=nameOf(h)});
  if(pending===k)return;pending=k;
  fetch("https://ntfy.sh/"+t,{method:"POST",body:JSON.stringify({v:1,hold:m}),keepalive:!!bg}).then(function(r){
    if(!r.ok)throw new Error(r.status);
    var s={t:Date.now(),k:k};try{localStorage.setItem(SKEY,JSON.stringify(s))}catch(e){}
    if(noteEl&&noteEl.isConnected)noteEl.textContent=sentText(s);
  }).catch(function(){if(noteEl&&noteEl.isConnected)noteEl.textContent="⚠️ 종목 목록을 서버로 보내지 못했습니다. 인터넷 연결 후 이 탭을 다시 열면 다시 보냅니다."}).then(function(){pending=null});
}
function sentText(s){return "📤 종목 목록을 서버로 보냈습니다("+new Date(s.t).toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit"})+"). 보통 1시간 안에 주간 공시·뉴스 점검 대상에 반영되고 ntfy로 확인 알림이 옵니다."}
function serverDiff(L){   // 서버(마지막 계산 때 config.json) 목록과 다른 종목 수. 오늘 데이터에 없는 종목은 비교 불가라 뺌
  var B=X.BY(),mine={},n=0;
  L.forEach(function(h){mine[h.c]=1;if(B[h.c]&&!B[h.c].hold)n++});
  Object.keys(B).forEach(function(c){if(B[c].hold&&!mine[c])n++});
  return n;
}

// ---------------------------------------------------------------- 계산
function calc(L){
  var B=X.BY(),T={val:0,day:0,cost:0,valc:0,noq:0,noa:0};
  var rows=L.map(function(h){
    var r=B[h.c]||null,px=r?r.close:null,q=+h.q||0,a=+h.a||0,o={h:h,r:r,name:nameOf(h),px:px,q:q,a:a};
    if(!q)T.noq++;
    if(px&&q){
      o.val=px*q;T.val+=o.val;
      if(r.chg!=null){o.day=o.val-o.val/(1+r.chg/100);T.day+=o.day}
      if(a){o.cost=a*q;o.pl=o.val-o.cost;o.ret=(px/a-1)*100;T.cost+=o.cost;T.valc+=o.val}else T.noa++;
    }
    return o;
  });
  rows.forEach(function(o){o.w=T.val&&o.val?o.val/T.val*100:null});
  return {rows:rows,T:T};
}
function sorter(k){
  var g=function(o){return k==="chg"||k==="total"?(o.r?o.r[k]:null):o[k]};
  return function(a,b){
    if(k==="name")return a.name.localeCompare(b.name,"ko");
    var x=g(a),y=g(b);if(x==null)return y==null?0:1;if(y==null)return -1;return y-x;
  };
}

// ---------------------------------------------------------------- 화면
function render(L){
  var H=list(),C=calc(H),T=C.T,M=X.meta()||{};
  var d=el("p","desc");d.innerHTML="실제 보유 종목. 가격은 <b>"+(M.asof||"")+" 종가</b> 기준입니다(장중 실시간 아님). 수량·평단은 <b>이 폰 브라우저에만</b> 저장되니 가끔 백업하세요.";L.appendChild(d);
  var bar=el("div","pfbar");
  [["수량·평단 편집",function(){edit(null)}],["백업·복원",backup]].forEach(function(x){var b=el("button","tgl",x[0]);b.type="button";b.onclick=x[1];bar.appendChild(b)});
  L.appendChild(bar);
  if(!H.length){L.appendChild(el("div","empty","보유 종목이 없습니다. '수량·평단 편집'에서 추가하거나 종목 상세 화면의 '보유 종목에 추가'를 누르세요."));syncNote(L,H);return}

  var s=el("div","card hsum");
  s.appendChild(el("div","small","평가금액"+(T.noq?" · 수량 미입력 "+T.noq+"종목 제외":"")));
  s.appendChild(el("div","big",T.val?won(T.val):"–"));
  var pl=T.valc-T.cost;
  if(T.cost)s.appendChild(el("div","hpl "+cls(pl),"평가손익 "+won(pl,1)+" ("+F.sp(pl/T.cost*100)+")"));
  else s.appendChild(el("div","small",T.val?"평단을 넣으면 손익이 계산됩니다.":"수량을 넣으면 평가금액이 계산됩니다."));
  var m=el("div","mets");
  met(m,"매입",T.cost?won(T.cost):"–");
  var prev=T.val-T.day;met(m,"전일 대비",T.val?won(T.day,1)+" ("+F.sp(prev?T.day/prev*100:0)+")":"–",cls(T.day));
  met(m,"종목",H.length+"개");
  s.appendChild(m);
  if(T.noa)s.appendChild(el("div","small","평단 미입력 "+T.noa+"종목은 손익에서 뺐습니다."));
  L.appendChild(s);

  var so=el("select");SORTS.forEach(function(o){var x=el("option",null,o[1]);x.value=o[0];so.appendChild(x)});so.value=sortK;
  so.onchange=function(){sortK=so.value;X.render()};
  var sr=el("label","pff");sr.appendChild(el("span",null,H.length+"종목 · 누르면 상세"));sr.appendChild(so);L.appendChild(sr);
  C.rows.slice().sort(sorter(sortK)).forEach(function(o){L.appendChild(card(o))});
  syncNote(L,H);
}
function card(o){
  var r=o.r,b=el("button","card hcard");b.type="button";
  var a=el("div","row1");a.appendChild(el("span","nm",o.name));
  a.appendChild(el("span","px",o.px?num(o.px):"–"));
  a.appendChild(el("span","chg "+cls(r&&r.chg),r&&r.chg!=null?(r.chg>0?"+":"")+r.chg.toFixed(2)+"%":"–"));
  b.appendChild(a);
  b.appendChild(el("div","sub",o.q?fq(o.q)+"주"+(o.a?" · 평단 "+num(o.a,o.a%1?1:0)+"원":" · 평단 미입력")+(o.val?" · 평가 "+won(o.val):"")+(o.w!=null?" · 비중 "+o.w.toFixed(0)+"%":""):"수량 미입력 — 눌러서 입력"));
  var m=el("div","mets");
  if(o.ret!=null){met(m,"수익률",F.sp(o.ret),cls(o.ret));met(m,"손익",won(o.pl,1),cls(o.pl))}
  if(r){met(m,"종합",F.s(r.total));if(r.mf!=null)met(m,"마법공식",r.mf+"위");met(m,"RSI",F.i(r.rsi));met(m,"52주고점",F.sp(r.hi52),cls(r.hi52))}
  if(m.children.length)b.appendChild(m);
  if(r&&(r.sig||r.st)){
    var cw=el("div","mets");
    (r.sig||"").split("|").filter(Boolean).forEach(function(s){cw.appendChild(el("span","chip",s))});
    (r.st||"").split("|").filter(Boolean).forEach(function(s){cw.appendChild(el("span","chip st",s))});
    b.appendChild(cw);
  }
  if(!r)b.appendChild(el("div","small","오늘 계산 대상에 없음(시총 300억·거래대금 2억 미만, 거래정지 등) — 가격·지표 없음"));
  b.onclick=function(){if(r&&o.q)X.openSheet(r);else edit({code:o.h.c,name:o.name})};
  return b;
}
function syncNote(L,H){
  var M=X.meta()||{},mine=load(),p=el("p","small");p.style.margin="14px 2px";
  if(!M.hold_topic||M.sample)return;
  if(!mine){if(H.length){p.textContent="이 목록은 서버 설정(config.json)에서 가져왔습니다. 수량·평단을 넣고 저장하면 이 폰 목록이 기준이 됩니다.";L.appendChild(p)}return}
  if(!serverDiff(H)){p.textContent="✓ 주간 공시·뉴스 점검(토요일 아침 ntfy)이 이 목록 기준으로 돕니다.";L.appendChild(p);return}
  var s=sent(),k=codesKey(H);noteEl=p;
  if(!s||s.k!==k||Date.now()-s.t>6*36e5){s=null;send(H,true)}
  p.textContent=s?sentText(s):"📤 종목 목록을 서버로 보내는 중…";
  L.appendChild(p);
}

// ---------------------------------------------------------------- 종목 상세 화면 연결
function info(r){
  var h=list().filter(function(x){return x.c===r.code})[0];if(!h)return null;
  var o=calc([h]).rows[0],p=el("div","hline");
  p.appendChild(el("span","chip st","보유"));
  p.appendChild(document.createTextNode(o.q?fq(o.q)+"주"+(o.a?" · 평단 "+num(o.a,o.a%1?1:0)+"원":"")+(o.val?" · 평가 "+won(o.val):""):"수량 미입력"));
  if(o.ret!=null)p.appendChild(el("span",cls(o.ret)," · "+F.sp(o.ret)+" ("+won(o.pl,1)+")"));
  return p;
}
function btn(r){
  var b=el("button","addpf",has(r.code)?"보유 수량·평단 수정":"+ 보유 종목에 추가");b.type="button";
  b.onclick=function(){edit(r)};
  return b;
}

// ---------------------------------------------------------------- 편집
function sheet(title){var B=$("#sheetBody");B.innerHTML="";B.appendChild(el("div","sec",title));X.show();$("#sheet").scrollTop=0;return B}
function edit(focus){
  var L=JSON.parse(JSON.stringify(list())),fc=focus&&focus.code,fin=null;
  if(fc&&!L.some(function(h){return h.c===fc}))L.push({c:fc,n:focus.name,q:null,a:null});
  var B=sheet("보유 종목 편집");
  B.appendChild(el("p","desc","증권사 앱에 나오는 보유 수량과 평균 단가(원)를 넣으세요. 평단을 비우면 손익 없이 평가금액만 계산합니다. 다 판 종목은 '삭제'."));
  var box=el("div");B.appendChild(box);
  function draw(){
    box.innerHTML="";fin=null;
    if(!L.length)box.appendChild(el("div","small","아래에서 종목을 검색해 추가하세요."));
    L.forEach(function(h,k){
      var w=el("div","hed"),t=el("div","row1");
      t.appendChild(el("span","nm",nameOf(h)));t.appendChild(el("span","small",h.c));
      var x=el("button","tgl","삭제");x.type="button";x.onclick=function(){L.splice(k,1);draw()};t.appendChild(x);
      w.appendChild(t);
      var g=el("div","hin");
      [["q","수량(주)"],["a","평단(원)"]].forEach(function(f){
        var lb=el("label");lb.appendChild(el("span","small",f[1]));
        var i=el("input");i.type="number";i.inputMode="decimal";i.min="0";i.step="any";i.value=h[f[0]]!=null?h[f[0]]:"";
        i.oninput=function(){h[f[0]]=i.value===""?null:+i.value};
        lb.appendChild(i);g.appendChild(lb);
        if(h.c===fc&&f[0]==="q")fin=i;
      });
      w.appendChild(g);box.appendChild(w);
    });
  }
  draw();
  B.appendChild(el("div","sec","종목 추가"));
  var q=el("input");q.placeholder="종목명·코드 검색";B.appendChild(q);
  var hits=el("div");B.appendChild(hits);
  q.oninput=function(){
    hits.innerHTML="";var s=q.value.trim().toLowerCase();if(!s)return;var BY=X.BY(),c=0;
    Object.keys(BY).some(function(code){
      var r=BY[code];if(r.name.toLowerCase().indexOf(s)<0&&code.toLowerCase().indexOf(s)!==0)return false;
      var on=L.some(function(h){return h.c===code});
      var b=el("button","pfhit",r.name+" "+code+(on?" ✓ 이미 있음":""));b.type="button";
      b.onclick=function(){if(!on)L.push({c:code,n:r.name,q:null,a:null});q.value="";hits.innerHTML="";fc=code;draw();if(fin)setTimeout(function(){fin.scrollIntoView({block:"center"});fin.focus()},50)};
      hits.appendChild(b);return ++c>=12;
    });
  };
  var sv=el("button","pfsave","저장");sv.type="button";B.appendChild(sv);
  sv.onclick=function(){
    if(L.some(function(h){return (h.q!=null&&!(h.q>=0))||(h.a!=null&&!(h.a>=0))})){alert("수량·평단은 0 이상의 숫자로 넣으세요.");return}
    L.forEach(function(h){h.n=nameOf(h)});
    var before=codesKey(list());
    if(!save(L))return;
    if(codesKey(L)!==before||serverDiff(L))send(L,false);
    X.closeSheet();X.goTab("hold");
  };
  if(fin)setTimeout(function(){fin.scrollIntoView({block:"center"});fin.focus()},260);
}

// ---------------------------------------------------------------- 백업
function backup(){
  var B=sheet("보유 백업·복원");
  B.appendChild(el("p","desc","아래 코드를 복사해 메모장 등에 보관하세요. 폰을 바꾸거나 브라우저 데이터를 지웠을 때 붙여넣고 '복원'을 누르면 지금 목록이 코드 내용으로 바뀝니다."));
  var ta=el("textarea","pfta");ta.value=btoa(unescape(encodeURIComponent(JSON.stringify(list()))));B.appendChild(ta);
  var cp=el("button","tgl","복사");cp.type="button";
  cp.onclick=function(){ta.select();try{navigator.clipboard.writeText(ta.value).then(function(){cp.textContent="복사됨"})}catch(e){document.execCommand("copy");cp.textContent="복사됨"}};
  B.appendChild(cp);
  var t2=el("textarea","pfta");t2.placeholder="여기에 백업 코드 붙여넣기";B.appendChild(t2);
  var rs=el("button","pfsave","복원");rs.type="button";
  rs.onclick=function(){
    try{
      var L=JSON.parse(decodeURIComponent(escape(atob(t2.value.trim()))));
      if(!Array.isArray(L)||L.some(function(h){return !h||typeof h.c!=="string"}))throw 0;
      var before=codesKey(list());
      if(!save(L))return;
      if(codesKey(L)!==before||serverDiff(L))send(L,false);
      alert(L.length+"종목 복원");X.closeSheet();X.goTab("hold");
    }catch(e){alert("코드가 올바르지 않습니다.")}
  };
  B.appendChild(rs);
}

window.HD={render:render,has:has,info:info,btn:btn,edit:edit};
})();
