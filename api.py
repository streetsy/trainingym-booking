from zoneinfo import ZoneInfo
import requests
import json
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
from random import random as random_decimal

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
    headers = dict()
    gym = dict()
    session = requests.Session()

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

        today = datetime.now().date()

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

    def next_activities(self):
        """ Return a list of upcoming activities. Include completed today """
        today = datetime.now()
        bookings = self.myBookings()
        activities = list()

        for booking_day in bookings["aaData"]:
            day = self.parse_date(booking_day["dateProgram"])
            if day.date() < today.date():
                continue
            for data in booking_day["schedules"]:
                fulldate = self.parse_hour(data["timeStart"], day)
                activity = {
                    "id": data["id"],
                    "name": data["activity"]["name"],
                    "date": fulldate
                }
                activities.append(activity)

        activities.sort(key=lambda x: x["date"])
        return activities

    def book_activities(self, parsed_activities: dict):
        """ Send the list of days->activites, and process if can do """
        booked_activities = self.next_activities()

        for gym_day in self.getSchedulesApp().get("calendar"):
            day_schedule = self.parse_date(gym_day["dateProgram"])
            dow = day_schedule.weekday()
            # si no haces clase ese día de la semana
            if dow not in parsed_activities.keys():
                continue

            time_start_since = parsed_activities[dow].get("start_time")
            wanted_activities = parsed_activities[dow].get("clases")
            for activity in gym_day.get("schedules"):
                time_start = self.parse_hour(activity["timeStart"]).time()
                if time_start < time_start_since:
                    continue
                name = activity["activity"].get("name")
                if not self.check_name(name, wanted_activities):
                    continue

                day_activity = datetime.combine(day_schedule, time_start)
                now = datetime.now(ZoneInfo("Europe/Madrid")).replace(tzinfo=None)
                if now >= day_activity:
                    continue

                # Only the class that just opened (~72h away). Lower bound leaves ~1h
                # slack for GitHub Actions dispatch delays; upper bound excludes anything
                # starting more than ~15 min after the trigger time.
                hours_until = (day_activity - now).total_seconds() / 3600
                if not (71 <= hours_until <= 72.25):
                    continue

                date_str = day_schedule.strftime("%a") + " " + time_start.strftime("%H:%M")
                booking_state = activity.get("bookingState")
                msg = f"Found {name} on {date_str}"

                if (
                    booking_state == BookingState.BOOKED or
                    activity["id"] in [x["id"] for x in self.next_activities()]
                ):
                    msg += " (already booked)"
                    #print(msg)
                    continue

                places = activity.get("capacityAssistant", 0) - activity["bookingInfo"].get("bookedPlaces", 0)
                if booking_state == BookingState.FULL or places <= 0:
                    msg += " (no places available)"
                    print(msg)
                    continue

                if booking_state == BookingState.NOT_AVAILABLE:
                    msg += " (not available)"
                    print(msg)
                    continue

                if not activity["bookingInfo"].get("isReservable"):
                    msg += " (not reservable)"
                    print(msg)
                    continue

                msg += f' {activity["id"]}'
                print(msg)

                # TODO: Trainingym does not allow booking two simultaneous classes (two sessions within same schedule)
                print(f"> Booking activity ({places} left)")
                self.activityBook(activity["id"])

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
        for name, method in vars(self).items():
            if callable(method) and hasattr(method, 'cache_clear'):
                method.cache_clear()


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
