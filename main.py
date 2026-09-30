#!/usr/bin/env python3

import argparse
from api import Trainingym, Dias
import logging
import yaml
from datetime import datetime
from time import sleep
import json

#logging.basicConfig()
#logging.getLogger().setLevel(logging.DEBUG)
#requests_log = logging.getLogger("requests.packages.urllib3")
#requests_log.setLevel(logging.DEBUG)
#requests_log.propagate = True

def print_activities(activities):
    msg = list()
    for act in activities:
        date = act["date"].strftime("%a %d %H:%M")
        txt = f"{act['name']} el {date} ({act['id']})"
        msg.append(txt)
    print("\n".join(msg))


def load_yaml(filepath: str = "clases.yaml"):
    activities = dict()
    with open(filepath, 'r') as file:
        data = yaml.safe_load(file)
        for dia in Dias:
            if dia.name in data.keys():
                activities[dia.value] = data[dia.name]
                activities[dia.value]["start_time"] = datetime.strptime(data[dia.name].get("desde"), "%I%p").time()
    return activities

def parse_arguments():
    parser = argparse.ArgumentParser(description="Login to Trainingym app")
    parser.add_argument("email", nargs="?", default=None, help="Email address to use for login")
    parser.add_argument("password", nargs="?", default=None, help="Password to use for login")
    parser.add_argument("-c", "--config", default="config.yaml", help="Path to config file")

    return parser.parse_args()

def load_credentials_from_config(filepath):
    try:
        with open(filepath, "r") as config_file:
            config_data = yaml.safe_load(config_file)
            if config_data is not None:
                return config_data.get("email"), config_data.get("password")
            else:
                raise ValueError("Invalid YAML data in config.yaml")
    except FileNotFoundError:
        raise FileNotFoundError("config.yaml not found")
    except Exception as e:
        raise ValueError(f"Error loading values from config.yaml: {e}")

def main():
    args = parse_arguments()

    if args.email is None or args.password is None:
        email, password = load_credentials_from_config(args.config)
        args.email = args.email or email
        args.password = args.password or password

    if args.email is None or args.password is None:
        raise ValueError("Missing email or password")

    trainingym = Trainingym()
    trainingym.login(args.email, args.password)
    print(f"Welcome {trainingym.person_fullname} !")

    print("Next activities before cancellation:")
    print_activities(trainingym.next_activities())

    # --- DIRECT CANCELLATION OVERRIDE ---
    target_cancel_id = 83884612  # Saturday's Circuit Training ID
    print(f"\n> Attempting API cancellation for Activity ID: {target_cancel_id}...")
    try:
        response = trainingym.activityCancel(target_cancel_id)
        print(f"Server Cancellation Response Status Code: {response.status_code}")
    except Exception as e:
        print(f"Cancellation request executed. Result check: {e}")
    # ------------------------------------

    # Clear cache and verify current state
    trainingym.myBookings.cache_clear()
    
    # Wait 2 seconds to let the gym backend complete the registration update
    sleep(2)
    
    print("\nNext activities after cancellation:")
    print_activities(trainingym.next_activities())

if __name__ == "__main__":
    main()
