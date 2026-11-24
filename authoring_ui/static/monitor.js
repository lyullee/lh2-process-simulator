/* Variable catalogue and graph rendering for physically scoped process data. */
const MONITOR_VARIABLES = {
  pressure_Pa: ['압력','MPa(a)',1e6,'#2375b8'], temperature_K: ['온도','K',1,'#dc8331'],
  mass_flow_kg_s: ['통과 질량유량','kg/s',1,'#168c77'],
  inlet_pressure_Pa: ['시작측 압력','MPa(a)',1e6], outlet_pressure_Pa: ['끝측 압력','MPa(a)',1e6],
  inlet_temperature_K: ['시작측 온도','K',1], outlet_temperature_K: ['끝측 온도','K',1],
  downstream_pressure_Pa: ['유동 하류 압력','MPa(a)',1e6], downstream_temperature_K: ['유동 하류 온도','K',1],
  pressure_drop_Pa: ['시작→끝 압력차','kPa',1000], temperature_change_K: ['끝−시작 온도차','K',1],
  vapor_temperature_K: ['기상 온도','K',1], density_kg_m3: ['밀도','kg/m³',1],
  volumetric_flow_m3_s: ['체적유량','m³/s',1], specific_enthalpy_J_kg: ['비엔탈피','kJ/kg',1000],
  specific_internal_energy_J_kg: ['비내부에너지','kJ/kg',1000], specific_entropy_J_kgK: ['비엔트로피','kJ/(kg·K)',1000],
  vapor_quality: ['건도','%',.01], liquid_fill_fraction: ['액체 체적분율','%',.01],
  liquid_height_m: ['액위 높이','m',1], mass_kg: ['수소 총질량','kg',1],
  liquid_mass_kg: ['액상 질량','kg',1], vapor_mass_kg: ['기상 질량','kg',1],
  liquid_volume_m3: ['액상 체적','m³',1], vapor_volume_m3: ['기상 체적','m³',1], interface_area_m2: ['상 계면 면적','m²',1],
  lower_wall_temperature_K: ['하부 벽체온도','K',1], upper_wall_temperature_K: ['상부 벽체온도','K',1], wall_temperature_K: ['벽체온도','K',1],
  inflow_kg_s: ['유입 질량유량','kg/s',1], outflow_kg_s: ['유출 질량유량','kg/s',1],
  net_mass_flow_kg_s: ['순유입 질량유량','kg/s',1], total_connection_flow_kg_s: ['연결 유량 절댓값 합','kg/s',1],
  enthalpy_flow_W: ['엔탈피 수송률','kW',1000], momentum_residual_Pa: ['운동량 폐합 잔차','Pa',1],
  mass_residual_kg: ['질량 보존 잔차','kg',1], energy_residual_J: ['에너지 보존 잔차','J',1],
  segment_count: ['계산 구간 수','개',1], flow_spread_kg_s: ['구간별 유량 차이','kg/s',1],
  speed_rpm: ['입력 회전수','rpm',1], opening_fraction: ['입력 개도','%',.01], load_fraction: ['입력 부하','%',.01],
  initial_input: ['계측 초기 입력','설정 단위',1], initial_output: ['제어 초기 출력','설정 단위',1], setpoint: ['설정값','설정 단위',1]
};

function monitorMeta(field, target='') {
  const base = MONITOR_VARIABLES[field] || [field,'SI',1];
  const unit=displayUnit(field);
  const item=unit.label?[base[0],unit.label,unit.scale,base[3]]:base;
  if(field==='mass_flow_kg_s' && target.startsWith('edge:')) return ['방향 부호 질량유량',...item.slice(1)];
  return item;
}
function finiteMonitorValue(value) { return typeof value==='number' && Number.isFinite(value); }
function availableMonitorFields(rows, initial={}) {
  return Object.keys(MONITOR_VARIABLES).filter(key=>rows.some(row=>finiteMonitorValue(row[key])) || (!rows.length && finiteMonitorValue(initial[key])));
}
function monitorValue(value, digits=6) {
  if(!finiteMonitorValue(value)) return '—';
  if(value!==0 && (Math.abs(value)<.001 || Math.abs(value)>=1e7)) return value.toExponential(4);
  return value.toLocaleString('ko-KR',{maximumFractionDigits:digits});
}
function escapedMonitorText(value) {
  return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function renderProcessChart(chart, rows, field, target, options={}) {
  chart.replaceChildren();
  const rect=chart.getBoundingClientRect();
  const width=Math.max(200,rect.width||640), height=Math.max(170,rect.height||300);
  chart.setAttribute('viewBox',`0 0 ${width} ${height}`);
  const [label,unit,factor,color='#238bbb']=monitorMeta(field,target);
  const valid=rows.filter(row=>finiteMonitorValue(row[field]) && finiteMonitorValue(row.time_s??row.time));
  if(!valid.length) { chart.appendChild(svg('text',{x:width/2,y:height/2,'text-anchor':'middle',fill:'#687e8a','font-size':13},'계산 시계열이 아직 없습니다')); return; }
  const allT=valid.map(row=>row.time_s??row.time), globalStart=Math.min(...allT), globalEnd=Math.max(...allT);
  const from=options.from??globalStart, to=options.to??globalEnd;
  const visible=valid.filter(row=>(row.time_s??row.time)>=from && (row.time_s??row.time)<=to);
  if(!visible.length) {chart.appendChild(svg('text',{x:width/2,y:height/2,'text-anchor':'middle','font-size':13},'선택 시간범위에 표본이 없습니다'));return;}
  const vals=visible.map(row=>toDisplayValue(field,row[field])), low=Math.min(...vals), high=Math.max(...vals);
  const margin=high===low?Math.max(Math.abs(low)*.001,.000001):(high-low)*.12;
  const yMin=low-margin,yMax=high+margin;
  const left=width<400?74:100, right=20, top=38, bottom=48;
  const x=t=>left+(t-from)/(to-from||1)*(width-left-right), y=v=>height-bottom-(v-yMin)/(yMax-yMin)*(height-top-bottom);
  chart.appendChild(svg('text',{x:12,y:20,fill:color,'font-size':13,'font-weight':600},`${label} [${unit}]`));
  for(let i=0;i<=4;i++) {
    const yy=top+i*(height-top-bottom)/4, value=yMax-i*(yMax-yMin)/4;
    chart.appendChild(svg('line',{x1:left,y1:yy,x2:width-right,y2:yy,stroke:'#dbe6ed'}));
    chart.appendChild(svg('text',{x:left-8,y:yy+4,'text-anchor':'end',fill:'#476170','font-size':11},monitorValue(value,7)));
  }
  const ticks=width<400?2:5;
  for(let i=0;i<=ticks;i++) {
    const t=from+i*(to-from)/ticks, xx=x(t);
    chart.appendChild(svg('line',{x1:xx,y1:top,x2:xx,y2:height-bottom,stroke:'#eef3f7'}));
    chart.appendChild(svg('text',{x:xx,y:height-bottom+19,'text-anchor':'middle',fill:'#476170','font-size':11},monitorValue(toDisplayValue('time_s',t),3)));
  }
  chart.appendChild(svg('path',{d:visible.map((row,i)=>`${i?'L':'M'} ${x(row.time_s??row.time)} ${y(toDisplayValue(field,row[field]))}`).join(' '),fill:'none',stroke:color,'stroke-width':2.5}));
  if(visible.length<=60) visible.forEach(row=>chart.appendChild(svg('circle',{cx:x(row.time_s??row.time),cy:y(toDisplayValue(field,row[field])),r:2.5,fill:color})));
  chart.appendChild(svg('text',{x:(left+width-right)/2,y:height-9,'text-anchor':'middle',fill:'#476170','font-size':12},'계산 시간 ['+displayUnit('time_s').label+']'));
  const cross=svg('g',{visibility:'hidden','pointer-events':'none'}), crossLine=svg('line',{y1:top,y2:height-bottom,stroke:'#678492','stroke-dasharray':'4 3'});
  const tooltip=svg('rect',{y:22,width:Math.min(260,width-24),height:42,rx:4,fill:'#173d53'}), text=svg('text',{y:39,fill:'#fff','font-size':11});
  cross.append(crossLine,tooltip,text);chart.appendChild(cross);
  chart.onpointermove=event=>{
    const bounds=chart.getBoundingClientRect(),px=(event.clientX-bounds.left)*width/bounds.width;
    const t=from+Math.max(0,Math.min(1,(px-left)/(width-left-right)))*(to-from);
    const row=visible.reduce((a,b)=>Math.abs((a.time_s??a.time)-t)<Math.abs((b.time_s??b.time)-t)?a:b);
    const xx=x(row.time_s??row.time),tx=Math.max(12,Math.min(xx-50,width-272));
    cross.setAttribute('visibility','visible');crossLine.setAttribute('x1',xx);crossLine.setAttribute('x2',xx);tooltip.setAttribute('x',tx);
    text.setAttribute('x',tx+10);text.replaceChildren(svg('tspan',{},`t = ${monitorValue(toDisplayValue('time_s',row.time_s??row.time),6)} ${displayUnit('time_s').label}`),svg('tspan',{x:tx+10,dy:16},`${label}: ${monitorValue(toDisplayValue(field,row[field]),9)} ${unit}`));
  };
  chart.onpointerleave=()=>cross.setAttribute('visibility','hidden');
}

function monitorInitial(key) {
  if(state.initialRows[key]) return state.initialRows[key];
  if(key.startsWith('node:')) {
    const node=state.design.nodes.find(n=>n.id===key.slice(5)), input=node?.initial_conditions||{};
    const row={time_s:0,source:'initial_input'};
    const mapping={pressure_Pa:['pressure_Pa_abs','inlet_pressure_Pa_abs'],temperature_K:['temperature_K','fluid_temperature_K','inlet_temperature_K'],
      wall_temperature_K:['wall_temperature_K'],lower_wall_temperature_K:['lower_wall_temperature_K'],upper_wall_temperature_K:['upper_wall_temperature_K'],
      liquid_fill_fraction:['liquid_fill_fraction'],speed_rpm:['speed_rpm'],opening_fraction:['opening_fraction'],load_fraction:['load_fraction'],initial_input:['initial_input'],initial_output:['initial_output'],setpoint:['setpoint']};
    Object.entries(mapping).forEach(([field,keys])=>{const found=keys.find(k=>finiteMonitorValue(input[k]));if(found)row[field]=input[found];});
    return row;
  }
  const edge=state.design.edges.find(e=>e.id===key.slice(5));
  if(!edge) return {};
  const start=monitorInitial('node:'+edge.source),end=monitorInitial('node:'+edge.target),row={time_s:0,source:'endpoint_inputs'};
  for(const field of ['pressure_Pa','temperature_K']) {
    if(finiteMonitorValue(start[field])) row[field]=row['inlet_'+field]=start[field];
    if(finiteMonitorValue(end[field])) row['outlet_'+field]=end[field];
  }
  return row;
}
function monitorTargetLabel(key) {
  if(key.startsWith('node:')) return state.design.nodes.find(n=>n.id===key.slice(5))?.label||key.slice(5);
  const edge=state.design.edges.find(e=>e.id===key.slice(5));
  return edge?`${edge.id} · ${edge.source} → ${edge.target}`:key.slice(5);
}
function selectedMonitorData() {
  const key=$('#monitorTarget').value, rows=state.telemetry[key]||[],initial=monitorInitial(key);
  return {key,rows,initial,last:rows.at(-1)||initial,field:$('#monitorVariable').value};
}
function replaceMonitorOptions(select, entries, selected) {
  const signature=JSON.stringify(entries);
  if(select.dataset.optionsSignature!==signature) {
    select.replaceChildren();
    entries.forEach(([value,label,disabled=false])=>{const option=document.createElement('option');option.value=value;option.textContent=label;option.disabled=disabled;select.appendChild(option);});
    select.dataset.optionsSignature=signature;
  }
  const allowed=[...select.options].some(option=>option.value===selected&&!option.disabled);
  select.value=allowed?selected:(entries.find(e=>!e[2])?.[0]||'');
}
function monitorStats(rows, field) {
  const values=rows.filter(r=>finiteMonitorValue(r[field])).map(r=>r[field]);
  return values.length?{first:values[0],last:values.at(-1),min:Math.min(...values),max:Math.max(...values),change:values.at(-1)-values[0],count:values.length}:null;
}
function renderMonitorPanel() {
  if(!state.design) return;
  const target=$('#monitorTarget'), previous=target.value;
  const desired=state.selected?`${state.selected.type}:${state.selected.id}`:previous;
  const standard=[...state.design.nodes.map(n=>'node:'+n.id),...state.design.edges.map(e=>'edge:'+e.id)];
  const keys=[...standard,...Object.keys(state.telemetry).filter(k=>!standard.includes(k)).sort()];
  const entries=keys.map(key=>[key,`${state.telemetry[key]?.length?'●':'○'} ${monitorTargetLabel(key)}${!standard.includes(key)?' [계산 포트/구간]':''}`]);
  replaceMonitorOptions(target,entries,desired);
  if(previous && target.value!==previous) state.chartRange=null;
  const {key,rows,initial,last}=selectedMonitorData();
  const available=availableMonitorFields(rows,initial), fields=[...new Set(['pressure_Pa','temperature_K','mass_flow_kg_s',...available])];
  const options=fields.map(field=>{const [name,unit]=monitorMeta(field,key);return [field,`${name} [${unit}]${available.includes(field)?'':' · 값 없음'}`,!available.includes(field)];});
  replaceMonitorOptions($('#monitorVariable'),options,$('#monitorVariable').value);
  const field=$('#monitorVariable').value||'pressure_Pa';
  const solved=rows.length>0, source=solved?'계산값':initial.source==='endpoint_inputs'?'끝점 입력 참고':'초기 입력';
  const lineCount=state.design.edges.length, solvedLines=state.design.edges.filter(e=>state.telemetry['edge:'+e.id]?.length).length;
  $('#monitorCoverage').textContent=`${source} · ${rows.length} 표본 · 계산된 라인 ${solvedLines}/${lineCount}`;
  $('#monitorCoverage').dataset.source=solved?'solved':'initial';
  $('#monitorHelp').textContent=solved ? `${monitorTargetLabel(key)} · t=${monitorValue(toDisplayValue('time_s',last.time_s))} ${displayUnit('time_s').label} · ${key.startsWith('edge:')?'기본 압력·온도는 유동 상류, 시작·끝은 연결 방향 기준. 음수 유량은 역류.':'탱크 기본 온도는 액상. 통과유량=max(유입,유출); 순유입은 별도 변수.'}` : (key.startsWith('edge:')?'양 끝 설비의 입력 압력·온도를 참고로 보여줍니다. 이번 경로에서 미해석된 라인의 내부 상태·유량은 미계산으로 구분합니다.':'유닛에 입력된 초기조건입니다. 계산을 실행하면 반환된 상태·물성에 맞춰 변수 목록이 확장됩니다.');
  const basics=['pressure_Pa','temperature_K','mass_flow_kg_s'];
  const tiles=$('#basicTrendCharts');
  if(!tiles.children.length) {
    tiles.innerHTML=basics.map(f=>`<section class="basic-trend" data-field="${f}"><div class="basic-trend-heading"><strong></strong><span></span></div><svg aria-label="${monitorMeta(f,key)[0]} 기본 추세"></svg><button type="button" title="이 변수 크게 보기">↗ 크게 보기</button></section>`).join('');
    tiles.querySelectorAll('section').forEach(tile=>tile.querySelector('button').addEventListener('click',()=>openExpandedMonitor(tile.dataset.field)));
  }
  basics.forEach((f,i)=>{const tile=tiles.children[i],[label,unit,factor]=monitorMeta(f,key);tile.querySelector('strong').textContent=label;tile.querySelector('span').textContent=`${monitorValue(toDisplayValue(f,last[f]))} ${unit}`;renderProcessChart(tile.querySelector('svg'),rows,f,key);});
  drawChart(rows,field);$('#monitorSampleCount').textContent=`${rows.length}개 계산 표본`;
  const stats=monitorStats(rows,field),[label,unit,factor]=monitorMeta(field,key);
  $('#resultMetrics').innerHTML=(stats?[[stats.first,'시작값'],[stats.last,'현재값'],[stats.min,'최솟값'],[stats.max,'최댓값'],[stats.change,'변화량']]:[[last[field],source]]).map(([value,title])=>`<div class="metric"><strong>${monitorValue(toDisplayValue(field,value,title==='변화량'),8)}</strong><span>${escapedMonitorText(title+' · '+unit)}</span></div>`).join('');
  $('#variableBrowser').innerHTML=`<table><caption>조회 가능한 변수 · 클릭하면 상세 그래프</caption><thead><tr><th>변수 / 단위</th><th>현재값</th><th>변화량</th></tr></thead><tbody>${fields.map(f=>{const [name,u,scale]=monitorMeta(f,key),st=monitorStats(rows,f);return `<tr data-field="${f}" class="${available.includes(f)?'':'unavailable'}"><td><button ${available.includes(f)?'':'disabled'}>${escapedMonitorText(name)}<small>${escapedMonitorText(u)}</small></button></td><td>${monitorValue(toDisplayValue(f,last[f]),8)}</td><td>${monitorValue(st?toDisplayValue(f,st.change,true):null,8)}</td></tr>`;}).join('')}</tbody></table>`;
  $('#variableBrowser').querySelectorAll('tr[data-field] button:not(:disabled)').forEach(button=>button.addEventListener('click',()=>{state.chartRange=null;$('#monitorVariable').value=button.closest('tr').dataset.field;updateMonitor();}));
  renderMonitorWatchlist();
  if($('#chartDialog').open) renderExpandedMonitor();
}

let monitorWatchlist=[];
try {monitorWatchlist=JSON.parse(localStorage.getItem('lh2-monitor-watchlist')||'[]').filter(item=>typeof item.target==='string'&&MONITOR_VARIABLES[item.field]).slice(0,20);} catch (_) { /* Invalid saved list is ignored. */ }
function renderMonitorWatchlist() {
  const container=$('#monitorWatchlist');
  container.innerHTML=monitorWatchlist.length?monitorWatchlist.map((item,i)=>{
    const row=state.telemetry[item.target]?.at(-1),[name,unit,factor]=monitorMeta(item.field,item.target);
    return `<div class="watch-row"><button data-watch="${i}">${escapedMonitorText(monitorTargetLabel(item.target))}<small>${escapedMonitorText(name+' ['+unit+']')}</small></button><strong>${monitorValue(toDisplayValue(item.field,row?.[item.field]),8)}</strong><button data-remove-watch="${i}" aria-label="감시 변수 제거">×</button></div>`;
  }).join(''):'<p class="parameter-help">대상과 변수를 고른 뒤 ＋ 감시 목록을 눌러 함께 확인하세요.</p>';
  container.querySelectorAll('[data-watch]').forEach(button=>button.addEventListener('click',()=>{const item=monitorWatchlist[Number(button.dataset.watch)];state.selected=null;$('#monitorTarget').value=item.target;updateMonitor();$('#monitorVariable').value=item.field;updateMonitor();}));
  container.querySelectorAll('[data-remove-watch]').forEach(button=>button.addEventListener('click',()=>{monitorWatchlist.splice(Number(button.dataset.removeWatch),1);localStorage.setItem('lh2-monitor-watchlist',JSON.stringify(monitorWatchlist));renderMonitorWatchlist();}));
}

function openExpandedMonitor(field=$('#monitorVariable').value) {
  const {rows,initial}=selectedMonitorData();
  if(!availableMonitorFields(rows,initial).includes(field)) {toast('이 대상에서 해당 변수는 아직 계산되지 않았습니다.');return;}
  state.chartRange=null;
  if(field) $('#monitorVariable').value=field;
  if(!$('#chartDialog').open) $('#chartDialog').showModal();
  renderExpandedMonitor();
}
function renderExpandedMonitor() {
  const {key,rows,last,field,initial}=selectedMonitorData(),available=availableMonitorFields(rows,initial);
  replaceMonitorOptions($('#expandedVariable'),available.map(f=>[f,monitorMeta(f,key).slice(0,2).join(' · ')]),field);
  const times=rows.map(r=>r.time_s),from=state.chartRange?.from??times[0]??0,to=state.chartRange?.to??times.at(-1)??0;
  $('#expandedChartTitle').textContent=monitorTargetLabel(key);
  $('#chartFromInput').closest('label').firstChild.textContent='시작 시간 ('+displayUnit('time_s').label+')';
  $('#chartToInput').closest('label').firstChild.textContent='종료 시간 ('+displayUnit('time_s').label+')';
  if(document.activeElement!==$('#chartFromInput')) $('#chartFromInput').value=displayNumberInput(toDisplayValue('time_s',from));
  if(document.activeElement!==$('#chartToInput')) $('#chartToInput').value=displayNumberInput(toDisplayValue('time_s',to));
  const selectedRows=rows.filter(r=>r.time_s>=from&&r.time_s<=to);
  renderProcessChart($('#expandedChart'),rows,field,key,{from,to});
  const stats=monitorStats(selectedRows,field),[name,unit,factor]=monitorMeta(field,key);
  $('#expandedChartHelp').textContent=`${name} [${unit}] · ${rows.length}개 원계산 표본 · 포인터를 그래프 위에 올리면 해당 시각 값을 볼 수 있습니다. 표는 선택 시간범위의 최근 500개 표본입니다.`;
  $('#expandedChartStats').innerHTML=(stats?[[stats.first,'시작'],[stats.last,'현재'],[stats.min,'최소'],[stats.max,'최대'],[stats.change,'변화량']]:[]).map(([v,l])=>`<div><span>${l}</span><strong>${monitorValue(toDisplayValue(field,v,l==='변화량'),9)} ${escapedMonitorText(unit)}</strong></div>`).join('');
  const basic=['pressure_Pa','temperature_K','mass_flow_kg_s'],fields=[...new Set([...basic,field])];
  $('#expandedDataTable').innerHTML=`<table><thead><tr><th>시간 [${displayUnit('time_s').label}]</th>${fields.map(f=>`<th>${escapedMonitorText(monitorMeta(f,key).slice(0,2).join(' ['))}]</th>`).join('')}</tr></thead><tbody>${selectedRows.slice(-500).map(row=>`<tr><td>${monitorValue(toDisplayValue('time_s',row.time_s),9)}</td>${fields.map(f=>`<td>${monitorValue(toDisplayValue(f,row[f]),9)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
function exportMonitorCsv() {
  const {key,rows}=selectedMonitorData();
  if(!rows.length) {toast('내보낼 계산 시계열이 없습니다. 초기 입력은 계산 결과로 내보내지 않습니다.');return;}
  const fields=availableMonitorFields(rows),quote=v=>'"'+String(v).replaceAll('"','""')+'"';
  const header=['계산시간 ['+displayUnit('time_s').label+']',...fields.map(f=>monitorMeta(f,key).slice(0,2).join(' [' )+']')];
  const lines=[header.map(quote).join(','),...rows.map(row=>[toDisplayValue('time_s',row.time_s),...fields.map(f=>finiteMonitorValue(row[f])?toDisplayValue(f,row[f]):'')].map(quote).join(','))];
  const url=URL.createObjectURL(new Blob(['\ufeff'+lines.join('\r\n')],{type:'text/csv;charset=utf-8'})),anchor=document.createElement('a');
  anchor.href=url;anchor.download='LH2_'+key.replace(/[^\w가-힣-]/g,'_')+'_계산시계열.csv';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function bindDetailedMonitor() {
  $('#expandChartBtn').addEventListener('click',()=>openExpandedMonitor());
  $('#closeChartBtn').addEventListener('click',()=>$('#chartDialog').close());
  $('#expandedVariable').addEventListener('change',()=>{$('#monitorVariable').value=$('#expandedVariable').value;state.chartRange=null;updateMonitor();});
  $('#applyChartRangeBtn').addEventListener('click',()=>{const from=toSIValue('time_s',Number($('#chartFromInput').value)),to=toSIValue('time_s',Number($('#chartToInput').value));if(!Number.isFinite(from)||!Number.isFinite(to)||from<0||to<=from){toast('종료 시간은 시작 시간보다 커야 합니다');return;}state.chartRange={from,to};renderExpandedMonitor();});
  $('#fitChartRangeBtn').addEventListener('click',()=>{state.chartRange=null;renderExpandedMonitor();});
  $('#exportMonitorBtn').addEventListener('click',exportMonitorCsv);$('#expandedExportBtn').addEventListener('click',exportMonitorCsv);
  $('#wideMonitorBtn').addEventListener('click',()=>{const wide=$('.workspace').classList.toggle('monitor-wide');$('#wideMonitorBtn').textContent=wide?'패널 기본 폭':'패널 확대';requestAnimationFrame(updateMonitor);});
  $('#pinMonitorBtn').addEventListener('click',()=>{const {key,field}=selectedMonitorData();if(!field)return;if(!monitorWatchlist.some(i=>i.target===key&&i.field===field)){if(monitorWatchlist.length>=20){toast('감시 목록은 최대 20개입니다');return;}monitorWatchlist.push({target:key,field});localStorage.setItem('lh2-monitor-watchlist',JSON.stringify(monitorWatchlist));}renderMonitorWatchlist();});
  const observer=new ResizeObserver(()=>requestAnimationFrame(()=>{if(state.design)updateMonitor();}));observer.observe($('.inspector-panel'));
  observer.observe($('#expandedChart'));
}
