"""Cooling Advisor workspace: API-backed experiments, evidence and case review."""
import html
import json
import os
from uuid import uuid4

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from cooling_advisor.gateway import Backend, BackendError
from cooling_advisor.models import SimulationRequest
from cooling_advisor.skills import SKILLS
from cooling_advisor.storage import ROOT

load_dotenv(ROOT/'.env',override=False)
st.set_page_config(page_title='Cooling Advisor',page_icon='🌡️',layout='wide',initial_sidebar_state='expanded')
st.markdown('''<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
html,body,[class*="css"],.stApp {font-family:'DM Sans',sans-serif;}
.stApp {background:#f5f7fb;color:#182b45;}
[data-testid="stSidebar"] {background:#eaf0f7;border-right:1px solid #d7e0ec;}
.block-container {padding-top:4rem;max-width:1600px;}
h1 {letter-spacing:-1.6px;font-weight:700!important;}
h2,h3 {letter-spacing:-.5px;}
.hero {background:linear-gradient(115deg,#112d4e,#145f72);border-radius:18px;padding:24px 28px;color:white;margin:12px 0 24px;}
.hero h2 {color:white;font-size:28px;margin:8px 0;}
.hero p {color:#d2e9ef;max-width:780px;font-size:16px;}
.eyebrow {font-size:11px;letter-spacing:2px;font-weight:700;color:#97dcd5;}
.card {background:white;border:1px solid #dde5ef;border-radius:14px;padding:22px;min-height:155px;}
.card b {font-size:18px;display:block;margin-bottom:10px;}
.card p {font-size:14px;color:#53647a;margin-bottom:0;}
.flow {display:flex;gap:8px;align-items:stretch;margin:12px 0 22px;}
.node {flex:1;background:white;border:1px solid #dce5ef;border-radius:12px;padding:16px 12px;font-size:13px;}
.node b {display:block;color:#126c70;margin-bottom:5px;}
.arrow {align-self:center;color:#8294a7;}
.trace {background:white;border:1px solid #e1e8f0;border-left:4px solid #1f8b82;border-radius:8px;padding:12px 16px;margin-bottom:8px;}
.trace small {color:#6c7f93;}
[data-testid="stMetric"] {background:white;border:1px solid #dce5ef;border-radius:14px;padding:16px;}
[data-testid="stMetricLabel"] {color:#5c6f85;}
.stButton button {border-radius:9px;}
</style>''',unsafe_allow_html=True)

PAGES = ['Overview','Analysis workspace','Simulation lab','Data imports','Agents & skills','Case history','Monitoring','Settings']
if 'page' not in st.session_state:
    st.session_state.page = PAGES[0]

with st.sidebar:
    st.markdown('### ◈ Cooling Advisor')
    st.caption('DATA CENTER ENERGY WORKSPACE')
    st.radio('Workspace',PAGES,key='page',label_visibility='collapsed')
    st.divider()
    st.caption('Decision support · no equipment control')
    st.caption('Python · FastAPI · LangGraph')

if os.getenv('COOLING_AUTH_MODE','local_token') == 'supabase':
    if not st.session_state.get('access_token'):
        st.title('Sign in to your workspace')
        with st.form('login'):
            email = st.text_input('Email')
            password = st.text_input('Password',type='password')
            submit = st.form_submit_button('Sign in',type='primary')
        if submit:
            try:
                from cooling_advisor.auth import supabase_client
                session = supabase_client().auth.sign_in_with_password({'email':email,'password':password}).session
                st.session_state.access_token = session.access_token
                st.rerun()
            except Exception:
                st.error('Sign-in failed. Check your credentials and workspace setup.')
        st.stop()
    if st.sidebar.button('Sign out'):
        token = st.session_state.get('access_token')
        try:
            from cooling_advisor.auth import supabase_client
            supabase_client(token).auth.sign_out()
        except Exception:
            pass
        st.session_state.clear()
        st.rerun()

backend = Backend(st.session_state.get('access_token'))
try:
    settings = backend.get('/settings')
    catalog = backend.get('/catalog')
except BackendError as exc:
    st.error(str(exc))
    st.code('.venv/bin/python -m cooling_advisor',language='bash')
    st.stop()

st.sidebar.caption('Storage: '+settings['storage'].capitalize())
st.sidebar.caption('LLM: '+('Configured' if settings['gemini_configured'] else 'Not configured · offline tools available'))


def navigate(page):
    st.session_state.page = page


def source_label(kind):
    return {'simulated':'SIMULATED · SUSTAINDC','synthetic':'SYNTHETIC · TEST FIXTURE','measured':'IMPORTED · SOURCE UNVERIFIED'}[kind]


def banner(scenario):
    kind = scenario['experiments'][0]['source_kind']
    if kind == 'simulated':
        st.info('SustainDC power-temperature simulation. Results describe modeled operating points; they are not measurements or validated equipment instructions.',icon='🔬')
    elif kind == 'synthetic':
        st.warning('Hand-authored test fixture. Use this scenario to inspect workflow and guardrail behavior.',icon='🧪')
    else:
        st.info('User-imported observations. Source, units and calibration are unverified. This window supports baseline analysis only.',icon='📥')


def plot_layout(fig,height=340):
    fig.update_layout(height=height,margin=dict(l=12,r=12,t=32,b=12),paper_bgcolor='rgba(0,0,0,0)',
                      plot_bgcolor='white',font=dict(family='DM Sans',color='#30485f'),
                      legend=dict(orientation='h',y=-.2),colorway=['#167f85','#578de0','#f2ae55','#a279c5','#7fb6a4'])
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor='#edf1f6')
    return fig


def timeseries(scenario, selected_id=None):
    baseline = next(r for r in scenario['experiments'] if r['experiment_id'] == scenario['baseline_id'])
    runs = [('Baseline',baseline)]
    if selected_id and selected_id != baseline['experiment_id']:
        chosen = next((r for r in scenario['experiments'] if r['experiment_id'] == selected_id),None)
        if chosen:
            runs.append(('Selected',chosen))
    rows=[]
    for label,run in runs:
        elapsed=0
        for sample in run['samples']:
            rows.append({'Hour':elapsed,'Option':label,'IT power (kW)':sample['it_kw'],
                         'Cooling power (kW)':sample['cooling_kw'],'Total power (kW)':sample['it_kw']+sample['cooling_kw'],
                         'Maximum inlet (°C)':sample['max_inlet_c'],'Ambient (°C)':sample.get('ambient_c')})
            elapsed += sample['interval_hours']
    frame=pd.DataFrame(rows)
    left,right=st.columns(2)
    with left:
        fig=px.line(frame,x='Hour',y='Total power (kW)',color='Option',line_shape='hv',title='Total electrical power')
        st.plotly_chart(plot_layout(fig),width='stretch')
    with right:
        fig=px.line(frame,x='Hour',y='Maximum inlet (°C)',color='Option',line_shape='hv',title='Maximum rack inlet temperature')
        limit=(scenario.get('policy') or {}).get('max_inlet_c',27)
        fig.add_hline(y=limit,line_dash='dash',line_color='#c16e36',annotation_text=f'Study limit {limit:g} °C')
        st.plotly_chart(plot_layout(fig),width='stretch')
    st.caption('Interval-average power is integrated over the stated horizon. Dashed temperature limits are study settings, not approved site operating limits.')


def provenance(scenario):
    with st.expander('Evidence provenance & model limits'):
        data=scenario.get('provenance',{})
        for limitation in data.get('limitations',[]):
            st.write('• '+limitation)
        st.json(data or {'origin':'Hand-authored synthetic fixture'})
        st.download_button('Download source scenario',json.dumps(scenario,indent=2),file_name=scenario['scenario_id']+'.json',mime='application/json',key='source-'+scenario['scenario_id'])


def trace(result):
    cols=st.columns(4)
    for col,label,value in zip(cols,['Tool calls','Model calls','Model tokens','Runtime'],
                               [result['tool_calls'],result.get('model_calls',0),result.get('model_tokens',0),f"{result.get('elapsed_ms',0)/1000:.2f} s"]):
        col.metric(label,value)
    st.caption(result['workflow_mode'])
    for actor,note in result.get('agent_notes',{}).items():
        with st.expander(actor.replace('_',' ').title()+' · model-generated note'):
            st.write(note)
    for event in result['events']:
        if event['action'] == 'skill_loaded' or event['status'] in ('failed','rejected','blocked') or event['actor'] in ('G1','G3'):
            color='#c17439' if event['status'] in ('failed','rejected','blocked') else '#1f8b82'
            st.markdown(f'<div class="trace" style="border-left-color:{color}"><small>{event["elapsed_ms"]:.0f} ms · {html.escape(event["actor"])}</small><br><b>{html.escape(event["action"].replace("_"," "))}</b> · {html.escape(event["status"])}<br>{html.escape(event["detail"])}</div>',unsafe_allow_html=True)
    with st.expander('All events'):
        st.dataframe(pd.DataFrame(result['events']),hide_index=True,width='stretch')


def show_result(result,scenario=None):
    st.caption(f"CASE {result['case_id']} · {source_label(result['source_kind'])}")
    if result['status'] == 'completed':
        st.success(result['explanation'])
    else:
        st.warning(result['explanation'])
    tabs=st.tabs(['Energy & decision','Agent trace','Evidence & export'])
    with tabs[0]:
        baseline,selected=result.get('baseline'),result.get('selected')
        if baseline:
            cols=st.columns(4)
            cols[0].metric('Baseline energy',f"{baseline['total_kwh']:,.1f} kWh")
            cols[1].metric('Selected energy' if selected else 'Cooling energy',f"{(selected or baseline)['total_kwh' if selected else 'cooling_kwh']:,.1f} kWh")
            pct=selected.get('savings_pct') if selected else None
            cols[2].metric('Modeled reduction' if result['source_kind']=='simulated' else 'Comparison reduction',f'{pct:.2f}%' if pct is not None else '—')
            inlet=(selected or baseline)['max_inlet_c']
            cols[3].metric('Maximum inlet',f'{inlet:.1f} °C' if inlet is not None else 'Missing')
        if result['candidates']:
            frame=pd.DataFrame(result['candidates']).sort_values('setpoint_c')
            frame['Option']=frame['setpoint_c'].map(lambda value:f'{value:g} °C')
            frame['Policy'] = frame['feasible'].map({True:'Within study limits',False:'Rejected by G3'})
            fig=px.bar(frame,x='Option',y='total_kwh',color='Policy',color_discrete_map={'Within study limits':'#167f85','Rejected by G3':'#da9253'},title='Same workload, weather and energy boundary',labels={'total_kwh':'Total energy (kWh)'})
            st.plotly_chart(plot_layout(fig),width='stretch')
            st.dataframe(frame[['Option','cooling_kwh','it_kwh','total_kwh','max_inlet_c','feasible','reasons']],hide_index=True,width='stretch',column_config={
                'cooling_kwh':st.column_config.NumberColumn('Cooling (kWh)',format='%.2f'),
                'it_kwh':st.column_config.NumberColumn('IT (kWh)',format='%.2f'),
                'total_kwh':st.column_config.NumberColumn('Total (kWh)',format='%.2f'),
                'max_inlet_c':st.column_config.NumberColumn('Max inlet (°C)',format='%.1f'),
                'feasible':'Within limits','reasons':'Rejection reasons'})
        if scenario:
            timeseries(scenario,selected['experiment_id'] if selected else None)
        if baseline and baseline.get('components_kwh'):
            components=[]
            for label,item in [('Baseline',baseline),('Selected',selected)]:
                if item:
                    components += [{'Option':label,'Component':key.replace('_',' ').title(),'Energy (kWh)':value} for key,value in item['components_kwh'].items()]
            st.plotly_chart(plot_layout(px.bar(pd.DataFrame(components),x='Option',y='Energy (kWh)',color='Component',title='Electrical energy by modeled component')),width='stretch')
        if result.get('gemini_explanation'):
            st.write(result['gemini_explanation'])
            st.caption('Optional model-generated explanation; numeric decisions are independently checked.')
        for limitation in result.get('limitations',[]):
            st.caption('• '+limitation)
        st.divider()
        cols=st.columns([2,1])
        action=cols[0].selectbox('Record your review',['mark_for_review','acknowledge','dismiss'],format_func=lambda x:x.replace('_',' ').title())
        if cols[1].button('Save review',width='stretch'):
            try:
                backend.post('/cases/'+result['case_id']+'/decision',{'action':action})
                st.success('Review recorded. This does not execute an equipment change.')
            except BackendError as exc:
                st.error(str(exc))
        try:
            reviews=backend.get('/cases/'+result['case_id']+'/reviews')
            if reviews:
                with st.expander('Recorded reviews'):
                    st.dataframe(pd.DataFrame(reviews),hide_index=True,width='stretch')
        except BackendError:
            st.caption('Review history is temporarily unavailable.')
    with tabs[1]:
        trace(result)
    with tabs[2]:
        st.download_button('Download full case JSON',json.dumps(result,indent=2),file_name='case-'+result['case_id']+'.json',mime='application/json')
        if result['candidates']:
            st.download_button('Download numeric comparison CSV',pd.DataFrame(result['candidates']).drop(columns=['components_kwh','reasons']).to_csv(index=False),file_name='comparison.csv',mime='text/csv')
        if scenario:
            provenance(scenario)
        st.json({'policy_version':result['policy_version'],'reason_codes':result['reason_codes'],'source_kind':result['source_kind']})


page=st.session_state.page
if page == 'Overview':
    st.caption('WORKSPACE / OVERVIEW')
    st.markdown('<div class="hero"><span class="eyebrow">COOLING INTELLIGENCE</span><h2>Understand the energy.<br>Inspect every recommendation.</h2><p>Run repeatable cooling experiments, compare total electricity use and review the evidence behind each decision.</p></div>',unsafe_allow_html=True)
    cols=st.columns(4)
    cols[0].metric('Available scenarios',len(catalog))
    cols[1].metric('SustainDC studies',sum(r['source_kind']=='simulated' for r in catalog))
    cols[2].metric('Agent roles','3')
    cols[3].metric('Guardrail layers','3')
    st.write('')
    cards=st.columns(3)
    for col,title,body in zip(cards,['01 / Explore evidence','02 / Run an experiment','03 / Review a case'],
        ['Inspect power, temperature and matched alternatives from public workload and weather traces.',
         'Change the study window and setpoints. Run pinned upstream physics in an isolated Python process.',
         'Follow skills, delegated tools and independent validation. Save the result for human review.']):
        col.markdown(f'<div class="card"><b>{title}</b><p>{body}</p></div>',unsafe_allow_html=True)
    st.write('')
    a,b,_=st.columns([1,1,2])
    a.button('Open analysis workspace',type='primary',on_click=navigate,args=('Analysis workspace',),width='stretch')
    b.button('Create an experiment',on_click=navigate,args=('Simulation lab',),width='stretch')
    real=next((r for r in catalog if r['source_kind']=='simulated'),None)
    if real:
        st.subheader('Inside a SustainDC study')
        scenario=backend.get('/scenarios/'+real['scenario_id'])
        st.caption(real['label']+' · '+real['description'])
        timeseries(scenario)
    st.caption('Offline analysis works without an LLM account. Gemini tool calling and Supabase are available when configured.')

elif page == 'Analysis workspace':
    st.caption('WORKSPACE / ANALYSIS')
    st.title('Analysis workspace')
    st.write('Select evidence, ask a question, and compare options under explicit study limits.')
    if not catalog:
        st.info('Create a simulation or import an observation window first.')
        st.stop()
    options={r['scenario_id']:r for r in catalog}
    preferred=st.session_state.pop('next_scenario',None)
    if preferred in options:
        st.session_state.scenario_id=preferred
    scenario_id=st.selectbox('Evidence scenario',list(options),format_func=lambda sid:options[sid]['label']+' · '+source_label(options[sid]['source_kind']),key='scenario_id')
    scenario=backend.get('/scenarios/'+scenario_id)
    banner(scenario)
    with st.expander('Analysis settings',expanded=not bool(st.session_state.get('result'))):
        with st.form('analysis'):
            a,b=st.columns(2)
            task=a.radio('Task',['recommend','analyze'],format_func=lambda x:'Compare cooling options' if x=='recommend' else 'Analyze the baseline')
            execution=b.radio('Agent execution',['rules','gemini'],format_func=lambda x:'Offline Python tools + LangGraph' if x=='rules' else 'Gemini parent + child tool calling')
            question=st.text_area('Question',value='Which supported setting uses the least total electricity while meeting the study temperature limit?',max_chars=2000,height=90)
            consent=st.checkbox('Allow this case summary and tool results to be sent to Gemini when Gemini mode is selected.',value=False)
            st.caption('Rules mode follows the same roles and guardrails without model calls. Gemini mode lets the parent and children select allowed tools; numeric calculations remain Python. Offline mode uses the selected task and does not interpret free-text questions.')
            run=st.form_submit_button('Run analysis',type='primary')
    if run:
        if execution=='gemini' and not settings['gemini_configured']:
            st.error('Gemini is not configured. Add the backend API key in Settings or use offline mode.')
        elif execution=='gemini' and not consent:
            st.error('Enable the case-data consent checkbox before using Gemini.')
        else:
            try:
                with st.spinner('Inspecting evidence → comparing options → validating G3 → saving case…'):
                    st.session_state.result=backend.post('/analysis',{'scenario_id':scenario_id,'mode':task,'agent_mode':execution,'question':question},str(uuid4()))
                st.rerun()
            except BackendError as exc:
                st.error(str(exc))
    result=st.session_state.get('result')
    if result:
        result_scenario=scenario if result['scenario_id']==scenario_id else backend.get('/scenarios/'+result['scenario_id'])
        if result['scenario_id']!=scenario_id:
            st.info('The displayed saved result belongs to '+result_scenario['label']+'. Run again to analyze your new selection.')
        show_result(result,result_scenario)
    else:
        timeseries(scenario)
        provenance(scenario)

elif page == 'Simulation lab':
    st.caption('WORKSPACE / EXPERIMENTS')
    st.title('Simulation lab')
    st.write('Generate comparable constant-setpoint alternatives using the pinned SustainDC power-temperature model.')
    if not settings['simulator']['available']:
        st.warning('Pinned simulator source is unavailable.')
        st.code('.venv/bin/python -m cooling_advisor.simulation install',language='bash')
    with st.form('simulation'):
        label=st.text_input('Experiment name','Phoenix cooling study',max_chars=80)
        a,b,c=st.columns(3)
        city=a.selectbox('Weather location',['AZ','NY','WA'],format_func=lambda x:{'AZ':'Phoenix, Arizona','NY':'New York','WA':'Seattle, Washington'}[x])
        workload=b.selectbox('Public workload trace',['alibaba1','alibaba2','google1'])
        day=c.number_input('Day of year',min_value=1,max_value=363,value=182)
        a,b,c=st.columns(3)
        hours=a.select_slider('Study horizon (hours)',options=[1,3,6,12,24,48],value=12)
        capacity=b.number_input('Configured IT capacity (MW)',min_value=.1,max_value=2.,value=1.,step=.1)
        seed=c.number_input('Reproducibility seed',min_value=0,max_value=100000,value=42)
        a,b=st.columns(2)
        baseline=a.number_input('Baseline setpoint (°C)',min_value=15.,max_value=21.6,value=18.,step=.5)
        limit=b.slider('Study maximum inlet (°C)',min_value=20.,max_value=32.,value=27.,step=.5)
        candidates=st.multiselect('Candidate setpoints (°C)',[15.,16.,17.,18.,19.,20.,21.,21.6],default=[15.,16.,17.,18.,19.,20.,21.6])
        st.caption('All options share the same input window and seed. Hourly traces are held for 15-minute intervals. The model has no thermal inertia; this is not a closed-loop control experiment.')
        submit=st.form_submit_button('Run SustainDC experiment',type='primary',disabled=not settings['simulator']['available'])
    if submit:
        try:
            request=SimulationRequest(label=label,city=city,workload=workload,day_of_year=day,hours=hours,capacity_mw=capacity,seed=seed,baseline_setpoint_c=baseline,max_inlet_c=limit,candidate_setpoints=candidates)
            with st.spinner('Running actual upstream power and temperature calculations…'):
                st.session_state.last_simulation=backend.post('/simulations',request.model_dump())
            st.success('Experiment completed and saved to your scenario catalog.')
        except (ValueError,BackendError) as exc:
            st.error(str(exc))
    if st.session_state.get('last_simulation'):
        scenario=st.session_state.last_simulation
        banner(scenario)
        timeseries(scenario)
        def open_experiment():
            st.session_state.next_scenario=scenario['scenario_id']
            navigate('Analysis workspace')
        st.button('Analyze this experiment',on_click=open_experiment,type='primary')
        provenance(scenario)
    st.caption('Weather/workload traces are paired by index. They are not recorded together at one real facility.')

elif page == 'Data imports':
    st.caption('WORKSPACE / OBSERVATIONS')
    st.title('Import an observation window')
    st.write('Bring interval-average power and maximum inlet temperature into baseline analysis.')
    template='timestamp,interval_hours,cooling_kw,it_kw,max_inlet_c,setpoint_c\n2026-01-01T00:00:00Z,0.25,40,100,24,18\n2026-01-01T00:15:00Z,0.25,42,102,24.5,18\n'
    st.download_button('Download CSV template',template,file_name='telemetry-template.csv',mime='text/csv')
    st.caption('Required: offset-aware timestamps; contiguous intervals; kW, hours and °C; one constant setpoint. Maximum 672 rows / 2 MB. Blank inlet temperature blocks a completed recommendation.')
    with st.form('import'):
        label=st.text_input('Observation name','Imported baseline',max_chars=80)
        file=st.file_uploader('Telemetry CSV',type=['csv'],max_upload_size=2)
        confirmed=st.checkbox('I confirm these columns represent interval-average electrical power and maximum inlet temperature in the stated units.')
        submit=st.form_submit_button('Validate & import',type='primary')
    if submit:
        if not file or not confirmed:
            st.error('Choose a CSV and confirm its units.')
        else:
            try:
                run=backend.post('/imports',{'label':label,'filename':file.name[:100],'csv_text':file.getvalue().decode('utf-8-sig')})
                st.session_state.imported=run
                st.success('Observation window imported. Alternative-setting recommendations are disabled for unmatched observations.')
            except (UnicodeDecodeError,BackendError) as exc:
                st.error(str(exc))
    if st.session_state.get('imported'):
        scenario=st.session_state.imported
        timeseries(scenario)
        provenance(scenario)

elif page == 'Agents & skills':
    st.caption('WORKSPACE / ORCHESTRATION')
    st.title('Agents, skills & guardrails')
    st.write('The parent delegates. Children use bounded tools. G3 independently recomputes the final decision.')
    st.markdown('<div class="flow"><div class="node"><b>G1 · Request</b>Identity, scope, schema</div><span class="arrow">→</span><div class="node"><b>Supervisor · S1</b>Delegate the task</div><span class="arrow">→</span><div class="node"><b>Data Analyst · S2 / S3</b>Inspect & calculate</div><span class="arrow">→</span><div class="node"><b>Cooling Advisor · S4</b>Compare & propose</div><span class="arrow">→</span><div class="node"><b>G3 → S5</b>Recheck & explain</div></div>',unsafe_allow_html=True)
    st.info('G2 wraps every tool call and delegation: role/skill allowlist, authorized zone, shared call budget and deadline. A model cannot bypass G3 by writing a convincing explanation.')
    st.dataframe(pd.DataFrame([{'Skill':s.id,'Owner':s.owner,'Purpose':s.name,'Allowed tools':', '.join(s.tools) or 'Validated composition only','Version':s.version} for s in SKILLS.values()]),hide_index=True,width='stretch')
    for skill in SKILLS.values():
        with st.expander(skill.id+' · '+skill.name):
            st.markdown(skill.instructions())
    st.caption('Gemini mode uses real model function calls when credentials are configured. Offline mode executes Python procedures. Neither mode can operate physical equipment.')

elif page == 'Case history':
    st.caption('WORKSPACE / AUDIT')
    st.title('Case history')
    try:
        rows=backend.get('/cases')
        if not rows:
            st.info('No saved cases yet. Run an analysis to begin the audit trail.')
        else:
            labels={r['scenario_id']:r['label'] for r in catalog}
            choices={r['case_id']:r for r in rows}
            case_id=st.selectbox('Saved case',list(choices),format_func=lambda cid:
                labels.get(choices[cid].get('scenario_id'),'Unavailable source')+' · '+choices[cid]['status']+' · '+cid[:8])
            with st.expander('Browse all recent cases'):
                st.dataframe(pd.DataFrame(rows)[['created_at','status','source_kind','decision','case_id']],hide_index=True,width='stretch')
            result=backend.get('/cases/'+case_id)
            try:
                scenario=backend.get('/scenarios/'+result['scenario_id'])
            except BackendError:
                scenario=None
            show_result(result,scenario)
    except BackendError as exc:
        st.error(str(exc))

elif page == 'Monitoring':
    st.caption('WORKSPACE / OBSERVABILITY')
    st.title('Monitoring')
    st.write('Inspect workflow outcomes, guarded calls and model usage for your recent cases.')
    try:
        metrics=backend.get('/monitoring')
        cols=st.columns(4)
        for col,label,key in zip(cols,['Saved cases','Completed','Blocked / failed','Model tokens'],['case_count','completed','blocked_or_failed','model_tokens']):
            col.metric(label,metrics[key])
        st.caption(metrics['window']+' · refresh by reopening this page')
        a,b=st.columns(2)
        a.metric('Guarded tool calls',metrics['tool_calls'])
        b.metric('Model calls',metrics['model_calls'])
        if metrics['latency_ms']:
            fig=px.bar(x=list(range(1,len(metrics['latency_ms'])+1)),y=[v/1000 for v in metrics['latency_ms']],labels={'x':'Recent case','y':'Runtime (seconds)'},title='Case latency')
            st.plotly_chart(plot_layout(fig),width='stretch')
        st.subheader('Guardrail blocks & rejections')
        if metrics['guard_events']:
            st.dataframe(pd.DataFrame(metrics['guard_events']),hide_index=True,width='stretch')
        else:
            st.info('No blocked guardrail events in this window.')
        st.caption('This page reports application events. It does not monitor live equipment health or send external alerts.')
    except BackendError as exc:
        st.error(str(exc))

elif page == 'Settings':
    st.caption('WORKSPACE / CONFIGURATION')
    st.title('Connections & readiness')
    statuses=[{'Connection':'Local backend','Status':'Connected'},
              {'Connection':'Pinned SustainDC source','Status':'Ready' if settings['simulator']['available'] else 'Install required'},
              {'Connection':'Gemini tool calling','Status':'Key configured · live validation required' if settings['gemini_configured'] else 'Key required'},
              {'Connection':'Supabase','Status':'Connection configured · verify migration and access' if settings['supabase_configured'] else 'Credentials required'}]
    st.dataframe(pd.DataFrame(statuses),hide_index=True,width='stretch')
    st.write('**Authentication:** '+settings['auth_mode']+' · **Storage:** '+settings['storage'])
    st.write('**Authorized zones:** '+', '.join(settings['zones']))
    st.code('GEMINI_API_KEY=...\nGEMINI_MODEL=gemini-flash-latest\n\n# Optional Supabase workspace\nCOOLING_AUTH_MODE=supabase\nCOOLING_STORAGE=supabase\nSUPABASE_URL=https://your-project.supabase.co\nSUPABASE_ANON_KEY=...',language='bash')
    st.caption('Set values in the backend .env file and restart the workspace. Values are never displayed here. Apply migration 002_user_workspace.sql and grant zone membership before signing in.')
    st.json({'version':'0.2.0','simulator_commit':settings['simulator']['commit'],'equipment_commands':False})
