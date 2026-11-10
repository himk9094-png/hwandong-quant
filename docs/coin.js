/* 코인 탭: 온체인 국면 · 코인 현황 · 전략 비교 · 4시간봉 UT Bot · 내 조합 전략
   데이터: coinhist.json(일봉·온체인), coin.json(4시간봉 상태·알림 기록) — 둘 다 GitHub Actions가 4시간마다 갱신 */
(function(){
"use strict";
var Q=window.HQ,el=Q.el,cls=Q.cls,F=Q.F,CE=window.CE;
var KEY="hq_coin_v1",H=null,X=null,J=null,loading=null,err=null,cur=null;
var NM={BTC:"비트코인",ETH:"이더리움",SOL:"솔라나",XRP:"리플"};
var SETS=[["all","4개 균등",{BTC:1,ETH:1,SOL:1,XRP:1}],["bes","BTC·ETH·SOL",{BTC:1,ETH:1,SOL:1}],["BTC","BTC만",{BTC:1}],["ETH","ETH만",{ETH:1}],["SOL","SOL만",{SOL:1}],["XRP","XRP만",{XRP:1}]];
var opt={start:null,lev:false,cs:"all"};

// ---------------------------------------------------------------- 저장
function load(){try{return JSON.parse(localStorage.getItem(KEY)||"[]")}catch(e){return []}}
function save(L){try{localStorage.setItem(KEY,JSON.stringify(L));return true}catch(e){alert("저장 실패: 브라우저 저장공간을 쓸 수 없습니다.");return false}}
function today(){return new Date(Date.now()+9*36e5).toISOString().slice(0,10)}
function uid(){return Date.now().toString(36)+Math.random().toString(36).slice(2,6)}
function get(id){return load().filter(function(p){return p.id===id})[0]}
function put(p){var L=load(),i=L.findIndex(function(x){return x.id===p.id});if(i>=0)L[i]=p;else L.push(p);return save(L)}
function del(id){save(load().filter(function(p){return p.id!==id}))}

// ---------------------------------------------------------------- 데이터
function data(){
  if(X)return Promise.resolve();if(loading)return loading;
  var t=Math.floor(Date.now()/9e5);
  loading=Promise.all([fetch("coinhist.json?t="+t).then(function(r){if(!r.ok)throw new Error(r.status);return r.json()}),
    fetch("coin.json?t="+t).then(function(r){return r.ok?r.json():null}).catch(function(){return null})])
  .then(function(a){H=a[0];J=a[1];X=CE.prep(H);if(opt.start==null)opt.start=(H.cfg&&H.cfg.start)||2018});
  loading.catch(function(e){loading=null;err=e});
  return loading;
}
var D=function(i){return CE.dateAt(X.start,i)};
function usd(v){if(!CE.isn(v))return "–";return "$"+(v>=100?Math.round(v).toLocaleString("en-US"):v>=1?v.toFixed(2):v.toFixed(4))}
function p0(v){return CE.isn(v)?(v>0?"+":"")+v.toFixed(0)+"%":"–"}
function rel(a,b){return CE.isn(a)&&CE.isn(b)&&b?(a/b-1)*100:null}
function setOf(k){return (SETS.filter(function(s){return s[0]===k})[0]||SETS[0])[2]}
function btOnly(s){return ((H&&H.cfg&&H.cfg.btOnly)||[]).indexOf(s)>=0}
function presetById(id){return CE.PRESETS.filter(function(p){return p.id===id})[0]}
function sec(L,t){L.appendChild(el("div","sec",t))}
function td2(a,b,c){var td=el("td");td.appendChild(el("div",c||"",a));if(b)td.appendChild(el("div","small",b));return td}
function table(head){var t=el("table"),tr=el("tr");head.forEach(function(h){tr.appendChild(el("th",null,h))});t.appendChild(tr);return t}
function wrap(t){var w=el("div");w.style.overflowX="auto";w.appendChild(t);return w}
function chip(t,c){var s=el("span","chip"+(c?" "+c:""),t);return s}
function utTxt(p){return p===1?"매수":p===-1?"매도":"–"}
function regCls(r){return r==="바닥권"?"good":r==="과열권"?"up":""}

// ---------------------------------------------------------------- 화면: 메인
function render(L){
  if(!X){L.appendChild(el("div","empty",err?"코인 데이터를 불러오지 못했습니다 ("+err.message+"). 코인 작업(coin)이 한 번 돌아야 생깁니다.":"불러오는 중…"));
    if(!err)data().then(function(){Q.render()}).catch(function(){Q.render()});return}
  if(cur)return renderOne(L);
  var d=el("p","desc");d.innerHTML="BTC·ETH·SOL·XRP 일봉(한국시간 오전 9시 마감) + 무료 온체인 데이터(Coin Metrics). <b>4시간마다 자동 갱신</b>되고 새 신호는 ntfy로 옵니다. XRP는 보유하지 않는 <b>백테스트용</b>.";L.appendChild(d);
  regimeCard(L);coinTable(L);compare(L);ut4h(L);mine(L);alerts(L);
  sec(L,"주의");
  ["비트코인 큰 사이클이 2014년 이후 3번뿐이라 백테스트는 통계적으로 약합니다. 좋게 나와도 확신하지 마세요.",
   "온체인 기준 기간(4년)은 2년 기준이 백테스트에서 실패한 걸 보고 바꾼 값입니다. 반감기 주기라는 근거는 있지만 과최적화 위험이 있습니다.",
   "2배 ETF 근사는 하루 수익률×2 − 연 5% 비용으로 계산했습니다. 실제 ETF는 괴리·환율·거래시간(미국장) 때문에 다릅니다.",
   "신호가 난 날 종가(한국시간 오전 9시)에 체결했다고 가정합니다. 매매비용 편도 0.15%. 바이낸스 상장 전 구간은 Coin Metrics 종가를 씁니다."].forEach(function(x){L.appendChild(el("p","desc","· "+x))});
  var foot=el("div","small","일봉 기준 "+X.last+" · 온체인 기준 "+(H.cmLast||"–")+(J?" · 4시간봉 갱신 "+J.generated:"")+" (한국시간)");L.appendChild(foot);
}
function regimeCard(L){
  sec(L,"온체인 국면");
  var w=CE.REGIME_W,p4=CE.lastVal(CE.pctOf(X,"btc",w)),p2=CE.lastVal(CE.pctOf(X,"btc",730)),mv=CE.lastVal(X.oc.mvrv.btc),r=CE.regimeOf(p4.v);
  var c=el("div","card"),a=el("div","row1");a.appendChild(el("span","nm","BTC MVRV "+(mv.v!=null?mv.v.toFixed(2):"–")));var rc=chip(r,regCls(r));a.appendChild(rc);c.appendChild(a);
  var m=el("div","mets");m.style.marginLeft="0";
  function met(k,v,vc){var s=el("span","met",k);s.appendChild(el("b",vc||"",v));m.appendChild(s)}
  met("4년 중",p4.v!=null?"하위 "+Math.round(p4.v)+"%":"–");met("2년 중",p2.v!=null?"하위 "+Math.round(p2.v)+"%":"–");
  var cap=CE.lastVal(X.oc.cap),hz=CE.lastVal(X.oc.hzone);met("해시리본",cap.v===true?"채굴자 항복 중":hz.v===true?"회복 30일 이내":"정상");
  var pu=CE.lastVal(X.oc.puell);met("퓨엘",pu.v!=null?pu.v.toFixed(2):"–");
  var nb=CE.lastVal(X.oc.nf.btc),ne=CE.lastVal(X.oc.nf.eth);
  met("거래소 30일 BTC",nb.v!=null?(nb.v>0?"+":"")+nb.v.toFixed(1)+"%":"–",cls(nb.v?-nb.v:0));met("ETH",ne.v!=null?(ne.v>0?"+":"")+ne.v.toFixed(1)+"%":"–",cls(ne.v?-ne.v:0));
  c.appendChild(m);
  var o=[];["eth","xrp"].forEach(function(k){var v=CE.lastVal(X.oc.mvrv[k]),q=CE.lastVal(CE.pctOf(X,k,w));if(v.v!=null)o.push(k.toUpperCase()+" MVRV "+v.v.toFixed(2)+(q.v!=null?" (4년 하위 "+Math.round(q.v)+"%)":""))});
  c.appendChild(el("div","small",o.join(" · ")));
  c.appendChild(mvrvChart());
  var ex=el("p","desc");ex.style.margin="8px 0 0";ex.innerHTML="<b>MVRV</b> = 시가총액 ÷ 보유자 평균 매수가 기준 시총. 낮으면 다수가 손실(바닥권), 높으면 과열. 고점이 사이클마다 낮아져서 고정 기준 대신 <b>최근 4년 안 위치</b>로 판단: 하위 20% 바닥권, 상위 20% 과열권. 거래소 순유출입 −는 거래소에서 빠져나감(매집).";c.appendChild(ex);
  L.appendChild(c);
}
function mvrvChart(){
  var a=X.oc.mvrv.btc,n=X.n,from=Math.max(0,n-CE.REGIME_W),v=[];for(var i=from;i<n;i++)v.push(a[i]);
  var s=v.filter(CE.isn).slice().sort(function(x,y){return x-y});if(s.length<30)return el("div");
  var q=function(f){return s[Math.floor(f*(s.length-1))]},lo=q(.2),hi=q(.8);
  var w=600,h=90,mn=s[0],mx=s[s.length-1],rg=mx-mn||1,y=function(x){return (h-4-(x-mn)/rg*(h-8)).toFixed(1)};
  var ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 "+w+" "+h);svg.setAttribute("preserveAspectRatio","none");svg.setAttribute("class","spark");
  [[lo,"#3ecf8e"],[hi,"#ff5d5d"]].forEach(function(z){var ln=document.createElementNS(ns,"line");ln.setAttribute("x1",0);ln.setAttribute("x2",w);ln.setAttribute("y1",y(z[0]));ln.setAttribute("y2",y(z[0]));ln.setAttribute("stroke",z[1]);ln.setAttribute("stroke-dasharray","4 4");ln.setAttribute("vector-effect","non-scaling-stroke");svg.appendChild(ln)});
  var pts=[];v.forEach(function(x,k){if(CE.isn(x))pts.push((k/(v.length-1)*w).toFixed(1)+","+y(x))});
  var pl=document.createElementNS(ns,"polyline");pl.setAttribute("points",pts.join(" "));pl.setAttribute("fill","none");pl.setAttribute("stroke","#f4c04e");pl.setAttribute("stroke-width","2");pl.setAttribute("vector-effect","non-scaling-stroke");svg.appendChild(pl);
  var wr=el("div");wr.style.marginTop="8px";wr.appendChild(svg);wr.appendChild(el("div","small","BTC MVRV 최근 4년 · 초록 점선 하위 20%("+lo.toFixed(2)+") · 빨강 점선 상위 20%("+hi.toFixed(2)+")"));return wr;
}
function coinTable(L){
  sec(L,"코인 현황");
  var t=table(["코인","일봉 종가","365일선","RSI","UT Bot"]),i=X.n-1;
  CE.COINS.forEach(function(s){var C=X.coins[s];if(!C)return;var tr=el("tr"),px=C.c[i],u=J&&J.ut4h&&J.ut4h[s];
    tr.appendChild(td2(s,btOnly(s)?"백테스트용":NM[s]));
    var ch=rel(px,C.c[i-1]);tr.appendChild(td2(usd(px),F.sp(ch),""));tr.lastChild.lastChild.className="small "+cls(ch);
    var r365=rel(px,C.s365[i]),r200=rel(px,C.s200[i]);tr.appendChild(td2(F.sp(r365),"200일 "+F.sp(r200),cls(r365)));
    var rs=C.rsi[i];tr.appendChild(td2(CE.isn(rs)?Math.round(rs)+"":"–","",rs<30?"good":rs>70?"up":""));
    var ud=C.ut[i];tr.appendChild(td2("4h "+(u?utTxt(u.pos):"–"),"1D "+utTxt(ud),u?(u.pos===1?"up":"dn"):""));
    tr.style.cursor="pointer";tr.onclick=function(){openCoin(s)};t.appendChild(tr)});
  L.appendChild(wrap(t));
  L.appendChild(el("div","small","코인을 누르면 차트·전략별 지금 상태. 365일선·200일선 = 종가가 이동평균보다 몇 % 위/아래인지. RSI 초록 30 미만(과매도), 빨강 70 초과. UT Bot 설정 Key "+X.cfg.utKey+" · ATR "+X.cfg.utAtr+"."));
}
function compare(L){
  sec(L,"전략 비교 (백테스트)");
  L.appendChild(controls(function(){Q.render()}));
  var t=table(["전략","BTC","ETH","SOL","XRP","4개"]);
  CE.PRESETS.forEach(function(p){var tr=el("tr"),a=el("td");a.appendChild(el("div",null,p.name));a.appendChild(el("div","small","자세히 ›"));tr.appendChild(a);
    [{BTC:1},{ETH:1},{SOL:1},{XRP:1},setOf("all")].forEach(function(c){var r=CE.simulate(X,Object.assign({},p,{coins:c,start:opt.start,lev:opt.lev})),s=r&&CE.stats(r.eq,r.expo);
      tr.appendChild(s?td2(p0(s.cagr),p0(s.mdd),cls(s.cagr)):td2("–"))});
    tr.style.cursor="pointer";tr.onclick=function(){cur={k:"preset",id:p.id};Q.render();window.scrollTo(0,0)};t.appendChild(tr)});
  L.appendChild(wrap(t));
  L.appendChild(el("div","small","위: 연평균 수익률 · 아래: 최대 낙폭(MDD). 4개 = 처음에 균등 배분 후 코인별로 따로 매매. SOL은 2020-08 상장 전까지 현금. 전략 이름을 누르면 차트·매매 기록."));
}
function controls(onchange){
  var bar=el("div","pfbar"),y=+today().slice(0,4),s=el("select");
  [2014,2016,2018,2020,2022,2024].filter(function(v){return v<y}).forEach(function(v){var o=el("option",null,v+"년부터");o.value=v;if(v===opt.start)o.selected=true;s.appendChild(o)});
  s.onchange=function(){opt.start=+s.value;onchange()};bar.appendChild(s);
  var lv=el("button","tgl"+(opt.lev?" on":""),"2배 ETF 근사");lv.onclick=function(){opt.lev=!opt.lev;onchange()};bar.appendChild(lv);
  return bar;
}
function ut4h(L){
  if(!J||!J.bt4h)return;
  sec(L,"4시간봉 UT Bot 성과 (참고)");
  var k=opt.lev?"x2":"x1",t=table(["코인","연평균","MDD","연 매매","승률","보유 시"]);
  CE.COINS.forEach(function(s){var b=J.bt4h[s];if(!b)return;var x=b[k],h=opt.lev?b.hold2:b.hold,tr=el("tr");
    tr.appendChild(td2(s,b.from.slice(0,7)+"~"));tr.appendChild(td2(p0(x.cagr),"",cls(x.cagr)));tr.appendChild(td2(p0(x.mdd)));tr.appendChild(td2(Math.round(x.tpy)+"회"));tr.appendChild(td2(Math.round(x.win)+"%"));
    tr.appendChild(td2(p0(h.cagr),"MDD "+p0(h.mdd),cls(h.cagr)));t.appendChild(tr)});
  L.appendChild(wrap(t));
  L.appendChild(el("p","desc",(opt.lev?"2배 ETF 근사 · ":"현물 · ")+"UT Bot 매수 신호에 사고 매도 신호에 파는 것만 반복(매매비용 편도 0.15%). 매매가 잦으면 비용만으로 연 수십 %가 깎입니다. 트레이딩뷰 UT Bot 설정값(Key·ATR)이 다르면 결과도 다릅니다."));
}
function mine(L){
  sec(L,"내 조합 전략");
  var bar=el("div","pfbar");
  [["+ 조건 전략",function(){editRule(null)}],["+ 전략 묶음",function(){editMix(null)}],["백업·복원",backup]].forEach(function(x){var b=el("button","tgl",x[0]);b.onclick=x[1];bar.appendChild(b)});
  L.appendChild(bar);
  var P=load();
  if(!P.length){L.appendChild(el("div","empty","아직 없습니다. 위 전략 비교에서 전략을 눌러 '복제해서 수정'하거나 버튼으로 새로 만드세요. 이 기기 브라우저에 저장됩니다."));return}
  P.slice().reverse().forEach(function(p){
    var b=el("button","card");b.type="button";var a=el("div","row1");a.appendChild(el("span","nm",p.name));a.appendChild(chip(p.type==="mix"?"묶음":"조건",p.type==="mix"?"st":""));b.appendChild(a);
    var r=run(p),s=r&&CE.stats(r.eq,r.expo),m=el("div","mets");m.style.marginLeft="0";
    if(s)[["누적",s.tot],["연",s.cagr],["MDD",s.mdd]].forEach(function(x){var t=el("span","met",x[0]);t.appendChild(el("b",cls(x[1]),p0(x[1])));m.appendChild(t)});
    b.appendChild(m);b.appendChild(el("div","small",(p.type==="mix"?mixDesc(p):ruleDesc(p))+" · 만든 날 "+p.created));
    b.onclick=function(){cur={k:"my",id:p.id};Q.render();window.scrollTo(0,0)};L.appendChild(b)});
}
function alerts(L){
  if(!J)return;
  sec(L,"최근 알림");
  var c=J.cfg||{};L.appendChild(el("p","desc","알림 대상: "+(c.alert_coins||[]).join("·")+" × "+(c.alert_presets||[]).map(function(id){var p=presetById(id);return p?p.name:id}).join(", ")+" · UT Bot 4시간봉 "+(c.alert_ut4h||[]).join("·")+" · 온체인 국면 변화. 내 조합 전략은 이 기기에만 있어서 알림이 가지 않습니다."));
  var A=J.alerts||[];if(!A.length){L.appendChild(el("div","small","아직 없음"));return}
  A.slice(0,10).forEach(function(a){var c2=el("div","card");c2.appendChild(el("div","small",a.t+" · "+a.title));a.lines.forEach(function(x){c2.appendChild(el("div",null,x))});c2.style.fontSize="13px";L.appendChild(c2)});
}

// ---------------------------------------------------------------- 코인 상세 시트
function openCoin(s){
  var B=$s(),C=X.coins[s],i=X.n-1,px=C.c[i];
  var h=el("div","row1");h.style.paddingRight="40px";h.appendChild(el("span","nm",s+" "+NM[s]));h.appendChild(el("span","px",usd(px)));var ch=rel(px,C.c[i-1]);h.appendChild(el("span","chg "+cls(ch),F.sp(ch)));B.appendChild(h);
  if(btOnly(s))B.appendChild(el("div","banner","보유하지 않는 백테스트용 코인입니다."));
  B.appendChild(priceChart(s));
  var g=el("div","grid"),u=J&&J.ut4h&&J.ut4h[s];
  [["365일선",F.sp(rel(px,C.s365[i])),cls(rel(px,C.s365[i]))],["200일선",F.sp(rel(px,C.s200[i])),cls(rel(px,C.s200[i]))],["RSI(일)",CE.isn(C.rsi[i])?Math.round(C.rsi[i])+"":"–",""],
   ["21일/365일",F.sp(rel(C.s21[i],C.s365[i])),""],["UT 1D",utTxt(C.ut[i])+" · 선 "+usd(C.uts[i]),""],["UT 4h",u?utTxt(u.pos)+" · 선 "+usd(u.stop):"–",""]].forEach(function(x){var c=el("div","cell");c.appendChild(el("div","k",x[0]));c.appendChild(el("div","v "+x[2],x[1]));g.appendChild(c)});
  B.appendChild(g);
  if(u)B.appendChild(el("div","small","UT 4h "+utTxt(u.pos)+" 전환: "+u.since+" ("+usd(u.sincePx)+") · 마지막 4시간봉 "+u.bar+" 마감 "+usd(u.px)));
  B.appendChild(el("div","sec","전략별 지금 상태 ("+opt.start+"년부터 매매했다면)"));
  var t=table(["전략","상태","조건"]);
  CE.PRESETS.forEach(function(p){if(p.hold)return;var r=CE.simulate(X,Object.assign({},p,{coins:{[s]:1},start:opt.start})),st=CE.status(X,r)[s],tr=el("tr");
    tr.appendChild(td2(p.name));tr.appendChild(stTd(st));tr.appendChild(td2(st.sell?"매도 조건 충족":st.buy?"매수 조건 충족":"–",st.sell?st.sw:st.buy?st.bw:"",st.sell?"dn":st.buy?"up":""));t.appendChild(tr)});
  B.appendChild(wrap(t));
  var lk=el("div","links");[["트레이딩뷰","https://www.tradingview.com/chart/?symbol=BINANCE:"+s+"USDT"],["바이낸스","https://www.binance.com/en/trade/"+s+"_USDT"]].forEach(function(x){var a=el("a",null,x[0]);a.href=x[1];a.target="_blank";a.rel="noopener";lk.appendChild(a)});B.appendChild(lk);
  Q.show();document.querySelector("#sheet").scrollTop=0;
}
function stTd(st){var main=st.hold>0?"보유 "+Math.round(st.hold*100)+"%":"현금",sub=[];
  if(st.hold>0&&st.nb)sub.push(st.nb+"차 매수까지");if(st.pnl!=null&&st.hold>0)sub.push("손익 "+p0(st.pnl*100));if(st.last)sub.push("마지막 "+(st.last.a==="buy"?"매수 ":"매도 ")+st.last.d.slice(2));
  return td2(main,sub.join(" · "),st.hold>0?"up":"")}
function $s(){var B=document.querySelector("#sheetBody");B.innerHTML="";return B}
function priceChart(s){
  var C=X.coins[s],n=X.n,from=Math.max(C.first,n-365),ser=[["c","#f4c04e",""],["s200","#4d8eff","4 3"],["s365","#3ecf8e","4 3"]],all=[];
  ser.forEach(function(z){for(var i=from;i<n;i++)if(CE.isn(C[z[0]][i]))all.push(C[z[0]][i])});
  var w=600,h=110,mn=Math.min.apply(null,all),mx=Math.max.apply(null,all),rg=mx-mn||1,N=n-from;
  var ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 "+w+" "+h);svg.setAttribute("preserveAspectRatio","none");svg.setAttribute("class","spark");svg.style.height="110px";
  ser.forEach(function(z){var pts=[];for(var i=from;i<n;i++){var v=C[z[0]][i];if(CE.isn(v))pts.push(((i-from)/(N-1)*w).toFixed(1)+","+(h-4-(v-mn)/rg*(h-8)).toFixed(1))}
    var pl=document.createElementNS(ns,"polyline");pl.setAttribute("points",pts.join(" "));pl.setAttribute("fill","none");pl.setAttribute("stroke",z[1]);pl.setAttribute("stroke-width",z[0]==="c"?"2":"1.3");if(z[2])pl.setAttribute("stroke-dasharray",z[2]);pl.setAttribute("vector-effect","non-scaling-stroke");svg.appendChild(pl)});
  var wr=el("div");wr.appendChild(svg);var lg=el("div","small");lg.innerHTML="최근 1년 · <span style='color:#f4c04e'>━ 종가</span> <span style='color:#4d8eff'>┅ 200일선</span> <span style='color:#3ecf8e'>┅ 365일선</span>";wr.appendChild(lg);return wr;
}

// ---------------------------------------------------------------- 전략 실행
function resolve(ref,cs){            // 묶음 안의 전략 참조 → 전략 정의
  if(!ref)return null;var a=ref.split(":");
  if(a[0]==="preset"){var p=presetById(a[1]);return p?Object.assign({},p,{coins:setOf(cs||"all"),name:p.name+"("+(SETS.filter(function(s){return s[0]===(cs||"all")})[0]||SETS[0])[1]+")"}):null}
  var q=get(a[1]);return q&&q.type==="rule"?q:null}
function run(p,start,lev){
  start=start||p.start;
  if(p.type==="mix")return CE.mix(X,p.items.map(function(it){var P=resolve(it.ref,it.cs);if(P&&lev!=null)P=Object.assign({},P,{lev:lev});return {w:it.w,P:P}}),start);
  var P=Object.assign({},p,{start:start});if(lev!=null)P.lev=lev;return CE.simulate(X,P)}
function hold(coins,start,lev){return CE.simulate(X,{hold:true,coins:coins,start:start,lev:lev})}

// ---------------------------------------------------------------- 화면: 전략 상세
function renderOne(L){
  var isP=cur.k==="preset",p=isP?presetById(cur.id):get(cur.id);if(!p){cur=null;return render(L)}
  var top=el("div","pfbar");
  var btn=[["← 코인",function(){cur=null;Q.render()}]];
  if(isP)btn.push(["복제해서 수정",function(){editRule(fromPreset(p))}]);
  else btn.push(["수정",function(){p.type==="mix"?editMix(p):editRule(p)}],["복제",function(){var q=JSON.parse(JSON.stringify(p));q.id=uid();q.name+=" 사본";q.created=today();put(q);cur={k:"my",id:q.id};Q.render()}],["삭제",function(){if(confirm("'"+p.name+"' 삭제할까요?")){del(p.id);cur=null;Q.render()}}]);
  btn.forEach(function(x){var b=el("button","tgl",x[0]);b.onclick=x[1];top.appendChild(b)});L.appendChild(top);
  sec(L,p.name);L.appendChild(el("p","desc",isP?p.d:p.type==="mix"?mixDesc(p):ruleDesc(p)));
  var start=isP?opt.start:(+p.start||opt.start),lev=isP?opt.lev:null,P;
  if(isP){var cb=controls(function(){Q.render()}),cs=el("select");SETS.forEach(function(s){var o=el("option",null,s[1]);o.value=s[0];if(s[0]===opt.cs)o.selected=true;cs.appendChild(o)});cs.onchange=function(){opt.cs=cs.value;Q.render()};cb.insertBefore(cs,cb.firstChild);L.appendChild(cb);
    P=Object.assign({},p,{coins:setOf(opt.cs)})}
  else P=p;
  var r=run(P,start,lev);if(!r){L.appendChild(el("div","empty","계산할 수 없습니다. 코인이나 전략을 확인하세요."));return}
  var coins=P.type==="mix"?mixCoins(P):P.coins,levB=isP?opt.lev:!!P.lev,h=hold(coins,start,levB),b=hold({BTC:1},start,false);
  var s=CE.stats(r.eq,r.expo),sh=CE.stats(h.eq,h.expo),sb=CE.stats(b.eq,b.expo);
  if(!s){L.appendChild(el("div","empty","기간이 너무 짧습니다."));return}
  L.appendChild(chart([[r.eq,"#f4c04e",""],[h.eq,"#8f9ab3",""],[b.eq,"#4d8eff","4 3"]],r.i0));
  var lg=el("div","small");lg.innerHTML="<span style='color:#f4c04e'>━ 이 전략</span> &nbsp; <span style='color:#8f9ab3'>━ 같은 코인 그냥 보유</span> &nbsp; <span style='color:#4d8eff'>┅ BTC 보유</span>";L.appendChild(lg);
  var t=table(["",D(r.i0).slice(0,7)+"~","연평균","MDD","샤프","투자비중"]);
  [["이 전략",s],["그냥 보유",sh],["BTC 보유",sb]].forEach(function(x){var v=x[1],tr=el("tr");tr.appendChild(el("td",null,x[0]));
    [v.tot,v.cagr,v.mdd].forEach(function(z){tr.appendChild(el("td",cls(z),p0(z)))});tr.appendChild(el("td",null,v.sh.toFixed(2)));tr.appendChild(el("td",null,Math.round(v.expo)+"%"));t.appendChild(tr)});
  L.appendChild(wrap(t));
  L.appendChild(el("div","small","투자비중 = 기간 평균으로 코인에 들어가 있던 비율(나머지는 현금, 이자 0). 매매비용 편도 0.15%"+(levB?" · 2배 ETF 근사":"")+"."));
  if(P.type!=="mix"){sec(L,"지금 상태 ("+X.last+" 종가 기준)");var st=CE.status(X,r),t2=table(["코인","상태","오늘 조건"]);
    Object.keys(st).forEach(function(k){var x=st[k],tr=el("tr");tr.appendChild(td2(k,btOnly(k)?"백테스트용":""));tr.appendChild(stTd(x));tr.appendChild(td2(x.sell?"매도 조건 충족":x.buy?"매수 조건 충족":"–",x.sell?x.sw:x.buy?x.bw:"",x.sell?"dn":x.buy?"up":""));t2.appendChild(tr)});
    L.appendChild(wrap(t2))}
  sec(L,"연도별");var Y=CE.yearly(X,r),Yh=CE.yearly(X,h),Yb=CE.yearly(X,b),t3=table(["연도","이 전략","그냥 보유","BTC"]);
  Object.keys(Y).sort().forEach(function(y){var tr=el("tr");tr.appendChild(el("td",null,y+(y===X.last.slice(0,4)?" (진행)":"")));[Y[y],Yh[y],Yb[y]].forEach(function(o){var z=o?(o.a-1)*100:null;tr.appendChild(el("td",cls(z),p0(z)))});t3.appendChild(tr)});
  L.appendChild(wrap(t3));
  sec(L,"매매 기록 (최근 40건, 총 "+r.trades.length+"건)");
  if(!r.trades.length)L.appendChild(el("div","empty","이 기간에 매매가 없습니다. 조건이 너무 까다로운지 확인하세요."));
  else{var t4=table(["날짜","코인","매매","가격"]);r.trades.slice(-40).reverse().forEach(function(x){var tr=el("tr");tr.appendChild(td2(D(x.i).slice(2),x.src||""));tr.appendChild(td2(x.s));tr.appendChild(td2((x.a==="buy"?"매수 ":"매도 ")+x.k,x.why||"",x.a==="buy"?"up":"dn"));tr.appendChild(td2(usd(x.px)));t4.appendChild(tr)});L.appendChild(wrap(t4))}
}
function chart(S,i0){
  var w=600,h=200,all=[];S.forEach(function(s){s[0].forEach(function(v){if(v>0)all.push(v)})});
  var lmn=Math.log(Math.min.apply(null,all)),rg=(Math.log(Math.max.apply(null,all))-lmn)||1,n=S[0][0].length,step=Math.max(1,Math.floor(n/400));
  var ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 "+w+" "+h);svg.setAttribute("preserveAspectRatio","none");svg.setAttribute("class","pfchart");
  var y=function(v){return (h-6-(Math.log(Math.max(v,1e-6))-lmn)/rg*(h-12)).toFixed(1)};
  var base=document.createElementNS(ns,"line");base.setAttribute("x1",0);base.setAttribute("x2",w);base.setAttribute("y1",y(1));base.setAttribute("y2",y(1));base.setAttribute("stroke","#2c3754");base.setAttribute("vector-effect","non-scaling-stroke");svg.appendChild(base);
  S.slice().reverse().forEach(function(s){var pts=[];for(var i=0;i<n;i+=step)pts.push((i/(n-1)*w).toFixed(1)+","+y(s[0][i]));pts.push(w+","+y(s[0][n-1]));
    var pl=document.createElementNS(ns,"polyline");pl.setAttribute("points",pts.join(" "));pl.setAttribute("fill","none");pl.setAttribute("stroke",s[1]);pl.setAttribute("stroke-width",s[1]==="#f4c04e"?"2.5":"1.5");if(s[2])pl.setAttribute("stroke-dasharray",s[2]);pl.setAttribute("vector-effect","non-scaling-stroke");svg.appendChild(pl)});
  var wr=el("div");wr.appendChild(svg);var ax=el("div","small");ax.style.display="flex";ax.style.justifyContent="space-between";ax.appendChild(el("span",null,D(i0).slice(0,7)));ax.appendChild(el("span",null,"로그 눈금"));ax.appendChild(el("span",null,D(i0+n-1).slice(0,7)));wr.appendChild(ax);return wr;
}

// ---------------------------------------------------------------- 설명문
var O={
 ma:[["","안 씀"],["below365","365일선 아래"],["below200","200일선 아래"],["above200","200일선 위 (추세)"],["above365","365일선 위 (추세)"]],
 rsi:[[0,"안 씀"],[25,"25 미만"],[30,"30 미만"],[35,"35 미만"],[40,"40 미만"],[50,"50 미만"]],
 mvrv:[[0,"안 씀"],[10,"하위 10%"],[20,"하위 20%"],[30,"하위 30%"],[50,"하위 50%"]],
 ut:[[0,"안 씀"],[1,"UT Bot 일봉 매수 상태"]],
 hr:[[0,"안 씀"],[1,"채굴자 항복 중 또는 회복 30일 이내"]],
 ext:[[0,"안 씀"],[30,"+30% 이상"],[50,"+50% 이상"],[80,"+80% 이상"],[100,"+100% 이상"]],
 rsiS:[[0,"안 씀"],[70,"70 초과"],[75,"75 초과"],[80,"80 초과"]],
 mvrvS:[[0,"안 씀"],[70,"상위 30%"],[80,"상위 20%"],[90,"상위 10%"],[95,"상위 5%"]],
 utS:[[0,"안 씀"],[1,"UT Bot 일봉 매도 신호"]],
 maS:[["","안 씀"],["below200","200일선 아래로 이탈"],["below365","365일선 아래로 이탈"]],
 stop:[[0,"안 씀"],[20,"−20%"],[30,"−30%"],[50,"−50%"]],
 oc:[["btc","BTC MVRV"],["own","코인 자체 MVRV (SOL은 BTC로)"]],
 ocw:[[1460,"최근 4년 (반감기 한 주기)"],[730,"최근 2년"]],
 nb:[[1,"한 번에"],[2,"2번"],[3,"3번"],[4,"4번"],[5,"5번"]],
 gap:[[1,"1일"],[3,"3일"],[7,"7일"],[14,"14일"],[30,"30일"]],
 ns:[[1,"한 번에"],[2,"2번"],[3,"3번"]],
 lev:[[0,"현물 (1배)"],[1,"2배 ETF 근사"]]
};
function lab(list,v){var x=list.filter(function(o){return String(o[0])===String(v)})[0];return x?x[1]:""}
function coinsTxt(c){return CE.COINS.filter(function(s){return +c[s]>0}).map(function(s){var vals=CE.COINS.map(function(k){return +c[k]||0}).filter(Boolean);return s+(vals.some(function(v){return v!==vals[0]})?" "+c[s]:"")}).join("·")}
function ruleDesc(p){
  var b=p.buy||{},s=p.sell||{},bb=[],ss=[],w=(+p.ocw||730)/365+"년";
  if(b.ma)bb.push(lab(O.ma,b.ma));if(b.rsi)bb.push("RSI "+b.rsi+" 미만");if(b.mvrv)bb.push("MVRV "+w+" 하위 "+b.mvrv+"%");if(b.ut)bb.push("UT 일봉 매수");if(b.hr)bb.push("해시리본");
  if(s.ext)ss.push("21일선 365일선 +"+s.ext+"%");if(s.rsi)ss.push("RSI "+s.rsi+" 초과");if(s.mvrv)ss.push("MVRV 상위 "+(100-s.mvrv)+"%");if(s.ut)ss.push("UT 일봉 매도");if(s.ma)ss.push(lab(O.maS,s.ma));if(s.stop)ss.push("손절 −"+s.stop+"%");
  return coinsTxt(p.coins||{})+" · 매수 "+(bb.join("+")||"없음")+(p.nb>1?" ("+p.nb+"분할·"+p.gap+"일)":"")+" · 매도 "+(ss.join(" 또는 ")||"없음")+(p.ns>1?" ("+p.ns+"분할)":"")+(p.lev?" · 2배 ETF":"")+" · "+p.start+"년부터"}
function mixDesc(p){return p.items.map(function(it){var P=resolve(it.ref,it.cs);return (P?P.name:"(삭제된 전략)")+" "+it.w}).join(" + ")+" · "+p.start+"년부터"}
function mixCoins(p){var c={};p.items.forEach(function(it){var P=resolve(it.ref,it.cs);if(P)Object.keys(P.coins).forEach(function(s){if(+P.coins[s]>0)c[s]=1})});return Object.keys(c).length?c:{BTC:1}}
function fromPreset(p){var q=JSON.parse(JSON.stringify(p));delete q.d;delete q.hold;q.id=null;q.type="rule";q.name=p.name+" (내 버전)";q.coins=Object.assign({},setOf(opt.cs));q.start=opt.start;q.lev=opt.lev?1:0;
  if(p.hold){q.buy={ma:"above365"};q.sell={ma:"below365"};q.nb=1;q.ns=1;q.gap=1}return q}

// ---------------------------------------------------------------- 편집
function sheet(title){var B=$s();B.appendChild(el("div","sec",title));Q.show();document.querySelector("#sheet").scrollTop=0;return B}
function field(B,label,node){var w=el("label","pff");w.appendChild(el("span",null,label));w.appendChild(node);B.appendChild(w);return node}
function sel(opts,v){var s=el("select");opts.forEach(function(o){var x=el("option",null,o[1]);x.value=o[0];if(String(o[0])===String(v))x.selected=true;s.appendChild(x)});return s}
function years(){var a=[],y=+today().slice(0,4);for(var k=2014;k<y;k++)a.push([k,k+"년부터"]);return a}
function editRule(p0_){
  var isNew=!p0_||!p0_.id,p=p0_?JSON.parse(JSON.stringify(p0_)):{type:"rule",name:"내 코인 전략 "+(load().filter(function(x){return x.type==="rule"}).length+1),coins:{BTC:1,ETH:1,SOL:1,XRP:0},buy:{ma:"below365",rsi:30},sell:{ext:50},oc:"btc",ocw:1460,nb:4,gap:7,ns:2,lev:0,start:opt.start||2018};
  if(!p.id)p.id=uid();if(!p.created)p.created=today();p.buy=p.buy||{};p.sell=p.sell||{};
  var B=sheet(isNew?"새 조건 전략":"조건 전략 수정");
  var nm=field(B,"이름",el("input"));nm.value=p.name;
  B.appendChild(el("div","sec","코인 비중 (0이면 제외, 숫자 비율)"));
  var cw={};CE.COINS.forEach(function(s){var i=el("input");i.type="number";i.min="0";i.step="any";i.value=+(p.coins[s]||0);i.className="pfw";cw[s]=field(B,s+" "+NM[s]+(btOnly(s)?" (백테스트용)":""),i)});
  B.appendChild(el("div","sec","매수 조건 (고른 것 모두 충족)"));
  var f={ma:field(B,"이동평균",sel(O.ma,p.buy.ma||"")),rsi:field(B,"RSI(14, 일봉)",sel(O.rsi,p.buy.rsi||0)),mvrv:field(B,"온체인 MVRV",sel(O.mvrv,p.buy.mvrv||0)),ut:field(B,"UT Bot",sel(O.ut,p.buy.ut||0)),hr:field(B,"해시리본",sel(O.hr,p.buy.hr||0))};
  B.appendChild(el("div","sec","매도 조건 (하나라도 충족)"));
  var g={ext:field(B,"과열: 21일선이 365일선보다",sel(O.ext,p.sell.ext||0)),rsi:field(B,"RSI 과열",sel(O.rsiS,p.sell.rsi||0)),mvrv:field(B,"온체인 MVRV 과열",sel(O.mvrvS,p.sell.mvrv||0)),ut:field(B,"UT Bot",sel(O.utS,p.sell.ut||0)),ma:field(B,"이동평균 이탈",sel(O.maS,p.sell.ma||"")),stop:field(B,"손절 (평균 매수가 대비)",sel(O.stop,p.sell.stop||0))};
  B.appendChild(el("div","sec","온체인 기준 · 분할 · 기타"));
  var oc=field(B,"MVRV 종류",sel(O.oc,p.oc||"btc")),ocw=field(B,"MVRV 비교 기간",sel(O.ocw,p.ocw||1460));
  var nb=field(B,"매수 분할",sel(O.nb,p.nb)),gap=field(B,"분할 간격(최소)",sel(O.gap,p.gap)),ns=field(B,"매도 분할",sel(O.ns,p.ns));
  var lv=field(B,"상품",sel(O.lev,p.lev?1:0)),st=field(B,"백테스트 시작",sel(years(),p.start));
  B.appendChild(el("p","desc","분할 매수: 조건이 켜질 때마다 처음 현금의 1/N씩, 최소 간격을 두고 삽니다. 분할 매도: 조건이 켜지면 남은 수량을 나눠 팝니다(손절은 전량). 매도 조건이 켜진 날엔 새로 사지 않습니다."));
  var sv=el("button","pfsave","저장하고 백테스트");B.appendChild(sv);
  sv.onclick=function(){
    var coins={},any=false;CE.COINS.forEach(function(s){var v=+cw[s].value||0;if(v>0){coins[s]=v;any=true}});if(!any){alert("코인을 하나 이상 넣으세요.");return}
    var buy={},sell={};Object.keys(f).forEach(function(k){var v=f[k].value;if(v&&v!=="0")buy[k]=isNaN(+v)?v:+v});Object.keys(g).forEach(function(k){var v=g[k].value;if(v&&v!=="0")sell[k]=isNaN(+v)?v:+v});
    if(!Object.keys(buy).length){alert("매수 조건을 하나 이상 고르세요.");return}
    if(!Object.keys(sell).length){alert("매도 조건을 하나 이상 고르세요. (그냥 보유와 비교하려면 '손절 −50%' 같은 느슨한 조건)");return}
    if(buy.ma&&sell.ma&&((buy.ma==="below365"&&sell.ma==="below365")||(buy.ma==="below200"&&sell.ma==="below200"))){alert("매수 조건과 매도 조건이 같은 이동평균 아래라서 사자마자 팔게 됩니다.");return}
    p.name=nm.value.trim()||p.name;p.coins=coins;p.buy=buy;p.sell=sell;p.oc=oc.value;p.ocw=+ocw.value;p.nb=+nb.value;p.gap=+gap.value;p.ns=+ns.value;p.lev=+lv.value;p.start=+st.value;
    if(put(p)){cur={k:"my",id:p.id};Q.closeSheet();setTimeout(function(){Q.render();window.scrollTo(0,0)},30)}};
}
function editMix(p0_){
  var isNew=!p0_,p=p0_?JSON.parse(JSON.stringify(p0_)):{id:uid(),type:"mix",name:"내 전략 묶음 "+(load().filter(function(x){return x.type==="mix"}).length+1),items:[{ref:"preset:mr_oc",cs:"bes",w:1},{ref:"preset:trend",cs:"bes",w:1}],start:opt.start||2018,created:today()};
  var B=sheet(isNew?"새 전략 묶음":"전략 묶음 수정");
  B.appendChild(el("p","desc","여러 전략에 처음 비중대로 돈을 나눠 넣고 각자 매매합니다(중간 재조정 없음). 예: 역추세 50 + 추세 50."));
  var nm=field(B,"이름",el("input"));nm.value=p.name;var st=field(B,"백테스트 시작",sel(years(),p.start));
  var refs=CE.PRESETS.map(function(x){return ["preset:"+x.id,x.name]}).concat(load().filter(function(x){return x.type==="rule"}).map(function(x){return ["my:"+x.id,"내 전략: "+x.name]}));
  var list=el("div");B.appendChild(list);
  function draw(){list.innerHTML="";p.items.forEach(function(it,k){
    var box=el("div","card");var s1=sel(refs,it.ref);s1.style.width="100%";s1.onchange=function(){it.ref=s1.value;draw()};box.appendChild(s1);
    var r=el("div","pfrow");if(it.ref.indexOf("preset:")===0){var s2=sel(SETS.map(function(s){return [s[0],s[1]]}),it.cs||"all");s2.onchange=function(){it.cs=s2.value};r.appendChild(s2)}else r.appendChild(el("span","small","코인은 그 전략 설정대로"));
    r.appendChild(el("span","small","비중"));var wi=el("input");wi.type="number";wi.min="0";wi.step="any";wi.value=it.w;wi.className="pfw";wi.oninput=function(){it.w=+wi.value||0};r.appendChild(wi);
    var x=el("button","tgl","삭제");x.onclick=function(){p.items.splice(k,1);draw()};r.appendChild(x);box.appendChild(r);list.appendChild(box)})}
  draw();
  var add=el("button","addpf","+ 전략 추가");add.onclick=function(){p.items.push({ref:"preset:hold",cs:"bes",w:1});draw()};B.appendChild(add);
  var sv=el("button","pfsave","저장하고 백테스트");B.appendChild(sv);
  sv.onclick=function(){p.items=p.items.filter(function(x){return x.w>0});if(p.items.length<1){alert("전략을 하나 이상 넣으세요.");return}
    p.name=nm.value.trim()||p.name;p.start=+st.value;if(put(p)){cur={k:"my",id:p.id};Q.closeSheet();setTimeout(function(){Q.render();window.scrollTo(0,0)},30)}};
}
function backup(){
  var B=sheet("코인 전략 백업·복원");
  B.appendChild(el("p","desc","아래 코드를 복사해 보관하세요. 복원할 땐 붙여넣고 '복원'을 누르면 기존 목록에 합쳐집니다."));
  var ta=el("textarea","pfta");ta.value=btoa(unescape(encodeURIComponent(JSON.stringify(load()))));B.appendChild(ta);
  var cp=el("button","tgl","복사");cp.onclick=function(){ta.select();try{navigator.clipboard.writeText(ta.value).then(function(){cp.textContent="복사됨"})}catch(e){document.execCommand("copy");cp.textContent="복사됨"}};B.appendChild(cp);
  var t2=el("textarea","pfta");t2.placeholder="여기에 백업 코드 붙여넣기";B.appendChild(t2);
  var rs=el("button","pfsave","복원");rs.onclick=function(){try{var L=JSON.parse(decodeURIComponent(escape(atob(t2.value.trim()))));if(!Array.isArray(L))throw 0;
    var M=load(),ids={};M.forEach(function(p){ids[p.id]=1});L.forEach(function(p){if(p&&p.id&&!ids[p.id])M.push(p)});save(M);alert(L.length+"개 복원");Q.closeSheet();Q.render()}catch(e){alert("코드가 올바르지 않습니다.")}};B.appendChild(rs);
}

window.CO={render:render,home:function(){cur=null}};
})();
