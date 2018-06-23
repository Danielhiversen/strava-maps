#!/usr/bin/env python

import datetime
import errno
import logging
import os
import random
import shutil
import string

import branca.colormap as cm
import dropbox
import flask
import folium
import gpxpy
import gpxpy.gpx
import numpy as np
import pandas as pd
import requests


import stravalib
from flask_caching import Cache

cache = Cache(config={'CACHE_TYPE': 'simple'})
app = flask.Flask(__name__)
app.config.from_object('config')
cache.init_app(app)

logging.basicConfig(level=logging.INFO)


@app.route('/')
def homepage():
    if 'access_token' not in flask.session:
        return flask.redirect(flask.url_for('login'))
    return flask.redirect(flask.url_for('activities'))


@app.route('/activities')
@app.route('/activities?sort=<var>')
def activities():
    if 'access_token' not in flask.session:
        return flask.redirect(flask.url_for('login'))
    res = get_all_activities()
    var = flask.request.args.get('sort')
    if var is not None and var in res.columns:
        if var in flask.request.referrer:
            ascending = not flask.session.get('ascending', False)
        else:
            ascending = True
        flask.session['ascending'] = ascending
        res = res.sort_values([var, 'start_date_local'], ascending=ascending)
    return flask.render_template('activities.html', activities=res, athlete=flask.session.get('athlete'))


@app.route('/activity')
@app.route('/activity?id=<var>')
# @cache.cached(timeout=3600, key_prefix='view_activity')
def view_activity():
    """ View an activity """
    if 'access_token' not in flask.session:
        return flask.redirect(flask.url_for('login'))
    activity_id = flask.request.args.get('id')
    if activity_id is None:
        res = get_all_activities()
        activity_id = res['id'][0]

    client = stravalib.client.Client(access_token=flask.session['access_token'])
    streams = client.get_activity_streams(activity_id, types=['latlng', 'time', 'altitude'], resolution='medium')
    start_date = client.get_activity(activity_id).start_date
    print(type(start_date))
    coords = streams['latlng'].data
    gpx = gpxpy.gpx.GPX()

    # Create first track in our GPX:
    gpx_track = gpxpy.gpx.GPXTrack()
    gpx.tracks.append(gpx_track)

    # Create first segment in our GPX track:
    gpx_segment = gpxpy.gpx.GPXTrackSegment()
    gpx_track.segments.append(gpx_segment)

    for k in range(len(coords)):
        coords[k] = tuple(coords[k])
        gpx_segment.points.append(gpxpy.gpx.GPXTrackPoint(latitude=coords[k][0], longitude=coords[k][1],
                                                          elevation=streams['altitude'].data[k],
                                                          time=start_date + datetime.timedelta(seconds=streams['time'].data[k])))

        # geometry.append(Point((coords[k][1], coords[k][0])))
        # data.append([(start_date + datetime.timedelta(seconds=streams['time'].data[k])).strftime('%Y-%m-%dT%H:%M:%S'),
        #              streams['altitude'].data[k]])
    dbx = dropbox.Dropbox(app.config['AUTH_TOKEN_DB'])

    with open('my_lines.gpx', 'w') as f:
        f.write(gpx.to_xml())
    with open('my_lines.gpx', 'rb') as f:
        dbx.files_upload(f.read(), '/my_lines.gpx', mode=dropbox.files.WriteMode('overwrite'))

    gpx_url = dbx.sharing_create_shared_link('/my_lines.gpx').url.replace('dl=0', 'dl=1')
    url = 'http://openwps.statkart.no/skwms1/wps.elevation2?request=Execute&' \
          'service=WPS&version=1.0.0&identifier=elevationChart&dataInputs=gpx=@xlink:href={}'.format(gpx_url)
    resp = requests.get(url=url)
    print(resp)
    data = resp.text
    k = data.find('wps:ComplexData mimeType="image/png">')
    l = data[k + 37:].find('</wps:ComplexData>')
    img_url = data[k+37:k+37+l]
    print(img_url)
    altitude_image = img_url

    ctr = tuple(np.mean(coords, axis=0))
    m = folium.Map(location=ctr, zoom_start=12, tiles=None)

    topo4 = folium.raster_layers.TileLayer(tiles='http://opencache.statkart.no/gatekeeper/gk/gk.open_gmaps?layers=topo4&zoom={z}&x={x}&y={y}',
                                           attr='<a href="http://www.kartverket.no/">Kartverket</a>')
    topo4.add_to(m)

    z = None
    if z is None:
        line = folium.PolyLine(locations=coords)
    else:
        zcolors = ['r', 'g', 'c', 'b', 'm']
        line = folium.features.ColorLine(coords,
                                         colors=z,
                                         colormap=cm.LinearColormap(zcolors, vmin=2, vmax=5),
                                         weight=3)

    line.add_to(m)
    # add markers for start and end
    folium.Marker(coords[0], icon=folium.Icon(color='green')).add_to(m)
    folium.Marker(coords[-1], icon=folium.Icon(color='black')).add_to(m)

    m.save('./src/cache/{0}/{1}.html'.format(flask.session.get('user_id'), activity_id))
    return flask.render_template('activity.html', id=activity_id,
                                 athlete=flask.session.get('athlete'), altitude_image=altitude_image)


@cache.cached(timeout=900, key_prefix='all_activities')
def get_all_activities():
    if 'access_token' not in flask.session:
        return flask.redirect(flask.url_for('login'))
    client = stravalib.client.Client(access_token=flask.session['access_token'])
    res = None
    for activity in client.get_activities():
        val = {}
        activity_dict = activity.to_dict()
        val['start_date_local'] = datetime.datetime.strptime(activity_dict.get('start_date_local', ''), '%Y-%m-%dT%H:%M:%S')
        val['moving_time'] = datetime.datetime.strptime(activity_dict.get('moving_time', ''), '%H:%M:%S')
        val['activity_name'] = activity_dict.get('name', '')
        val['type'] = activity_dict.get('type', '')
        val['id'] = activity.id
        for key in ['distance', 'average_speed', 'max_speed']:
            val[key] = float(activity_dict.get(key, 0))

        if res is None:
            res = pd.DataFrame(columns=val.keys())
        res = res.append(val, ignore_index=True)
    return res

# --------------------------------
# Templates for Jinja conversions.
# --------------------------------


@app.template_filter('speed_to_pace')
def speed_to_pace(x):
    """ convert speed in m/s to pace in min/km """
    if x == 0:
        return 0
    else:
        p = 16.6666666 / x  # 1 m/s --> min/km
        return ':'.join([str(int(p)),
                         str(int((p * 60) % 60)).zfill(2)])


@app.template_filter('get_date')
def get_date_filter(dt):
    return dt.date()


@app.template_filter('get_time')
def get_time_filter(dt):
    return dt.time()


@app.route('/maps/<int:id>.html')
def show_map(id):
    """ GIven an activity ID, get and show the map """
    if 'access_token' not in flask.session:
        return flask.redirect(flask.url_for('login'))
    return flask.send_file('./cache/{0}/{1}.html'.format(flask.session.get('user_id'), id))


@app.route('/login')
def login():
    # https: // flask - login.readthedocs.io / en / latest /
    client = stravalib.client.Client()
    auth_url = client.authorization_url(client_id=app.config['CLIENT_ID'],
                                        scope=None,
                                        redirect_uri=app.config['AUTH_URL'])
    return flask.render_template('login.html', auth_url=auth_url)


@app.route('/logout')
def logout():
    flask.session.pop('access_token')
    try:
        shutil.rmtree('./src/cache/{0}'.format(flask.session.get('user_id',' ')))
    except OSError:
        pass
    return flask.redirect(flask.url_for('homepage'))


@app.route('/auth')
def auth_done():
    code = flask.request.args.get('code', '')
    client = stravalib.client.Client()
    token = client.exchange_code_for_token(client_id=app.config['CLIENT_ID'],
                                           client_secret=app.config['CLIENT_SECRET'],
                                           code=code)
    flask.session['athlete'] = client.get_athlete().to_dict()
    flask.session['user_id'] = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(9))
    flask.session['access_token'] = token

    try:
        os.makedirs('./src/cache/{0}'.format(flask.session['user_id']))
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    return flask.redirect(flask.url_for('homepage'))
