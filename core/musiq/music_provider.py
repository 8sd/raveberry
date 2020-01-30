from core.models import ArchivedSong
from core.models import ArchivedQuery
from core.models import RequestLog

from core.musiq.player import Player

from django.db import transaction
from django.db.models import F


class MusicProvider:
    def __init__(self, musiq, query, key):
        self.musiq = musiq
        self.query = query
        self.key = key
        self.id = None
        self.placeholder = None

        if key is None:
            self.archived = False
        else:
            self.archived = True


    def check_cached(self, music_id):
        pass

    def check_downloadable(self):
        pass

    def enqueue(self, ip, archive=True, manually_requested=True):
        metadata = self.get_metadata()

        # Increase counter of song/playlist
        with transaction.atomic():
            queryset = ArchivedSong.objects.filter(url=metadata['url'])
            if queryset.count() == 0:
                initial_counter = 1 if archive else 0
                archived_song = ArchivedSong.objects.create(url=metadata['url'], artist=metadata['artist'], title=metadata['title'], counter=initial_counter)
            else:
                if archive:
                    queryset.update(counter=F('counter')+1)
                archived_song = queryset.get()

            if archive:
                ArchivedQuery.objects.get_or_create(song=archived_song, query=self.query)

        if archive and ip:
            RequestLog.objects.create(song=archived_song, address=ip)

        song = self.musiq.queue.enqueue(metadata, manually_requested)
        if self.placeholder:
            self.placeholder['replaced_by'] = song.id
            self.musiq.update_state()
        Player.queue_semaphore.release()

    def download(self, ip, background=True):
        self.enqueue(ip)
        pass

    def get_metadata(self):
        return dict()

    def get_internal_url(self):
        pass