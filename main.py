import sys
from api import Trainingym

def main():
    # 1. Log in safely
    trainingym = Trainingym()
    trainingym.login()
    
    # 2. Fetch the activities payload
    activities = trainingym.get_activities()
    
    # 3. Pull Saturday's list (Saturday is internal ID 5)
    saturday_classes = activities.get(5, [])
    
    print("\n--- AVAILABLE ON SATURDAY ---")
    if not saturday_classes:
        print("No activities found for Saturday.")
        sys.exit(0)
        
    for act in saturday_classes:
        print(f"Class: {act.get('name')} | Time: {act.get('hour')} | ID: {act.get('id')}")

if __name__ == '__main__':
    main()
