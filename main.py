import sys
from datetime import datetime
import yaml
from api import Trainingym, Dias

def load_yaml():
    with open("clases.yaml", "r") as stream:
        try:
            data = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)
            sys.exit(1)

    activities = {}
    for dia in Dias:
        if dia.name in data and data[dia.name] is not None:
            activities[dia.value] = {}
            activities[dia.value]["start_time"] = datetime.strptime(data[dia.name].get("desde"), "%I%p").time()
            activities[dia.value]["end_time"] = datetime.strptime(data[dia.name].get("hasta"), "%I%p").time()
            
            # This line caused the crash! It expects a string or a structure under 'activity'
            activities[dia.value]["activities"] = data[dia.name].get("activity")

    return activities

def main():
    trainingym = Trainingym("config.yaml")
    trainingym.login()
    
    activities = trainingym.get_activities()
    want_list = load_yaml()

    trainingym.book_activities(want_list)

if __name__ == '__main__':
    main()

if __name__ == "__main__":
    main()
