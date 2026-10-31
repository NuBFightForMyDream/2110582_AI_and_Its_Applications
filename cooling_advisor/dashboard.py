"""Cooling Advisor dashboard: pick a situation, get the recommended cooling setpoint."""
import os
from uuid import uuid4

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv

from cooling_advisor.gateway import Backend, BackendError
from cooling_advisor.models import SimulationRequest
from cooling_advisor.storage import ROOT

load_dotenv(ROOT/'.env',override=False)
st.set_page_config(page_title='Cooling Advisor',page_icon='🌡️',layout='centered')
st.markdown('''<style>
.answer {background:#0f766e;border-radius:16px;padding:22px 26px;margin:6px 0 18px;}
.answer small {color:#ccefe9;font-size:13px;letter-spacing:1px;font-weight:600;}
.answer h2 {color:white;font-size:42px;margin:2px 0 8px;padding:0;}
.answer p {color:#e6f6f3;font-size:16px;margin:2px 0;}
</style>''',unsafe_allow_html=True)

CITIES = {'AZ':'Phoenix, Arizona','NY':'New York','WA':'Seattle, Washington'}

if os.getenv('COOLING_AUTH_MODE','local_token') == 'supabase' and not st.session_state.get('access_token'):
    st.title('Sign in')
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
            st.error('Sign-in failed. Check your email and password.')
    st.stop()

backend = Backend(st.session_state.get('access_token'))
try:
    settings = backend.get('/settings')
    studies = {row['scenario_id']:row for row in backend.get('/catalog') if row['source_kind'] == 'simulated'}
except BackendError as exc:
    st.error(str(exc))
    if st.session_state.get('access_token'):
        # Supabase sessions expire; let the user start a fresh sign-in instead of a dead end.
        if st.button('Sign in again',type='primary'):
            st.session_state.clear()
            st.rerun()
    else:
        st.code('.venv/bin/python -m cooling_advisor',language='bash')
    st.stop()


def recommend(scenario_id):
    # One saved analysis per situation per browser session; switching back reuses it.
    results = st.session_state.setdefault('results',{})
    if scenario_id not in results:
        with st.spinner('Comparing cooling setpoints…'):
            results[scenario_id] = backend.post('/analysis',{'scenario_id':scenario_id,'mode':'recommend'},str(uuid4()))
    return results[scenario_id]


def energy_chart(result,limit):
    rows = sorted(result['candidates'],key=lambda row:row['setpoint_c'])
    best, current = result['selected']['experiment_id'], result['baseline']['experiment_id']
    def label(row):
        notes = {best:' · best',current:' · now'}
        note = notes.get(row['experiment_id'],'') if row['feasible'] else ' · too hot'
        return f"{row['setpoint_c']:g} °C{note}"
    x = [label(row) for row in rows]
    opacity = [1 if row['experiment_id'] in (best,current) else .45 for row in rows]
    fig = go.Figure([go.Bar(name='IT equipment',x=x,y=[row['it_kwh'] for row in rows],marker=dict(color='#0f766e',opacity=opacity)),
                     go.Bar(name='Cooling system',x=x,y=[row['cooling_kwh'] for row in rows],marker=dict(color='#60a5d8',opacity=opacity))])
    fig.update_layout(barmode='stack',height=360,margin=dict(l=8,r=8,t=36,b=8),title='Total data-center electricity by setpoint',
                      yaxis_title='kWh',legend=dict(orientation='h',y=-.18),plot_bgcolor='white',paper_bgcolor='rgba(0,0,0,0)')
    fig.update_yaxes(gridcolor='#edf1f6')
    st.plotly_chart(fig,width='stretch')
    if not all(row['feasible'] for row in rows):
        st.caption(f'Bars marked "too hot" put the hottest rack inlet above the {limit:g} °C limit and are never recommended.')


st.title('Cooling Advisor')
st.write('Which cooling setpoint uses the least electricity for the **whole data center** (IT equipment + cooling) '
         'without letting the racks get too hot?')

if not studies:
    st.info('No simulated situations yet. Use "Simulate a new situation" below.')
else:
    preferred = st.session_state.pop('next_study',None)
    if preferred in studies:
        st.session_state.study = preferred
    study_id = st.selectbox('Situation',list(studies),key='study',
                            format_func=lambda sid:f"{studies[sid]['label']} ({studies[sid]['horizon_hours']:g} h)")
    study = studies[study_id]
    limit = (study.get('policy') or {}).get('max_inlet_c',27)
    try:
        result = recommend(study_id)
    except BackendError as exc:
        st.error(str(exc))
        st.stop()
    baseline, selected = result.get('baseline'), result.get('selected')
    if result['status'] != 'completed' or not selected:
        st.warning(result['explanation'])
    else:
        hours = study['horizon_hours']
        saved = baseline['total_kwh']-selected['total_kwh']
        if result['decision'] == 'no_change':
            headline, detail = f"Keep {selected['setpoint_c']:g} °C", 'The current setting already uses the least electricity.'
        else:
            headline = f"Set to {selected['setpoint_c']:g} °C"
            detail = f"Saves {saved:,.0f} kWh ({selected['savings_pct']:.1f}%) over {hours:g} h compared with the current {baseline['setpoint_c']:g} °C."
        st.markdown(f'''<div class="answer"><small>RECOMMENDED SETPOINT</small><h2>{headline}</h2><p>{detail}</p>
<p>Whole data center: {baseline['total_kwh']:,.0f} → {selected['total_kwh']:,.0f} kWh ·
hottest rack inlet {selected['max_inlet_c']:.1f} °C (limit {limit:g} °C)</p></div>''',unsafe_allow_html=True)
        if result['decision'] != 'no_change':
            it, cooling = baseline['it_kwh']-selected['it_kwh'], baseline['cooling_kwh']-selected['cooling_kwh']
            st.write(f"**Why:** compared with {baseline['setpoint_c']:g} °C, IT equipment uses {abs(it):,.0f} kWh "
                     f"{'less' if it >= 0 else 'more'} and the cooling system uses {abs(cooling):,.0f} kWh {'less' if cooling >= 0 else 'more'}.")
        energy_chart(result,limit)
        with st.expander('All setpoints'):
            frame = pd.DataFrame(sorted(result['candidates'],key=lambda row:row['setpoint_c']))
            st.dataframe(pd.DataFrame({'Setpoint (°C)':frame['setpoint_c'],'IT (kWh)':frame['it_kwh'],'Cooling (kWh)':frame['cooling_kwh'],
                                       'Total (kWh)':frame['total_kwh'],'Saving vs now (%)':frame['savings_pct'],
                                       'Hottest inlet (°C)':frame['max_inlet_c'],'Within limit':frame['feasible']}).round(1),
                         hide_index=True,width='stretch')

if settings['simulator']['available']:
    with st.expander('Simulate a new situation'):
        with st.form('simulate'):
            a,b,c = st.columns(3)
            city = a.selectbox('Location',list(CITIES),format_func=CITIES.get)
            day = b.number_input('Day of year',min_value=1,max_value=363,value=182)
            hours = c.select_slider('Hours',options=[6,12,24,48],value=12)
            submit = st.form_submit_button('Run simulation',type='primary')
        if submit:
            try:
                request = SimulationRequest(label=f'{CITIES[city]} · day {day}',city=city,day_of_year=day,hours=hours)
                with st.spinner('Running the SustainDC simulator…'):
                    st.session_state.next_study = backend.post('/simulations',request.model_dump())['scenario_id']
                st.rerun()
            except (ValueError,BackendError) as exc:
                st.error(str(exc))

st.caption('Results come from the SustainDC simulator, not from measurements at a real facility. '
           'This tool only recommends a setting; it never changes any equipment.')
if st.session_state.get('access_token') and st.button('Sign out'):
    st.session_state.clear()
    st.rerun()
