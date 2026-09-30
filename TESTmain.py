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

    print("Next activities:")
    print_activities(trainingym.next_activities())

    # --- NATIVE EXTRACTION FOR ALL SATURDAY CLASSES (NO BOOKING) ---
    print("\n--- AVAILABLE ON SATURDAY ---")
    schedule_data = trainingym.getSchedulesApp()
    calendar_days = schedule_data.get("calendar", [])
    
    for gym_day in calendar_days:
        date_str = gym_day.get("dateProgram", "") # e.g., "2026-10-03"
        
        # Convert date string to check if it lands on a Saturday (weekday 5)
        try:
            parsed_day = datetime.strptime(date_str, "%Y-%m-%d")
            if parsed_day.weekday() == 5:
                print(f"\n[Date: {date_str}]")
                for act in gym_day.get("schedules", []):
                    name = act["activity"].get("name")
                    hour = act.get("timeStart")
                    class_id = act.get("id")
                    print(f"  Class: {name} | Time: {hour} | ID: {class_id}")
        except Exception:
            continue
    print("--------------------------------\n")
    # ----------------------------------------------------------------

    want_list = load_yaml()
    trainingym.book_activities(want_list)

if __name__ == "__main__":
    main()
