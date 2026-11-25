/* 소형주: 강환국식 소형주 전략 7종 백테스트(생존편향 없음·비용 반영)와 전략별 지금 종목. 데이터 = smallcap.json (매일 밤 collect 작업이 갱신). */
(function(){
"use strict";
var X=window.HQ,el=X.el,num=X.num,cls=X.cls;
var KEY="hq_small_v1",J=null,loading=false,err=false;
var st={b:"S",f:"Q",n:20,r:1,s:3,v:"now",tp:0};
try{var sv=JSON.parse(localStorage.getItem(KEY)||"null");if(sv)Object.keys(st).forEach(function(k){if(sv[k]!=null)st[k]=sv[k]})}catch(e){}
function keep(){try{localStorage.setItem(KEY,JSON.stringify(st))}catch(e){}}
var FQ={Q:"분기",M:"매월",W:"매주"};
function pc(v,sign){return v==null?"–":(sign&&v>0?"+":"")+v.toFixed(1)+"%"}
function row(s,f,n,r,b){b=b||st.b;for(var i=0;i<J.grid.length;i++){var g=J.grid[i];if((g.b||"S")===b&&g.s===s&&g.f===f&&g.n===n&&g.r===r&&(g.tp||0)===st.tp)return g}return null}
function nm(s){var x=J.names[String(s)]||String(s);return st.b==="S"?x:x.replace(/^소형주 /,"")}
function band(){var B=J.bands||[{k:"S",l:"소형주",d:"시가총액 하위 0~20%"}];for(var i=0;i<B.length;i++)if(B[i].k===st.b)return B[i];return B[0]}
function seg(label,opts,key){
  var w=el("div","pff");w.appendChild(el("span",null,label));var b=el("div","pfbar");b.style.margin="0";
  opts.forEach(function(o){var t=el("button","tgl"+(st[key]===o[0]?" on":""),o[1]);t.type="button";t.setAttribute("aria-pressed",st[key]===o[0]);t.onclick=function(){st[key]=o[0];keep();X.render()};b.appendChild(t)});
  w.appendChild(b);return w;
}

// ---------------------------------------------------------------- 곡선(로그 눈금)
function chart(lines){
  var W=600,H=230,pl=44,pr=8,pt=10,pb=26,ns="http://www.w3.org/2000/svg",M=J.months;
  var lo=Infinity,hi=-Infinity;
  lines.forEach(function(L){L.c.forEach(function(v){if(v>0){lo=Math.min(lo,v);hi=Math.max(hi,v)}})});
  if(!isFinite(lo))return el("div","small","곡선 없음");
  lo=Math.min(lo,1);var a=Math.log(lo),b=Math.log(hi),rg=(b-a)||1;
  function xx(i){return pl+i/(M.length-1)*(W-pl-pr)}
  function yy(v){return pt+(1-(Math.log(v)-a)/rg)*(H-pt-pb)}
  var svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 "+W+" "+H);svg.setAttribute("class","pfchart");svg.style.height="auto";
  svg.setAttribute("role","img");svg.setAttribute("aria-label","자산 곡선(로그 눈금)");
  function mk(t,at){var e=document.createElementNS(ns,t);Object.keys(at).forEach(function(k){e.setAttribute(k,at[k])});svg.appendChild(e);return e}
  [0.25,0.5,1,2,4,8,16,32,64,128,256].forEach(function(v){if(v<lo*0.98||v>hi*1.02)return;var y=yy(v);
    mk("line",{x1:pl,x2:W-pr,y1:y,y2:y,stroke:"#2c3754","stroke-width":1});
    var t=mk("text",{x:pl-6,y:y+5,"text-anchor":"end","font-size":15,fill:"#8f9ab3"});t.textContent=v+"배"});
  var lastY="";M.forEach(function(m,i){var y=m.slice(0,4);if(y!==lastY&&m.slice(5)==="01"&&(+y)%2===0){var t=mk("text",{x:xx(i),y:H-6,"text-anchor":"middle","font-size":15,fill:"#8f9ab3"});t.textContent="'"+y.slice(2)}lastY=y});
  lines.forEach(function(L){
    var d="",pen=false;L.c.forEach(function(v,i){if(v>0){d+=(pen?"L":"M")+xx(i).toFixed(1)+","+yy(v).toFixed(1);pen=true}else pen=false});
    var p=mk("path",{d:d,fill:"none",stroke:L.col,"stroke-width":L.w||2,"stroke-linejoin":"round"});if(L.dash)p.setAttribute("stroke-dasharray",L.dash);
  });
  var wrap=el("div");wrap.appendChild(svg);
  var lg=el("div","pfbar");lg.style.margin="4px 0 0";
  lines.forEach(function(L){var s=el("span","met");var sw=el("span");sw.style.cssText="display:inline-block;width:14px;height:0;border-top:3px "+(L.dash?"dotted ":"solid ")+L.col+";margin-right:5px;vertical-align:middle";s.appendChild(sw);s.appendChild(document.createTextNode(L.name+" "));s.appendChild(el("b",null,pc(L.cagr)+"/년"));lg.appendChild(s)});
  wrap.appendChild(lg);return wrap;
}

// ---------------------------------------------------------------- 표
function table(cols,rows,onclick){
  var w=el("div","table-frame");w.tabIndex=0;var tb=el("table","scrollt"),hr=el("tr");
  cols.forEach(function(c){hr.appendChild(el("th",null,c))});tb.appendChild(hr);
  rows.forEach(function(r){var tr=el("tr");r.cells.forEach(function(v,i){var td=el("td",i?r.cls[i]||"":"",v);if(!i&&r.bold)td.style.fontWeight="700";tr.appendChild(td)});
    if(r.on){tr.style.background="#3a3215";Array.prototype.forEach.call(tr.children,function(td){td.style.background="#3a3215"})}
    if(onclick&&r.key!=null){tr.style.cursor="pointer";tr.onclick=function(){onclick(r.key)}}
    tb.appendChild(tr)});
  w.appendChild(tb);return w;
}

// ---------------------------------------------------------------- 종목 카드
function card(c,i,o,snap){
  var b=el("button","card");b.type="button";
  var a=el("div","row1");a.appendChild(el("span","rk",i+1));a.appendChild(el("span","nm",o.n));
  if(o.risk)a.appendChild(el("span","chip","🟡 희석"));
  if(o.halt)a.appendChild(el("span","chip st","거래 없음"));
  a.appendChild(el("span","px",o.px!=null?num(o.px):"–"));
  if(snap==="q"&&o.since!=null)a.appendChild(el("span","chg "+cls(o.since),pc(o.since,true)));
  b.appendChild(a);
  b.appendChild(el("div","sub",c+" · 시총 "+num(o.cap)+"억"+(o.fin?" · 재무 "+o.fin:"")));
  var m=el("div","mets");
  [["PBR",o.pbr!=null?o.pbr.toFixed(2):"–"],["PER",o.per!=null?o.per.toFixed(1):"–"],["PCR",o.pcr!=null?o.pcr.toFixed(1):"–"],["GP/A",o.gpa!=null?o.gpa.toFixed(0)+"%":"–"],["F",o.f+"/3"],["12개월",pc(o.mom,true)]].forEach(function(x){var s=el("span","met",x[0]);s.appendChild(el("b",null,x[1]));m.appendChild(s)});
  b.appendChild(m);
  if(o.risk){var r=el("div","sub","최근 1년 공시: "+o.risk);r.style.color="#f4c04e";b.appendChild(r)}
  b.onclick=function(){var R=X.BY()[c];if(R)X.openSheet(R);else window.open("https://m.stock.naver.com/domestic/stock/"+c+"/total","_blank","noopener")};
  return b;
}

// ---------------------------------------------------------------- 화면
function draw(L){
  if(J.bands&&!J.bands.some(function(x){return x.k===st.b}))st.b="S";
  if(st.b!=="S"&&st.f==="W")st.f="Q";
  var hasTP=J.grid.some(function(g){return g.tp===10});
  if(!hasTP)st.tp=0;
  var ord=J.order,s=st.s;if(ord.indexOf(s)<0||!row(s,st.f,st.n,st.r))s=st.s=3;
  var BD=band(),d=el("p","desc");
  d.innerHTML="거래 가능한 보통주 중 <b>"+BD.l+"("+BD.d+")</b>에서 고르는 강환국식 팩터 전략. "+(st.b==="S"?"":"<b>강환국 원래 전략은 소형주용</b>이고, 이 구간은 같은 규칙을 적용해 본 비교용입니다. ")+J.start.slice(0,7).replace("-",".")+"~"+J.asof.slice(0,7).replace("-",".")+" 백테스트 · <b>상장폐지 종목 포함</b> · 재무는 공시 다음 날부터 · 전날 종가 신호 → 다음 날 시가 체결 · 수수료+매도세+슬리피지 "+J.slip.toFixed(1)+"% 반영 · 20일 평균 거래대금 "+J.min_val+"억↑. 데이터 "+J.asof+".";
  L.appendChild(d);
  if(J.bands)L.appendChild(seg("대상",J.bands.map(function(x){return [x.k,x.l]}),"b"));
  L.appendChild(seg("리밸런싱",st.b==="S"?[["Q","분기"],["M","매월"],["W","매주"]]:[["Q","분기"],["M","매월"]],"f"));
  L.appendChild(seg("종목 수",[[10,"10"],[20,"20"],[30,"30"]],"n"));
  L.appendChild(seg("🟡 희석 공시 종목",[[1,"빼기"],[0,"포함"]],"r"));

  if(hasTP)L.appendChild(seg("종목별 익절",[[0,"없음"],[10,"10%"],[20,"20%"],[30,"30%"]],"tp"));
  if(st.tp)L.appendChild(el("p","desc","종가가 평균 매입가보다 "+st.tp+"% 이상 오르면 다음 거래 가능 시가에 전량 매도합니다. 매도 대금은 다음 리밸런싱까지 현금으로 둡니다."));

  // 성과 표
  L.appendChild(el("div","sec",BD.l+" 전략별 성과 · "+FQ[st.f]+" "+st.n+"종목 · 🟡"+(st.r?"빼기":"포함")+(st.tp?" · 익절 "+st.tp+"%":"")));
  var rows=ord.map(function(k){var g=row(k,st.f,st.n,st.r);if(!g)return null;var y=g.y||{};
    return {key:k,on:k===s,bold:k===99,cells:[nm(k),pc(g.cagr),pc(g.mdd),pc(y["2026"],true),pc(y["2025"],true),pc(g.cost)],cls:{1:cls(g.cagr),3:cls(y["2026"]),4:cls(y["2025"])}}}).filter(Boolean);
  L.appendChild(table(["전략","연수익","최대낙폭","26년","25년","비용/년"],rows,function(k){st.s=k;keep();X.render()}));
  L.appendChild(el("div","small","줄을 누르면 아래 곡선·종목이 그 전략으로 바뀝니다. 26년은 "+J.asof.slice(5).replace("-","/")+"까지. 비용 = 수수료+세금+슬리피지(계좌 대비 연간)."));
  var g=row(s,st.f,st.n,st.r);
  var dd=el("p","desc");dd.style.marginTop="8px";dd.innerHTML="<b>"+nm(s)+"</b> — "+(J.desc[String(s)]||"");L.appendChild(dd);

  // 곡선
  var co=row(99,st.f,st.n,st.r),b0=row(0,st.f,st.n,st.r),lines=[];
  if(g)lines.push({name:nm(s),c:g.c,col:"#f4c04e",cagr:g.cagr,w:2.5});
  if(co&&s!==99)lines.push({name:"7전략 동일비중",c:co.c,col:"#3ecf8e",cagr:co.cagr});
  if(b0&&s!==0)lines.push({name:"초소형주(비교)",c:b0.c,col:"#8f9ab3",cagr:b0.cagr,dash:"4 4",w:1.5});
  L.appendChild(el("div","sec","자산 곡선 (1 = 시작, 로그 눈금)"));L.appendChild(chart(lines));

  // 연도별
  if(g){
    L.appendChild(el("div","sec","연도별 수익률"));
    var ys=Object.keys(g.y).sort().reverse();
    L.appendChild(table(["연도",nm(s),"7전략","초소형주"],ys.map(function(y){var a=g.y[y],c2=co&&co.y[y],d0=b0&&b0.y[y];return {cells:[y,pc(a,true),s===99?"·":pc(c2,true),pc(d0,true)],cls:{1:cls(a),2:cls(c2),3:cls(d0)}}})));
    L.appendChild(el("div","small","2016년은 4월부터, "+J.asof.slice(0,4)+"년은 "+J.asof.slice(5).replace("-","/")+"까지."));
    // 리밸런싱 빈도 × 🟡
    L.appendChild(el("div","sec","리밸런싱 빈도 · 🟡 빼기 효과 ("+nm(s)+", "+st.n+"종목)"));
    L.appendChild(table(["빈도","🟡 포함","🟡 빼기","비용/년"],(st.b==="S"?["Q","M","W"]:["Q","M"]).map(function(f){var a0=row(s,f,st.n,0),a1=row(s,f,st.n,1);
      return {on:f===st.f,cells:[FQ[f],a0?pc(a0.cagr)+" · "+pc(a0.mdd):"–",a1?pc(a1.cagr)+" · "+pc(a1.mdd):"–",a1?pc(a1.cost):"–"],cls:{}}})));
    L.appendChild(el("div","small","칸 = 연수익 · 최대낙폭. 자주 바꿀수록 비용이 커집니다."));
  }

  // 지금 종목
  if(s===99){L.appendChild(el("div","sec","종목"));L.appendChild(el("p","desc","7전략 동일비중은 위 표에서 1~7번 전략을 하나씩 눌러 각 목록을 보세요(겹치는 종목은 합쳐서 삽니다)."));return}
  var Q=J.q,N=J.now;
  L.appendChild(el("div","sec","종목 · "+nm(s)+" 상위 "+st.n+(st.r?" (🟡 뺌)":"")));
  L.appendChild(seg("보기",[["now","다음 교체 후보"],["q",(st.tp?"분기 편입 목록(":"지금 보유(")+Q.date.slice(5).replace("-","/")+" 편입)"]],"v"));
  var S=st.v==="q"?Q:N,LB=S.lists[st.b]||(st.b==="S"&&S.lists["3"]?S.lists:{}),list=((LB[String(s)]||{})[String(st.r)]||[]).slice(0,st.n);
  var info=el("p","desc");
  if(st.v==="q"){info.innerHTML="분기 리밸런싱("+Q.date+" 시가)에 산 종목 · 신호 "+Q.sig+" 종가 · "+Q.basis+" 반영. 오른쪽 % = 편입일 시가 → "+J.asof+" 종가."+(st.tp?" 익절 매도 이력은 이 목록에 반영되지 않습니다.":"")}
  else{
    var nb=J.next_basis,stale=0;
    if(J.next_fin)list.forEach(function(c){var o=S.stocks[c];if(!o||o.fin!==J.next_fin)stale++});
    info.innerHTML="<b>다음 분기 리밸런싱: "+(J.next||"–")+" 시가</b>(공휴일이면 다음 거래일) · "+(nb||"")+" 반영 예정. 지금 목록은 "+N.sig+" 종가와 그때까지 낸 보고서 기준이라 그날까지 바뀝니다. 리밸런싱 날 0시쯤 ntfy로 알림."+(stale&&nb?" <b style='color:#f4c04e'>"+nb+"가 아직 반영 안 된 종목 "+stale+"개</b> — 매일 밤 수집하며 채워집니다.":"");
    if(st.f!=="Q")info.innerHTML+=" ("+FQ[st.f]+" 리밸런싱이면 매 "+(st.f==="M"?"월 첫":"주 첫")+" 거래일 시가에 이 목록으로 교체.)";
  }
  L.appendChild(info);
  if(!list.length){L.appendChild(el("div","empty","목록이 없습니다."));}
  list.forEach(function(c,i){var o=S.stocks[c];if(o)L.appendChild(card(c,i,o,st.v))});

  // 주의
  L.appendChild(el("div","sec","주의"));
  ["과거 성과입니다. 정기 교체 때 거래할 수 없는 종목도 마지막 평가액으로 정리하는 가정이 있어, 특히 초소형주의 수익과 최대낙폭이 달라질 수 있습니다.",
   "강환국 책의 연 37~40%는 2004~2016년 결과입니다. 같은 규칙을 2016년 이후에 돌리면 크게 낮아지며, 책 방식(연 1회 7월)은 이 기간에 분기보다 못했습니다.",
   "중소형·중대형·대형·전체 구간에도 같은 선별 규칙을 적용했습니다.",
   "초소형주는 호가가 얇아 시가 체결 가정과 실제 체결가의 차이가 클 수 있습니다.",
   "7전략 동일비중 = 처음에 7등분하고 서로 재조정하지 않음. 수익률 높은 전략에 더 싣는 방식은 결과를 보고 고르는 것이라 뺐습니다.",
   "🟡 = 최근 1년 안에 유상증자·전환사채·신주인수권부사채·교환사채·감자 결정 공시. 🔴(횡령·불성실공시·감사의견·거래정지)는 공시 수집이 끝나면 추가합니다.",
   "슬리피지 "+J.slip.toFixed(1)+"%(편도)는 가정입니다. 계좌가 크면 소형주 체결 비용이 더 듭니다."].forEach(function(x){L.appendChild(el("p","desc","· "+x))});
  L.appendChild(el("div","small","계산 "+J.made+" · 매일 밤 갱신"));
}
function render(L){
  if(J)return draw(L);
  if(err){L.appendChild(el("div","empty","소형주 전략 데이터를 불러오지 못했습니다."));return}
  L.appendChild(el("div","empty","불러오는 중…"));
  if(loading)return;loading=true;
  fetch("smallcap.json?t="+Date.now()).then(function(r){if(!r.ok)throw new Error(r.status);return r.json()}).then(function(j){J=j;X.render()}).catch(function(){err=true;X.render()});
}
window.SC={render:render};
})();
