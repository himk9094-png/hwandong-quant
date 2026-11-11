/* 환동퀀트 코인 엔진: 지표·온체인·전략 시뮬레이션.
   브라우저(window.CE)와 GitHub Actions의 Node(require) 양쪽에서 같은 코드로 돌린다 → 화면과 알림이 항상 같은 계산. */
(function(root,factory){if(typeof module==="object"&&module.exports)module.exports=factory();else root.CE=factory()})(typeof self!=="undefined"?self:this,function(){
"use strict";
var COST=0.0015;              // 매수·매도 각각 0.15% (수수료+슬리피지 근사)
var LEV_COST=0.05/365;        // 2배 ETF 근사: 하루 수익률×2 − 연 5%(운용보수+차입비용 근사)
var COINS=["BTC","ETH","SOL","XRP"];
var DAY=864e5;

function isn(v){return v!==null&&v!==undefined&&isFinite(v)}
function nul(n){var a=new Array(n);for(var i=0;i<n;i++)a[i]=null;return a}
function firstIdx(a){for(var i=0;i<a.length;i++)if(isn(a[i]))return i;return -1}
function dateAt(start,i){var p=start.split("-");return new Date(Date.UTC(+p[0],+p[1]-1,+p[2])+i*DAY).toISOString().slice(0,10)}
function idxOf(start,d){var a=start.split("-"),b=d.split("-");return Math.round((Date.UTC(+b[0],+b[1]-1,+b[2])-Date.UTC(+a[0],+a[1]-1,+a[2]))/DAY)}

// ---------------------------------------------------------------- 지표
function sma(a,n){var o=nul(a.length),s=0,c=0;
  for(var i=0;i<a.length;i++){if(isn(a[i])){s+=a[i];c++}if(i>=n&&isn(a[i-n])){s-=a[i-n];c--}if(c===n)o[i]=s/n}
  return o}
function sum(a,n){var o=nul(a.length),s=0,c=0;
  for(var i=0;i<a.length;i++){if(isn(a[i])){s+=a[i];c++}if(i>=n&&isn(a[i-n])){s-=a[i-n];c--}if(c>=n*0.8&&i>=n-1)o[i]=s*n/c}
  return o}
function rsi(a,n){n=n||14;var o=nul(a.length),ag=0,al=0,k=0,prev=null,init=false;
  for(var i=0;i<a.length;i++){var v=a[i];if(!isn(v)){prev=null;k=0;init=false;ag=al=0;continue}
    if(prev===null){prev=v;continue}
    var d=v-prev,g=d>0?d:0,l=d<0?-d:0;prev=v;
    if(!init){ag+=g;al+=l;k++;if(k===n){ag/=n;al/=n;init=true;o[i]=al===0?100:100-100/(1+ag/al)}continue}
    ag=(ag*(n-1)+g)/n;al=(al*(n-1)+l)/n;o[i]=al===0?100:100-100/(1+ag/al)}
  return o}
// UT Bot Alerts(트레이딩뷰)와 같은 식: ATR 추적 손절선, 종가가 선을 위로 뚫으면 매수(1), 아래로 뚫으면 매도(-1)
function utbot(c,h,l,key,per){key=key||1;per=per||10;
  var n=c.length,stop=nul(n),pos=nul(n),atr=null,trs=0,k=0,ts=null,p=0,pc=null;
  for(var i=0;i<n;i++){var v=c[i];if(!isn(v)){pc=null;continue}
    var hi=h?h[i]:null,lo=l?l[i]:null,tr;
    if(isn(hi)&&isn(lo))tr=pc===null?hi-lo:Math.max(hi-lo,Math.abs(hi-pc),Math.abs(lo-pc));
    else tr=pc===null?null:Math.abs(v-pc);                  // 고가·저가가 없는 옛 데이터는 종가 변동폭으로 대체
    if(tr!==null){if(atr===null){trs+=tr;k++;if(k===per)atr=trs/per}else atr=(atr*(per-1)+tr)/per}
    if(atr===null){pc=v;continue}
    var loss=key*atr,pt=ts===null?0:ts;
    if(v>pt&&pc>pt)ts=Math.max(pt,v-loss);
    else if(v<pt&&pc<pt)ts=Math.min(pt,v+loss);
    else ts=v>pt?v-loss:v+loss;
    if(pc<pt&&v>ts)p=1;else if(pc>pt&&v<ts)p=-1;
    stop[i]=ts;pos[i]=p;pc=v}
  return {stop:stop,pos:pos}}
// 최근 win일 안에서 오늘 값의 위치(0=최저, 100=최고)
function rollPct(a,win,minN){var n=a.length,o=nul(n);minN=minN||Math.floor(win/2);
  for(var i=0;i<n;i++){var v=a[i];if(!isn(v))continue;var lo=0,cnt=0;
    for(var j=Math.max(0,i-win+1);j<=i;j++){var x=a[j];if(isn(x)){cnt++;if(x<v)lo++}}
    if(cnt>=minN)o[i]=cnt>1?lo/(cnt-1)*100:50}
  return o}

// ---------------------------------------------------------------- 데이터 준비
function unpack(a,n){if(!a)return null;if(Array.isArray(a))return a;var o=nul(n);for(var k=0;k<a.v.length;k++)o[a.o+k]=a.v[k];return o}  // {o:시작칸, v:값들} → 전체 길이 배열
function prep(H0){
  var n=H0.n,H={n:n,start:H0.start,last:H0.last,cfg:H0.cfg,coins:{},oc:{}};
  Object.keys(H0.coins).forEach(function(s){var d=H0.coins[s];H.coins[s]={c:unpack(d.c,n),h:unpack(d.h,n),l:unpack(d.l,n),u4:unpack(d.u4,n),bin:d.bin}});
  Object.keys(H0.oc).forEach(function(a){var d=H0.oc[a],o={};Object.keys(d).forEach(function(k){o[k]=unpack(d[k],n)});H.oc[a]=o});
  var cfg=H.cfg||{},X={n:n,start:H.start,last:H.last,coins:{},oc:{},cfg:cfg};
  COINS.forEach(function(s){var d=H.coins[s];if(!d)return;var c=d.c;var u=utbot(c,d.h,d.l,cfg.utKey,cfg.utAtr);
    X.coins[s]={c:c,s21:sma(c,21),s200:sma(c,200),s365:sma(c,365),rsi:rsi(c,14),ut:u.pos,uts:u.stop,ut4:d.u4||nul(n),first:firstIdx(c),bin:d.bin}});
  var b=H.oc.btc,o=X.oc;
  o.mvrv={btc:b.mvrv,eth:H.oc.eth&&H.oc.eth.mvrv,xrp:H.oc.xrp&&H.oc.xrp.mvrv};
  o.pctW={};o.pct={btc:pctOf(X,"btc",730),eth:pctOf(X,"eth",730),xrp:pctOf(X,"xrp",730)};
  // 해시 리본: 해시레이트 30일 평균 < 60일 평균 = 채굴자 항복, 다시 위로 = 회복
  var h30=sma(b.hash,30),h60=sma(b.hash,60),cap=nul(n),zone=nul(n),lastRec=-1e9;
  for(var i=0;i<n;i++){if(!isn(h30[i])||!isn(h60[i]))continue;cap[i]=h30[i]<h60[i];if(i&&cap[i-1]===true&&!cap[i])lastRec=i;zone[i]=cap[i]||i-lastRec<=30}
  o.hash=b.hash;o.cap=cap;o.hzone=zone;
  // 퓨엘 멀티플: 채굴 발행액 ÷ 365일 평균 (반감기 직후엔 낮게 나오는 왜곡 있음)
  var is365=sma(b.iss,365);o.puell=b.iss.map(function(v,i){return isn(v)&&isn(is365[i])?v/is365[i]:null});
  // 거래소 순유입 30일 합 ÷ 거래소 보유량 (−면 거래소에서 빠져나감 = 매집)
  function nf(x){if(!x||!x.net)return null;var s=sum(x.net,30);return s.map(function(v,i){return isn(v)&&isn(x.exs[i])&&x.exs[i]>0?v/x.exs[i]*100:null})}
  o.nf={btc:nf(b),eth:nf(H.oc.eth)};
  return X;
}
function pctOf(X,src,win){var c=X.oc.pctW[win]||(X.oc.pctW[win]={});if(c[src]===undefined){var m=X.oc.mvrv[src];c[src]=m?rollPct(m,win,Math.floor(win*0.75)):null}return c[src]}   // MVRV 백분위(기간별로 한 번만 계산)
function lastVal(a,upto){if(!a)return {v:null,i:-1};for(var i=upto==null?a.length-1:upto;i>=0;i--)if(isn(a[i]))return {v:a[i],i:i};return {v:null,i:-1}}
var REGIME_W=1460;   // 국면 판단: MVRV 최근 4년(반감기 한 주기) 백분위
function regimeOf(p){return !isn(p)?"–":p<=20?"바닥권":p>=80?"과열권":"중립"}

// ---------------------------------------------------------------- 전략
var MA={above200:["s200",1],below200:["s200",-1],above365:["s365",1],below365:["s365",-1]};
var PRESETS=[
 {id:"hold",name:"단순 보유",d:"처음에 사서 계속 보유 (비교 기준)",hold:true},
 {id:"trend",name:"200일선 추세",d:"종가가 200일선 위면 보유, 아래로 내려가면 전량 매도",buy:{ma:"above200"},sell:{ma:"below200"},nb:1,ns:1,gap:1},
 {id:"mr",name:"역추세 기본",d:"365일선 아래 + RSI 30 미만이면 4번 나눠 매수(7일 간격) · 21일선이 365일선보다 50% 이상 높아지면 2번 나눠 매도",buy:{ma:"below365",rsi:30},sell:{ext:50},nb:4,gap:7,ns:2},
 {id:"mr_oc",name:"역추세 + 온체인",d:"역추세 기본 + BTC MVRV가 최근 4년(반감기 한 주기) 하위 30% 안일 때만 매수 · MVRV 상위 10%면 과열로 보고 매도",buy:{ma:"below365",rsi:30,mvrv:30},sell:{ext:50,mvrv:90},ocw:1460,nb:4,gap:7,ns:2},
 {id:"oc",name:"온체인 단독",d:"BTC MVRV 최근 4년 하위 20%에서 4번 나눠 매수(14일 간격) · 상위 10%에서 2번 나눠 매도",buy:{mvrv:20},sell:{mvrv:90},ocw:1460,nb:4,gap:14,ns:2},
 {id:"ut",name:"UT Bot 일봉",d:"UT Bot 매수 신호에 전량 매수, 매도 신호에 전량 매도",buy:{ut:1},sell:{ut:1},nb:1,ns:1,gap:1}
];
function norm(P){var q={};for(var k in P)q[k]=P[k];
  q.buy=q.buy||{};q.sell=q.sell||{};q.nb=Math.max(1,+q.nb||1);q.ns=Math.max(1,+q.ns||1);q.gap=Math.max(1,+q.gap||1);
  q.coins=q.coins||{BTC:1};q.oc=q.oc||"btc";q.ocw=+q.ocw||730;q.start=+q.start||2018;return q}
function weights(X,coins){var w={},t=0;COINS.forEach(function(s){var v=+coins[s]||0;if(v>0&&X.coins[s]){w[s]=v;t+=v}});for(var s in w)w[s]/=t;return w}
function ocPct(X,P,sym,i){var own=sym.toLowerCase(),src=P.oc==="own"&&X.oc.mvrv[own]?own:"btc",a=pctOf(X,src,+P.ocw||730);return i>0&&a?a[i-1]:null}   // 온체인은 하루 늦게 공개 → 전날 값 사용
function buyOk(X,P,sym,i,why){var C=X.coins[sym],b=P.buy,px=C.c[i],any=false;
  if(b.ma){var m=MA[b.ma],v=C[m[0]][i];if(!isn(v))return false;if(m[1]>0?px<=v:px>=v)return false;any=true;if(why)why.push((m[0]==="s200"?"200":"365")+"일선 "+fmtP(px/v-1))}
  if(b.rsi){var r=C.rsi[i];if(!isn(r)||r>=b.rsi)return false;any=true;if(why)why.push("RSI "+Math.round(r))}
  if(b.mvrv){var p=ocPct(X,P,sym,i);if(!isn(p)||p>b.mvrv)return false;any=true;if(why)why.push("MVRV "+(P.ocw/365)+"년 하위 "+Math.round(p)+"%")}
  if(b.ut){if(C.ut[i]!==1)return false;any=true;if(why)why.push("UT 매수")}
  if(b.ut4){if(C.ut4[i]!==1)return false;any=true;if(why)why.push("UT 4h 매수")}     // 4시간봉 UT: 그날 일봉 마감(09시) 시점 상태
  if(b.hr){var z=X.oc.hzone[i-1];if(z!==true)return false;any=true;if(why)why.push("해시리본")}
  return any}
function sellOk(X,P,sym,i,pnl,why){var C=X.coins[sym],s=P.sell,px=C.c[i],hit=false;
  if(s.ext){var a=C.s21[i],b=C.s365[i];if(isn(a)&&isn(b)&&a>b*(1+s.ext/100)){hit=true;if(why)why.push("21일선이 365일선 "+fmtP(a/b-1))}}
  if(s.rsi){var r=C.rsi[i];if(isn(r)&&r>s.rsi){hit=true;if(why)why.push("RSI "+Math.round(r))}}
  if(s.mvrv){var p=ocPct(X,P,sym,i);if(isn(p)&&p>=s.mvrv){hit=true;if(why)why.push("MVRV "+(P.ocw/365)+"년 상위 "+Math.round(100-p)+"%")}}
  if(s.ut&&C.ut[i]===-1){hit=true;if(why)why.push("UT 매도")}
  if(s.ut4&&C.ut4[i]===-1){hit=true;if(why)why.push("UT 4h 매도")}
  if(s.ma){var m=MA[s.ma],v=C[m[0]][i];if(isn(v)&&(m[1]>0?px>v:px<v)){hit=true;if(why)why.push((m[0]==="s200"?"200":"365")+"일선 이탈")}}
  if(s.stop&&pnl!=null&&pnl<=-s.stop/100){hit=true;if(why)why.push("손절 "+fmtP(pnl))}
  return hit}
function fmtP(x){return (x>=0?"+":"−")+Math.abs(x*100).toFixed(0)+"%"}
function startIdx(X,year){return Math.max(0,Math.min(X.n-1,idxOf(X.start,year+"-01-01")))}

function simulate(X,P0){
  var P=norm(P0),n=X.n,i0=startIdx(X,P.start),w=weights(X,P.coins),syms=Object.keys(w);
  if(!syms.length)return null;
  var S={};syms.forEach(function(s){S[s]={cash:1,inv:0,basis:0,nb:0,base:0,lastB:-1e9,ns:0,lastS:-1e9,last:null}});
  var eq=[],expo=[],trades=[];
  for(var i=i0;i<n;i++){
    var tv=0,ti=0;
    syms.forEach(function(s){
      var st=S[s],C=X.coins[s],px=C.c[i];
      if(isn(px)){
        var pnl=st.basis>0?st.inv/st.basis-1:null;
        if(P.hold){if(st.inv===0&&st.cash>0){st.inv=st.cash*(1-COST);st.basis=st.cash;st.cash=0;trades.push({i:i,s:s,a:"buy",k:"1/1",px:px})}}
        else{
          var why=[],sc=st.inv>0&&sellOk(X,P,s,i,pnl,why);
          if(sc){                                            // 매도 조건이 켜져 있는 날엔 새로 사지 않는다
            if(st.ns===0||i-st.lastS>=P.gap){
              var stopHit=P.sell.stop&&pnl!=null&&pnl<=-P.sell.stop/100,fr=stopHit?1:1/(P.ns-st.ns),amt=st.inv*fr;
              st.cash+=amt*(1-COST);st.basis*=(1-fr);st.inv-=amt;st.ns++;st.lastS=i;st.nb=0;
              trades.push({i:i,s:s,a:"sell",k:(stopHit||st.inv<1e-12&&P.ns===1?"전량":st.ns+"/"+P.ns),px:px,why:why.join(" · ")});
              if(st.inv<1e-12){st.inv=0;st.basis=0;st.ns=0}
              st.last={i:i,a:"sell"};
            }
          }else{why=[];
            if(st.cash>1e-9&&st.nb<P.nb&&(st.nb===0||i-st.lastB>=P.gap)&&buyOk(X,P,s,i,why)){
              if(st.nb===0)st.base=st.cash;
              var a2=st.nb===P.nb-1?st.cash:Math.min(st.cash,st.base/P.nb);
              st.inv+=a2*(1-COST);st.basis+=a2;st.cash-=a2;st.nb++;st.lastB=i;st.ns=0;
              trades.push({i:i,s:s,a:"buy",k:st.nb+"/"+P.nb,px:px,why:why.join(" · ")});
              st.last={i:i,a:"buy"};
            }
          }
        }
      }
      tv+=w[s]*(st.cash+st.inv);ti+=w[s]*st.inv;
      if(isn(px)&&i+1<n&&isn(C.c[i+1])&&st.inv>0){var r=C.c[i+1]/px-1;st.inv*=P.lev?Math.max(0,1+2*r-LEV_COST):1+r}
    });
    eq.push(tv);expo.push(tv>0?ti/tv:0);
  }
  return {i0:i0,eq:eq,expo:expo,trades:trades,state:S,w:w,P:P};
}
// 지금(마지막 날) 각 코인의 상태와 조건 충족 여부
function status(X,res){var P=res.P,i=X.n-1,out={};
  Object.keys(res.w).forEach(function(s){var st=res.state[s],C=X.coins[s],tot=st.cash+st.inv,bw=[],sw=[];
    var pnl=st.basis>0?st.inv/st.basis-1:null;
    out[s]={hold:tot>0?st.inv/tot:0,nb:st.nb,pnl:pnl,last:st.last?{d:dateAt(X.start,st.last.i),a:st.last.a}:null,
      buy:!P.hold&&isn(C.c[i])&&buyOk(X,P,s,i,bw),sell:!P.hold&&st.inv>0&&isn(C.c[i])&&sellOk(X,P,s,i,pnl,sw),bw:bw.join(" · "),sw:sw.join(" · ")}});
  return out}
function mix(X,items,start){       // 전략 묶음: 각 전략에 처음 비중대로 나눠 넣고 그대로 둔다(재조정 없음)
  var tot=0,eq=null,expo=null,i0=null,trades=[];
  items.forEach(function(it){var w=+it.w||0;if(w<=0||!it.P)return;var P=Object.assign({},it.P,{start:start}),r=simulate(X,P);if(!r)return;
    tot+=w;i0=r.i0;
    if(!eq){eq=r.eq.map(function(v){return v*w});expo=r.expo.map(function(v,k){return v*w*r.eq[k]})}
    else r.eq.forEach(function(v,k){eq[k]+=v*w;expo[k]+=r.expo[k]*w*v});
    r.trades.forEach(function(t){t.src=it.P.name;trades.push(t)})});
  if(!tot)return null;
  return {i0:i0,eq:eq.map(function(v){return v/tot}),expo:expo.map(function(v,k){return eq[k]?v/eq[k]:0}),trades:trades.sort(function(a,b){return a.i-b.i}),w:{},P:{name:"묶음",start:start}};
}
function stats(eq,expo){
  var n=eq.length-1;if(n<30)return null;
  var pk=eq[0],mdd=0,s=0,s2=0;
  for(var i=1;i<=n;i++){pk=Math.max(pk,eq[i]);mdd=Math.min(mdd,eq[i]/pk-1);var r=eq[i]/eq[i-1]-1;s+=r;s2+=r*r}
  var m=s/n,sd=Math.sqrt(Math.max(0,s2/n-m*m)),yrs=n/365;
  var ex=0;if(expo){expo.forEach(function(v){ex+=v});ex/=expo.length}
  return {tot:(eq[n]/eq[0]-1)*100,cagr:(Math.pow(eq[n]/eq[0],1/yrs)-1)*100,mdd:mdd*100,vol:sd*Math.sqrt(365)*100,sh:sd?m/sd*Math.sqrt(365):0,expo:ex*100,yrs:yrs}}
function yearly(X,res){var out={},o=null,prev=res.eq[0];
  res.eq.forEach(function(v,k){if(!k)return;var y=dateAt(X.start,res.i0+k).slice(0,4);o=out[y]||(out[y]={a:1});o.a*=v/prev;prev=v});
  return out}

return {COINS:COINS,PRESETS:PRESETS,pctOf:pctOf,COST:COST,LEV_COST:LEV_COST,isn:isn,sma:sma,rsi:rsi,utbot:utbot,rollPct:rollPct,prep:prep,lastVal:lastVal,regimeOf:regimeOf,REGIME_W:REGIME_W,
  simulate:simulate,status:status,mix:mix,stats:stats,yearly:yearly,dateAt:dateAt,idxOf:idxOf,startIdx:startIdx,norm:norm,fmtP:fmtP};
});
