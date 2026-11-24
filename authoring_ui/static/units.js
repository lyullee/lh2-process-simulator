/* All stored/model values remain SI. Display conversion is affine and reversible. */
const UNIT_OPTIONS={
 pressure:{Pa:[1,0],kPa:[1000,0],MPa:[1e6,0],bar:[1e5,0],psi:[6894.757293168,0],'mmH₂O':[9.80665,0]},
 temperature:{K:[1,0],'°C':[1,273.15],'°F':[5/9,255.3722222222222]},
 massFlow:{'kg/s':[1,0],'kg/h':[1/3600,0],'g/s':[.001,0],'lb/h':[.45359237/3600,0]},
 volumeFlow:{'m³/s':[1,0],'m³/h':[1/3600,0],'L/min':[.001/60,0],'ft³/min':[.028316846592/60,0]},
 length:{m:[1,0],mm:[.001,0],cm:[.01,0],ft:[.3048,0],in:[.0254,0]},
 volume:{'m³':[1,0],L:[.001,0],'ft³':[.028316846592,0]},
 mass:{kg:[1,0],g:[.001,0],lb:[.45359237,0]},
 energy:{J:[1,0],kJ:[1000,0],'Btu(IT)':[1055.05585262,0]},
 power:{W:[1,0],kW:[1000,0],'Btu/h':[1055.05585262/3600,0]},
 time:{s:[1,0],min:[60,0],h:[3600,0]}, fraction:{'0–1':[1,0],'%':[.01,0]}
};
const UNIT_FAMILY_NAMES={pressure:'압력',temperature:'온도',massFlow:'질량유량',volumeFlow:'체적유량',length:'길이',volume:'체적',mass:'질량',energy:'에너지',power:'열량·동력',time:'시간',fraction:'분율·개도·효율'};
const UNIT_PRESETS={
 SI:{pressure:'Pa',temperature:'K',massFlow:'kg/s',volumeFlow:'m³/s',length:'m',volume:'m³',mass:'kg',energy:'J',power:'W',time:'s',fraction:'0–1',pressureBasis:'absolute',referencePressurePa:101325},
 engineering:{pressure:'bar',temperature:'°C',massFlow:'kg/h',volumeFlow:'m³/h',length:'m',volume:'m³',mass:'kg',energy:'kJ',power:'kW',time:'s',fraction:'%',pressureBasis:'absolute',referencePressurePa:101325},
 US:{pressure:'psi',temperature:'°F',massFlow:'lb/h',volumeFlow:'ft³/min',length:'ft',volume:'ft³',mass:'lb',energy:'Btu(IT)',power:'Btu/h',time:'s',fraction:'%',pressureBasis:'absolute',referencePressurePa:101325}
};
function validUnitPreferences(input={}) {
 const result={...UNIT_PRESETS.engineering};
 for(const family of Object.keys(UNIT_OPTIONS)) if(UNIT_OPTIONS[family][input[family]]) result[family]=input[family];
 result.pressureBasis=input.pressureBasis==='gauge'?'gauge':'absolute';
 if(typeof input.referencePressurePa==='number'&&Number.isFinite(input.referencePressurePa)&&input.referencePressurePa>0) result.referencePressurePa=input.referencePressurePa;
 return result;
}
let unitPreferences;
try {unitPreferences=validUnitPreferences(JSON.parse(localStorage.getItem('lh2-display-units')||'{}'));} catch (_) {unitPreferences=validUnitPreferences();}
function baseDisplayUnit(family) {const label=unitPreferences[family], [scale,offset]=UNIT_OPTIONS[family][label];return {label,scale,offset};}
const FRACTION_FIELDS=new Set(['liquid_fill_fraction','vapor_quality','quality','opening_fraction','load_fraction','split_fractions','liquid_boundaries','vapor_boundaries','isentropic_efficiency','discharge_coefficient','accommodation_coefficient','energy_accommodation_coefficient','accuracy_fraction_of_span','default_relative_humidity','frost_deposition_efficiency','effective_emissivity','emissivity','liquid_wall_volume_fraction','vapor_wall_volume_fraction']);
function fieldDimension(key) {
 if(FRACTION_FIELDS.has(key)) return 'fraction';
 if(key==='conversion_rate_constant_s'||key.endsWith('_per_s')) return 'inverseTime';
 if(key.endsWith('_W_m2K_Pa')) return 'gasHeatCoefficient';
 if(key.endsWith('_Pa_abs')) return 'pressure';
 if(key.endsWith('_Pa')) {
  if(/vacuum/.test(key)) return 'absolutePressure';
  return /drop|rise|difference|delta|adjustment|residual|cracking/.test(key)?'pressureDifference':'pressure';
 }
 if(key.endsWith('_K')&&!key.endsWith('_W_K')&&!key.endsWith('_J_K')) return /change|difference|delta/.test(key)?'temperatureDifference':'temperature';
 const suffixes={W_m2K_Pa:'gasHeatCoefficient',m2K_W:'areaThermalResistance',kg_m3:'density',J_kgK:'specificHeat',J_kg:'specificEnergy',W_m2K:'heatTransferCoefficient',W_mK:'conductivity',W_K:'conductance',J_K:'heatCapacity',K_W:'thermalResistance',m3_s:'volumeFlow',kg_s:'massFlow',m_s:'velocity',m2:'area',m3:'volume',kg:'mass',J:'energy',W:'power',m:'length',s:'time'};
 for(const [suffix,dimension] of Object.entries(suffixes)) if(key.endsWith('_'+suffix)) return dimension;
 return null;
}
function displayUnit(key) {
 const d=fieldDimension(key);
 if(!d) return {label:'',scale:1,offset:0};
 if(d==='pressure'||d==='absolutePressure'||d==='pressureDifference') {
  const base=baseDisplayUnit('pressure'),gauge=d==='pressure'&&unitPreferences.pressureBasis==='gauge';
  return {...base,label:base.label+(d==='pressureDifference'?'':gauge?'(g)':'(a)'),offset:gauge?unitPreferences.referencePressurePa:0};
 }
 if(d==='temperatureDifference') {const base=baseDisplayUnit('temperature');return {...base,offset:0,label:'Δ'+base.label};}
 if(UNIT_OPTIONS[d]) return baseDisplayUnit(d);
 const l=baseDisplayUnit('length'),v=baseDisplayUnit('volume'),m=baseDisplayUnit('mass'),e=baseDisplayUnit('energy'),p=baseDisplayUnit('power'),t=baseDisplayUnit('time'),k=baseDisplayUnit('temperature'),pr=baseDisplayUnit('pressure');
 const expressions={
  area:[l.scale**2,l.label+'²'],density:[m.scale/v.scale,m.label+'/'+v.label],
  specificEnergy:[e.scale/m.scale,e.label+'/'+m.label],specificHeat:[e.scale/(m.scale*k.scale),e.label+'/('+m.label+'·Δ'+k.label+')'],
  velocity:[l.scale/t.scale,l.label+'/'+t.label],inverseTime:[1/t.scale,'1/'+t.label],
  heatCapacity:[e.scale/k.scale,e.label+'/Δ'+k.label],conductance:[p.scale/k.scale,p.label+'/Δ'+k.label],
  conductivity:[p.scale/(l.scale*k.scale),p.label+'/('+l.label+'·Δ'+k.label+')'],
  heatTransferCoefficient:[p.scale/(l.scale**2*k.scale),p.label+'/('+l.label+'²·Δ'+k.label+')'],
  thermalResistance:[k.scale/p.scale,'Δ'+k.label+'/'+p.label],
  areaThermalResistance:[l.scale**2*k.scale/p.scale,l.label+'²·Δ'+k.label+'/'+p.label],
  gasHeatCoefficient:[p.scale/(l.scale**2*k.scale*pr.scale),p.label+'/('+l.label+'²·Δ'+k.label+'·'+pr.label+')']
 };
 const [scale,label]=expressions[d]; return {label,scale,offset:0};
}
function toDisplayValue(key,value,delta=false) {if(typeof value!=='number'||!Number.isFinite(value))return null;const u=displayUnit(key);return (value-(delta?0:u.offset))/u.scale;}
function toSIValue(key,value,delta=false) {if(typeof value!=='number'||!Number.isFinite(value))throw new Error('유한한 숫자를 입력하세요');const u=displayUnit(key);return value*u.scale+(delta?0:u.offset);}
function convertFieldValue(key,value,direction) {
 if(key==='head_curve_points') {
  if(!Array.isArray(value)||!value.every(row=>Array.isArray(row)&&row.length===2)) throw new Error('곡선 점은 [질량유량, 압력상승] 배열이어야 합니다');
  return value.map(row=>row.map((v,i)=>(direction==='display'?toDisplayValue:toSIValue)(i===0?'mass_flow_kg_s':'pressure_rise_Pa',v)));
 }
 if(Array.isArray(value)) return value.map(item=>convertFieldValue(key,item,direction));
 if(typeof value==='number') return (direction==='display'?toDisplayValue:toSIValue)(key,value);
 return value;
}
function displayNumberInput(value) {return typeof value==='number'?Number(value.toPrecision(14)):value;}
function readDisplayNumber(value) {if(String(value).trim()===''||!Number.isFinite(Number(value)))throw new Error('유한한 숫자를 입력하세요');return Number(value);}
function unitInputLabel(key,label) {
 const base=label.replace(/\s*\([^)]*\)\s*$/,'');
 const unit=displayUnit(key).label;
 return unit?`${base} (${unit})`:label;
}
function pressureValueText(value) {return `${monitorValue(toDisplayValue('pressure_Pa',value),6)} ${displayUnit('pressure_Pa').label}`;}
function renderUnitDialog(preferences=unitPreferences) {
 $('#unitPresetSelect').value=Object.keys(UNIT_PRESETS).find(name=>Object.keys(UNIT_PRESETS[name]).every(k=>UNIT_PRESETS[name][k]===preferences[k]))||'';
 $('#unitFields').innerHTML=Object.keys(UNIT_OPTIONS).map(family=>`<label>${UNIT_FAMILY_NAMES[family]}<select data-unit-family="${family}">${Object.keys(UNIT_OPTIONS[family]).map(unit=>`<option ${preferences[family]===unit?'selected':''}>${unit}</option>`).join('')}</select></label>`).join('');
 $('#pressureBasisInput').value=preferences.pressureBasis;
 const pressureScale=UNIT_OPTIONS.pressure[preferences.pressure][0];
 $('#referencePressureInput').value=displayNumberInput(preferences.referencePressurePa/pressureScale);
 $('#referencePressureInput').dataset.scale=pressureScale;
 $('#referencePressureInput').closest('label').firstChild.textContent='게이지압 기준 대기압 ('+preferences.pressure+'(a))';
}
function applyUnitPreferences(preferences) {
 unitPreferences=validUnitPreferences(preferences);
 localStorage.setItem('lh2-display-units',JSON.stringify(unitPreferences));
 if(state.design) {state.design.display_units={...unitPreferences};persistLocal();}
 $('#unitsBtn').textContent='단위 설정';
 // Convert display only; retained rows, design parameters and initial states remain SI.
 Object.entries(state.nodeValues).forEach(([id])=>{const last=state.telemetry['node:'+id]?.at(-1);if(last?.pressure_Pa!=null)state.nodeValues[id]=pressureValueText(last.pressure_Pa);else delete state.nodeValues[id];});
 render();updateMonitor();syncProcessDialog();
}
function bindUnitSettings() {
 $('#unitsBtn').addEventListener('click',()=>{document.activeElement?.blur();renderUnitDialog();$('#unitsDialog').showModal();});
 $('#unitPresetSelect').addEventListener('change',()=>{const preset=UNIT_PRESETS[$('#unitPresetSelect').value];if(preset)renderUnitDialog(preset);});
 $('#unitFields').addEventListener('change',event=>{if(event.target.dataset.unitFamily!=='pressure')return;const input=$('#referencePressureInput'),si=Number(input.value)*Number(input.dataset.scale),unit=event.target.value,scale=UNIT_OPTIONS.pressure[unit][0];input.value=displayNumberInput(si/scale);input.dataset.scale=scale;input.closest('label').firstChild.textContent='게이지압 기준 대기압 ('+unit+'(a))';});
 $('#applyUnitsBtn').addEventListener('click',()=>{const values={};$('#unitFields').querySelectorAll('[data-unit-family]').forEach(select=>values[select.dataset.unitFamily]=select.value);values.pressureBasis=$('#pressureBasisInput').value;values.referencePressurePa=Number($('#referencePressureInput').value)*UNIT_OPTIONS.pressure[values.pressure][0];if(!Number.isFinite(values.referencePressurePa)||values.referencePressurePa<=0){toast('게이지압 기준 대기압을 양수로 입력하세요');return;}applyUnitPreferences(values);$('#unitsDialog').close();toast('전체 표시·입력 단위를 적용했습니다');});
 $('#cancelUnitsBtn').addEventListener('click',()=>$('#unitsDialog').close());
}
