// SSG Operations Center — Funções utilitárias
// Gerado por refactor Fase 4

var CH = {};
function mkChart(id,type,labels,datasets,opts){
  if(CH[id]){CH[id].destroy();CH[id]=null;}
  var ctx=document.getElementById(id); if(!ctx)return;
  CH[id]=new Chart(ctx,{type:type,data:{labels:labels,datasets:datasets},options:Object.assign({responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}}},opts||{})});
}
function fmtDate(s){var p=s.split('-');return p[2]+'/'+p[1];}
var G='rgba(13,27,46,0.07)',TK='#9CA3AF';
var PAL=['#4f8ef7','#00d4a8','#8b5cf6','#f59e0b','#f43f5e','#10b981','#f97316','#38bdf8'];
function axisOpts(yL){return{x:{ticks:{color:TK,font:{size:9,family:"'JetBrains Mono'"},maxRotation:45,autoSkip:true},grid:{display:false}},y:{ticks:{color:TK,font:{size:9,family:"'JetBrains Mono'"}},grid:{color:G},title:yL?{display:true,text:yL,color:TK,font:{size:9}}:undefined}};}
function ePill(e){var m={'Fechado':'pg','Resolvido':'pg','Fechado s/ Êxito':'px','Aberto':'pb','Em Atendimento':'pb','Aguardando Cliente':'py','Aguardando Interno':'py','Retorno Cliente':'py','Aguardando Externo':'pp'};var ic={'Fechado':'<polyline points="20 6 9 17 4 12"/>','Resolvido':'<polyline points="20 6 9 17 4 12"/>','Fechado s/ Êxito':'<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>','Aberto':'<circle cx="12" cy="12" r="4"/>','Em Atendimento':'<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>','Aguardando Cliente':'<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>','Aguardando Interno':'<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>','Retorno Cliente':'<polyline points="9 10 4 15 9 20"/><path d="M20 4v7a4 4 0 01-4 4H4"/>','Aguardando Externo':'<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>'};var svg=ic[e]?'<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0">'+ic[e]+'</svg>':'';return'<span class="pill '+(m[e]||'px')+'">'+svg+e+'</span>';}
function pPill(p){var m={'Muito Alta':'pr','Alta':'py','Normal':'pb','Baixa':'pt','Muito Baixa':'px'};var ic={'Muito Alta':'<path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>','Alta':'<polyline points="18 15 12 9 6 15"/>','Normal':'<line x1="5" y1="12" x2="19" y2="12"/>','Baixa':'<polyline points="6 9 12 15 18 9"/>','Muito Baixa':'<polyline points="7 13 12 18 17 13"/><polyline points="7 6 12 11 17 6"/>'};var svg=ic[p]?'<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0">'+ic[p]+'</svg>':'';return'<span class="pill '+(m[p]||'px')+'">'+svg+p+'</span>';}
function pct(a,b){return b>0?Math.round(a/b*100):0;}
function avg(arr){return arr.length?arr.reduce(function(a,b){return a+b;},0)/arr.length:0;}
