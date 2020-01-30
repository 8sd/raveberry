from django.conf import settings
from django.shortcuts import render
from django.http import HttpResponse
from django.http import JsonResponse
from django.http import HttpResponseBadRequest
from django.http import HttpResponseServerError
from django.core import serializers
from django.forms.models import model_to_dict
from django.views.decorators.csrf import csrf_exempt

from core.models import QueuedSong
from core.models import CurrentSong
from core.models import ArchivedSong
from core.musiq.suggestions import Suggestions
from core.musiq.player import Player
from core.musiq.song_queue import SongQueue
from core.musiq.youtube import SongTooLargeException, YoutubeProvider, NoPlaylistException
import core.musiq.song_utils as song_utils
import core.state_handler as state_handler

import youtube_dl
import re
import threading

import time
import logging
import ipware


class Musiq:
    def __init__(self, base):
        self.base = base

        self.logger = logging.getLogger('raveberry')

        self.suggestions = Suggestions(self)

        self.queue = QueuedSong.objects
        self.placeholders = []

        self.player = Player(self)
        self.player.start()

    def request_music(self, request):
        key = request.POST.get('key')
        playlist = request.POST.get('playlist') == 'true'
        query = request.POST.get('query')
        # only get ip on user requests
        if self.base.settings.logging_enabled:
            ip, is_routable = ipware.get_client_ip(request)
            if ip is None:
                ip = ''
        else:
            ip = ''

        if playlist:
            #provider = YoutubePlaylistProvider()
            pass
        else:
            provider = YoutubeProvider(self, query, key)

        if not provider.check_cached():
            if not provider.check_downloadable():
                return HttpResponseBadRequest(provider.error)
            provider.download(ip)
        else:
            provider.enqueue(ip)
        return HttpResponse(provider.ok_response)

    def request_radio(self, request):
        try:
            current_song = CurrentSong.objects.get()
        except CurrentSong.DoesNotExist:
            return HttpResponseBadRequest('Need a song to play the radio')
        song_id = song_utils.id_from_url(current_song.url)
        radio_id = 'RD' + song_id
        response = self.request_playlist(request, self.song_provider.get_new_playlist, radio_id)
        if type(response) == HttpResponse:
            return HttpResponse('Queuing radio')
        else:
            return response

    @csrf_exempt
    def post_song(self, request):
        return self.request_music(request)

    def index(self, request):
        context = self.base.context(request)
        return render(request, 'musiq.html', context)

    def state_dict(self):
        state_dict = self.base.state_dict()
        try:
            current_song = CurrentSong.objects.get()
            current_song = model_to_dict(current_song)
        except CurrentSong.DoesNotExist:
            current_song = None
        song_queue = []
        all_songs = self.queue.all()
        if self.base.settings.voting_system:
            all_songs = all_songs.order_by('-votes', 'index')
        for song in all_songs:
            song_dict = model_to_dict(song)
            song_dict['duration_formatted'] = song_utils.format_seconds(song_dict['duration'])
            song_dict['confirmed'] = True
            # find the query of the placeholder that this song replaces (if any)
            for i, placeholder in enumerate(self.placeholders[:]):
                if placeholder['replaced_by'] == song.id:
                    song_dict['replaces'] = placeholder['query']
                    self.placeholders.remove(placeholder)
                    break
            else:
                song_dict['replaces'] = None
            song_queue.append(song_dict)
        song_queue += [{'title': placeholder['query'], 'confirmed': False} for placeholder in self.placeholders]

        if state_dict['alarm']:
            state_dict['current_song'] = {
                'queue_key': -1,
                'manually_requested': False,
                'votes': None,
                'internal_url': '',
                'external_url': '',
                'artist': 'Raveberry',
                'title': 'ALARM!',
                'duration': 10,
                'created': ''
            }
        else:
            state_dict['current_song'] =  current_song
        state_dict['paused'] =  self.player.paused()
        state_dict['progress'] =  self.player.progress()
        state_dict['shuffle'] =  self.player.shuffle
        state_dict['repeat'] =  self.player.repeat
        state_dict['autoplay'] =  self.player.autoplay
        state_dict['volume'] =  self.player.volume
        state_dict['song_queue'] =  song_queue
        return state_dict

    def get_state(self, request):
        state = self.state_dict()
        return JsonResponse(state)

    def update_state(self):
        state_handler.update_state(self.state_dict())
