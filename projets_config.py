"""
Gestion des projets/chantiers et séparation des données par projet.

Les chantiers sont stockés dans la table Supabase `projets` (ils ne sont plus
écrits en dur) : un responsable de chantier peut donc en créer de nouveaux
depuis la plateforme. Le registre est gardé dans st.session_state (jamais
dans une variable globale du module, partagée entre tous les utilisateurs).
"""

import json
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

# ------------------------------------------------------------------------------
# SIGNATAIRES DES PV (propres à chaque chantier)
# ------------------------------------------------------------------------------
FONCTIONS_SIGNATAIRES = ("Chef de laboratoire", "Responsable d'essai", "Coordinateur d'essai")
MAX_SIGNATAIRES = 3
# Intitulé de la case de visa sur le PV (celui de « Chef de laboratoire » est
# conservé tel qu'il figurait déjà sur les PV de LGV CASA SUD)
_TITRES_VISA = {
    "Chef de laboratoire": "Visa Chef du laboratoire",
    "Responsable d'essai": "Visa Responsable d'essai",
    "Coordinateur d'essai": "Visa Coordinateur d'essai",
}
# Ordre d'affichage sur le PV, de gauche à droite
_ORDRE_VISA = ("Responsable d'essai", "Coordinateur d'essai", "Chef de laboratoire")
# Signataires historiques de LGV CASA SUD (repris tant qu'aucun n'est enregistré)
SIGNATAIRES_LGV = [
    {"nom": "O.IKKEN", "fonction": "Responsable d'essai"},
    {"nom": "H.BAALLAL", "fonction": "Chef de laboratoire"},
]


def nettoyer_signataires(brut):
    """Liste [{"nom", "fonction"}] valide (nom non vide, fonction reconnue,
    3 maximum). Accepte une liste ou un JSON sous forme de texte."""
    if isinstance(brut, str):
        try:
            brut = json.loads(brut)
        except Exception:
            return []
    if not isinstance(brut, list):
        return []
    res = []
    for e in brut:
        if not isinstance(e, dict):
            continue
        nom = str(e.get("nom") or "").strip()
        fonction = str(e.get("fonction") or "").strip()
        if nom and fonction in FONCTIONS_SIGNATAIRES:
            res.append({"nom": nom, "fonction": fonction})
    return res[:MAX_SIGNATAIRES]


# ==============================================================================
# REGISTRE DES PROJETS (table Supabase `projets`)
# ==============================================================================
def charger_projets(supabase, force=False):
    """Charge le registre depuis Supabase dans la session (1 appel par session,
    sauf force=True, par exemple après la création d'un chantier)."""
    if not force and "projets_registre" in st.session_state:
        return st.session_state["projets_registre"]
    registre = {}
    source = "base"
    try:
        if not supabase:
            raise RuntimeError("Supabase non configuré")
        res = supabase.table("projets").select("*").execute()
        for row in res.data or []:
            if row.get("actif", True):
                registre[row["id"]] = {
                    "nom": row.get("nom") or row["id"],
                    "client": row.get("client") or "-",
                    "num_dossier": row.get("num_dossier") or "",
                    "intitule": row.get("intitule") or "",
                    "signataires": nettoyer_signataires(row.get("signataires")),
                    "cree_par": row.get("cree_par"),
                }
    except Exception:
        # Table `projets` inaccessible (absente, réseau...) : registre de
        # secours en lecture seule. Une table lue avec succès mais VIDE n'est
        # PAS remplacée par le secours (sinon un chantier supprimé
        # réapparaîtrait).
        registre = {k: dict(v) for k, v in _PROJETS_REPLI.items()}
        source = "repli"
    st.session_state["projets_registre_source"] = source
    st.session_state["projets_registre"] = registre
    return registre


def get_projets():
    """Registre des projets de la session (dict id -> {nom, client, ...})."""
    if "projets_registre" in st.session_state:
        return st.session_state["projets_registre"]
    return {k: dict(v) for k, v in _PROJETS_REPLI.items()}


def registre_en_secours():
    """True si la table `projets` n'a pas pu être lue (registre de secours)."""
    return st.session_state.get("projets_registre_source") == "repli"


def _slug_projet(nom):
    s = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_").upper()
    return s[:40]


def creer_projet(supabase, nom, client, createur, num_dossier="", intitule="", signataires=None):
    """Crée un chantier. Retourne (True, projet_id) ou (False, message).
    Réservé aux rôles admin / responsable_chantier (vérifié ici, pas
    seulement dans l'interface)."""
    user = st.session_state.get("user") or {}
    if user.get("role") not in ROLES_CREATEURS_PROJET:
        return False, "Votre rôle ne permet pas de créer un chantier."
    nom = (nom or "").strip()
    client = (client or "").strip()
    num_dossier = (num_dossier or "").strip()
    intitule = (intitule or "").strip()
    signataires = nettoyer_signataires(signataires)
    if len(nom) < 3:
        return False, "Le nom du chantier doit contenir au moins 3 caractères."
    if not client:
        return False, "Le client est obligatoire."
    if not num_dossier:
        return False, "Le N° de dossier est obligatoire."
    if not signataires:
        return False, ("Indiquez au moins une personne qui signe les PV "
                       "(nom et fonction).")
    pid = _slug_projet(nom)
    if not pid:
        return False, "Nom de chantier invalide."
    if not supabase:
        return False, "Client Supabase non configuré."
    try:
        existe = supabase.table("projets").select("id, cree_par").eq("id", pid).execute()
        if existe.data:
            if existe.data[0].get("cree_par") == createur:
                # Le chantier a déjà été créé par cette même personne (création
                # précédente dont l'accès n'avait pas été enregistré) : on le
                # reprend au lieu de bloquer.
                st.session_state["_creation_reprise"] = True
                charger_projets(supabase, force=True)
                return True, pid
            return False, (f"Un chantier avec l'identifiant {pid} existe déjà"
                           " (créé par une autre personne). Choisissez un autre nom.")
        ligne = {"id": pid, "nom": nom, "client": client, "num_dossier": num_dossier,
                 "cree_par": createur, "actif": True}
        ligne["signataires"] = signataires
        if intitule:  # colonne facultative : n'est envoyée que si renseignée
            ligne["intitule"] = intitule
        supabase.table("projets").insert(ligne).execute()
    except Exception as e:
        return False, _msg_erreur_projets(e)
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
    projets = [p for p in (user_info.get("projets_autorises") or []) if p in registre]
    # Un responsable de chantier voit toujours les chantiers qu'il a créés,
    # même si l'enregistrement de son accès en base a échoué.
    if user_info.get("role") == "responsable_chantier":
        for pid, info in registre.items():
            if info.get("cree_par") and info.get("cree_par") == user_info.get("username") \
                    and pid not in projets:
                projets.append(pid)
    return projets


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

    labels = [libelle_projet(p) for p in projets_dispo]
    courant = st.session_state.get("projet_actif")
    index_defaut = projets_dispo.index(courant) if courant in projets_dispo else 0

    st.markdown("---")
    # Sélection demandée par le code (ex : chantier qui vient d'être créé) :
    # doit être posée AVANT la création du widget.
    a_selectionner = st.session_state.pop("_projet_a_selectionner", None)
    if a_selectionner in projets_dispo:
        st.session_state["selecteur_projet_actif"] = libelle_projet(a_selectionner)
    elif st.session_state.get("selecteur_projet_actif") not in labels:
        st.session_state.pop("selecteur_projet_actif", None)
    choix_label = st.selectbox(
        "📁 Projet actif", labels, index=index_defaut, key="selecteur_projet_actif"
    )
    st.session_state["projet_actif"] = projets_dispo[labels.index(choix_label)]


def libelle_projet(projet_id):
    """Nom, client et N° de dossier (s'il existe), pour les listes déroulantes."""
    info = get_projets().get(projet_id)
    if not info:
        return projet_id or "-"
    txt = f"{info['nom']} ({info['client']})"
    if info.get("num_dossier"):
        txt += f" - Dossier {info['num_dossier']}"
    return txt


def client_projet(projet_id=None):
    """Client du projet (par défaut : le projet actif de la session)."""
    pid = projet_id or projet_actif(st.session_state.get("user") or {})
    return (get_projets().get(pid) or {}).get("client") or "-"


# Valeurs historiques du chantier LGV CASA SUD (reprises telles quelles tant que
# l'intitulé / le N° de dossier ne sont pas renseignés pour ce chantier).
def chantier_pv(defaut_lgv, projet_id=None):
    """Texte de la case « Chantier » des PV : intitulé du projet, sinon son
    nom ; pour LGV CASA SUD sans intitulé, le texte historique `defaut_lgv`."""
    pid = projet_id or projet_actif(st.session_state.get("user") or {})
    info = get_projets().get(pid) or {}
    if info.get("intitule"):
        return info["intitule"]
    if pid == PROJET_PAR_DEFAUT:
        return defaut_lgv
    return info.get("nom") or "-"


def dossier_pv(defaut_lgv, projet_id=None):
    """N° de dossier des PV : celui du projet ; pour LGV CASA SUD sans N°
    renseigné, la valeur historique `defaut_lgv`."""
    pid = projet_id or projet_actif(st.session_state.get("user") or {})
    info = get_projets().get(pid) or {}
    if info.get("num_dossier"):
        return info["num_dossier"]
    return defaut_lgv if pid == PROJET_PAR_DEFAUT else "-"


def _signataires_effectifs(projet_id=None):
    pid = projet_id or projet_actif(st.session_state.get("user") or {})
    info = get_projets().get(pid) or {}
    sig = info.get("signataires") or []
    if sig:
        return sig
    if pid == PROJET_PAR_DEFAUT:
        return SIGNATAIRES_LGV
    # Chantier sans signataire enregistré : cases de visa vides (à signer à
    # la main) plutôt que les noms d'un autre chantier.
    return [{"nom": "", "fonction": "Responsable d'essai"},
            {"nom": "", "fonction": "Chef de laboratoire"}]


def visas_pv(projet_id=None):
    """Cases de visa du PV, de gauche à droite : liste de (titre, nom)."""
    ordre = {f: i for i, f in enumerate(_ORDRE_VISA)}
    tri = sorted(_signataires_effectifs(projet_id), key=lambda e: ordre.get(e["fonction"], 9))
    return [(_TITRES_VISA[e["fonction"]], e["nom"]) for e in tri]


def colonnes_visas(n):
    """Colonnes Excel (1 à 8, début-fin) occupées par n cases de visa."""
    return {1: [(3, 6)], 2: [(2, 4), (6, 8)], 3: [(1, 2), (4, 5), (7, 8)]}[max(1, min(n, 3))]


def nom_signataire(fonction, projet_id=None):
    """Nom du signataire ayant cette fonction sur le chantier ('' si aucun)."""
    for e in _signataires_effectifs(projet_id):
        if e["fonction"] == fonction:
            return e["nom"]
    return ""


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


# ==============================================================================
# DROITS, MODIFICATION ET SUPPRESSION D'UN CHANTIER
# ==============================================================================
def peut_modifier_projet(user_info, projet_id):
    """Admin : tous les chantiers. Responsable de chantier : ceux auxquels
    il a accès."""
    if not user_info:
        return False
    if user_info.get("role") == "admin":
        return True
    return (
        user_info.get("role") == "responsable_chantier"
        and projet_id in liste_projets_utilisateur(user_info)
    )


def peut_supprimer_projet(user_info, projet_id):
    """Admin : tous les chantiers. Responsable de chantier : uniquement ceux
    qu'il a lui-même créés (la suppression efface aussi les données)."""
    if not user_info:
        return False
    if user_info.get("role") == "admin":
        return True
    info = get_projets().get(projet_id) or {}
    return (
        user_info.get("role") == "responsable_chantier"
        and bool(info.get("cree_par"))
        and info.get("cree_par") == user_info.get("username")
    )


def _msg_erreur_projets(e):
    txt = str(e)
    if "signataires" in txt or "intitule" in txt or "num_dossier" in txt:
        return ("Colonne manquante dans la table `projets` : exécutez le script "
                f"ajout_table_projets.sql dans Supabase. ({txt})")
    return f"Erreur Supabase : {txt}"


def _projet_en_base(supabase, projet_id):
    res = supabase.table("projets").select("id").eq("id", projet_id).execute()
    return bool(res.data)


def modifier_projet(supabase, projet_id, nom, client, num_dossier, intitule="", signataires=None):
    """Modifie nom, client et N° de dossier. L'identifiant ne change jamais
    (les données y sont rattachées). Retourne (ok, message)."""
    user = st.session_state.get("user") or {}
    if not peut_modifier_projet(user, projet_id):
        return False, "Vous n'avez pas le droit de modifier ce chantier."
    nom, client, num_dossier = (nom or "").strip(), (client or "").strip(), (num_dossier or "").strip()
    intitule = (intitule or "").strip()
    if len(nom) < 3:
        return False, "Le nom du chantier doit contenir au moins 3 caractères."
    if not client:
        return False, "Le client est obligatoire."
    if not num_dossier:
        return False, "Le N° de dossier est obligatoire."
    if signataires is not None:
        signataires = nettoyer_signataires(signataires)
        if not signataires:
            return False, ("Indiquez au moins une personne qui signe les PV "
                           "(nom et fonction).")
    try:
        if not _projet_en_base(supabase, projet_id):
            return False, ("Ce chantier n'est pas enregistré dans la table `projets` "
                           "(exécutez ajout_table_projets.sql).")
        valeurs = {"nom": nom, "client": client, "num_dossier": num_dossier}
        if intitule != ((get_projets().get(projet_id) or {}).get("intitule") or ""):
            valeurs["intitule"] = intitule or None  # seulement si modifié
        if signataires is not None and signataires != (
                (get_projets().get(projet_id) or {}).get("signataires") or []):
            valeurs["signataires"] = signataires
        supabase.table("projets").update(valeurs).eq("id", projet_id).execute()
    except Exception as e:
        return False, _msg_erreur_projets(e)
    charger_projets(supabase, force=True)
    return True, "Chantier mis à jour."


def compter_donnees_projet(supabase, projet_id):
    """(nb fiches de bétonnage, nb éprouvettes) du chantier, None si inconnu."""
    def _n(table):
        try:
            r = supabase.table(table).select("id", count="exact").eq("projet_id", projet_id).limit(1).execute()
            return r.count
        except Exception:
            return None
    return _n("suivi_betonnage"), _n("suivi_controle_beton")


def supprimer_projet(supabase, projet_id):
    """Supprime DÉFINITIVEMENT un chantier et toutes ses données (fiches de
    bétonnage et éprouvettes), puis retire l'accès à tous les utilisateurs
    (pour qu'un chantier recréé plus tard avec le même nom ne redonne pas
    l'accès aux anciens utilisateurs). Retourne (ok, message)."""
    user = st.session_state.get("user") or {}
    if not peut_supprimer_projet(user, projet_id):
        return False, "Vous n'avez pas le droit de supprimer ce chantier."
    try:
        if not _projet_en_base(supabase, projet_id):
            return False, ("Ce chantier n'est pas enregistré dans la table `projets` "
                           "(exécutez ajout_table_projets.sql).")
        supabase.table("suivi_controle_beton").delete().eq("projet_id", projet_id).execute()
        supabase.table("suivi_betonnage").delete().eq("projet_id", projet_id).execute()
        supabase.table("projets").delete().eq("id", projet_id).execute()
        # Retirer ce chantier des accès de tous les utilisateurs
        res = supabase.table("app_users").select("username, projets_autorises").execute()
        for row in res.data or []:
            liste = [p.strip() for p in (row.get("projets_autorises") or "").split(",") if p.strip()]
            if projet_id in liste:
                liste.remove(projet_id)
                supabase.table("app_users").update(
                    {"projets_autorises": ",".join(liste)}
                ).eq("username", row["username"]).execute()
    except Exception as e:
        return False, f"Erreur Supabase : {e}"
    charger_projets(supabase, force=True)
    if st.session_state.get("projet_actif") == projet_id:
        st.session_state["projet_actif"] = None
    return True, "Chantier supprimé."


# ==============================================================================
# ACCÈS DU CRÉATEUR (table app_users)
# ==============================================================================
def compte_en_base(supabase, username):
    """True si le compte existe dans la table app_users (nécessaire pour
    enregistrer l'accès à un nouveau chantier)."""
    try:
        res = supabase.table("app_users").select("username").eq("username", username).execute()
        return bool(res.data)
    except Exception:
        return False


def attribuer_acces(supabase, username, projet_id):
    """Ajoute projet_id aux chantiers autorisés de l'utilisateur (mise à jour
    ciblée : ne touche ni au mot de passe ni au rôle). Retourne (ok, erreur)."""
    try:
        res = supabase.table("app_users").select("projets_autorises").eq("username", username).execute()
        if not res.data:
            return False, f"Le compte {username} n'existe pas dans la table app_users."
        actuels = [p.strip() for p in (res.data[0].get("projets_autorises") or "").split(",") if p.strip()]
        if projet_id not in actuels:
            actuels.append(projet_id)
        supabase.table("app_users").update(
            {"projets_autorises": ",".join(actuels)}
        ).eq("username", username).execute()
        return True, None
    except Exception as e:
        return False, str(e)
