osso = None
try:
    import osso
except ImportError:
    pass

class device_monitor(osso.DeviceState):

    display_on = None
    display_off = None

    def __init__(self, osso_c):
        osso.DeviceState.__init__(self, osso_c)
        self.set_display_event_cb(self.display_cb, None)

    def set_display_off_cb(self, off_func):
        self.display_off = off_func

    def set_display_on_cb(self, on_func):
        self.display_on = on_func

    def display_cb(self, display_state, user_data=None):
        if (display_state == osso.device_state.OSSO_DISPLAY_OFF):
            if self.display_off != None:
                self.display_off()
        if (display_state == osso.device_state.OSSO_DISPLAY_ON):
             if self.display_on != None:
                self.display_on()

        return False
