import streamlit as st
import pandas as pd
from datetime import datetime, date
from audit_log import enregistrer_modification, afficher_historique_modifications
import projets_config


def configurer_acces_terrain(supabase_client):
    """Ajoute le code attendu par la politique RLS de suivi_betonnage.

    Le client est créé dans app.py. Selon la version de supabase-py, les
    en-têtes HTTP sont exposés à des emplacements différents ; cette fonction
    les met donc à jour de façon compatible avant chaque utilisation du module.
    """
    if supabase_client is None:
        return False

    code_acces = st.secrets.get("CODE_ACCES_TERRAIN", "lpee2026")
    entete = {"x-code-acces-terrain": str(code_acces)}
    cibles = []

    postgrest = getattr(supabase_client, "postgrest", None)
    if postgrest is not None:
        cibles.extend([
            getattr(postgrest, "session", None),
            getattr(postgrest, "_client", None),
            postgrest,
        ])

    configure = False
    for cible in cibles:
        headers = getattr(cible, "headers", None)
        if headers is not None:
            try:
                headers.update(entete)
                configure = True
            except Exception:
                pass
    return configure


def show(supabase):
    st.title("🏗️ Suivi et Contrôle Qualité Béton")

    # La table suivi_betonnage est protégée par une politique RLS Supabase.
    # La configuration est répétée ici pour couvrir les clients recréés après
    # une reconnexion Streamlit ou une synchronisation hors ligne.
    configurer_acces_terrain(supabase)
    
    # Récupération des informations de session
    user_info = st.session_state.get("user", {})
    username = user_info.get("username", "")
    role = st.session_state.get("role", "")
    can_edit = st.session_state.get("can_edit", False) or (user_info.get("can_edit", False))
    is_admin = (role == "admin")
    is_baallal_admin = str(username).strip().upper() == "BAALLAL" and is_admin

    # Projet actif : toutes les lectures/écritures de cette page sont
    # limitées à ce projet, pour garantir l'étanchéité entre chantiers.
    projet_id_actif = projets_config.projet_actif(user_info)
    if not projet_id_actif:
        st.error("⚠️ Aucun projet ne vous est autorisé. Contactez un administrateur.")
        return
    st.caption(f"📁 Projet actif : **{projets_config.nom_projet(projet_id_actif)}**")
    
    # ---------------------------------------------------------
    # 1. FORMULAIRE DE SAISIE
    # ---------------------------------------------------------
    st.subheader("Saisie d'un contrôle")
    
    date_livraison = st.date_input("Date de livraison", value=date.today(), key="saisie_date")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        technicien = st.text_input("Nom du Technicien LPEE", value="Agent LPEE", key="saisie_tech")
        bl_num = st.text_input("N° BL", value="BL-2026-001", key="saisie_bl")
        ouvrage = st.text_input("Ouvrage", value="Voile / Semelle", key="saisie_ouvrage")
        quantite_m3 = st.number_input("Quantité (m³)", min_value=0.0, value=8.0, step=0.5, key="saisie_qte")
        
    with col2:
        client = st.text_input("Client", value="TGCC", disabled=True, key="saisie_client")
        
        # Saisie des heures
        heure_fin = st.time_input("Heure de fin de production", value=datetime.strptime("08:00", "%H:%M").time(), key="saisie_h_fin")
        heure_arrivee = st.time_input("Heure d'arrivée au chantier", value=datetime.strptime("08:35", "%H:%M").time(), key="saisie_h_arr")
        
        # Calcul sécurisé de la durée en minutes
        dt_fin = datetime.combine(date.today(), heure_fin)
        dt_arr = datetime.combine(date.today(), heure_arrivee)
        duree_minutes = int((dt_arr - dt_fin).total_seconds() / 60)
        if duree_minutes < 0:
            duree_minutes += 1440  # Passage de minuit si besoin
        
        st.text_input("Durée de transport / attente (min)", value=f"{duree_minutes} min", disabled=True, key="saisie_duree")
        
        classe_beton = st.selectbox(
            "Classe", 
            ["C25/30", "C30/37", "C35/45", "C40/50", "C45/55"],
            key="saisie_classe"
        )
        
    with col3:
        centrale = st.text_input("Centrale à Béton", value="TG PREFA", key="saisie_centrale")
        meteo = st.selectbox("Météo", ["Ensoleillé ☀️", "Nuageux ☁️", "Pluie 🌧️"], key="saisie_meteo")
        
        temp_beton = st.number_input("Température du Béton (°C)", value=20.0, step=0.1, format="%.1f", key="saisie_t_beton")
        temp_ambiante = st.number_input("Température Ambiante (°C)", value=25.0, step=0.1, format="%.1f", key="saisie_t_amb")
        affaissement = st.number_input("Affaissement (mm)", min_value=0, value=150, step=10, key="saisie_aff")
        
        prelevement = st.selectbox(
            "Prélèvement", 
            ["OUI", "NON"],
            key="saisie_prel"
        )
        
        is_non_prelevement = (prelevement == "NON")
        
        nb_eprouvettes = st.number_input(
            "Nb d'éprouvettes", 
            min_value=0, 
            value=0 if is_non_prelevement else 6,
            disabled=is_non_prelevement,
            key="saisie_eprov"
        )

    observations = st.text_area("Observations", value="Béton conforme", key="saisie_obs")

    # Bouton Enregistrer
    if st.button("💾 Enregistrer", key="btn_enregistrer"):
        bl_clean = bl_num.strip()
        
        if not bl_clean:
            st.error("⚠️ Le numéro BL ne peut pas être vide.")
        else:
            # Vérification si le N° BL existe déjà en base (au sein de ce projet)
            check_bl = supabase.table("suivi_betonnage").select("id").eq("bl_num", bl_clean).eq("projet_id", projet_id_actif).execute()
            
            if check_bl.data:
                st.error(f"❌ Le N° BL **{bl_clean}** existe déjà. Impossible d'ajouter un doublon !")
            else:
                data = {
                    "date_livraison": str(date_livraison),
                    "bl_num": bl_clean,
                    "ouvrage": ouvrage,
                    "quantite_m3": float(quantite_m3),
                    "client": client,
                    "classe_beton": classe_beton,
                    "centrale_beton": centrale,
                    "meteo": meteo,
                    "heure_fin_coulage": heure_fin.strftime("%H:%M"),
                    "heure_arrivee": heure_arrivee.strftime("%H:%M"),
                    "temperature": float(temp_beton),
                    "temperature_ambiante": float(temp_ambiante),
                    "affaissement": int(affaissement),
                    "prelevement": prelevement,
                    "nb_eprouvettes": 0 if prelevement == "NON" else int(nb_eprouvettes),
                    "observations": observations,
                    "technicien": technicien,
                    "projet_id": projet_id_actif,
                }
                
                try:
                    res_ins = supabase.table("suivi_betonnage").insert(data).execute()
                    if res_ins.data:
                        nouvel_id = res_ins.data[0].get("id")
                        enregistrer_modification(
                            supabase,
                            table_concernee="suivi_betonnage",
                            enregistrement_id=nouvel_id,
                            action="CREATION",
                            nouvelles_valeurs=data,
                        )
                    st.success("Enregistrement réussi !")
                    st.rerun()
                except Exception as e:
                    if "row-level security" in str(e).lower():
                        st.error(
                            "Enregistrement refusé par la sécurité Supabase. "
                            "Vérifiez que CODE_ACCES_TERRAIN est configuré dans "
                            "les secrets Streamlit avec la même valeur que la "
                            "politique RLS de suivi_betonnage."
                        )
                    else:
                        st.error(f"Erreur d'enregistrement : {e}")

    # ---------------------------------------------------------
    # 2. AFFICHAGE DE L'HISTORIQUE ET ESPACE DE MODIFICATION
    # ---------------------------------------------------------
    st.markdown("---")
    st.subheader("📊 Historique")
    
    try:
        res = supabase.table("suivi_betonnage").select("*").eq("projet_id", projet_id_actif).order("id", desc=True).execute()
        if res.data:
            df = pd.DataFrame(res.data)
            
            # Nettoyage d'affichage de la colonne "prelevement" et mise à zéro systématique si NON
            if "prelevement" in df.columns:
                df["prelevement"] = df["prelevement"].apply(lambda x: "OUI" if "OUI" in str(x).upper() else ("NON" if "NON" in str(x).upper() else str(x)))
                
                # RÈGLE : Si Prélèvement est NON, Nb Éprouvettes doit être systématiquement 0
                if "nb_eprouvettes" in df.columns:
                    df.loc[df["prelevement"] == "NON", "nb_eprouvettes"] = 0

            # 1. Calcul de la colonne "Durée de transport (min)"
            if "heure_fin_coulage" in df.columns and "heure_arrivee" in df.columns:
                def calculer_duree(row):
                    try:
                        str_fin = str(row["heure_fin_coulage"]).split(".")[0]
                        str_arr = str(row["heure_arrivee"]).split(".")[0]
                        
                        fmt = "%H:%M:%S" if len(str_fin.split(":")) == 3 else "%H:%M"
                        h_fin = datetime.strptime(str_fin, fmt)
                        
                        fmt_arr = "%H:%M:%S" if len(str_arr.split(":")) == 3 else "%H:%M"
                        h_arr = datetime.strptime(str_arr, fmt_arr)
                        
                        diff = int((h_arr - h_fin).total_seconds() / 60)
                        if diff < 0:
                            diff += 1440
                        return diff
                    except:
                        return None
                
                df["Durée de transport"] = df.apply(calculer_duree, axis=1)

            # 2. Masquer les colonnes secondaires
            cols_to_drop = [
                col for col in ["created_at", "created", "heure_fin_coulage", "heure_fin", "client", "centrale_beton"] 
                if col in df.columns
            ]
            if cols_to_drop:
                df = df.drop(columns=cols_to_drop)

            # 3. Renommage des colonnes
            df = df.rename(columns={
                "id": "ID",
                "date_livraison": "Date Livraison",
                "heure_arrivee": "Heure d'arrivée",
                "bl_num": "N° BL",
                "ouvrage": "Ouvrage",
                "quantite_m3": "Quantité (m³)",
                "classe_beton": "Classe",
                "temperature": "Temp. Béton",
                "temperature_ambiante": "Temp. Ambiante",
                "affaissement": "Affaissement",
                "prelevement": "Prélèvement",
                "nb_eprouvettes": "Nb Éprouvettes",
                "observations": "Observations",
                "technicien": "Technicien",
                "meteo": "Météo"
            })

            # 4. Positionnement spécifique des colonnes
            desired_first_cols = ["ID", "Date Livraison", "N° BL", "Nb Éprouvettes"]
            remaining_cols = [c for c in df.columns if c not in desired_first_cols]
            
            final_cols = [c for c in desired_first_cols if c in df.columns] + remaining_cols
            df = df[final_cols]

            # 5. Style d'alerte rouge pour les durées > 120 min
            def style_duree_transport(val):
                if pd.notna(val) and isinstance(val, (int, float)) and val > 120:
                    return 'background-color: #ffcdd2; color: #b71c1c; font-weight: bold;'
                return ''

            # Dictionnaire de formatage des nombres
            format_dict = {
                "Durée de transport": lambda x: f"{int(x)} min" if pd.notna(x) else "-",
                "Quantité (m³)": "{:.0f}",
                "Temp. Béton": "{:.1f}",
                "Temp. Ambiante": "{:.1f}",
                "Nb Éprouvettes": "{:.0f}"
            }

            styled_df = df.style.map(style_duree_transport, subset=["Durée de transport"]).format(format_dict)

            # 6. Affichage du tableau stylisé
            st.dataframe(styled_df, use_container_width=True, hide_index=True)

            # --- BLOC MODIFICATION ---
            if is_admin or can_edit:
                st.markdown("---")
                st.subheader("🛠️ Espace Administration - Suivi Béton")
                
                record_options = {f"ID {r['id']} - BL: {r.get('bl_num', 'N/A')} - Ouvrage: {r.get('ouvrage', '')}": r for r in res.data}
                selected_key = st.selectbox("Sélectionner l'enregistrement à gérer", list(record_options.keys()), key="admin_select_record")
                selected_item = record_options[selected_key]
                rec_id = selected_item["id"]
                
                if is_admin:
                    col_ed, col_del = st.columns([2, 1])
                else:
                    col_ed = st.container()

                # --- BLOC DE MODIFICATION ---
                with col_ed:
                    with st.expander("📝 Modifier ce contrôle (Tous les champs)", expanded=True):
                        with st.form(f"edit_form_beton_{rec_id}"):
                            try:
                                def_date = datetime.strptime(str(selected_item.get("date_livraison", date.today())), "%Y-%m-%d").date()
                            except:
                                def_date = date.today()

                            def parse_heure_safe(val_str, default_str):
                                try:
                                    clean_str = str(val_str).split(".")[0]
                                    fmt = "%H:%M:%S" if len(clean_str.split(":")) == 3 else "%H:%M"
                                    return datetime.strptime(clean_str, fmt).time()
                                except:
                                    return datetime.strptime(default_str, "%H:%M").time()

                            def_h_fin = parse_heure_safe(selected_item.get("heure_fin_coulage"), "08:00")
                            def_h_arr = parse_heure_safe(selected_item.get("heure_arrivee"), "08:35")

                            new_date_livraison = st.date_input("Date de livraison", value=def_date, key=f"edit_date_{rec_id}")
                            new_technicien = st.text_input("Nom du Technicien LPEE", value=selected_item.get("technicien", "Agent LPEE"), key=f"edit_tech_{rec_id}")
                            new_bl = st.text_input("N° BL", value=selected_item.get("bl_num", ""), key=f"edit_bl_{rec_id}")
                            new_ouvrage = st.text_input("Ouvrage", value=selected_item.get("ouvrage", ""), key=f"edit_ouvrage_{rec_id}")
                            new_quantite = st.number_input("Quantité (m³)", value=float(selected_item.get("quantite_m3", 0.0)), key=f"edit_qte_{rec_id}")
                            
                            new_heure_fin = st.time_input("Heure de fin de production", value=def_h_fin, key=f"edit_h_fin_{rec_id}")
                            new_heure_arrivee = st.time_input("Heure d'arrivée au chantier", value=def_h_arr, key=f"edit_h_arr_{rec_id}")
                            
                            classes_list = ["C25/30", "C30/37", "C35/45", "C40/50", "C45/55"]
                            current_classe = selected_item.get("classe_beton", "C25/30")
                            idx_classe = classes_list.index(current_classe) if current_classe in classes_list else 0
                            new_classe = st.selectbox("Classe", classes_list, index=idx_classe, key=f"edit_classe_{rec_id}")
                            
                            new_centrale = st.text_input("Centrale à Béton", value=selected_item.get("centrale_beton", "TG PREFA"), key=f"edit_centrale_{rec_id}")
                            
                            meteo_list = ["Ensoleillé ☀️", "Nuageux ☁️", "Pluie 🌧️"]
                            current_meteo = selected_item.get("meteo", "Ensoleillé ☀️")
                            idx_meteo = meteo_list.index(current_meteo) if current_meteo in meteo_list else 0
                            new_meteo = st.selectbox("Météo", meteo_list, index=idx_meteo, key=f"edit_meteo_{rec_id}")
                            
                            new_temp_beton = st.number_input("Température du Béton (°C)", value=float(selected_item.get("temperature", 20.0)), step=0.1, format="%.1f", key=f"edit_t_beton_{rec_id}")
                            new_temp_amb = st.number_input("Température Ambiante (°C)", value=float(selected_item.get("temperature_ambiante", 25.0)), step=0.1, format="%.1f", key=f"edit_t_amb_{rec_id}")
                            new_affaissement = st.number_input("Affaissement (mm)", value=int(selected_item.get("affaissement", 150)), step=10, key=f"edit_aff_{rec_id}")
                            
                            prelevement_list = ["OUI", "NON"]
                            raw_prel = str(selected_item.get("prelevement", "NON")).upper()
                            current_prel = "OUI" if "OUI" in raw_prel else "NON"
                            idx_prel = prelevement_list.index(current_prel)
                            new_prelevement = st.selectbox("Prélèvement", prelevement_list, index=idx_prel, key=f"edit_prel_{rec_id}")
                            
                            def_eprov = 0 if new_prelevement == "NON" else int(selected_item.get("nb_eprouvettes", 0))
                            new_nb_eprouvettes = st.number_input("Nb d'éprouvettes", value=def_eprov, min_value=0, key=f"edit_eprov_{rec_id}")
                            new_observations = st.text_area("Observations", value=selected_item.get("observations", ""), key=f"edit_obs_{rec_id}")
                            
                            if st.form_submit_button("💾 Enregistrer toutes les modifications"):
                                new_bl_clean = new_bl.strip()
                                
                                check_bl_edit = supabase.table("suivi_betonnage").select("id").eq("bl_num", new_bl_clean).eq("projet_id", projet_id_actif).neq("id", rec_id).execute()
                                
                                if check_bl_edit.data:
                                    st.error(f"❌ Le N° BL **{new_bl_clean}** est déjà utilisé par un autre enregistrement.")
                                else:
                                    try:
                                        nouvelles_valeurs_beton = {
                                            "date_livraison": str(new_date_livraison),
                                            "technicien": new_technicien,
                                            "bl_num": new_bl_clean,
                                            "ouvrage": new_ouvrage,
                                            "quantite_m3": float(new_quantite),
                                            "heure_fin_coulage": new_heure_fin.strftime("%H:%M"),
                                            "heure_arrivee": new_heure_arrivee.strftime("%H:%M"),
                                            "classe_beton": new_classe,
                                            "centrale_beton": new_centrale,
                                            "meteo": new_meteo,
                                            "temperature": float(new_temp_beton),
                                            "temperature_ambiante": float(new_temp_amb),
                                            "affaissement": int(new_affaissement),
                                            "prelevement": new_prelevement,
                                            "nb_eprouvettes": 0 if new_prelevement == "NON" else int(new_nb_eprouvettes),
                                            "observations": new_observations
                                        }
                                        anciennes_valeurs_beton = {
                                            k: selected_item.get(k) for k in nouvelles_valeurs_beton
                                        }
                                        supabase.table("suivi_betonnage").update(nouvelles_valeurs_beton).eq("id", rec_id).execute()
                                        enregistrer_modification(
                                            supabase,
                                            table_concernee="suivi_betonnage",
                                            enregistrement_id=rec_id,
                                            action="MODIFICATION",
                                            anciennes_valeurs=anciennes_valeurs_beton,
                                            nouvelles_valeurs=nouvelles_valeurs_beton,
                                        )

                                        st.success("Modifications enregistrées avec succès !")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"Erreur de mise à jour : {e}")

                # --- BLOC DE SUPPRESSION ---
                if is_admin:
                    with col_del:
                        st.markdown("##### ⚠️ Suppression")
                        if st.button("🗑️ Supprimer définitivement ce contrôle", type="primary", key=f"btn_supprimer_admin_{rec_id}"):
                            try:
                                enregistrer_modification(
                                    supabase,
                                    table_concernee="suivi_betonnage",
                                    enregistrement_id=rec_id,
                                    action="SUPPRESSION",
                                    anciennes_valeurs={k: v for k, v in selected_item.items() if k != "id"},
                                    commentaire="Suppression définitive de l'enregistrement",
                                )
                                supabase.table("suivi_betonnage").delete().eq("id", rec_id).execute()
                                st.success("Enregistrement supprimé avec succès.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Erreur de suppression : {e}")

                if is_baallal_admin:
                    afficher_historique_modifications(supabase, "suivi_betonnage", rec_id)

        else:
            st.info("Aucune donnée enregistrée pour le moment.")
            
    except Exception as e:
        st.error(f"Erreur lors de la récupération de l'historique : {e}")
