"""
Bastet AI V2 - Calendar Integration (MyGES)
Charge l'agenda dynamiquement selon la personne reconnue.
Retire l'agenda du contexte si la personne n'est plus visible depuis X secondes.
"""

import os
import json
import time
import threading
from datetime import datetime, timedelta

# Import MyGES integration
try:
    from .myges_integration import MyGesIntegration
except ImportError:
    MyGesIntegration = None

# Timeout en secondes avant de retirer l'agenda du contexte
PRESENCE_TIMEOUT = 5.0
CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "cache")


class CalendarIntegration:
    def __init__(self, config: dict, shared_data: dict = None):
        self.config = config
        self.shared_data = shared_data if shared_data is not None else {}
        self.ready = False
        self.myges = None
        self.current_person = None
        self.last_seen_time = 0
        self.cached_agendas = {}  # {person_name: agenda_context}
        
        # Créer le dossier cache
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)
        
        # Expose l'état dans shared_data
        self.shared_data['calendar_loaded'] = False
        self.shared_data['schedule_context'] = ""

    def connect(self):
        """Tente de se connecter à MyGES."""
        if not MyGesIntegration:
            print("⚠ Module MyGES non disponible")
            return False
        
        try:
            self.myges = MyGesIntegration()
            result = self.myges.login()
            
            if result:
                self.ready = True
                self.shared_data['calendar_loaded'] = True
                print(f"✓ Calendrier MyGES connecté pour {getattr(self.myges, 'full_name', 'Utilisateur')}")
                return True
            else:
                print("⚠ Connexion MyGES échouée (identifiants invalides?)")
                return False
                
        except Exception as e:
            print(f"⚠ Erreur connexion MyGES: {e}")
            return False

    def is_loaded(self) -> bool:
        return self.shared_data.get('calendar_loaded', False)

    def update_for_person(self, person_name: str):
        """Met à jour le contexte quand une personne est reconnue."""
        if not person_name or person_name == "Inconnu":
            return
        
        now = time.time()
        
        # Si c'est une nouvelle personne ou retour après absence
        if person_name != self.current_person:
            self.current_person = person_name
            self._load_or_fetch_agenda(person_name)
            print(f"📅 Agenda chargé pour: {person_name}")
        
        # Mettre à jour le timestamp de dernière présence
        self.last_seen_time = now

    def check_presence_timeout(self):
        """Vérifie si la personne est toujours là, sinon retire l'agenda du contexte."""
        if not self.current_person:
            return
        
        now = time.time()
        
        if now - self.last_seen_time > PRESENCE_TIMEOUT:
            # Personne partie depuis plus de X secondes
            print(f"⏳ {self.current_person} plus visible - agenda retiré du contexte")
            
            # Sauvegarder en cache avant de retirer
            self._save_to_cache(self.current_person)
            
            # Retirer du contexte LLM
            self.current_person = None
            self.shared_data['schedule_context'] = ""

    def _load_or_fetch_agenda(self, person_name: str):
        """Charge l'agenda depuis le cache ou fetch depuis MyGES."""
        cache_file = os.path.join(CACHE_DIR, f"{person_name.lower()}_agenda.json")
        
        # Essayer de charger depuis le cache (si moins de 30 min)
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    cache_time = data.get('timestamp', 0)
                    if time.time() - cache_time < 1800:  # 30 minutes
                        self.shared_data['schedule_context'] = data.get('context', '')
                        self.cached_agendas[person_name] = data.get('context', '')
                        return
            except:
                pass
        
        # Sinon fetch depuis MyGES
        if self.myges and self.ready:
            if time.time() < getattr(self.myges, 'token_expires_at', 0):
                self.myges._fetch_agenda()
            context = self._build_context()
            self.shared_data['schedule_context'] = context
            self.cached_agendas[person_name] = context
            self._save_to_cache(person_name)

    def _save_to_cache(self, person_name: str):
        """Sauvegarde l'agenda en cache."""
        if person_name not in self.cached_agendas:
            return
        
        cache_file = os.path.join(CACHE_DIR, f"{person_name.lower()}_agenda.json")
        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump({
                    'timestamp': time.time(),
                    'person': person_name,
                    'context': self.cached_agendas[person_name]
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠ Erreur sauvegarde cache: {e}")

    def _build_context(self) -> str:
        """Construit le contexte agenda pour aujourd'hui et demain."""
        if not self.myges or not self.myges.schedule:
            return "Aucun cours prévu"
        
        now = datetime.now()
        today = now.date()
        tomorrow = today + timedelta(days=1)
        
        lines = []
        
        full_name = getattr(self.myges, 'full_name', 'Utilisateur')
        grade = getattr(self.myges, 'grade', '')
        lines.append(f"IDENTITÉ: {full_name} ({grade})")
        
        # Aujourd'hui
        today_events = [e for e in self.myges.schedule if e['start'].date() == today]
        if today_events:
            lines.append("\nAUJOURD'HUI:")
            for e in sorted(today_events, key=lambda x: x['start']):
                start = e['start'].strftime("%H:%M")
                end = e['end'].strftime("%H:%M")
                status = ""
                if e['start'] <= now <= e['end']:
                    status = " [EN COURS]"
                elif e['end'] < now:
                    status = " [TERMINÉ]"
                lines.append(f"  {start}-{end}: {e['course']} ({e['room']}){status}")
        else:
            lines.append("\nAUJOURD'HUI: Pas de cours")
        
        # Demain
        tomorrow_events = [e for e in self.myges.schedule if e['start'].date() == tomorrow]
        if tomorrow_events:
            lines.append("\nDEMAIN:")
            for e in sorted(tomorrow_events, key=lambda x: x['start']):
                start = e['start'].strftime("%H:%M")
                end = e['end'].strftime("%H:%M")
                lines.append(f"  {start}-{end}: {e['course']} ({e['room']})")
        else:
            lines.append("\nDEMAIN: Pas de cours")
        
        return "\n".join(lines)

    def get_schedule_context(self) -> str:
        return self.shared_data.get('schedule_context', "")
