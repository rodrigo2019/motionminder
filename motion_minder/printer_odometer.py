import json
import random
import time
from threading import Thread

import requests
import websocket

import motion_minder

_MOONRAKER_URL = "127.0.0.1:7125"
_NAMESPACE = "motion_minder"


class PrinterOdometer:
    def __init__(self, update_interval=20):
        self._id = random.randint(0, 10000)

        x, y, z = motion_minder.get_odometer(f"http://{_MOONRAKER_URL}", _NAMESPACE)
        self._odom = {"x": x, "y": y, "z": z}
        self._last_position = {"x": None, "y": None, "z": None}

        self._homed_axis = ""
        self._get_homed_axis()

        self._messages_counter = 0
        self._update_interval = update_interval
        self._subscribed = False

        self._state_thread = Thread(target=self.check_klipper_state_routine)
        self._state_thread.daemon = True
        self._state_thread.start()

        self.websocket = websocket.WebSocketApp(
            f"ws://{_MOONRAKER_URL}/websocket",
            on_message=self.on_message,
            on_open=self.on_open,
        )
        self.websocket.run_forever(reconnect=5)

    def _get_homed_axis(self):
        ret = requests.get(f"http://{_MOONRAKER_URL}/printer/objects/query?toolhead")
        self._homed_axis = ""
        try:
            if 200 <= ret.status_code < 300:
                self._homed_axis = ret.json().get("result", {}).get("status", {}). \
                    get("toolhead", {}).get("homed_axes", "")
        except:
            pass

    def check_klipper_state_routine(self):
        while True:
            if not self._subscribed:
                try:
                    klipper_state = requests.get(f"http://{_MOONRAKER_URL}/server/info")
                    if 200 <= klipper_state.status_code < 300:
                        klipper_state = klipper_state.json()["result"]["klippy_state"]
                        if klipper_state == "ready":
                            self.subscribe(self.websocket)
                            self._subscribed = True
                except:
                    pass
            time.sleep(2)

    def _update_single_axis_odometer(self, axis, value):
        if self._last_position[axis] is not None:
            self._odom[axis] += abs(value - self._last_position[axis])
        self._last_position[axis] = value

    def _process_motion_report(self, param):
        if "motion_report" not in param:
            return

        live_position = param["motion_report"].get("live_position", None)
        if live_position is None:
            return

        for axis in ["x", "y", "z"]:
            value = live_position.get(axis)
            if value is not None and axis in self._homed_axis:
                self._update_single_axis_odometer(axis, value)
        self._messages_counter += 1

        if self._messages_counter % self._update_interval == 0:
            motion_minder.set_odometer(
                f"http://{_MOONRAKER_URL}", _NAMESPACE,
                self._odom["x"], self._odom["y"], self._odom["z"]
            )

    def _process_toolhead(self, param):
        if not "toolhead" in param:
            return
        homed_axes = param["toolhead"].get("homed_axes", None)
        if homed_axes is not None:
            self._homed_axis = homed_axes

    def _process_klipper_state(self, param):
        if not "klipper" in param:
            return
        klipper = param["klipper"]
        state = klipper.get("active_state", None)
        if state is not None and state == "inactive":
            self._subscribed = False

    def on_message(self, ws, message):
        message = json.loads(message)
        params = message["params"]
        for param in params:
            self._process_motion_report(param)
            self._process_toolhead(param)
            self._process_klipper_state(param)

    def subscribe(self, websock):
        subscribe_objects = {
            "motion_report": None,
            "toolhead": ["homed_axes"],
        }

        websock.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "printer.objects.subscribe",
                    "params": {"objects": subscribe_objects},
                    "id": self._id,
                }
            )
        )

    def on_open(self, ws):
        self.subscribe(ws)


if __name__ == "__main__":
    p = PrinterOdometer()
