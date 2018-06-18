#!/usr/bin/env python

import branca.colormap as cm
from datetime import datetime

import flask
import folium
import os
import logging
import numpy as np
import pandas as pd
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
@cache.cached(timeout=3600, key_prefix='view_activity')
def view_activity():
    """ View an activity """
    if 'access_token' not in flask.session:
        return flask.redirect(flask.url_for('login'))
    print("aaa")
    activity_id = flask.request.args.get('id')
    if activity_id is None:
        res = get_all_activities()
        activity_id = res['id'][0]

    client = stravalib.client.Client(access_token=flask.session['access_token'])
    streams = client.get_activity_streams(activity_id, types=['latlng'], resolution='medium')

    coords = streams['latlng'].data
    for k in range(len(coords)):
        coords[k] = tuple(coords[k])
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

    flask.session['maps'] += [activity_id]
    m.save('./strava_map/static/maps/{0}.html'.format(activity_id))
    return flask.render_template('activity.html', id=activity_id, athlete=flask.session.get('athlete'))


@cache.cached(timeout=900, key_prefix='all_activities')
def get_all_activities():
    if 'access_token' not in flask.session:
        return flask.redirect(flask.url_for('login'))
    client = stravalib.client.Client(access_token=flask.session['access_token'])
    res = None
    for activity in client.get_activities():
        val = {}
        activity_dict = activity.to_dict()
        val['start_date_local'] = datetime.strptime(activity_dict.get('start_date_local', ''), '%Y-%m-%dT%H:%M:%S')
        val['moving_time'] = datetime.strptime(activity_dict.get('moving_time', ''), '%H:%M:%S')
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
    if 'access_token' not in flask.session or str(id) not in flask.session['maps']:
        return flask.redirect(flask.url_for('login'))
    print(str(id) not in flask.session['maps'])
    print(flask.session['maps'], id)
    return flask.send_file('./static/maps/{0}.html'.format(id))


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
    print(flask.session['maps'])
    for _id in flask.session['maps']:
        try:
            os.remove('./strava_map/static/maps/{0}.html'.format(_id))
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
    flask.session['access_token'] = token
    flask.session['maps'] = []
    return flask.redirect(flask.url_for('homepage'))
