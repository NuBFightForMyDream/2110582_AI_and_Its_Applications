# Dashboard

[Architecture index](../../architecture.md)

Streamlit and Plotly provide eight views: Overview, Analysis workspace, Simulation lab, Data imports, Agents & skills, Case history, Monitoring and Settings. All UI labels are English. FastAPI is called through `gateway.py` with bearer authentication.

The analysis workspace distinguishes offline Python execution from Gemini tool calling and requests explicit consent before sending case summaries/tool evidence externally. Source banners distinguish upstream simulated studies, synthetic fixtures and source-unverified imports. Results show total/IT/cooling/component energy, matched options, maximum inlet, study threshold, trace and limitations. JSON/CSV export and human review records support inspection.

Case history reopens persisted cases. Monitoring reads the current owner's recent cases rather than global tenant data. Settings shows readiness booleans and setup instructions, not API key values. Supabase mode adds email/password sign-in; expired sessions require sign-in again. No physical control button or autonomous action approval flow exists.
