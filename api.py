import json
import sys
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
from random import random as random_decimal
from time import sleep
from zoneinfo import ZoneInfo

import requests

# The gym works in Madrid time, but GitHub runners are UTC, so never use a bare datetime.now()
TZ = ZoneInfo("Europe/Madrid")

# Booking opens this many hours before a class starts...
BOOKING_OPENS_HOURS = 72
# ...and we only go for classes that are this many hours away from "now". The lower bound
# leaves room for a late start, the upper bound excludes anything that starts later in the
# day than the class that has just opened (e.g. an 11:30 or an afternoon class).
WINDOW_MIN_HOURS = 71
WINDOW_MAX_HOURS = 72.25


def now_madrid() -> datetime:
    """Current Madrid time as a naive datetime (comparable with the gym's class times)"""
    return datetime.now(TZ).replace(tzinfo=None)


def log(msg: str):
    """Progress log. Goes to stderr (visible in the GitHub Actions log) so that stdout stays a
    short summary that main.py pipes to Telegram."""
    print(msg, file=sys.stderr, flush=True)


class Dias(Enum):
    lunes = 0
    martes = 1
    miercoles = 2
    jueves = 3
    viernes = 4
    sabado = 5
    domingo = 6

class BookingState(Enum):
    NOT_OPEN = 0 # sin mensaje
    AVAILABLE = 1 # boton para Reserva ya
    ONLINE_BOOKING_UNAVAILABLE = 2 # boton Sin plaza online
    BOOKED = 3 # boton para Cancelar reserva
    NOT_AVAILABLE = 5 # boton No Disponible
    FINISHED = 6 # se acabo la clase
    FULL = 7 # boton Completa

class Trainingym:
    base_url = "https://www.trainingymapp.com/webtouch/"

    def __init__(self):
        # Everything that belongs to one gym member lives on the instance (NOT on the class),
        # otherwise several members would share one cookie jar and overwrite each other's login.
        self.headers = dict()
        self.gym = dict()
        self.session = requests.Session()
        self.dry_run = False
        self.window_min = WINDOW_MIN_HOURS  # only classes starting this many hours from now...
        self.window_max = WINDOW_MAX_HOURS  # ...to this many (test runs can widen it)
        self.any_class = False  # test mode: ignore clases.yaml day/name/time, take whatever is ~72h away
        self._sent = {}  # class id -> times seen "full" since we sent a booking request

    @property
    def person_fullname(self):
        return " ".join([self.gym["nombreSocio"], self.gym["apellidoSocio"]]).title()

    def login(self, email, password):
        data = {
            "user": email,
            "pass": password,
            "tokenKiosko": ""
        }

        req = self.query("/api/indexs/login", json=data, method="POST")

        login = req.json()

        assert login["Result"] == 0, "failed to login?"
        assert json.loads(login.get("d")).get("baja", True) == False, "user not active?"
        centros = json.loads(login.get("d")).get("Centros", {})
        assert len(centros) >= 1, "no centers found"

        #print(centros)
        self.gym = centros[0]
        # NOTE: We are using the connect.sid cookie, not the Token.
        #self.headers["Authorization"] = "Bearer " + self.gym.get("accessToken")

    def query(self, path, json=None, data=None, method="GET", headers=dict()):
        url = self.base_url + path.lstrip("/")
        headers = {**self.headers, **headers}

        req = self.session.request(method, url,
            headers=headers,
            data=data, json=json
        )

        return req

    def query_user(self, path, referer: str, nocache: bool = True):
        headers = {
            "Accept": "application/json, text/plain, */*",
        }
        if referer:
            headers["Referer"] = f"{self.base_url}/{referer}"
        if nocache:
            arg = f"noCache={random_decimal()}"
            if "?" in path:
                path += f"&{arg}"
            else:
                path += f"?{arg}"
        req = self.query(path, headers=headers)
        req.raise_for_status()

        return req.json()

    def parse_date(self, date):
        return datetime.strptime(date, "%Y-%m-%d")

    def parse_hour(self, hour, date=None):
        hour_parsed = datetime.strptime(hour, "%H:%M")
        if not date:
            return hour_parsed
        return date.replace(hour=hour_parsed.hour, minute=hour_parsed.minute)

    @lru_cache
    def myBookings(self):
        # aaData[], new to old, 100 entries
        return self.query_user("/api/usuarios/reservas/myBookings", referer="actividades")

    @lru_cache
    def getActivityGroups(self, lang: int = 6):
        return self.query_user("/api/usuarios/reservas/getActivityGroups?idLanguage={lang}", referer="actividades")

    @lru_cache
    def getSchedulesApp(self, start_date: str = None, end_days: int = 4):
        if start_date is not None:
            raise NotImplementedError("Setting a start date is not implemented yet")

        today = now_madrid().date()

        start_date = str(today)
        end_date = str(today + timedelta(days=end_days))

        url = f"/api/usuarios/reservas/getSchedulesApp/?startDateTime={start_date}&endDateTime={end_date}"
        # calendar[*] dateProgram,schedules[*]
        return self.query_user(url, referer="actividades")

    def activityBook(self, activity_id: int):
        return self._virtual_activity(activity_id, "bookTouch")

    def activityCancel(self, activity_id: int):
        return self._virtual_activity(activity_id, "cancelBook")

    # this is processed in background by the server
    # returns response on Websocket (currently not implemented)
    # returns empty list [] if request was accepted?
    def _virtual_activity(self, activity_id: int, action: str):
        url = f"/api/usuarios/reservas/{action}/{activity_id}"
        body = {"connectionClientId":""}
        return self.query(url, method="POST", json=body)

    def next_activities(self, strict: bool = False):
        """ Return a list of upcoming activities. Include completed today """
        today = now_madrid()
        bookings = self.myBookings()
        activities = list()

        if not isinstance(bookings, dict) or bookings.get("aaData") is None:
            # A member with no bookings may legitimately get an empty reply. When booking, carry on
            # (the schedule itself says whether the class is already booked); only the final
            # report of bookings (strict) treats it as "could not read".
            if strict:
                raise RuntimeError("gym returned no bookings data")
            bookings = {"aaData": []}
        for booking_day in bookings["aaData"]:
            day = self.parse_date(booking_day["dateProgram"])
            if day.date() < today.date():
                continue
            for data in booking_day.get("schedules") or []:
                fulldate = self.parse_hour(data["timeStart"], day)
                activity = {
                    "id": data["id"],
                    "name": data["activity"]["name"],
                    "date": fulldate
                }
                activities.append(activity)

        activities.sort(key=lambda x: x["date"])
        return activities

    def refresh(self):
        """ Forget cached gym data so the next call asks the gym again """
        type(self).myBookings.cache_clear()
        type(self).getSchedulesApp.cache_clear()

    def find_target_classes(self, parsed_activities: dict):
        """
        Classes from clases.yaml that we want to book right now: right weekday, wanted name,
        not earlier than "desde", and starting WINDOW_MIN_HOURS..WINDOW_MAX_HOURS from now
        (i.e. the class whose booking has just opened, not a later one the same day).
        """
        now = now_madrid()
        targets = []

        schedule = self.getSchedulesApp()
        if not isinstance(schedule, dict) or not schedule.get("calendar"):
            raise RuntimeError("gym returned no schedule")
        for gym_day in schedule["calendar"]:
            day_schedule = self.parse_date(gym_day["dateProgram"])
            dow = day_schedule.weekday()
            # si no haces clase ese día de la semana
            if dow not in parsed_activities.keys() and not self.any_class:
                continue

            time_start_since = parsed_activities.get(dow, {}).get("start_time")
            wanted_activities = parsed_activities.get(dow, {}).get("clases")
            for activity in gym_day.get("schedules") or []:
                time_start = self.parse_hour(activity["timeStart"]).time()
                name = activity["activity"].get("name")
                if not self.any_class:
                    if time_start < time_start_since:
                        continue
                    if not self.check_name(name, wanted_activities):
                        continue

                start = datetime.combine(day_schedule, time_start)
                hours_until = (start - now).total_seconds() / 3600
                if not (self.window_min <= hours_until <= self.window_max):
                    continue

                targets.append({
                    "id": activity["id"],
                    "name": name,
                    "start": start,
                    "opens": start - timedelta(hours=BOOKING_OPENS_HOURS),
                    "activity": activity,
                })

        return targets

    def booking_attempt(self, parsed_activities: dict):
        """
        One complete pass: ask the gym for fresh data, find the target class(es) and try to
        book each one. Returns a list of (target, status), where status is one of:
          booked       we booked it and the gym confirmed it
          already      it was already booked
          full         no places left
          waiting      booking is not open yet / not reservable (worth retrying)
          unconfirmed  we sent the booking but the gym did not show it yet (worth retrying)
          dry-run      dry run only, nothing was sent
        """
        self.refresh()
        booked_ids = {x["id"] for x in self.next_activities()}

        results = []
        for target in self.find_target_classes(parsed_activities):
            results.append((target, self._book_target(target, booked_ids)))
        return results

    def _book_target(self, target: dict, booked_ids: set) -> str:
        activity = target["activity"]
        activity_id = target["id"]
        booking_state = activity.get("bookingState")

        if activity_id in booked_ids or booking_state == BookingState.BOOKED.value:
            return "already"

        info = activity.get("bookingInfo") or {}
        capacity = activity.get("capacityAssistant")
        places = None if capacity is None else capacity - info.get("bookedPlaces", 0)

        if booking_state == BookingState.FULL.value or (places is not None and places <= 0):
            # If we already sent a booking, the "full" may just be our own place not showing in
            # our bookings yet: give it a few more checks before accepting that we lost out
            if activity_id in self._sent and self._sent[activity_id] < 3:
                self._sent[activity_id] += 1
                return "unconfirmed"
            return "full"

        if not info.get("isReservable"):
            return "waiting"

        # TODO: Trainingym does not allow booking two simultaneous classes (two sessions within same schedule)
        if self.dry_run:
            log(f"> DRY RUN: would book {target['name']} {target['start']:%a %H:%M} id {activity_id} ({places} left)")
            return "dry-run"

        log(f"> Booking {target['name']} {target['start']:%a %H:%M} id {activity_id} ({places} left)")
        self.activityBook(activity_id)
        self._sent[activity_id] = 0

        # The gym processes the booking in the background, so check that it really registered
        for _ in range(2):
            sleep(2)
            type(self).myBookings.cache_clear()
            if activity_id in {x["id"] for x in self.next_activities()}:
                return "booked"
        return "unconfirmed"

    def book_activities(self, parsed_activities: dict):
        """ Single pass, no retries (kept for TESTmain.py). main.py uses booking_attempt() """
        for target, status in self.booking_attempt(parsed_activities):
            print(f"{target['name']} on {target['start']:%a %H:%M}: {status}")

    def check_name(self, name: str, search_in: list) -> bool:
        """ Perform name variants to check if name is inside """

        name = name.lower()
        names = [x.lower() for x in search_in]

        if name in names:
            return True
        if name.replace(" ", "") in names:
            return True
        if name in [x.replace(" ", "") for x in names]:
            return True

        return False

    def cache_clear(self):
        self.refresh()


# https://www.trainingymapp.com/webtouch/api/viewss/menu/usuario
#
# https://www.trainingymapp.com/webtouch/api/ejercicios/sessionActual
#
# https://www.trainingymapp.com/webtouch/actividades
# https://www.trainingymapp.com/webtouch/api/usuarios/reservas/getActivityGroups?idLanguage=6&noCache=0.24304466395171853
# https://www.trainingymapp.com/webtouch/api/viewss/reservas/reservashorario
# https://www.trainingymapp.com/webtouch/api/usuarios/reservas/myBookings
# https://www.trainingymapp.com/webtouch/api/usuarios/reservas/getSchedulesApp/?startDateTime=2023-11-06&endDateTime=2023-11-11&noCache=0.9027295201678431
#
#
# POST https://www.trainingymapp.com/webtouch/api/usuarios/reservas/bookTouch/60383865?noCache=0.55294557303735
# {"connectionClientId":""}
# POST https://www.trainingymapp.com/webtouch/api/usuarios/reservas/cancelBook/60383865?noCache=0.9921674306812154
