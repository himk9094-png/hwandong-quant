/* 내 포트폴리오: 종목 바스켓·규칙 전략을 만들고 폰에서 바로 백테스트 (데이터: hist.json, 월 1회 갱신) */
(function(){
"use strict";
var X=window.HQ,el=X.el,num=X.num,cls=X.cls,$=X.$;
var KEY="hq_pf_v1",H=null,loading=null,cur=null;
var COST=0.0015;                        // 매수·매도 각각 0.15% (수수료+세금+슬리피지 근사)
var FAC=[["v","밸류"],["q","퀄리티"],["m","모멘텀"],["mf","마법공식"],["sm","소형주"]];

// ---------------------------------------------------------------- 저장
function load(){try{return JSON.parse(localStorage.getItem(KEY)||"[]")}catch(e){return []}}
function save(L){try{localStorage.setItem(KEY,JSON.stringify(L));return true}catch(e){alert("저장 실패: 브라우저 저장공간을 쓸 수 없습니다.");return false}}
function today(){var d=new Date(Date.now()+9*36e5);return d.toISOString().slice(0,10)}
function uid(){return Date.now().toString(36)+Math.random().toString(36).slice(2,6)}
function get(id){return load().filter(function(p){return p.id===id})[0]}
function put(p){var L=load(),i=L.findIndex(function(x){return x.id===p.id});if(i>=0)L[i]=p;else L.push(p);return save(L)}
function del(id){save(load().filter(function(p){return p.id!==id}))}

// ---------------------------------------------------------------- 데이터
function hist(){
  if(H)return Promise.resolve(H);
  if(loading)return loading;
  loading=fetch("hist.json?t="+Math.floor(Date.now()/36e5)).then(function(r){if(!r.ok)throw new Error(r.status);return r.json()}).then(function(h){
    h.n=h.codes.length;h.T=h.dates.length;h.at={};h.codes.forEach(function(c,i){h.at[c]=i});
    var fi={};h.fields.forEach(function(f,i){fi[f]=i});h.fi=fi;H=h;return h});
  loading.catch(function(){loading=null});
  return loading;
}
function lastPx(c){var B=X.BY()[c];if(B&&B.close)return B.close;if(!H)return null;var j=H.at[c];if(j==null)return null;var p=H.px[j];for(var i=p.length-1;i>=0;i--)if(p[i]!=null)return p[i];return null}
function nameOf(c){var B=X.BY()[c];if(B)return B.name;return H&&H.at[c]!=null?H.names[H.at[c]]:c}
function ym(d){return d.slice(0,4)+"."+d.slice(4,6)}
function fyAt(d){var y=+d.slice(0,4),m=+d.slice(4,6);return m>=4?y-1:y-2}

// ---------------------------------------------------------------- 지표
function pct(vals,asc){            // 백분위(0~100), 높을수록 좋게. NaN은 그대로
  var idx=[];vals.forEach(function(v,i){if(v!=null&&isFinite(v))idx.push(i)});
  idx.sort(function(a,b){return asc?vals[b]-vals[a]:vals[a]-vals[b]});
  var out=new Array(vals.length).fill(null),n=idx.length;
  idx.forEach(function(i,k){out[i]=n>1?k/(n-1)*100:50});
  return out;
}
function mean(a,min){var s=0,c=0;a.forEach(function(v){if(v!=null){s+=v;c++}});return c>=(min||1)?s/c:null}

function universe(P,i){        // i시점 투자 가능 종목
  var h=H,out=[],mk=P.mkt,lo=P.capMin||0,hi=P.capMax||Infinity,tv=P.tvMin||0;
  for(var j=0;j<h.n;j++){
    var p=h.px[j][i];if(p==null||!h.shares[j])continue;
    if(mk&&h.mkt[j]!==mk)continue;
    var mc=p*h.shares[j]/1e8;if(mc<lo||mc>=hi)continue;
    if((h.tv[j][i]||0)<tv)continue;
    out.push(j);
  }
  return out;
}
function score(P,i,U){        // 규칙 점수 → [{j,s,info}]
  var h=H,F=h.fin[String(fyAt(h.dates[i]))],fi=h.fi,w=P.w||{};
  var need=FAC.filter(function(f){return (w[f[0]]||0)>0});
  var needFin=need.some(function(f){return f[0]!=="m"&&f[0]!=="sm"})||P.profit;
  var rows=[];
  U.forEach(function(j){
    var p=h.px[j],mc=p[i]*h.shares[j]/1e8,f=F?F[j]:null;
    var g=function(k){return f?f[fi[k]]:null};
    var ni=g("ni"),op=g("op"),rev=g("rev"),eq=g("eq"),liab=g("liab"),mi=g("mi")||0,as=g("assets"),ic=g("ic");
    if(needFin&&(ni==null||op==null))return;
    if(P.profit&&!(ni>0&&op>0))return;
    var r={j:j,mc:mc,
      ep:ni!=null?ni/mc:null,bp:eq!=null?eq/mc:null,sp:rev!=null?rev/mc:null,
      ey:(op!=null&&liab!=null)?op/(mc+liab+mi):null,
      roe:(ni!=null&&eq>0)?ni/eq:null,roa:(ni!=null&&as>0)?ni/as:null,opm:(op!=null&&rev>0)?op/rev:null,debt:(liab!=null&&eq>0)?liab/eq:null,
      roc:(op>0&&ic>0)?op/ic:null,
      m12:(i>=12&&p[i-12]&&p[i-1])?p[i-1]/p[i-12]-1:null,m6:(i>=6&&p[i-6]&&p[i-1])?p[i-1]/p[i-6]-1:null,m3:(i>=3&&p[i-3])?p[i]/p[i-3]-1:null};
    rows.push(r);
  });
  var col=function(k,asc){return pct(rows.map(function(r){return r[k]}),asc!==false)};
  var R={ep:col("ep"),bp:col("bp"),sp:col("sp"),ey:col("ey"),roe:col("roe"),roa:col("roa"),opm:col("opm"),debt:col("debt",false),
    roc:col("roc"),m12:col("m12"),m6:col("m6"),m3:col("m3"),sm:col("mc",false)};
  var out=[];
  rows.forEach(function(r,k){
    var c={v:mean([R.ep[k],R.bp[k],R.sp[k],R.ey[k]],2),q:mean([R.roe[k],R.roa[k],R.opm[k],R.debt[k]],2),
      m:mean([R.m12[k],R.m6[k],R.m3[k]],2),mf:(r.ey!=null&&r.roc!=null&&r.ey>0)?(R.ey[k]+R.roc[k])/2:null,sm:R.sm[k]};
    var s=0,ws=0,ok=true;
    need.forEach(function(f){var v=c[f[0]];if(v==null){ok=false;return}s+=v*w[f[0]];ws+=w[f[0]]});
    if(!ok||!ws)return;
    out.push({j:r.j,s:s/ws,r:r,c:c});
  });
  out.sort(function(a,b){return b.s-a.s});
  return out;
}

// ---------------------------------------------------------------- 시뮬레이션
function inSeason(d){var m=+d.slice(4,6);return m>=10||m<=3}   // 10월 말~4월 말 보유(=11~4월 수익)
function simulate(P){
  var h=H,T=h.T,start=0,rule=P.type==="rule";
  var sy=String(P.start||2020);
  for(var i=0;i<T;i++){if(h.dates[i].slice(0,4)>=sy||i===T-1){start=Math.max(0,i-1);break}}   // 전년 12월 말에 매수
  if(rule)start=Math.max(start,12);           // 12개월 모멘텀 계산용(데이터가 2019년 7월부터)
  var reb=+P.reb||0,eq=[1],bm=[1],ew=[1],dates=[h.dates[start]],holds={},since=0,invested=false,turn=0,picksLog=[];
  var kx=h.idx[P.mkt==="Q"?"KOSDAQ":"KOSPI"];
  for(var i=start;i<T-1;i++){
    var d=h.dates[i],want=!P.season||inSeason(d),cost=0;
    if(want&&(!invested||(reb&&since>=reb)||(!reb&&!invested))){
      var tgt={};
      if(rule){var U=universe(P,i),S=score(P,i,U).slice(0,P.n||20);S.forEach(function(x){tgt[x.j]=1/S.length});picksLog.push([d,S.length])}
      else{var tot=0;P.items.forEach(function(it){var j=h.at[it.c];if(j!=null&&h.px[j][i]!=null){tgt[j]=+it.w||1;tot+=tgt[j]}});Object.keys(tgt).forEach(function(j){tgt[j]/=tot})}
      var keys={};Object.keys(holds).concat(Object.keys(tgt)).forEach(function(k){keys[k]=1});
      Object.keys(keys).forEach(function(k){cost+=Math.abs((tgt[k]||0)-(holds[k]||0))*COST});
      turn++;holds=tgt;invested=Object.keys(tgt).length>0;since=0;
    }else if(!want&&invested){
      Object.keys(holds).forEach(function(k){cost+=holds[k]*COST});holds={};invested=false;
    }
    var r=0,nh={},tw=0;
    Object.keys(holds).forEach(function(k){var p=h.px[k],a=p[i],b=p[i+1];var g=(a&&b)?b/a:1;r+=holds[k]*(g-1);nh[k]=holds[k]*g;tw+=nh[k]});
    if(tw>0)Object.keys(nh).forEach(function(k){nh[k]/=tw});holds=nh;
    eq.push(eq[eq.length-1]*(1+r)*(1-cost));
    // 벤치마크: 지수, 같은 조건 전 종목 균등(매월)
    bm.push(bm[bm.length-1]*((kx[i]&&kx[i+1])?kx[i+1]/kx[i]:1));
    var U2=rule?universe(P,i):h.codes.map(function(_,j){return j}).filter(function(j){return h.px[j][i]!=null}),s2=0,c2=0;
    U2.forEach(function(j){var a=h.px[j][i],b=h.px[j][i+1];if(a&&b){s2+=b/a-1;c2++}});
    ew.push(ew[ew.length-1]*(1+(c2?s2/c2:0)));
    dates.push(h.dates[i+1]);if(invested)since++;
  }
  return {dates:dates,eq:eq,bm:bm,ew:ew,bmName:P.mkt==="Q"?"코스닥":"코스피",turn:turn};
}
function stats(v,dates){
  var n=v.length-1;if(n<1)return null;
  var tot=v[n]/v[0]-1,yrs=n/12,cagr=Math.pow(v[n]/v[0],1/yrs)-1,pk=v[0],mdd=0,r=[];
  for(var i=1;i<=n;i++){pk=Math.max(pk,v[i]);mdd=Math.min(mdd,v[i]/pk-1);r.push(v[i]/v[i-1]-1)}
  var m=r.reduce(function(a,b){return a+b},0)/n,sd=Math.sqrt(r.reduce(function(a,b){return a+(b-m)*(b-m)},0)/Math.max(1,n-1));
  return {tot:tot*100,cagr:cagr*100,mdd:mdd*100,vol:sd*Math.sqrt(12)*100,sh:sd?m/sd*Math.sqrt(12):0,win:r.filter(function(x){return x>0}).length/n*100};
}
function yearly(res){
  var out={},D=res.dates;
  for(var i=1;i<D.length;i++){var y=D[i].slice(0,4),o=out[y]||(out[y]={a:1,b:1,c:1});
    o.a*=res.eq[i]/res.eq[i-1];o.b*=res.bm[i]/res.bm[i-1];o.c*=res.ew[i]/res.ew[i-1]}
  return out;
}

// ---------------------------------------------------------------- 화면: 목록
function render(L){
  cur=null;
  var d=el("p","desc");d.innerHTML="직접 만든 <b>종목 바스켓</b>이나 <b>규칙 전략</b>을 2020년부터 백테스트합니다. 계산은 폰에서 바로 하고, 과거 데이터는 매월 자동 갱신됩니다. 포트폴리오는 이 기기 브라우저에 저장되니 가끔 <b>백업</b>하세요.";L.appendChild(d);
  var bar=el("div","pfbar");
  [["+ 종목 바스켓",function(){editBasket(null)}],["+ 규칙 전략",function(){editRule(null)}],["백업·복원",backup]].forEach(function(x){var b=el("button","tgl",x[0]);b.onclick=x[1];bar.appendChild(b)});
  L.appendChild(bar);
  var P=load();
  if(!P.length){L.appendChild(el("div","empty","아직 없습니다. 종목 상세 화면의 '포트폴리오에 담기'나 위 버튼으로 만드세요."));return}
  var box=el("div");L.appendChild(box);box.appendChild(el("div","empty","계산 중…"));
  hist().then(function(){
    box.innerHTML="";
    P.slice().reverse().forEach(function(p){
      var b=el("button","card");b.type="button";var a=el("div","row1");
      a.appendChild(el("span","nm",p.name));a.appendChild(el("span","chip "+(p.type==="rule"?"st":""),p.type==="rule"?"규칙":"바스켓"));b.appendChild(a);
      var s;try{var res=simulate(p);s=stats(res.eq,res.dates)}catch(e){s=null}
      var m=el("div","mets");m.style.marginLeft="0";
      if(s){[["누적",s.tot,"sp"],["연",s.cagr,"sp"],["MDD",s.mdd,"sp"]].forEach(function(x){var t=el("span","met",x[0]);t.appendChild(el("b",cls(x[1]),X.F.sp(x[1])));m.appendChild(t)})}
      if(p.type==="basket"){var fw=forward(p);if(fw){var t=el("span","met","생성 후");t.appendChild(el("b",cls(fw.tot),X.F.sp(fw.tot)));m.appendChild(t)}}
      b.appendChild(m);b.appendChild(el("div","small",(p.type==="rule"?ruleDesc(p):p.items.length+"종목")+" · 만든 날 "+p.created));
      b.onclick=function(){cur=p.id;X.render()};
      box.appendChild(b);
    });
    box.appendChild(el("div","small","과거 데이터 기준: "+ym(H.dates[H.T-1])+" ("+H.generated+" 갱신)"));
  }).catch(noData(box));
}
function noData(box){return function(e){box.innerHTML="";box.appendChild(el("div","empty","과거 데이터(hist.json)를 불러오지 못했습니다. 월간 작업이 한 번 돌아야 생깁니다. ("+(e&&e.message)+")"))}}
function ruleDesc(p){
  var w=FAC.filter(function(f){return (p.w[f[0]]||0)>0}).map(function(f){return f[1]+(p.w[f[0]]>1?"×"+p.w[f[0]]:"")}).join("+");
  return (p.mkt==="K"?"코스피":p.mkt==="Q"?"코스닥":"전체")+" · "+w+" · 상위 "+p.n+" · "+({1:"매월",3:"분기",6:"반년",12:"매년"}[p.reb]||"")+(p.season?" · 11~4월만":"");
}

// ---------------------------------------------------------------- 화면: 결과
function renderOne(L){
  var p=get(cur);if(!p){cur=null;return render(L)}
  var top=el("div","pfbar");
  [["← 목록",function(){cur=null;X.render()}],["수정",function(){p.type==="rule"?editRule(p):editBasket(p)}],["복제",function(){var q=JSON.parse(JSON.stringify(p));q.id=uid();q.name+=" 사본";q.created=today();if(q.type==="basket")q.entry=entryPx(q.items);put(q);cur=q.id;X.render()}],["삭제",function(){if(confirm("'"+p.name+"' 삭제할까요?")){del(p.id);cur=null;X.render()}}]].forEach(function(x){var b=el("button","tgl",x[0]);b.onclick=x[1];top.appendChild(b)});
  L.appendChild(top);
  L.appendChild(el("div","sec",p.name));
  L.appendChild(el("p","desc",p.type==="rule"?ruleDesc(p):p.items.map(function(it){return nameOf(it.c)+(it.w&&p.items.some(function(x){return x.w!==it.w})?" "+it.w:"")}).join(", ")));
  var box=el("div");L.appendChild(box);box.appendChild(el("div","empty","계산 중…"));
  hist().then(function(){setTimeout(function(){box.innerHTML="";drawResult(box,p)},10)}).catch(noData(box));
}
function drawResult(box,p){
  var res=simulate(p),s=stats(res.eq,res.dates),sb=stats(res.bm,res.dates),se=stats(res.ew,res.dates);
  if(!s){box.appendChild(el("div","empty","기간이 너무 짧습니다."));return}
  box.appendChild(chart(res));
  var leg=el("div","small");leg.innerHTML="<span style='color:#f4c04e'>━ 내 전략</span> &nbsp; <span style='color:#8f9ab3'>━ "+res.bmName+" 지수</span> &nbsp; <span style='color:#4d8eff'>┅ "+(p.type==="rule"?"같은 조건 전 종목 평균":"전 종목 평균")+"</span>";box.appendChild(leg);
  var tb=el("table"),hr=el("tr");["",ym(res.dates[0])+"~","연평균","MDD","샤프"].forEach(function(x){hr.appendChild(el("th",null,x))});tb.appendChild(hr);
  [["내 전략",s],[res.bmName,sb],["전 종목 평균",se]].forEach(function(x){var tr=el("tr");tr.appendChild(el("td",null,x[0]));var v=x[1];
    [v.tot,v.cagr,v.mdd].forEach(function(z){tr.appendChild(el("td",cls(z),X.F.sp(z)))});tr.appendChild(el("td",null,v.sh.toFixed(2)));tb.appendChild(tr)});
  box.appendChild(tb);
  box.appendChild(el("div","small","월 단위 계산 · 월 승률 "+Math.round(s.win)+"% · 연 변동성 "+s.vol.toFixed(0)+"% · 매매비용 편도 0.15% 반영"+(p.season?" · 5~10월 현금(이자 0)":"")));
  // 생성 이후
  var ci=res.dates.findIndex(function(d){return d>=p.created.replace(/-/g,"")});
  if(ci>0&&ci<res.dates.length-1){var a=res.eq[res.dates.length-1]/res.eq[ci-1]-1,b=res.bm[res.dates.length-1]/res.bm[ci-1]-1;
    box.appendChild(el("div","sec","만든 이후 (월말 기준)"));box.appendChild(el("p","desc","내 전략 "+X.F.sp(a*100)+" / "+res.bmName+" "+X.F.sp(b*100)+" — 이 구간이 진짜 성적표입니다."))}
  // 연도별
  var Y=yearly(res),t2=el("table"),h2=el("tr");["연도","내 전략",res.bmName,"전 종목"].forEach(function(x){h2.appendChild(el("th",null,x))});t2.appendChild(h2);
  Object.keys(Y).sort().forEach(function(y){var o=Y[y],tr=el("tr");tr.appendChild(el("td",null,y+(y===res.dates[res.dates.length-1].slice(0,4)?" (진행)":"")));[o.a,o.b,o.c].forEach(function(z){z=(z-1)*100;tr.appendChild(el("td",cls(z),X.F.sp(z)))});t2.appendChild(tr)});
  box.appendChild(el("div","sec","연도별"));box.appendChild(t2);
  if(p.type==="rule")drawPicks(box,p);else drawBasket(box,p);
  box.appendChild(el("div","sec","주의"));
  ["현재 상장된 종목만으로 계산합니다(상장폐지 종목 없음) → 실제보다 좋게 나옵니다.",
   p.type==="basket"?"오늘 고른 종목으로 과거를 돌리면 이미 오른 종목을 고른 셈이라 좋게 나오는 게 당연합니다. '만든 이후' 성과를 보세요.":"재무는 연간 실적을 다음 해 4월 말부터 반영합니다. 2022년 이전 순이익·자본은 연결 수치 × 지배주주 비율로 근사했습니다.",
   "시가총액은 현재 상장주식수 × 과거 수정주가로 근사했습니다.","여러 조건을 바꿔가며 가장 좋은 걸 고르면 과최적화입니다. 단순한 규칙이 오래 갑니다."].forEach(function(x){box.appendChild(el("p","desc","· "+x))});
}
function drawPicks(box,p){
  var i=H.T-1,S=score(p,i,universe(p,i)).slice(0,p.n||20);
  var on=!p.season||inSeason(H.dates[i]);
  box.appendChild(el("div","sec",(on?"지금 이 전략이면 살 종목":"다음 시즌(10월 말) 후보 — 지금은 쉬는 구간")+" ("+ym(H.dates[i])+" 기준)"));
  if(!S.length){box.appendChild(el("div","empty","조건에 맞는 종목이 없습니다."));return}
  var tb=el("table"),hr=el("tr");["종목","점수","시총","12-1M"].forEach(function(x){hr.appendChild(el("th",null,x))});tb.appendChild(hr);
  S.forEach(function(x,k){var j=x.j,tr=el("tr"),c=H.codes[j],a=el("td");a.appendChild(el("div",null,(k+1)+". "+H.names[j]));a.appendChild(el("div","small",c+" · "+(H.mkt[j]==="K"?"코스피":"코스닥")));tr.appendChild(a);
    tr.appendChild(el("td",null,Math.round(x.s)));tr.appendChild(el("td",null,X.F.eok(x.r.mc)));tr.appendChild(el("td",cls(x.r.m12),x.r.m12!=null?X.F.sp(x.r.m12*100):"–"));
    var B=X.BY()[c];if(B){tr.style.cursor="pointer";tr.onclick=function(){X.openSheet(B)}}
    tb.appendChild(tr)});
  box.appendChild(tb);
}
function entryPx(items){var e={};items.forEach(function(it){var v=lastPx(it.c);if(v)e[it.c]=v});return e}
function forward(p){
  if(!p.entry)return null;var tot=0,ws=0,rows=[];
  p.items.forEach(function(it){var a=p.entry[it.c],b=lastPx(it.c),w=+it.w||1;if(a&&b){var r=(b/a-1)*100;rows.push({c:it.c,r:r,w:w});tot+=r*w;ws+=w}});
  return ws?{tot:tot/ws,rows:rows}:null;
}
function drawBasket(box,p){
  var fw=forward(p);
  box.appendChild(el("div","sec","만든 날("+p.created+") 이후 실제 추적 (일별 종가)"));
  if(!fw){box.appendChild(el("div","empty","기록된 매수가가 없습니다."));return}
  box.appendChild(el("p","desc","비중 반영 수익률 "+X.F.sp(fw.tot)));
  var tb=el("table"),hr=el("tr");["종목","만든 날","현재","수익률"].forEach(function(x){hr.appendChild(el("th",null,x))});tb.appendChild(hr);
  fw.rows.forEach(function(x){var tr=el("tr");tr.appendChild(el("td",null,nameOf(x.c)));tr.appendChild(el("td",null,num(p.entry[x.c])));tr.appendChild(el("td",null,num(lastPx(x.c))));tr.appendChild(el("td",cls(x.r),X.F.sp(x.r)));
    var B=X.BY()[x.c];if(B){tr.style.cursor="pointer";tr.onclick=function(){X.openSheet(B)}}tb.appendChild(tr)});
  box.appendChild(tb);
}
function chart(res){
  var w=600,h=200,all=res.eq.concat(res.bm,res.ew),mn=Math.min.apply(null,all),mx=Math.max.apply(null,all);
  var lmn=Math.log(mn),rg=(Math.log(mx)-lmn)||1,n=res.eq.length;
  var ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 "+w+" "+h);svg.setAttribute("preserveAspectRatio","none");svg.setAttribute("class","pfchart");
  var y=function(v){return (h-6-(Math.log(v)-lmn)/rg*(h-12)).toFixed(1)};
  var base=document.createElementNS(ns,"line");base.setAttribute("x1",0);base.setAttribute("x2",w);base.setAttribute("y1",y(1));base.setAttribute("y2",y(1));base.setAttribute("stroke","#2c3754");base.setAttribute("vector-effect","non-scaling-stroke");svg.appendChild(base);
  [[res.ew,"#4d8eff","4 3"],[res.bm,"#8f9ab3",""],[res.eq,"#f4c04e",""]].forEach(function(s){
    var pl=document.createElementNS(ns,"polyline");pl.setAttribute("points",s[0].map(function(v,i){return (i/(n-1)*w).toFixed(1)+","+y(v)}).join(" "));
    pl.setAttribute("fill","none");pl.setAttribute("stroke",s[1]);pl.setAttribute("stroke-width",s[1]==="#f4c04e"?"2.5":"1.5");if(s[2])pl.setAttribute("stroke-dasharray",s[2]);pl.setAttribute("vector-effect","non-scaling-stroke");svg.appendChild(pl)});
  var wr=el("div");wr.appendChild(svg);var ax=el("div","small");ax.style.display="flex";ax.style.justifyContent="space-between";ax.appendChild(el("span",null,ym(res.dates[0])));ax.appendChild(el("span",null,"로그 눈금"));ax.appendChild(el("span",null,ym(res.dates[n-1])));wr.appendChild(ax);
  return wr;
}

// ---------------------------------------------------------------- 편집: 바스켓
function sheet(title){var B=$("#sheetBody");B.innerHTML="";B.appendChild(el("div","sec",title));X.show();$("#sheet").scrollTop=0;return B}
function field(B,label,node){var w=el("label","pff");w.appendChild(el("span",null,label));w.appendChild(node);B.appendChild(w);return node}
function sel(opts,v){var s=el("select");opts.forEach(function(o){var x=el("option",null,o[1]);x.value=o[0];if(String(o[0])===String(v))x.selected=true;s.appendChild(x)});return s}
function yearOpts(){var a=[],y=+today().slice(0,4);for(var k=2020;k<=y;k++)a.push([k,k+"년부터"]);return a}
function editBasket(p,addCode){
  var isNew=!p;p=p?JSON.parse(JSON.stringify(p)):{id:uid(),type:"basket",name:"내 바스켓 "+(load().filter(function(x){return x.type==="basket"}).length+1),items:[],start:2020,reb:3,created:today()};
  if(addCode&&!p.items.some(function(x){return x.c===addCode}))p.items.push({c:addCode,w:1});
  var B=sheet(isNew?"새 종목 바스켓":"바스켓 수정");
  var nm=field(B,"이름",el("input"));nm.value=p.name;
  var st=field(B,"백테스트 시작",sel(yearOpts(),p.start));
  var rb=field(B,"리밸런싱(비중 맞추기)",sel([[0,"안 함(처음 산 그대로)"],[1,"매월"],[3,"분기"],[12,"매년"]],p.reb));
  B.appendChild(el("div","sec","종목 (비중: 숫자 비율, 같으면 균등)"));
  var list=el("div");B.appendChild(list);
  function drawItems(){list.innerHTML="";if(!p.items.length)list.appendChild(el("div","small","아래에서 종목을 검색해 추가하세요."));
    p.items.forEach(function(it,k){var r=el("div","pfrow");r.appendChild(el("span","nm",nameOf(it.c)));var wi=el("input");wi.type="number";wi.min="0";wi.step="any";wi.value=it.w;wi.className="pfw";wi.oninput=function(){it.w=+wi.value||0};r.appendChild(wi);
      var x=el("button","tgl","삭제");x.onclick=function(){p.items.splice(k,1);drawItems()};r.appendChild(x);list.appendChild(r)})}
  var q=el("input");q.placeholder="종목명·코드 검색 (코스피·코스닥 전 종목)";B.appendChild(q);var hits=el("div");B.appendChild(hits);
  q.oninput=function(){hits.innerHTML="";var s=q.value.trim().toLowerCase();if(!s||!H)return;var c=0;
    for(var j=0;j<H.n&&c<12;j++){if(H.names[j].toLowerCase().indexOf(s)>=0||H.codes[j].indexOf(s)===0){(function(j){var b=el("button","pfhit",H.names[j]+" "+H.codes[j]);b.onclick=function(){if(!p.items.some(function(x){return x.c===H.codes[j]}))p.items.push({c:H.codes[j],w:1});q.value="";hits.innerHTML="";drawItems()};hits.appendChild(b)})(j);c++}}};
  hist().then(drawItems).catch(function(){drawItems()});
  var sv=el("button","pfsave","저장하고 백테스트");B.appendChild(sv);
  sv.onclick=function(){p.items=p.items.filter(function(x){return x.w>0});if(!p.items.length){alert("종목을 하나 이상 넣으세요.");return}
    p.name=nm.value.trim()||p.name;p.start=+st.value;p.reb=+rb.value;
    var old=get(p.id);p.entry=p.entry||{};var e=entryPx(p.items);p.items.forEach(function(it){if(!p.entry[it.c]&&e[it.c])p.entry[it.c]=e[it.c]});
    if(old&&old.entry)Object.keys(p.entry).forEach(function(c){if(!p.items.some(function(x){return x.c===c}))delete p.entry[c]});
    if(put(p)){cur=p.id;X.closeSheet();X.goPf()}};
}
// ---------------------------------------------------------------- 편집: 규칙
function editRule(p){
  var isNew=!p;p=p?JSON.parse(JSON.stringify(p)):{id:uid(),type:"rule",name:"내 전략 "+(load().filter(function(x){return x.type==="rule"}).length+1),mkt:"",capMin:0,capMax:0,tvMin:5,w:{v:1,q:1,m:1,mf:0,sm:0},n:20,reb:3,season:false,profit:true,start:2020,created:today()};
  var B=sheet(isNew?"새 규칙 전략":"규칙 전략 수정");
  var nm=field(B,"이름",el("input"));nm.value=p.name;
  var mk=field(B,"시장",sel([["","전체"],["K","코스피"],["Q","코스닥"]],p.mkt));
  var c1=field(B,"시총 최소(억)",el("input"));c1.type="number";c1.value=p.capMin||"";c1.placeholder="0";
  var c2=field(B,"시총 최대(억, 비우면 제한 없음)",el("input"));c2.type="number";c2.value=p.capMax||"";
  var tv=field(B,"20일 평균 거래대금 최소(억)",el("input"));tv.type="number";tv.value=p.tvMin;
  B.appendChild(el("div","sec","팩터 비중 (0이면 안 씀)"));
  var ws={};FAC.forEach(function(f){ws[f[0]]=field(B,f[1],sel([[0,"0"],[1,"1"],[2,"2"],[3,"3"]],p.w[f[0]]||0))});
  B.appendChild(el("p","desc","밸류: 이익·순자산·매출·이익수익률 / 퀄리티: ROE·ROA·영업이익률·낮은 부채 / 모멘텀: 12-1·6-1·3개월 / 마법공식: 이익수익률+ROC / 소형주: 시총 작을수록"));
  var n=field(B,"보유 종목 수",sel([[5,"5"],[10,"10"],[15,"15"],[20,"20"],[30,"30"],[50,"50"]],p.n));
  var rb=field(B,"리밸런싱",sel([[1,"매월"],[3,"분기"],[6,"반년"],[12,"매년"]],p.reb));
  var se=field(B,"계절성",sel([[0,"1년 내내 보유"],[1,"11~4월만 보유(5~10월 현금)"]],p.season?1:0));
  var pr=field(B,"흑자 기업만",sel([[1,"예(순이익·영업이익 흑자)"],[0,"아니오"]],p.profit?1:0));
  var st=field(B,"백테스트 시작",sel(yearOpts(),p.start));
  var sv=el("button","pfsave","저장하고 백테스트");B.appendChild(sv);
  sv.onclick=function(){
    p.name=nm.value.trim()||p.name;p.mkt=mk.value;p.capMin=+c1.value||0;p.capMax=+c2.value||0;p.tvMin=+tv.value||0;
    FAC.forEach(function(f){p.w[f[0]]=+ws[f[0]].value});if(!FAC.some(function(f){return p.w[f[0]]>0})){alert("팩터를 하나 이상 고르세요.");return}
    p.n=+n.value;p.reb=+rb.value;p.season=se.value==="1";p.profit=pr.value==="1";p.start=+st.value;
    if(put(p)){cur=p.id;X.closeSheet();X.goPf()}};
}
// ---------------------------------------------------------------- 담기 · 백업
function addFromSheet(r){
  var B=sheet(r.name+" 담기"),bs=load().filter(function(p){return p.type==="basket"});
  bs.forEach(function(p){var b=el("button","pfhit",p.name+" ("+p.items.length+"종목)"+(p.items.some(function(x){return x.c===r.code})?" ✓ 이미 있음":""));
    b.onclick=function(){if(!p.items.some(function(x){return x.c===r.code})){p.items.push({c:r.code,w:1});p.entry=p.entry||{};p.entry[r.code]=r.close;put(p)}X.closeSheet();setTimeout(function(){cur=p.id;X.goPf()},50)};B.appendChild(b)});
  var nb=el("button","pfsave","+ 새 바스켓 만들기");nb.onclick=function(){editBasket(null,r.code)};B.appendChild(nb);
}
function backup(){
  var B=sheet("백업·복원");
  B.appendChild(el("p","desc","아래 코드를 복사해 메모장 등에 보관하세요. 복원할 땐 코드를 붙여넣고 '복원'을 누르면 기존 목록에 합쳐집니다."));
  var ta=el("textarea","pfta");ta.value=btoa(unescape(encodeURIComponent(JSON.stringify(load()))));B.appendChild(ta);
  var cp=el("button","tgl","복사");cp.onclick=function(){ta.select();try{navigator.clipboard.writeText(ta.value).then(function(){cp.textContent="복사됨"})}catch(e){document.execCommand("copy");cp.textContent="복사됨"}};B.appendChild(cp);
  var t2=el("textarea","pfta");t2.placeholder="여기에 백업 코드 붙여넣기";B.appendChild(t2);
  var rs=el("button","pfsave","복원");rs.onclick=function(){try{var L=JSON.parse(decodeURIComponent(escape(atob(t2.value.trim()))));if(!Array.isArray(L))throw 0;
    var M=load(),ids={};M.forEach(function(p){ids[p.id]=1});L.forEach(function(p){if(p&&p.id&&!ids[p.id])M.push(p)});save(M);alert(L.length+"개 복원");X.closeSheet();X.goPf()}catch(e){alert("코드가 올바르지 않습니다.")}};B.appendChild(rs);
}

window.PF={render:function(L){if(cur)renderOne(L);else render(L)},add:addFromSheet,_sim:simulate,_stats:stats,_hist:hist,_put:put,_load:load};
})();
