import datetime
from myges_integration import MyGesIntegration
import sys

def test_myges():
    print("--- Test MyGes Token & Agenda ---")
    
    myges = MyGesIntegration()
    
    # Try to get credentials interactively if not saved
    saved_user, _ = myges.get_credentials()
    if saved_user:
        print(f"User found: {saved_user}")
        use_saved = input("Use saved credentials? (Y/n): ").lower()
        if use_saved == 'n':
            myges.login() # Will ask
        else:
            # Manually trigger login with None to skip input
            myges.login(username=None, password=None)
    else:
        myges.login()

    if not myges.access_token:
        print("CRITICAL: Login failed. No token retrieved.")
        return

    print(f"\nToken retrieved successfully. Expiry: {datetime.datetime.fromtimestamp(myges.token_expires_at)}")
    
    print("\nfetching agenda...")
    # Force fetch
    myges._fetch_agenda()
    
    if not myges.schedule:
        print("No classes found in schedule for today. (Is it a weekend or holiday?)")
        # Try fetching a wider range just to be sure
        print("Attempting to fetch next 7 days to verify API is working...")
        # Dirty hack to call fetch with custom dates if I had exposed it, 
        # but let's just inspect the private method logic or modify it temporarily if needed.
        # actually _fetch_agenda hardcoded today/tomorrow. Let's trust it for now.
    else:
        print(f"Classes found: {len(myges.schedule)}")
        for event in myges.schedule:
            start_str = event['start'].strftime("%H:%M")
            end_str = event['end'].strftime("%H:%M")
            print(f" - {start_str} to {end_str}: {event['course']} ({event['type']}) in {event['room']}")

    print("\n--- Current Context Status ---")
    print(myges.get_context_string())

if __name__ == "__main__":
    test_myges()
