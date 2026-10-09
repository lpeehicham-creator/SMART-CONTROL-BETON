"""
Configuration centrale des projets/chantiers et fonctions utilitaires pour
appliquer la séparation des données par projet dans tous les modules
(Suivi de Bétonnage, Essai à la Plaque, Suivi Contrôle Béton, Synthèse
Béton, Synthèse Plaque, Historique Complet & PVs).

Module séparé (plutôt que défini dans app.py) car les modules de vues
(views/*.py) ne peuvent pas importer app.py sans créer une dépendance
circulaire (c'est app.py qui les importe).
"""

import streamlit as st

# ==============================================================================
# REGISTRE DES PROJETS
# ==============================================================================
# Pour ajouter un nouveau chantier plus tard : ajouter une entrée ici, puis
# cocher ce projet pour les utilisateurs concernés dans "Gestion Utilisateurs".
PROJETS = {
    "LGV_CASA_SUD": {"nom": "LGV CASA SUD", "client": "TGCC"},
    "GARE_LGV_CASA_SUD": {"nom": "Gare LGV Casa Sud", "client": "SOGEA"},
}

PROJET_PAR_DEFAUT = "LGV_CASA_SUD"


def liste_projets_utilisateur(user_info):
    """Retourne la liste des identifiants de projets auxquels l'utilisateur
    connecté a accès. Un administrateur a accès à tous les projets."""
    if not user_info:
        return []
    if user_info.get("role") == "admin":
        return list(PROJETS.keys())
    return user_info.get("projets_autorises") or []


def projet_actif(user_info):
    """Identifiant du projet actuellement actif pour cette session (choisi
    via le sélecteur si l'utilisateur a accès à plusieurs projets), avec
    repli sur le premier projet autorisé."""
    projets_dispo = liste_projets_utilisateur(user_info)
    if not projets_dispo:
        return None
    choix = st.session_state.get("projet_actif")
    if choix in projets_dispo:
        return choix
    return projets_dispo[0]


def afficher_selecteur_projet(user_info):
    """Affiche un sélecteur de projet actif (barre latérale) si
    l'utilisateur a accès à plusieurs projets. N'affiche rien s'il n'a
    accès qu'à un seul projet (rien à choisir), mais fixe quand même
    correctement le projet actif en session."""
    projets_dispo = liste_projets_utilisateur(user_info)
    if not projets_dispo:
        st.session_state["projet_actif"] = None
        return
    if len(projets_dispo) == 1:
        st.session_state["projet_actif"] = projets_dispo[0]
        return

    labels = [f"{PROJETS[p]['nom']} ({PROJETS[p]['client']})" for p in projets_dispo]
    courant = st.session_state.get("projet_actif")
    index_defaut = projets_dispo.index(courant) if courant in projets_dispo else 0

    st.markdown("---")
    choix_label = st.selectbox(
        "📁 Projet actif", labels, index=index_defaut, key="selecteur_projet_actif"
    )
    st.session_state["projet_actif"] = projets_dispo[labels.index(choix_label)]


def nom_projet(projet_id):
    """Libellé lisible d'un identifiant de projet (pour affichage)."""
    info = PROJETS.get(projet_id)
    return info["nom"] if info else (projet_id or "-")


def filtrer_par_projet(query, user_info, colonne="projet_id"):
    """Applique un filtre `.in_(colonne, [...])` à une requête Supabase
    (query builder) selon les projets autorisés de l'utilisateur. Ne filtre
    rien pour un administrateur (accès à tout). Renvoie la requête
    (éventuellement filtrée) pour chaînage."""
    if not user_info or user_info.get("role") == "admin":
        return query
    projets = liste_projets_utilisateur(user_info)
    if not projets:
        # Aucun projet autorisé : ne doit rien voir. On force un filtre qui
        # ne peut jamais correspondre plutôt que de renvoyer la requête
        # non filtrée (qui montrerait tout par erreur).
        return query.in_(colonne, ["__aucun_projet_autorise__"])
    return query.in_(colonne, projets)
