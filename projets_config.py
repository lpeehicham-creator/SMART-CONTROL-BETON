"""
Gestion des projets/chantiers et séparation des données par projet.

Les chantiers sont stockés dans la table Supabase `projets` (ils ne sont plus
écrits en dur) : un responsable de chantier peut donc en créer de nouveaux
depuis la plateforme. Le registre est gardé dans st.session_state (jamais
dans une variable globale du module, partagée entre tous les utilisateurs).
"""

import re
import unicodedata

import streamlit as st

PROJET_PAR_DEFAUT = "LGV_CASA_SUD"

# Repli si la table `projets` est inaccessible (jamais d'écriture depuis ici).
_PROJETS_REPLI = {
    "LGV_CASA_SUD": {"nom": "LGV CASA SUD", "client": "TGCC"},
    "GARE_LGV_CASA_SUD": {"nom": "Gare LGV Casa Sud", "client": "SOGEA"},
}

ROLES_CREATEURS_PROJET = ("admin", "responsable_chantier")


# ==============================================================================
# REGISTRE DES PROJETS (table Supabase `projets`)
# ==============================================================================
def charger_projets(supabase, force=False):
    """Charge le registre depuis Supabase dans la session (1 appel par session,
    sauf force=True, par exemple après la création d'un chantier)."""
    if not force and "projets_registre" in st.session_state:
        return st.session_state["projets_registre"]
    registre = {}
    if supabase:
        try:
            res = supabase.table("projets").select("*").execute()
            for row in res.data or []:
                if row.get("actif", True):
                    registre[row["id"]] = {
                        "nom": row.get("nom") or row["id"],
                        "client": row.get("client") or "-",
                        "cree_par": row.get("cree_par"),
                    }
        except Exception:
            registre = {}
    if not registre:
        registre = {k: dict(v) for k, v in _PROJETS_REPLI.items()}
    st.session_state["projets_registre"] = registre
    return registre


def get_projets():
    """Registre des projets de la session (dict id -> {nom, client, ...})."""
    return st.session_state.get("projets_registre") or {
        k: dict(v) for k, v in _PROJETS_REPLI.items()
    }


def _slug_projet(nom):
    s = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()
    return s[:40]


def creer_projet(supabase, nom, client, createur):
    """Crée un chantier. Retourne (True, projet_id) ou (False, message).
    Réservé aux rôles admin / responsable_chantier (vérifié ici, pas
    seulement dans l'interface)."""
    user = st.session_state.get("user") or {}
    if user.get("role") not in ROLES_CREATEURS_PROJET:
        return False, "Votre rôle ne permet pas de créer un chantier."
    nom = (nom or "").strip()
    client = (client or "").strip()
    if len(nom) < 3:
        return False, "Le nom du chantier doit contenir au moins 3 caractères."
    if not client:
        return False, "Le client est obligatoire."
    pid = _slug_projet(nom)
    if not pid:
        return False, "Nom de chantier invalide."
    if not supabase:
        return False, "Client Supabase non configuré."
    try:
        existe = supabase.table("projets").select("id").eq("id", pid).execute()
        if existe.data:
            return False, f"Un chantier avec l'identifiant {pid} existe déjà."
        supabase.table("projets").insert(
            {"id": pid, "nom": nom, "client": client, "cree_par": createur, "actif": True}
        ).execute()
    except Exception as e:
        return False, f"Erreur Supabase : {e}"
    charger_projets(supabase, force=True)
    return True, pid


# ==============================================================================
# ACCÈS PAR UTILISATEUR
# ==============================================================================
def liste_projets_utilisateur(user_info):
    """Projets auxquels l'utilisateur a accès (l'admin les voit tous).
    Les identifiants inconnus du registre sont ignorés."""
    if not user_info:
        return []
    registre = get_projets()
    if user_info.get("role") == "admin":
        return list(registre.keys())
    return [p for p in (user_info.get("projets_autorises") or []) if p in registre]


def projet_actif(user_info):
    """Projet actif de la session (choisi via le sélecteur), avec repli sur
    le premier projet autorisé."""
    projets_dispo = liste_projets_utilisateur(user_info)
    if not projets_dispo:
        return None
    choix = st.session_state.get("projet_actif")
    if choix in projets_dispo:
        return choix
    return projets_dispo[0]


def afficher_selecteur_projet(user_info):
    """Sélecteur de projet actif (barre latérale). Silencieux s'il n'y a
    qu'un seul projet, mais fixe quand même le projet actif en session."""
    projets_dispo = liste_projets_utilisateur(user_info)
    registre = get_projets()
    if not projets_dispo:
        st.session_state["projet_actif"] = None
        return
    if len(projets_dispo) == 1:
        st.session_state["projet_actif"] = projets_dispo[0]
        return

    labels = [f"{registre[p]['nom']} ({registre[p]['client']})" for p in projets_dispo]
    courant = st.session_state.get("projet_actif")
    index_defaut = projets_dispo.index(courant) if courant in projets_dispo else 0

    st.markdown("---")
    choix_label = st.selectbox(
        "📁 Projet actif", labels, index=index_defaut, key="selecteur_projet_actif"
    )
    st.session_state["projet_actif"] = projets_dispo[labels.index(choix_label)]


def nom_projet(projet_id):
    """Libellé lisible d'un identifiant de projet (pour affichage)."""
    info = get_projets().get(projet_id)
    return info["nom"] if info else (projet_id or "-")


def filtrer_par_projet(query, user_info, colonne="projet_id"):
    """Filtre `.in_(colonne, [...])` selon les projets autorisés. Aucun
    projet autorisé => filtre impossible (jamais "tout")."""
    if not user_info:
        return query.in_(colonne, ["__aucun_projet_autorise__"])
    projets = liste_projets_utilisateur(user_info)
    if not projets:
        return query.in_(colonne, ["__aucun_projet_autorise__"])
    return query.in_(colonne, projets)


def filtrer_projet_actif(query, colonne="projet_id"):
    """Restreint une requête Supabase au SEUL projet actif de la session.
    À utiliser sur toute lecture faite à partir d'un identifiant (id,
    betonnage_id, scan QR...)."""
    pid = projet_actif(st.session_state.get("user") or {})
    if not pid:
        return query.eq(colonne, "__aucun_projet_autorise__")
    return query.eq(colonne, pid)
