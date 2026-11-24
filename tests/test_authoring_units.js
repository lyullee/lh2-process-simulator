// Runs without HTTP server, browser, or physical simulation: node tests/test_authoring_units.js
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..');
const ctx=vm.createContext({localStorage:{getItem:()=>null},console,Blob,setTimeout(){}});
for(const name of ['units','parameter_labels','monitor']) vm.runInContext(fs.readFileSync(path.join(root,'authoring_ui/static',name+'.js'),'utf8'),ctx);
const evaluate=source=>vm.runInContext(source,ctx);
const near=(a,b)=>assert(Math.abs(a-b)<1e-9*Math.max(1,Math.abs(b)),`${a} != ${b}`);
const quantities={pressure_Pa:400000,temperature_K:20,mass_flow_kg_s:.12,volume_m3:50,length_m:2,wall_heat_capacity_J_K:4e6,ambient_UA_W_K:20,frost_conductivity_W_mK:.2,wall_liquid_U_W_m2K:10,wall_specific_heat_J_kgK:400,residual_gas_coefficient_W_m2K_Pa:.01,pressure_drop_Pa:12000,specific_entropy_J_kgK:100,mass_kg:100,inlet_enthalpy_J_kg:100000,conversion_rate_constant_s:.02,time_s:3600,opening_fraction:.5};
for(const preset of ['SI','engineering','US']) {
 evaluate(`unitPreferences=validUnitPreferences(UNIT_PRESETS.${preset})`);
 for(const [key,value] of Object.entries(quantities)) near(evaluate(`toSIValue('${key}',toDisplayValue('${key}',${value}))`),value);
}
evaluate("unitPreferences=validUnitPreferences({...UNIT_PRESETS.engineering,pressureBasis:'gauge'})");
near(evaluate("toDisplayValue('pressure_Pa',401325)"),3);
near(evaluate("toDisplayValue('pressure_drop_Pa',100000)"),1);
near(evaluate("toDisplayValue('pressure_Pa',10000)"),-.91325);
near(evaluate("toDisplayValue('temperature_K',20)"),-253.15);
near(evaluate("toDisplayValue('temperature_K',2,true)"),2);
evaluate("unitPreferences=validUnitPreferences(UNIT_PRESETS.US)");
near(evaluate("toDisplayValue('temperature_K',273.15)"),32);
near(evaluate("toDisplayValue('temperature_K',10,true)"),18);
near(evaluate("toDisplayValue('temperature_change_K',10)"),18);
const curve='[[0,320000],[0.1,160000]]';
const back=evaluate(`convertFieldValue('head_curve_points',convertFieldValue('head_curve_points',${curve},'display'),'si')`);
back.forEach((row,i)=>row.forEach((value,j)=>near(value,JSON.parse(curve)[i][j])));
assert.equal(evaluate("fieldDimension('residual_gas_coefficient_W_m2K_Pa')"),'gasHeatCoefficient');
assert.equal(evaluate("toDisplayValue('vapor_quality',null)"),null);
assert.throws(()=>evaluate("readDisplayNumber('')"));

function element(tag){return {tag,children:[],attrs:{},appendChild(child){this.children.push(child);return child;},append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},setAttribute(key,value){this.attrs[key]=value;},getBoundingClientRect(){return {width:900,height:440,left:0};}};}
ctx.svg=(tag,attrs={},text=null)=>Object.assign(element(tag),{attrs,text});
ctx.chart=element('svg');
evaluate("renderProcessChart(chart,[{time_s:0,temperature_K:273.15},{time_s:60,temperature_K:283.15}], 'temperature_K','edge:E18')");
assert(ctx.chart.children.some(node=>node.text==='온도 [°F]'));
ctx.sourceRows=Object.freeze([Object.freeze({time_s:60,pressure_Pa:101325,temperature_K:273.15,mass_flow_kg_s:0})]);
evaluate("selectedMonitorData=()=>({key:'edge:E18',rows:sourceRows})");
ctx.document={createElement:()=>({click(){},set href(v){ctx.exportHref=v;},set download(v){ctx.exportFilename=v;}})};
ctx.URL={createObjectURL(blob){ctx.exportBlob=blob;return 'blob:test';},revokeObjectURL(){}};
evaluate('exportMonitorCsv()');
ctx.exportBlob.text().then(csv=>{
 assert(csv.includes('°F')&&csv.includes('psi(a)')&&csv.includes('lb/h'));
 near(Number(csv.split('\r\n')[1].split(',')[2].replaceAll('"','')),32);
 assert.equal(ctx.sourceRows[0].temperature_K,273.15);
 assert.equal(ctx.sourceRows[0].pressure_Pa,101325);
 console.log('54 quantity round-trips; affine/delta, mixed arrays, chart/CSV units and SI preservation: passed');
}).catch(error=>{console.error(error);process.exitCode=1;});
