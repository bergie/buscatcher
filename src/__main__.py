#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
 Copyright (C) Henri Bergius 2010 <henri.bergius@iki.fi>
 Based on adventure_tablet by:
 Copyright (C) Susanna Huhtanen 2010 <ihmis.suski@gmail.com>

 buscatcher.py is free software: you can redistribute it and/or modify it
 under the terms of the GNU General Public License as published by the
 Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 buscatcher.py is distributed in the hope that it will be useful, but
 WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
 See the GNU General Public License for more details.

 You should have received a copy of the GNU General Public License along
 with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
import gtk, gobject
import point
import urllib, urllib2
from xml.etree import ElementTree as ET
import tempfile
import os
import socket
import optparse

gobject.threads_init()
gtk.gdk.threads_init()
import osmgpsmap

location = None
Geoclue = None
options = None
try:
    import Geoclue
except ImportError:
    try:
        import location
    except ImportError:
        print "No location service found"

osso = None
try:
    import osso
except ImportError:
    pass

conic = None
try:
    import conic
except ImportError:
    pass

class buscatcher(gtk.Window):
    location = None
    kmlfetch = None
    buses = {}
    icons = {}
    downloads = {}
    stop_fetching = False

    def __init__(self):
        win = gtk.Window.__init__(self)

        self.build_ui()
        self.get_location()

        self.icondir = os.path.expanduser('~/.cache/buscatcher')
        if not os.path.exists(self.icondir):
            os.makedirs(self.icondir)

        # Set a default timeout for our HTTP requests so they don't hang when cell connection is bad
        socket.setdefaulttimeout(10)

    def build_ui(self):
        self.set_default_size(500, 500)
        self.connect('destroy', gtk.main_quit, None)
        self.set_title('Helsinki Bus Catcher')

        self.osm = osmgpsmap.GpsMap()

        #connect keyboard shortcuts
        self.osm.set_keyboard_shortcut(osmgpsmap.KEY_FULLSCREEN, gtk.gdk.keyval_from_name("F11"))
        self.osm.set_keyboard_shortcut(osmgpsmap.KEY_UP, gtk.gdk.keyval_from_name("Up"))
        self.osm.set_keyboard_shortcut(osmgpsmap.KEY_DOWN, gtk.gdk.keyval_from_name("Down"))
        self.osm.set_keyboard_shortcut(osmgpsmap.KEY_LEFT, gtk.gdk.keyval_from_name("Left"))
        self.osm.set_keyboard_shortcut(osmgpsmap.KEY_RIGHT, gtk.gdk.keyval_from_name("Right"))

        self.add(self.osm)

    def update_bus(self, bus):
        busid = bus['id']
        if busid not in self.buses:
            # First time we see this bus
            self.buses[busid] = bus

        if 'icon' not in self.buses[busid]:
            # Bus doesn't have an icon yet
            self.buses[busid]['icon'] = gtk.gdk.pixbuf_new_from_file_at_size(self.icons[bus['styleid']], 55, 55)
        else:
            self.osm.remove_image(self.buses[busid]['icon'])

        if self.buses[busid]['styleid'] != bus['styleid']:
            self.buses[busid]['icon'] = gtk.gdk.pixbuf_new_from_file_at_size(self.icons[bus['styleid']], 55, 55)

        self.buses[busid]['location'] = bus['location']

        self.osm.add_image(self.buses[busid]['location'].lat, self.buses[busid]['location'].lon, self.buses[busid]['icon'])

    def get_location(self):
        if options.no_update_position:
            return
        elif Geoclue:
            self.get_location_geoclue()
        elif location:
           self.get_location_liblocation()

    def set_location(self, location):
        if location.lat == 0.0 or location.lon == 0.0:
            return
        self.location = location
        self.osm.set_mapcenter(self.location.lat, self.location.lon, 15)

        if self.kmlfetch is None:
            self.kmlfetch = gobject.timeout_add(options.update_interval, self.fetch_kml)

    def get_location_liblocation(self):
        self.control = location.GPSDControl.get_default()
        self.device = location.GPSDevice()
        self.control.set_properties(preferred_method=location.METHOD_USER_SELECTED,
            preferred_interval=location.INTERVAL_10S)

        self.device.connect("changed", self.location_changed_liblocation, self.control)
        self.control.start()
        if self.device.fix:
            if self.device.fix[1] & location.GPS_DEVICE_LATLONG_SET:
                # We have a "hot" fix
                self.set_location(point.point(self.device.fix[4], self.device.fix[5]))

    def get_location_geoclue(self):
        self.geoclue = Geoclue.DiscoverLocation()
        self.geoclue.init()
        providers = self.geoclue.get_available_providers()
        selected_provider = None
        lat_accuracy = 0
        for provider in providers:
            if not provider['position']:
                continue
            if provider['service'] == 'org.freedesktop.Geoclue.Providers.Example':
                continue
            self.geoclue.set_position_provider(provider['name'])
            coordinates = self.geoclue.get_location_info()
            # Ugly hack for determining most accurate provider as python-geoclue doesn't pass this info
            lat_accuracy_provider = len(str(coordinates['latitude']))
            if lat_accuracy_provider > lat_accuracy:
                lat_accuracy = lat_accuracy_provider
                selected_provider = provider['name']

        self.geoclue.set_position_provider(selected_provider)
        self.geoclue.position.connect_to_signal("PositionChanged", self.location_changed_geoclue)

        try:
            self.set_location(point.point(coordinates['latitude'], coordinates['longitude']))
        except KeyError, e:
            #TODO: Define exception for no location
            pass

    def location_changed_liblocation(self, device, control):
        if not self.device:
            return
        if self.device.fix:
            if self.device.fix[1] & location.GPS_DEVICE_LATLONG_SET:
                self.set_location(point.point(self.device.fix[4], self.device.fix[5]))

    def location_changed_geoclue(self, fields, timestamp, latitude, longitude, altitude, accuracy):
        self.set_location(point.point(latitude, longitude))

    def tracking_start(self):
        self.stop_fetching = False
        if self.kmlfetch is None:
            self.kmlfetch = gobject.timeout_add(options.update_interval, self.fetch_kml)
        if Geoclue:
            pass
        elif location:
            self.control.start()

    def tracking_stop(self):
        self.stop_fetching = True
        if Geoclue:
            pass
        elif location:
            self.control.stop()

    def fetch_kml(self):
        if self.stop_fetching:
            self.kmlfetch = None
            return False

        opener = urllib2.build_opener()
        opener.addheaders = [('User-agent', 'buscatcher/0.1')]
        try:
            req = opener.open(options.kml_url)
            kml = req.read(100000)
        except urllib2.HTTPError, e:
            print('KML HTTP error %s' % (e.code))
            return True
        except urllib2.URLError, e:
            print("KML Connection failed, error %s" % (e.message))
            return True
        except IOError, e:
            print "KML Connection failed"
            return True

        self.parse_kml(kml)

        return True

    def parse_kml(self, kmlxml):
        kml = ET.fromstring(kmlxml)
        buses = []
        for document in kml:
            for folder in document:
                if folder.tag == '{http://www.opengis.net/kml/2.2}Folder':
                    for placemark in folder:
                        if placemark.tag != '{http://www.opengis.net/kml/2.2}Placemark':
                            continue
                        
                        bus = {}
                        bus['id'] = placemark.get('id')
                        numberelement = placemark.find('{http://www.opengis.net/kml/2.2}name')
                        bus['number'] = numberelement.text
                        pointelement = placemark.find('{http://www.opengis.net/kml/2.2}Point').find('{http://www.opengis.net/kml/2.2}coordinates')
                        locationstring = pointelement.text.split(',')
                        bus['location'] = point.point(locationstring[1], locationstring[0])
                        bus['styleid'] = placemark.find('{http://www.opengis.net/kml/2.2}styleUrl').text[1:]
                        buses.append(bus)

                elif folder.tag == '{http://www.opengis.net/kml/2.2}Style':
                    style = folder
                    styleid = style.get('id')
                    iconurl = style.find('{http://www.opengis.net/kml/2.2}IconStyle').find('{http://www.opengis.net/kml/2.2}Icon').find('{http://www.opengis.net/kml/2.2}href').text
                    iconname = os.path.basename(iconurl)
                    iconpath = self.icondir + '/' + iconname

                    if iconurl not in self.downloads:
                        if not os.path.exists(iconpath):
                            print "Downloading " + iconurl
                            web = urllib.urlopen(iconurl)
                            local = open(iconpath, 'w')
                            local.write(web.read(10000))
                            web.close()
                            local.close()
                        self.downloads[iconurl] = iconpath

                    self.icons[styleid] = iconpath

        for bus in buses:
            self.update_bus(bus)

if __name__ == "__main__":
    parser = optparse.OptionParser("buscatcher OPTIONS")
    parser.add_option("--kml-url", type="string", default="http://hkl.seuranta.org/kml")
    parser.add_option("--update-interval", type="int", default=5000)
    parser.add_option("--initial-lat", type="float", default=0.0)
    parser.add_option("--initial-lon", type="float", default=0.0)
    parser.add_option("--no-update-position", action="store_true")
    (options, args) = parser.parse_args()

    if conic:
        # Request Maemo to start an internet connection, as buscatcher doesn't really make sense without one
        connection = conic.Connection()

    u = buscatcher()
    if options.initial_lat != 0.0:
        initial_location = point.point(options.initial_lat, options.initial_lon)
        u.set_location(initial_location)
    u.show_all()

    if osso:
        import devicemonitor
        osso_c = osso.Context("buscatcher", "0.0.1", False)
        device_monitor = devicemonitor.device_monitor(osso_c)
        device_monitor.set_display_off_cb(u.tracking_stop)
        device_monitor.set_display_on_cb(u.tracking_start)

    gtk.main()
