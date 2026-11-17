/* 목록 탭 위의 '이 탭대로 샀다면' 백테스트 카드. 데이터 = tabperf.json (매일 밤 collect 작업이 갱신). */
(function(){
"use strict";
var J=null,loading=false,err=false,KEY="hq_tp_open";
var COL=["#f4c04e","#5aa9ff","#3ecf8e","#e07bff","#ff8a5c"],GREY="#8f9ab3";
function colr(L,i){return L.b?GREY:COL[i%COL.length]}
function X(){return window.HQ}
function pc(v,sign){return v==null?"–":(sign&&v>0?"+":"")+v.toFixed(1)+"%"}
function isOpen(){try{return localStorage.getItem(KEY)!=="0"}catch(e){return true}}
function setOpen(v){try{localStorage.setItem(KEY,v?"1":"0")}catch(e){}}
function chart(lines,M){
  var W=600,H=200,pl=44,pr=8,pt=10,pb=24,ns="http://www.w3.org/2000/svg",lo=Infinity,hi=-Infinity;
  lines.forEach(function(L){L.c.forEach(function(v){if(v>0){lo=Math.min(lo,v);hi=Math.max(hi,v)}})});
  lo=Math.min(lo,1);var a=Math.log(lo),b=Math.log(hi),rg=(b-a)||1;
  function xx(i){return pl+i/(M.length-1)*(W-pl-pr)}
  function yy(v){return pt+(1-(Math.log(v)-a)/rg)*(H-pt-pb)}
  var svg=document.createElementNS(ns,"svg");svg.setAttribute("viewBox","0 0 "+W+" "+H);svg.style.width="100%";svg.style.height="auto";svg.style.display="block";
  svg.setAttribute("role","img");svg.setAttribute("aria-label","백테스트 자산 곡선");
  function mk(t,at){var e=document.createElementNS(ns,t);Object.keys(at).forEach(function(k){e.setAttribute(k,at[k])});svg.appendChild(e);return e}
  [0.25,0.5,1,2,4,8,16,32].forEach(function(v){if(v<lo*0.98||v>hi*1.02)return;var y=yy(v);mk("line",{x1:pl,x2:W-pr,y1:y,y2:y,stroke:"#2c3754","stroke-width":1});var t=mk("text",{x:pl-6,y:y+5,"text-anchor":"end","font-size":15,fill:"#8f9ab3"});t.textContent=v+"배"});
  M.forEach(function(m,i){var y=m.slice(0,4);if(m.slice(5)==="01"&&(+y)%2===0){var t=mk("text",{x:xx(i),y:H-5,"text-anchor":"middle","font-size":15,fill:"#8f9ab3"});t.textContent="'"+y.slice(2)}});
  lines.slice().reverse().forEach(function(L,ri){var i=lines.length-1-ri,d="",pen=false;L.c.forEach(function(v,k){if(v>0){d+=(pen?"L":"M")+xx(k).toFixed(1)+","+yy(v).toFixed(1);pen=true}else pen=false});
    var p=mk("path",{d:d,fill:"none",stroke:colr(L,i),"stroke-width":i===0?2.5:L.b?1.5:1.8,"stroke-linejoin":"round"});if(L.b)p.setAttribute("stroke-dasharray","4 4")});
  return svg;
}
function card(k){
  var T=J.tabs[k];if(!T)return null;var el=X().el,cls=X().cls;
  var box=el("details","tpcard");box.open=isOpen();box.style.cssText="margin:0 0 12px;border:1px solid #2c3754;border-radius:12px;padding:10px 12px;background:rgba(255,255,255,.02)";
  box.addEventListener("toggle",function(){setOpen(box.open)});
  var L0=T.lines[0],y=L0.y||{};
  var sm=el("summary");sm.style.cssText="cursor:pointer;font-weight:700;list-style-position:inside";
  sm.innerHTML="이 탭대로 샀다면 · 연 <span class='"+cls(L0.cagr)+"'>"+pc(L0.cagr)+"</span> · 최대낙폭 "+pc(L0.mdd)+" · 26년 <span class='"+cls(y["2026"])+"'>"+pc(y["2026"],true)+"</span>";
  box.appendChild(sm);
  var body=el("div");body.style.marginTop="8px";
  body.appendChild(chart(T.lines,J.months));
  var tb=el("table","scrollt");tb.style.marginTop="6px";var hr=el("tr");["","연수익","최대낙폭","26년","25년","24년"].forEach(function(h){hr.appendChild(el("th",null,h))});tb.appendChild(hr);
  T.lines.forEach(function(Ln,i){var tr=el("tr"),yy=Ln.y||{};var td=el("td");var sw=el("span");sw.style.cssText="display:inline-block;width:12px;height:0;border-top:3px "+(Ln.b?"dotted ":"solid ")+colr(Ln,i)+";margin-right:6px;vertical-align:middle";td.appendChild(sw);td.appendChild(document.createTextNode(Ln.name));tr.appendChild(td);
    [[Ln.cagr,0],[Ln.mdd,0],[yy["2026"],1],[yy["2025"],1],[yy["2024"],1]].forEach(function(v,j){tr.appendChild(el("td",j===1?"":cls(v[0]),pc(v[0],!!v[1])))});tb.appendChild(tr)});
  var w=el("div");w.style.overflowX="auto";w.appendChild(tb);body.appendChild(w);
  body.appendChild(el("div","small",T.cond+" "+J.start.replace("-",".")+"~"+J.asof.slice(0,7).replace("-",".")+" · 상장폐지 포함 · 재무는 공시 다음 날부터 · 다음 날 시가 체결 · 수수료+세금+슬리피지 "+J.slip.toFixed(1)+"% 반영. 과거 성과이며 보장 아님."));
  box.appendChild(body);return box;
}
function mount(L,k){
  if(J){var c=card(k);if(c)L.appendChild(c);return}
  if(err||loading)return;loading=true;
  fetch("tabperf.json?t="+Date.now()).then(function(r){if(!r.ok)throw new Error(r.status);return r.json()}).then(function(j){J=j;X().render()}).catch(function(){err=true});
}
window.TP={mount:mount};
})();
