import streamlit as st

def projet_actif() -> str:
    user = st.session_state["user"]
    pid = st.session_state.get("projet_actif")
    if user["role"] != "admin" and pid not in user.get("projets_autorises", []):
        st.error("⛔ Accès refusé à ce projet.")
        st.stop()
    return pid

def select(sb, table, cols="*"):
    return sb.table(table).select(cols).eq("projet_id", projet_actif())

def insert(sb, table, row: dict):
    return sb.table(table).insert({**row, "projet_id": projet_actif()})

def update(sb, table, values: dict):
    return sb.table(table).update(values).eq("projet_id", projet_actif())

def delete(sb, table):
    return sb.table(table).delete().eq("projet_id", projet_actif())
