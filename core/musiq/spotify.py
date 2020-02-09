from core.musiq.music_provider import MusicProvider
from core.models import ArchivedSong, ArchivedPlaylist, PlaylistEntry, ArchivedPlaylistQuery, \
    RequestLog

from urllib.parse import urlparse

class SpotifyProvider(MusicProvider):
    @staticmethod
    def get_id_from_external_url(url):
        return urlparse(url).path.split('/')[-1]

    @staticmethod
    def get_id_from_internal_url(url):
        return url.split(':')[-1]

    def __init__(self, musiq, query, key):
        super().__init__(musiq, query, key)
        self.type = 'spotify'
        self.spotify_library = musiq.player.player.library
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
        if self.id is None:
            results = self.spotify_library.search({'any': [self.query]})

            try:
                track_info = results[0].tracks[0]
            except AttributeError:
                self.error = 'Song not found'
                return False
            self.id = SpotifyProvider.get_id_from_internal_url(track_info.uri)
            self.gather_metadata(track_info=track_info)
        else:
            self.gather_metadata()

        return True

    def download(self, ip, background=True, archive=True, manually_requested=True):
        self.enqueue(ip, archive=archive, manually_requested=manually_requested)
        # spotify need to be streamed, no download possible
        return True

    def gather_metadata(self, track_info=None):
        if not track_info:
            results = self.spotify_library.search({'uri': [self.get_internal_url()]})
            track_info = results[0].tracks[0]

        self.metadata['internal_url'] = track_info.uri
        self.metadata['external_url'] = self.get_external_url()
        self.metadata['artist'] = track_info.artists[0].name
        self.metadata['title'] = track_info.name
        self.metadata['duration'] = track_info.length / 1000

    def get_metadata(self):
        if not self.metadata:
            self.gather_metadata()
        return self.metadata

    def get_internal_url(self):
        return 'spotify:track:' + self.id

    def get_external_url(self):
        return 'https://open.spotify.com/track/' + self.id
