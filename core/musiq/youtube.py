from django.conf import settings

import core.musiq.song_utils as song_utils

import youtube_dl
import subprocess
import requests
import pickle
import errno
import json
import os
import threading
import mutagen.easymp4

from urllib.parse import urlparse
from urllib.parse import parse_qs

from core.models import ArchivedSong
from core.musiq.music_provider import MusicProvider


class MyLogger(object):
    def debug(self, msg):
        if settings.DEBUG:
            print(msg)
    def warning(self, msg):
        if settings.DEBUG:
            print(msg)
    def error(self, msg):
        print(msg)


class SongTooLargeException(Exception):
    pass


class NoPlaylistException(Exception):
    pass


class Downloader:

    def __init__(self, musiq, target):
        self.musiq = musiq
        self.target = target
        self.info_dict = None
        # youtube-dl --format bestaudio[ext=m4a]/best[ext=m4a] --output '%(id)s.%(ext)s --no-playlist --write-thumbnail --default-search auto --add-metadata --embed-thumbnail
        self.ydl_opts = {
            'format': 'bestaudio[ext=m4a]/best[ext=m4a]',
            'outtmpl': os.path.join(settings.SONGS_CACHE_DIR, '%(id)s.%(ext)s'),
            'noplaylist': True,
            'no_color': True,
            'writethumbnail': True,
            'default_search': 'auto',
            'postprocessors': [{
                    'key': 'FFmpegMetadata',
                }, {
                    'key': 'EmbedThumbnail',
                    # overwrite any thumbnails already present
                    'already_have_thumbnail': True,
                }],
            'logger': MyLogger(),
        }

    def get_playlist_info(self):
        del self.ydl_opts['noplaylist']
        self.ydl_opts['extract_flat'] = True

        # in case of a radio playist, restrict the number of songs that are downloaded
        # if we received just the id, it is an id starting with 'RD'
        # if its a url, the id is behind a '&list='
        if song_utils.is_radio(self.target):
            self.ydl_opts['playlistend'] = self.musiq.base.settings.max_playlist_items

        with youtube_dl.YoutubeDL(self.ydl_opts) as ydl:
            self.info_dict = ydl.extract_info(self.target, download=False)

        if self.info_dict['_type'] != 'playlist' or 'entries' not in self.info_dict:
            raise NoPlaylistException('Not a Playlist')

        playlist_info = {}
        playlist_info['id'] = self.info_dict['id']
        playlist_info['urls'] = []
        if 'title' in self.info_dict:
            playlist_info['title'] = self.info_dict['title']
        for entry in self.info_dict['entries']:
            playlist_info['urls'].append('https://www.youtube.com/watch?v=' + entry['id'])
        return playlist_info


class YoutubeProvider(MusicProvider):
    def __init__(self, musiq, query, key):
        super().__init__(musiq, query, key)
        self.ok_response = 'song queued'

        self.info_dict = None

        # youtube-dl --format bestaudio[ext=m4a]/best[ext=m4a] --output '%(id)s.%(ext)s --no-playlist --write-thumbnail --default-search auto --add-metadata --embed-thumbnail
        self.ydl_opts = {
            'format': 'bestaudio[ext=m4a]/best[ext=m4a]',
            'outtmpl': os.path.join(settings.SONGS_CACHE_DIR, '%(id)s.%(ext)s'),
            'noplaylist': True,
            'no_color': True,
            'writethumbnail': True,
            'default_search': 'auto',
            'postprocessors': [{
                    'key': 'FFmpegMetadata',
                }, {
                    'key': 'EmbedThumbnail',
                    # overwrite any thumbnails already present
                    'already_have_thumbnail': True,
                }],
            'logger': MyLogger(),
        }

    def check_cached(self):
        if self.key is not None:
            archived_song = ArchivedSong.objects.get(id=self.key)
        else:
            try:
                archived_song = ArchivedSong.objects.get(url=self.query)
                # TODO check for other yt url formats (youtu.be)
            except ArchivedSong.DoesNotExist:
                return False
        self.id = self.id_from_url(archived_song.url)
        return os.path.isfile(self.get_path())

    def check_downloadable(self):
        try:
            with youtube_dl.YoutubeDL(self.ydl_opts) as ydl:
                self.info_dict = ydl.extract_info(self.query, download=False)
        except youtube_dl.utils.DownloadError as e:
            self.error = e
            return False

        # this value is not an exact match, but it's a good approximation
        if 'entries' in self.info_dict:
            self.info_dict = self.info_dict['entries'][0]

        self.id = self.info_dict['id']

        size = self.info_dict['filesize']
        max_size = self.musiq.base.settings.max_download_size * 1024 * 1024
        if max_size != 0 and song_utils.path_from_id(self.info_dict['id']) is None and (size is None or size > max_size):
            self.error = 'Song too long'
            return False
        return True

    def _download (self, ip):
        error = None
        location = None

        self.placeholder = {'query': self.query, 'replaced_by': None}
        self.musiq.placeholders.append(self.placeholder)
        self.musiq.update_state()

        try:
            with youtube_dl.YoutubeDL(self.ydl_opts) as ydl:
                ydl.download(['https://www.youtube.com/watch?v=' + self.id])

            location = self.get_path()
            base = os.path.splitext(location)[0]
            thumbnail = base + '.jpg'
            try:
                os.remove(thumbnail)
            except FileNotFoundError:
                self.musiq.base.logger.info('tried to delete ' + thumbnail + ' but does not exist')

            try:
                # tag the file with replaygain to perform volume normalization
                subprocess.call(['aacgain', '-q', '-c', location], stdout=subprocess.DEVNULL)
            except OSError as e:
                if e.errno == errno.ENOENT:
                    pass  # the aacgain package was not found. Skip normalization
                else:
                    raise

        except youtube_dl.utils.DownloadError as e:
            error = e

        if error is not None or location is None:
            self.musiq.logger.error('accessible video could not be downloaded: ' + str(self.id))
            self.musiq.logger.error(error)
            self.musiq.logger.error('location: ' + str(location))
            self.musiq.placeholders.remove(placeholder)
            self.musiq.update_state()
            return
        print(ip)
        self.enqueue(ip)

    def download(self, ip, background=True):
        # check if file was already downloaded and only download if necessary
        if not os.path.isfile(self.get_path()):
            thread = threading.Thread(target=self._download, args=(ip,), daemon=True)
            thread.start()
            if not background:
                thread.join()

    def get_metadata(self):
        '''gathers the metadata for the song at the given location.
        'title' and 'duration' is read from tags, the 'url' is built from the location'''

        parsed = mutagen.easymp4.EasyMP4(self.get_path())
        metadata = dict()

        metadata['url'] = 'https://www.youtube.com/watch?v=' + self.id
        if parsed.tags is not None:
            if 'artist' in parsed.tags:
                metadata['artist'] = parsed.tags['artist'][0]
            if 'title' in parsed.tags:
                metadata['title'] = parsed.tags['title'][0]
        if 'artist' not in metadata:
            metadata['artist'] = ''
        if 'title' not in metadata:
            metadata['title'] = metadata['url']
        if parsed.info is not None and parsed.info.length is not None:
            metadata['duration'] = self._format_seconds(parsed.info.length)
        else:
            metadata['duration'] = '??:??'

        metadata['internal_url'] = self.get_internal_url()

        return metadata

    def get_path(self):
        path = os.path.join(settings.SONGS_CACHE_DIR, self.id + '.m4a')
        path = path.replace('~', os.environ['HOME'])
        path = os.path.abspath(path)
        return path

    def get_internal_url(self):
        return 'file://' + self.get_path()

    def _format_seconds(self, seconds):
        hours, seconds = seconds // 3600, seconds % 3600
        minutes, seconds = seconds // 60, seconds % 60

        formatted = ''
        if hours > 0:
            formatted += '{:02d}:'.format(int(hours))
        formatted += '{0:02d}:{1:02d}'.format(int(minutes), int(seconds))
        return formatted

    def id_from_url(url):
        return parse_qs(urlparse(url).query)['v'][0]

if __name__ == '__main__':
    Downloader().fetch()

