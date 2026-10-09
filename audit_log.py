"""
Module de traçabilité des modifications, conforme aux exigences de la norme
NF EN ISO/CEI 17025 (§7.5 « Enregistrements techniques » et §7.11 « Maîtrise
des données et gestion de l'information »).

Principes appliqués :
- Journal en AJOUT SEUL (append-only) : une entrée du journal n'est jamais
  modifiée ni supprimée, pour garantir l'intégrité de la traçabilité.
- Chaque entrée capture : QUI (utilisateur connecté), QUAND (horodatage
  serveur), QUOI (table, enregistrement, champ modifié, ancienne valeur,
  nouvelle valeur) et l'ACTION (création, modification, validation...).
- Seuls les champs réellement modifiés sont journalisés (comparaison
  ancienne/nouvelle valeur), pour garder un journal lisible et exploitable.

Utilisation type, à chaque écriture en base (dans n'importe quel module :
suivi_betonnage, essai_plaque, suivi_controle_beton, ...) :

    from audit_log import enregistrer_modification

    anciennes_valeurs = {"force_kn": 0.0, "fc_mpa": 0.0}
    nouvelles_valeurs = {"force_kn": 430.3, "fc_mpa": 24.4}
    supabase.table("suivi_controle_beton").update(nouvelles_valeurs).eq("id", ep_id).execute()
    enregistrer_modification(
        supabase,
        table_concernee="suivi_controle_beton",
        enregistrement_id=ep_id,
        action="MODIFICATION",
        anciennes_valeurs=anciennes_valeurs,
        nouvelles_valeurs=nouvelles_valeurs,
    )
"""

import datetime
import streamlit as st

TABLE_JOURNAL = "journal_modifications_iso17025"


def _utilisateur_courant():
    """Identifie l'utilisateur connecté à partir de la session (mêmes clés
    que celles posées par l'authentification de l'application)."""
    user_info = st.session_state.get("user") or {}
    return (
        user_info.get("username")
        or st.session_state.get("username")
        or "inconnu"
    )


def enregistrer_modification(
    supabase,
    table_concernee,
    enregistrement_id,
    action,
    anciennes_valeurs=None,
    nouvelles_valeurs=None,
    commentaire=None,
):
    """Enregistre une (ou plusieurs) entrée(s) de traçabilité.

    - table_concernee : nom de la table Supabase modifiée
      (ex: "suivi_betonnage", "essai_plaque", "suivi_controle_beton").
    - enregistrement_id : identifiant de la ligne modifiée.
    - action : "CREATION", "MODIFICATION", "VALIDATION", "SUPPRESSION"... .
    - anciennes_valeurs / nouvelles_valeurs : dicts {champ: valeur} avant et
      après modification. Seuls les champs dont la valeur a réellement
      changé sont journalisés. Peuvent être omis pour une action globale
      sans détail champ par champ (ex: CREATION).
    - commentaire : texte libre optionnel (ex: motif de rejet).

    Une éventuelle erreur d'écriture du journal ne bloque jamais
    l'opération métier déjà effectuée : elle est seulement signalée par un
    avertissement, car la traçabilité ne doit jamais empêcher le travail
    courant du laboratoire.
    """
    anciennes_valeurs = anciennes_valeurs or {}
    nouvelles_valeurs = nouvelles_valeurs or {}
    utilisateur = _utilisateur_courant()
    horodatage = datetime.datetime.utcnow().isoformat()

    champs_a_comparer = set(anciennes_valeurs.keys()) | set(nouvelles_valeurs.keys())
    lignes = []

    if not champs_a_comparer:
        lignes.append({
            "table_concernee": table_concernee,
            "enregistrement_id": str(enregistrement_id),
            "action": action,
            "champ_modifie": None,
            "ancienne_valeur": None,
            "nouvelle_valeur": None,
            "utilisateur": utilisateur,
            "horodatage": horodatage,
            "commentaire": commentaire,
        })
    else:
        for champ in sorted(champs_a_comparer):
            av = anciennes_valeurs.get(champ)
            nv = nouvelles_valeurs.get(champ)
            if str(av) == str(nv):
                continue  # Valeur inchangée : pas d'entrée de journal inutile
            lignes.append({
                "table_concernee": table_concernee,
                "enregistrement_id": str(enregistrement_id),
                "action": action,
                "champ_modifie": champ,
                "ancienne_valeur": None if av is None else str(av),
                "nouvelle_valeur": None if nv is None else str(nv),
                "utilisateur": utilisateur,
                "horodatage": horodatage,
                "commentaire": commentaire,
            })

    if not lignes:
        return  # Rien n'a réellement changé : inutile de journaliser

    try:
        supabase.table(TABLE_JOURNAL).insert(lignes).execute()
    except Exception as e:
        st.warning(
            "⚠️ Note : la traçabilité ISO 17025 de cette modification n'a"
            f" pas pu être enregistrée ({e}). La modification elle-même a"
            " bien été appliquée."
        )


def afficher_historique_modifications(supabase, table_concernee, enregistrement_id, titre=None):
    """Affiche (dans un expander) l'historique des modifications d'un
    enregistrement donné. À placer par exemple sous chaque formulaire de
    saisie/validation, pour consultation par l'auditeur qualité."""
    import pandas as pd

    with st.expander(titre or "🕓 Historique des modifications (traçabilité ISO 17025)"):
        try:
            res = (
                supabase.table(TABLE_JOURNAL)
                .select("*")
                .eq("table_concernee", table_concernee)
                .eq("enregistrement_id", str(enregistrement_id))
                .order("horodatage", desc=True)
                .execute()
            )
            lignes = res.data or []
        except Exception as e:
            st.warning(f"Historique indisponible : {e}")
            return

        if not lignes:
            st.caption("Aucune modification enregistrée pour cet élément.")
            return

        df = pd.DataFrame([{
            "Date/Heure": str(l.get("horodatage", "-"))[:19].replace("T", " "),
            "Utilisateur": l.get("utilisateur", "-"),
            "Action": l.get("action", "-"),
            "Champ": l.get("champ_modifie") or "-",
            "Ancienne valeur": l.get("ancienne_valeur") or "-",
            "Nouvelle valeur": l.get("nouvelle_valeur") or "-",
            "Commentaire": l.get("commentaire") or "-",
        } for l in lignes])
        st.dataframe(df, use_container_width=True, hide_index=True)
