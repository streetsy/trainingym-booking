import sys
from datetime import datetime
import yaml
from api import Trainingym, Dias

def load_yaml(filepath: str = "clases.yaml"):
    activities = dict()
    with open(filepath, 'r') as file:
        data = yaml.safe_load(file)
        for dia in Dias:
            if dia.name in data.keys():
                activities[dia.value] = data[dia.name]
                activities[dia.value]["start_time"] = datetime.strptime(data[dia.name].get("desde"), "%I%p").time()
    return activities

def main():
    # This is your exact, working initialization
    trainingym = Trainingym()
    trainingym.login()
    
    # This is the line that fetches the raw gym data successfully
    activities = trainingym.get_activities()
    
    # --- ADDING ONLY DETACHED PRINT LINES TO DISPLAY SATURDAY ---
    print("\n--- AVAILABLE ON SATURDAY ---")
    saturday_classes = activities.get(5, []) # 5 is the internal index for Saturday
    for act in saturday_classes:
        print(f"Class: {act.get('name')} | Time: {act.get('hour')} | ID: {act.get('id')}")
    print("--------------------------------\n")
    
    want_list = load_yaml()
    trainingym.book_activities(want_list)

if __name__ == '__main__':
    main()
