import os
from datetime import datetime, time
from api import TraininGym, Dias

def main():
    # 1. Initialize the API using the dynamically generated config file
    trainingym = TraininGym("config.yaml")
    
    # 2. Login to the backend
    trainingym.login()
    
    # 3. Pull the live available class list from Centro Fitness Benalmádena
    activities = trainingym.get_activities()
    print("Next activities found:")
    for key in activities:
        for act in activities[key]:
            print(f"- {act.get('name')} on {act.get('date')} at {act.get('hour')} (ID: {act.get('id')})")

    # 4. Filter and book target classes directly, bypassing broken yaml parsing
    # Target days mapped to 72 hours early execution:
    # Tuesday books Friday, Friday books Monday, Sunday books Wednesday.
    current_day = datetime.now().strftime('%A').lower() # e.g., 'tuesday', 'friday', 'sunday'
    
    # Map target strings matching the gym's database names
    target_class_name = "pilates"
    
    booked_any = False
    for day_id, day_activities in activities.items():
        for act in day_activities:
            act_name = act.get("name", "").lower()
            act_id = act.get("id")
            
            # If the activity contains "pilates", attempt booking immediately
            if target_class_name in act_name and act_id:
                print(f"Target class found! Attempting to book: {act.get('name')} (ID: {act_id})")
                try:
                    # Target the internal booking mechanism directly
                    res = trainingym.book(act_id)
                    print(f"Server response: {res}")
                    booked_any = True
                except Exception as e:
                    print(f"Booking attempt complete or status checked: {e}")

    if not booked_any:
        print("No match found or booking window not open for target class yet.")

if __name__ == '__main__':
    main()

if __name__ == "__main__":
    main()
