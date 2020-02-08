from core.musiq.music_provider import MusicProvider
from core.models import ArchivedSong, ArchivedPlaylist, PlaylistEntry, ArchivedPlaylistQuery, \
    RequestLog

from urllib.parse import urlparse

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

class SpotifyException(RuntimeError):
    pass

_spotify = None
def get_spotify(settings, check_settings=True, credentials_changed=False):
    global _spotify
    if check_settings and not settings.spotify_enabled:
        raise SpotifyException('Spotify not configured')
    if _spotify is None or credentials_changed:
        _spotify = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials())
    return _spotify

class SpotifyProvider(MusicProvider):

    @staticmethod
    def get_id_from_external_url(url):
        return urlparse(url).path.split('/')[-1]

    def __init__(self, musiq, query, key):
        super().__init__(musiq, query, key)
        self.spotify = get_spotify(musiq.base.settings)
        self.metadata = dict()

    def check_cached(self):
        if self.id is not None:
            archived_song = ArchivedSong.objects.get(url=self.get_external_url())
        elif self.key is not None:
            archived_song = ArchivedSong.objects.get(id=self.key)
        else:
            try:
                archived_song = ArchivedSong.objects.get(url=self.query)
                # TODO check for other yt url formats (youtu.be)
            except ArchivedSong.DoesNotExist:
                return False
        self.id = SpotifyProvider.get_id_from_external_url(archived_song.url)
        # Spotify songs cannot be cached and have to be streamed everytime
        return False

    def check_downloadable(self):
        if self.id is not None:
            results = self.spotify.search(q=self.query, type='track', limit=1)
            try:
                track_info = results['tracks']['items'][0]
            except IndexError:
                self.error = 'Song not found'
                return False
            self.id = track_info['id']
        else:
            track_info = self.spotify.track(self.id)

        self.metadata['internal_url'] = track_info['uri']
        self.metadata['external_url'] = self.get_external_url()
        self.metadata['artist'] = track_info['artist']['name']
        self.metadata['title'] = track_info['name']
        self.metadata['duration'] = track_info['duration_ms'] / 1000

        return True

    def download(self, ip, background=True, archive=True, manually_requested=True):
        self.enqueue(ip, archive=archive, manually_requested=manually_requested)
        # spotify need to be streamed, no download possible
        return True

    def get_metadata(self):
        return self.metadata

    def get_external_url(self):
        return 'https://open.spotify.com/track/' + self.id
