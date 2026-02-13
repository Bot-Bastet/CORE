import datetime
import keyring
import getpass
import requests
import base64
import time
import urllib.parse

SERVICE_ID = "MyGes_AI_Helper"

class MyGesIntegration:
    def __init__(self):
        self.username = None
        self.password = None
        self.access_token = None
        self.token_expires_at = 0
        self.schedule = []
        self.headers = {
            "Authorization": "",
            "Content-Type": "application/json"
        }

    def get_credentials(self):
        """
        Try to retrieve stored credentials.
        Returns (username, password) or (None, None).
        Also loads full_name and grade if available.
        """
        try:
            import os
            import json
            if os.path.exists("user_config.txt"):
                with open("user_config.txt", "r") as f:
                    content = f.read().strip()
                    if not content:
                        return None, None
                    
                    try:
                        data = json.loads(content)
                        stored_user = data.get("username")
                        self.full_name = data.get("full_name", "Étudiant")
                        self.grade = data.get("grade", "")
                    except json.JSONDecodeError:
                        # Legacy plain text
                        stored_user = content
                        self.full_name = "Teano" # Default fallback based on persona
                        self.grade = ""

                    if stored_user:
                        pwd = keyring.get_password(SERVICE_ID, stored_user)
                        return stored_user, pwd
            return None, None
        except Exception as e:
            print(f"Error retrieving credentials: {e}")
            return None, None

    def save_credentials(self, username, password, full_name="Teano", grade=""):
        try:
            import json
            keyring.set_password(SERVICE_ID, username, password)
            
            data = {
                "username": username,
                "full_name": full_name,
                "grade": grade
            }
            
            with open("user_config.txt", "w") as f:
                json.dump(data, f)
            print("Credentials saved securely.")
        except Exception as e:
            print(f"Could not save credentials: {e}")

    def _get_token(self):
        print(f"Connecting to MyGes (authentication.kordis.fr) for {self.username}...")
        
        # New approach based on "skolae-app" implicit flow with Basic Auth
        url = "https://authentication.kordis.fr/oauth/authorize?response_type=token&client_id=skolae-app"
        
        # User-Agent is critical to avoid 403 blocks
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
        }

        try:
            # We must use Basic Auth with the request
            # allow_redirects=False is key because we want to grab the Location header from the 302
            response = requests.get(url, auth=(self.username, self.password), headers=headers, allow_redirects=False)
            
            # The token is returned in the Location header of the redirect (302)
            if response.status_code == 302 and "Location" in response.headers:
                location = response.headers["Location"]
                parsed = urllib.parse.urlparse(location)
                # The params are in the fragment (hash) or query depending on implementation. Implicit flow = hash.
                params = urllib.parse.parse_qs(parsed.fragment)
                
                if "access_token" in params:
                    self.access_token = params["access_token"][0]
                    expires_in = int(params.get("expires_in", [3600])[0])
                    self.token_expires_at = time.time() + expires_in
                    
                    self.headers["Authorization"] = f"Bearer {self.access_token}"
                    self.headers["User-Agent"] = headers["User-Agent"]
                    print("MyGes: Authentication successful (Token retrieved).")
                    return True
                else:
                    print("MyGes: Could not find access_token in redirect location.")
                    # Fallback check query params just in case
                    params_q = urllib.parse.parse_qs(parsed.query)
                    if "access_token" in params_q:
                        self.access_token = params_q["access_token"][0]
                        self.headers["Authorization"] = f"Bearer {self.access_token}"
                        self.headers["User-Agent"] = headers["User-Agent"]
                        return True
                    return False
            
            elif response.status_code == 200:
                print("MyGes: Login invalid (returned 200 Page instead of redirect). Creds likely wrong.")
                return False
            elif response.status_code == 401:
                print("MyGes: Login Failed - Invalid Username or Password (401).")
                return False
            elif response.status_code == 403:
                print("MyGes: Access Forbidden (403). WAF Blocking or Bad Creds.")
                return False
            else:
                print(f"MyGes: Unexpected response code {response.status_code}")
                return False

        except Exception as e:
            print(f"MyGes Login connection error: {e}")
            return False


    def _fetch_agenda(self):
        if not self.access_token:
            return

        # Get agenda for today + 7 days
        now = datetime.datetime.now()
        
        # MyGes V2 expects TIMESTAMPS in MILLISECONDS
        start_ts = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        end_time = now + datetime.timedelta(days=7)
        end_ts = int(end_time.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        
        url = f"https://api.kordis.fr/me/agenda?start={start_ts}&end={end_ts}"
        
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            
            events = data.get("result", [])
            self.schedule = []
            
            for event in events:
                start_ts_event = event.get("start_date") / 1000
                end_ts_event = event.get("end_date") / 1000
                
                start_dt = datetime.datetime.fromtimestamp(start_ts_event)
                end_dt = datetime.datetime.fromtimestamp(end_ts_event)
                
                name = event.get("name", "Unknown Class")
                request_type = event.get("type", "CLASS")
                
                rooms = event.get("rooms", [])
                if rooms and isinstance(rooms[0], dict):
                    room_name = rooms[0].get("name", "Unknown Room")
                else:
                    room_name = "Unknown Room"
                
                teacher = event.get("teacher", "Unknown Teacher")
                
                self.schedule.append({
                    "course": name,
                    "start": start_dt,
                    "end": end_dt,
                    "room": room_name,
                    "professor": teacher,
                    "type": request_type
                })
                
            print(f"MyGes: Loaded {len(self.schedule)} events for the next 7 days.")
            
        except Exception as e:
            print(f"Error fetching agenda: {e}")

    def login(self, username=None, password=None):
        if not username or not password:
            saved_user, saved_pass = self.get_credentials()
            if saved_user and saved_pass:
                # Auto-use for convenience if not interactive or valid
                username = saved_user
                password = saved_pass
            
        if not username:
            username = input("Enter MyGes Username: ")
        if not password:
            # Simple masking info - getpass is standard
            print("Enter MyGes Password (hidden): ", end="", flush=True)
            password = getpass.getpass(prompt="")

        self.username = username
        self.password = password
        
        if self._get_token():
            # Save properly if successful login
            if username and password:
                # Ensure we don't overwrite detailed info with defaults if we have it
                fn = getattr(self, "full_name", "Teano")
                gr = getattr(self, "grade", "")
                self.save_credentials(username, password, fn, gr)
            
            self._fetch_agenda()
            return True
        return None
    
    def get_current_activity(self):
        now = datetime.datetime.now()
        for item in self.schedule:
            if item["start"] <= now <= item["end"]:
                return item
        return None
    
    def get_next_activity(self):
        now = datetime.datetime.now()
        # Sort by start time just in case
        sorted_schedule = sorted(self.schedule, key=lambda x: x['start'])
        for item in sorted_schedule:
            if item["start"] > now:
                return item
        return None

    def get_context_string(self):
        # Default: Only TODAY context to keep it light
        identity_header = f"IDENTITÉ: {getattr(self, 'full_name', 'Étudiant')} ({getattr(self, 'grade', '')})"
        
        if not self.schedule:
            return f"{identity_header}\nContext MyGes: Aucun cours prévu."

        now = datetime.datetime.now()
        today = now.date()
        today_events = [e for e in self.schedule if e['start'].date() == today]
        
        if not today_events:
            return f"{identity_header}\nContext MyGes: Aucun cours aujourd'hui."

        summary_parts = [f"{identity_header}\nContext MyGes (Aujourd'huiOnly):"]
        user_status = "Libre"
        
        for item in today_events:
            start_str = item['start'].strftime("%H:%M")
            end_str = item['end'].strftime("%H:%M")
            course_name = item['course']
            room = item['room']
            
            if now > item['end']:
                state = "[FINI]"
            elif item['start'] <= now <= item['end']:
                state = "[ACTUEL]"
                user_status = f"En cours de {course_name}"
            else:
                state = "[FUTUR]"
            
            summary_parts.append(f"- {start_str}-{end_str} {course_name} ({room})")
            
        summary_parts.append(f"Statut: {user_status}")
        return "\n".join(summary_parts)

    def get_week_summary(self):
        # For on-demand injection
        identity_header = f"IDENTITÉ: {getattr(self, 'full_name', 'Étudiant')} ({getattr(self, 'grade', '')})"
        
        if not self.schedule:
            return f"{identity_header}\nAgenda vide cette semaine."
            
        summary = f"{identity_header}\nAgenda Semaine Prochaine:\n"
        sorted_schedule = sorted(self.schedule, key=lambda x: x['start'])
        
        current_day = None
        for item in sorted_schedule:
            day_str = item['start'].strftime("%A %d/%m")
            if day_str != current_day:
                summary += f"\n[{day_str}]:\n"
                current_day = day_str
            
            start = item['start'].strftime("%H:%M")
            end = item['end'].strftime("%H:%M")
            summary += f"  {start}-{end} : {item['course']}\n"
            
        return summary
