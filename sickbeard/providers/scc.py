# Author: Idan Gutman
# Modified by jkaberg, https://github.com/jkaberg for SceneAccess
# URL: http://code.google.com/p/sickbeard/
#
# This file is part of SickRage.
#
# SickRage is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# SickRage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with SickRage.  If not, see <http://www.gnu.org/licenses/>.

import re
import traceback
import datetime
import urlparse
import sickbeard
import generic
import urllib
from sickbeard.common import Quality
from sickbeard import logger
from sickbeard import tvcache
from sickbeard import db
from sickbeard import classes
from sickbeard import helpers
from sickbeard import show_name_helpers
from sickbeard.exceptions import ex
from sickbeard import clients
from lib import requests
from lib.requests import exceptions
from sickbeard.bs4_parser import BS4Parser
from lib.unidecode import unidecode
from sickbeard.helpers import sanitizeSceneName


class SCCProvider(generic.TorrentProvider):

    def __init__(self):

        generic.TorrentProvider.__init__(self, "SceneAccess")

        self.supportsBacklog = True

        self.enabled = False
        self.username = None
        self.password = None
        self.ratio = None
        self.minseed = None
        self.minleech = None

        self.cache = SCCCache(self)

        self.urls = {'base_url': 'https://sceneaccess.eu',
                'login': 'https://sceneaccess.eu/login',
                'detail': 'https://www.sceneaccess.eu/details?id=%s',
                'search': 'https://sceneaccess.eu/browse?search=%s&method=1&%s',
                'nonscene': 'https://sceneaccess.eu/nonscene?search=%s&method=1&c44=44&c45=44',
                'foreign': 'https://sceneaccess.eu/foreign?search=%s&method=1&c34=34&c33=33',
                'archive': 'https://sceneaccess.eu/archive?search=%s&method=1&c26=26',
                'download': 'https://www.sceneaccess.eu/%s',
                }

        self.url = self.urls['base_url']

        self.categories = "c27=27&c17=17&c11=11"

    def isEnabled(self):
        return self.enabled

    def imageName(self):
        return 'scc.png'

    def getQuality(self, item, anime=False):

        quality = Quality.sceneQuality(item[0], anime)
        return quality

    def _doLogin(self):

        login_params = {'username': self.username,
                        'password': self.password,
                        'submit': 'come on in',
        }

        self.session = requests.Session()

        try:
            response = self.session.post(self.urls['login'], data=login_params, headers=self.headers, timeout=30, verify=False)
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError), e:
            logger.log(u'Unable to connect to ' + self.name + ' provider: ' + ex(e), logger.ERROR)
            return False

        if re.search('Username or password incorrect', response.text) \
                or re.search('<title>SceneAccess \| Login</title>', response.text) \
                or response.status_code == 401:
            logger.log(u'Invalid username or password for ' + self.name + ' Check your settings', logger.ERROR)
            return False

        return True

    def _get_season_search_strings(self, ep_obj):

        search_string = {'Season': []}
        for show_name in set(show_name_helpers.allPossibleShowNames(self.show)):
            if ep_obj.show.air_by_date or ep_obj.show.sports:
                ep_string = show_name + ' ' + str(ep_obj.airdate).split('-')[0]
            elif ep_obj.show.anime:
                ep_string = show_name + ' ' + "%d" % ep_obj.scene_absolute_number
            else:
                ep_string = show_name + ' S%02d' % int(ep_obj.scene_season)  #1) showName SXX

            search_string['Season'].append(ep_string)

        return [search_string]

    def _get_episode_search_strings(self, ep_obj, add_string=''):

        search_string = {'Episode': []}

        if not ep_obj:
            return []

        if self.show.air_by_date:
            for show_name in set(show_name_helpers.allPossibleShowNames(self.show)):
                ep_string = sanitizeSceneName(show_name) + ' ' + \
                            str(ep_obj.airdate).replace('-', '|')
                search_string['Episode'].append(ep_string)
        elif self.show.sports:
            for show_name in set(show_name_helpers.allPossibleShowNames(self.show)):
                ep_string = sanitizeSceneName(show_name) + ' ' + \
                            str(ep_obj.airdate).replace('-', '|') + '|' + \
                            ep_obj.airdate.strftime('%b')
                search_string['Episode'].append(ep_string)
        elif self.show.anime:
            for show_name in set(show_name_helpers.allPossibleShowNames(self.show)):
                ep_string = sanitizeSceneName(show_name) + ' ' + \
                            "%i" % int(ep_obj.scene_absolute_number)
                search_string['Episode'].append(ep_string)
        else:
            for show_name in set(show_name_helpers.allPossibleShowNames(self.show)):
                ep_string = show_name_helpers.sanitizeSceneName(show_name) + ' ' + \
                            sickbeard.config.naming_ep_type[2] % {'seasonnumber': ep_obj.scene_season,
                                                                  'episodenumber': ep_obj.scene_episode} + ' %s' % add_string

                search_string['Episode'].append(re.sub('\s+', ' ', ep_string))

        return [search_string]

    def _isSection(self, section, text):
        title = '<title>.+? \| %s</title>' % section
        if re.search(title, text, re.IGNORECASE):
            return True
        else:
            return False

    def _doSearch(self, search_params, search_mode='eponly', epcount=0, age=0):

        results = []
        items = {'Season': [], 'Episode': [], 'RSS': []}

        if not self._doLogin():
            return results

        data = []
        searchURLS = []

        for mode in search_params.keys():
            for search_string in search_params[mode]:

                if isinstance(search_string, unicode):
                    search_string = unidecode(search_string)

                if mode == 'Season' and search_mode == 'sponly':
                    searchURLS += [self.urls['archive'] % (urllib.quote(search_string))]
                else:
                    searchURLS += [self.urls['search'] % (urllib.quote(search_string), self.categories)]
                    searchURLS += [self.urls['nonscene'] % (urllib.quote(search_string))]
                    searchURLS += [self.urls['foreign'] % (urllib.quote(search_string))]

                for searchURL in searchURLS:
                    logger.log(u"Search string: " + searchURL, logger.DEBUG)
                    try:
                        data += [x for x in [self.getURL(searchURL)] if x]
                    except Exception as e:
                        logger.log(u"Unable to fetch data reason: {0}".format(str(e)), logger.WARNING)

                if not len(data):
                    continue

            try:
                for dataItem in data:
                    with BS4Parser(dataItem, features=["html5lib", "permissive"]) as html:
                        torrent_table = html.find('table', attrs={'id': 'torrents-table'})
                        torrent_rows = torrent_table.find_all('tr') if torrent_table else []

                        #Continue only if at least one Release is found
                        if len(torrent_rows) < 2:
                            if html.title:
                                source = self.name + " (" + html.title.string + ")"
                            else:
                                source = self.name
                            logger.log(u"The Data returned from " + source + " does not contain any torrent", logger.DEBUG)
                            continue

                        for result in torrent_table.find_all('tr')[1:]:

                            try:
                                link = result.find('td', attrs={'class': 'ttr_name'}).find('a')
                                all_urls = result.find('td', attrs={'class': 'td_dl'}).find_all('a', limit=2)
                                # Foreign section contain two links, the others one
                                if self._isSection('Foreign', dataItem):
                                    url = all_urls[1]
                                else:
                                    url = all_urls[0]

                                title = link.string
                                if re.search('\.\.\.', title):
                                    data = self.getURL(self.url + "/" + link['href'])
                                    if data:
                                        with BS4Parser(data) as details_html:
                                            title = re.search('(?<=").+(?<!")', details_html.title.string).group(0)
                                download_url = self.urls['download'] % url['href']
                                id = int(link['href'].replace('details?id=', ''))
                                seeders = int(result.find('td', attrs={'class': 'ttr_seeders'}).string)
                                leechers = int(result.find('td', attrs={'class': 'ttr_leechers'}).string)
                            except (AttributeError, TypeError):
                                continue

                            if mode != 'RSS' and (seeders < self.minseed or leechers < self.minleech):
                                continue

                            if not title or not download_url:
                                continue

                            item = title, download_url, id, seeders, leechers
                            logger.log(u"Found result: " + title.replace(' ','.') + " (" + searchURL + ")", logger.DEBUG)

                            items[mode].append(item)

                # for each search mode sort all the items by seeders
                items[mode].sort(key=lambda tup: tup[3], reverse=True)
                results += items[mode]

            except Exception, e:
                logger.log(u"Failed parsing " + self.name + " Traceback: " + traceback.format_exc(), logger.ERROR)
                continue

        return results

    def _get_title_and_url(self, item):

        title, url, id, seeders, leechers = item

        if title:
            title = u'' + title
            title = title.replace(' ', '.')

        if url:
            url = str(url).replace('&amp;', '&')

        return (title, url)

    def findPropers(self, search_date=datetime.datetime.today()):

        results = []

        myDB = db.DBConnection()
        sqlResults = myDB.select(
            'SELECT s.show_name, e.showid, e.season, e.episode, e.status, e.airdate FROM tv_episodes AS e' +
            ' INNER JOIN tv_shows AS s ON (e.showid = s.indexer_id)' +
            ' WHERE e.airdate >= ' + str(search_date.toordinal()) +
            ' AND (e.status IN (' + ','.join([str(x) for x in Quality.DOWNLOADED]) + ')' +
            ' OR (e.status IN (' + ','.join([str(x) for x in Quality.SNATCHED]) + ')))'
        )

        if not sqlResults:
            return []

        for sqlshow in sqlResults:
            self.show = helpers.findCertainShow(sickbeard.showList, int(sqlshow["showid"]))
            if self.show:
                curEp = self.show.getEpisode(int(sqlshow["season"]), int(sqlshow["episode"]))

                searchString = self._get_episode_search_strings(curEp, add_string='PROPER|REPACK')

                for item in self._doSearch(searchString[0]):
                    title, url = self._get_title_and_url(item)
                    results.append(classes.Proper(title, url, datetime.datetime.today(), self.show))

        return results

    def seedRatio(self):
        return self.ratio


class SCCCache(tvcache.TVCache):
    def __init__(self, provider):

        tvcache.TVCache.__init__(self, provider)

        # only poll SCC every 10 minutes max
        self.minTime = 20

    def _getRSSData(self):
        search_params = {'RSS': ['']}
        return {'entries': self.provider._doSearch(search_params)}

provider = SCCProvider()
