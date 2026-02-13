# Prompt pour Générer une Présentation PowerPoint

Utilise ce prompt avec ChatGPT, Claude, ou un outil de génération de slides (Gamma.app, Tome, etc.) :

---

## PROMPT :

```
Crée une présentation PowerPoint professionnelle de 8-10 slides pour un projet annuel d'école d'informatique (environ 8 minutes de présentation).

**PROJET : BASTET - Robot Intelligent avec Vision IA pour Campus Universitaire**

**STRUCTURE DEMANDÉE :**

**Slide 1 - Titre**
- Titre : "Robot IA Campus - Vision Contextuelle et Interaction"
- Sous-titre : Projet Annuel B2 - [Noms des membres de l'équipe]
- Date : Janvier 2026

**Slide 2 - Rappel du Projet (30 sec)**
- Objectif : Robot autonome pour reconnaissance d'étudiants et assistance contextuelle
- Design : Quadrupède inspiré de **Boston Dynamics Spot** (version miniature, budget étudiant)
- Déployé sur le campus universitaire
- Capable de reconnaître les visages et fournir des informations personnalisées (emploi du temps)

**Slide 3 - Architecture Globale (1 min)**
- Schéma avec : Robot (ROS2 + Caméra) → Serveur RTSP → PC Traitement IA → Frontend Web
- Expliquer le flux de données vidéo et les communications

**Slide 4 - Pourquoi RTSP ? (45 sec)**
- Protocole temps réel optimisé pour la vidéo
- Faible latence (essentiel pour la robotique)
- Compatible ROS2 et la plupart des caméras
- Permet un serveur mandataire pour distribuer le flux

**Slide 5 - Composants Logiciels (1 min 30)**
- Vision : YOLOv8 + face_recognition
- IA : Agent "Bastet" (LLM via LM Studio)
- Backend : FastAPI + WebSocket
- Frontend : React.js
- Intégration MyGES pour l'emploi du temps

**Slide 6 - Fichiers URDF et ROS2 (1 min)**
- URDF = Description XML du robot pour ROS2
- Permet simulation (Gazebo), visualisation (RViz)
- Nos fichiers décrivent le châssis, roues, caméra
- Prêts pour l'intégration ROS2

**Slide 7 - Avancement Actuel (1 min 30)**
Tableau avec :
| Composant | Statut |
| Modèles 3D | ✅ 100% |
| URDF | ✅ 100% |
| Logiciel Vision | ✅ 90% |
| Hardware | 🔄 80% |
| Assemblage Robot | ❌ 0% |
| ROS2 | ❌ En attente |

**Slide 8 - Problèmes Rencontrés (1 min)**
- Latence IA → résolu par streaming + LM Studio
- Reconnaissance faciale instable → persistance temporelle
- API MyGES non documentée → reverse-engineering
- Conflits audio/threads → initialisation séquencée

**Slide 9 - Planning Futur (45 sec)**
- Janvier-Mars : Assemblage robot + électronique
- Mars-Mai : Intégration ROS2 + navigation
- Mai-Juin : Tests sur campus

**Slide 10 - Démo / Conclusion (30 sec)**
- Démo rapide de l'interface web si possible
- Questions ?

**STYLE :**
- Design moderne et épuré
- Couleurs : Bleu foncé + accents cyan ou violet
- Icônes pour illustrer les concepts
- Pas trop de texte, phrases courtes
- Schémas et diagrammes privilégiés

**LANGUE : Français**
```

---

## VERSION COURTE (pour IA rapide) :

```
Crée 10 slides en français pour présenter un projet de robot IA campus :
1. Titre + équipe
2. Objectif : robot reconnaissance visage + emploi du temps
3. Architecture : Robot → RTSP → Serveur IA → Web
4. Pourquoi RTSP (temps réel, ROS2 compatible)
5. Stack : YOLOv8, face_recognition, LLM, FastAPI, React
6. URDF : fichiers XML pour ROS2 (simulation, visualisation)  
7. Avancement : Logiciel 90%, Hardware 80%, Robot pas assemblé
8. Problèmes : latence IA, API MyGES, audio threads
9. Planning : assemblage, ROS2, tests campus
10. Conclusion + questions

Style moderne, bleu foncé, peu de texte.
```

---

## OUTILS RECOMMANDÉS :

1. **Gamma.app** - Génère des présentations à partir de prompts
2. **Tome.app** - IA pour créer des slides visuels
3. **Canva** - Templates modernes + génération IA
4. **Beautiful.ai** - Slides auto-formatées
5. **Google Slides + Gemini** - IA intégrée

---

## CONSEILS POUR LA PRÉSENTATION ORALE (8 min + 5 min questions) :

### Timing suggéré :
- Introduction : 30 sec
- Rappel projet : 30 sec  
- Architecture + RTSP : 1 min 45
- Composants logiciels : 1 min 30
- URDF/ROS2 : 1 min
- Avancement : 1 min 30
- Problèmes : 1 min
- Planning : 45 sec
- Conclusion : 30 sec

### Points à anticiper pour les questions :
1. "Pourquoi pas du HTTP streaming classique ?" → Latence, contrôle bidirectionnel
2. "Comment fonctionne la reconnaissance faciale ?" → Encodage 128D, comparaison euclidienne
3. "Et si MyGES change son API ?" → Abstraction, fallback manuel possible
4. "Quand le robot sera-t-il opérationnel ?" → Objectif fin d'année scolaire
5. "Quel modèle IA utilisez-vous ?" → Mistral 7B via LM Studio, streaming pour réactivité
